"""
bot/code_planning_view.py
개쵸 자동 코드 변경의 2단계 승인 UI.

🆕 v2 수정사항:
- 1단계 → 2단계 전환 로직 개선
- 버튼 즉시 비활성화 (코드 생성 시작 전)
- 모든 메시지가 같은 메시지에서 진행 (혼란 방지)
- 코드 변경 시 R&D 포럼 자동 게시
"""

import asyncio
import logging
from datetime import datetime

import discord

log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# 공통 헬퍼
# ═══════════════════════════════════════════════════════════════════

async def _disable_view(message: discord.Message, view: discord.ui.View) -> None:
    """View의 모든 버튼을 비활성화하고 메시지 갱신."""
    for child in view.children:
        if hasattr(child, "disabled"):
            child.disabled = True
    try:
        await message.edit(view=view)
    except Exception as e:
        log.warning(f"View 비활성화 실패: {e}")


def _build_progress_embed(title: str, description: str, color: int = 0xD97706) -> discord.Embed:
    """진행 중 Embed."""
    return discord.Embed(title=title, description=description, color=color)


# ═══════════════════════════════════════════════════════════════════
# Stage 1: 계획 승인 View
# ═══════════════════════════════════════════════════════════════════

class PlanApprovalView(discord.ui.View):
    """변경 계획 1차 승인 UI."""

    def __init__(
        self,
        session_id: str,
        owner_id: int,
        message: discord.Message | None = None,
        timeout: float = 600,
    ):
        super().__init__(timeout=timeout)
        self.session_id = session_id
        self.owner_id = owner_id
        self.message = message  # 🆕 자신이 붙어 있는 메시지 참조
        self.handled = False
        self.lock = asyncio.Lock()  # 🆕 동시 클릭 방지

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "❌ 요청자만 사용 가능합니다.", ephemeral=True,
            )
            return False
        if self.handled:
            await interaction.response.send_message(
                "⚠️ 이미 처리된 제안입니다.", ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(
        label="계획 승인 → 코드 생성",
        style=discord.ButtonStyle.success,
        emoji="✅",
    )
    async def approve_plan(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with self.lock:
            if self.handled:
                await interaction.response.send_message(
                    "⚠️ 이미 처리 중입니다.", ephemeral=True,
                )
                return
            self.handled = True

        # ✅ 즉시 응답 (3초 타임아웃 방지)
        await interaction.response.defer()

        # ✅ 1단계 버튼 즉시 비활성화 (혼란 방지)
        for child in self.children:
            child.disabled = True
        try:
            await interaction.edit_original_response(view=self)
        except Exception as e:
            log.warning(f"버튼 비활성화 실패: {e}")

        # 동일한 메시지에서 진행 상태 표시
        target_msg = self.message or await interaction.original_response()

        try:
            # 1단계 계획 승인 처리
            from modules.code_planner import (
                approve_plan as approve_plan_fn,
                generate_code_for_session,
                get_session,
            )

            if not approve_plan_fn(self.session_id):
                err_embed = discord.Embed(
                    title="❌ 승인 실패",
                    description="세션이 plan_pending 상태가 아닙니다.",
                    color=0xE11D48,
                )
                await target_msg.edit(embed=err_embed, view=None)
                return

            # 진행 표시
            progress_embed = _build_progress_embed(
                "🔧 개쵸 — 코드 생성 중...",
                (
                    f"세션 `{self.session_id}` 파일별 코드 생성 진행 중...\n"
                    "각 파일에 대해 AI가 변경 코드를 생성하고 있습니다.\n"
                    "**예상 시간**: 1~3분"
                ),
            )
            await target_msg.edit(embed=progress_embed, view=None)

            # 코드 생성 실행
            result = await generate_code_for_session(self.session_id)

            if not result["success"]:
                err_embed = discord.Embed(
                    title="❌ 코드 생성 실패",
                    description=result.get("error", "알 수 없는 오류"),
                    color=0xE11D48,
                )
                await target_msg.edit(embed=err_embed, view=None)
                return

            # 2단계 UI로 전환
            session = get_session(self.session_id)
            await self._show_code_review(target_msg, session)

        except Exception as e:
            log.exception(f"approve_plan 처리 실패: {e}")
            err_embed = discord.Embed(
                title="❌ 처리 중 오류",
                description=str(e)[:1500],
                color=0xE11D48,
            )
            try:
                await target_msg.edit(embed=err_embed, view=None)
            except Exception:
                pass

    @discord.ui.button(
        label="계획 거부",
        style=discord.ButtonStyle.danger,
        emoji="❌",
    )
    async def reject_plan(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with self.lock:
            if self.handled:
                await interaction.response.send_message(
                    "⚠️ 이미 처리됨.", ephemeral=True,
                )
                return
            self.handled = True

        await interaction.response.defer()

        from modules.code_planner import reject_session
        reject_session(self.session_id, stage="plan", reason="Cho 거부")

        for child in self.children:
            child.disabled = True

        embed = discord.Embed(
            title="❌ 계획 거부됨",
            description=f"세션 `{self.session_id}` 폐기되었습니다.",
            color=0xE11D48,
        )

        target_msg = self.message or await interaction.original_response()
        try:
            await target_msg.edit(embed=embed, view=self)
        except Exception as e:
            log.warning(f"reject_plan 메시지 갱신 실패: {e}")

    @discord.ui.button(
        label="계획 상세",
        style=discord.ButtonStyle.secondary,
        emoji="📋",
    )
    async def show_plan_detail(self, interaction: discord.Interaction, button: discord.ui.Button):
        from modules.code_planner import get_session
        session = get_session(self.session_id)
        if not session:
            await interaction.response.send_message("세션 없음", ephemeral=True)
            return

        plan = session.get("plan", {})
        files = plan.get("files", [])

        lines = []
        for f in files[:10]:
            action_emoji = "🆕" if f.get("action") == "create" else "✏️"
            lines.append(
                f"{action_emoji} **`{f['path']}`**\n"
                f"   └ 목적: {f.get('purpose', '')[:120]}\n"
                f"   └ 지시: {f.get('instruction', '')[:200]}\n"
                f"   └ 예상 {f.get('estimated_lines', '?')}줄"
            )

        embed = discord.Embed(
            title=f"📋 계획 상세 — `{self.session_id}`",
            description="\n\n".join(lines) if lines else "(변경 파일 없음)",
            color=0x4F46E5,
        )
        deps = plan.get("requires_dependencies", [])
        if deps:
            embed.add_field(
                name="📦 추가 패키지",
                value="\n".join(f"• `{d}`" for d in deps),
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def _show_code_review(self, message: discord.Message, session: dict):
        """2단계 코드 검토 UI 표시."""
        proposals = session.get("file_proposals", [])
        errors = session.get("generation_errors", [])

        total_lines = sum(p.get("lines_changed", 0) for p in proposals)

        review_embed = discord.Embed(
            title="🔍 개쵸 — 코드 변경 검토 (2단계)",
            description=(
                f"**세션**: `{session['id']}`\n"
                f"**생성된 파일**: {len(proposals)}개 / 총 {total_lines}줄 변경\n"
                f"**예상 비용**: ${session['total_cost']:.5f}\n"
                f"**생성 오류**: {len(errors)}개"
            ),
            color=0x059669,
        )

        # 파일별 요약
        file_lines = []
        for p in proposals[:10]:
            action_emoji = "🆕" if p.get("action") == "create" else "✏️"
            file_lines.append(
                f"{action_emoji} `{p['path']}` "
                f"({p.get('lines_changed', 0)}줄)"
            )
        if file_lines:
            review_embed.add_field(
                name="📂 변경 파일",
                value="\n".join(file_lines)[:1024],
                inline=False,
            )

        if errors:
            review_embed.add_field(
                name="⚠️ 생성 오류",
                value="\n".join(errors[:5])[:1024],
                inline=False,
            )

        review_embed.set_footer(
            text="⚠️ 코드 검토 후 [PR 생성]을 누르면 GitHub PR이 생성됩니다 (10분 후 자동 만료)",
        )

        # 새 View 생성
        code_view = CodeReviewView(
            session_id=session["id"],
            owner_id=self.owner_id,
            message=message,
            timeout=600,
        )

        # 같은 메시지에서 갱신
        await message.edit(embed=review_embed, view=code_view)


# ═══════════════════════════════════════════════════════════════════
# Stage 2: 코드 승인 View
# ═══════════════════════════════════════════════════════════════════

class CodeReviewView(discord.ui.View):
    """생성된 코드 2차 승인 + GitHub PR 생성."""

    def __init__(
        self,
        session_id: str,
        owner_id: int,
        message: discord.Message | None = None,
        timeout: float = 600,
    ):
        super().__init__(timeout=timeout)
        self.session_id = session_id
        self.owner_id = owner_id
        self.message = message
        self.handled = False
        self.lock = asyncio.Lock()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "❌ 요청자만 사용 가능합니다.", ephemeral=True,
            )
            return False
        if self.handled:
            await interaction.response.send_message(
                "⚠️ 이미 처리됨.", ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(
        label="PR 생성",
        style=discord.ButtonStyle.success,
        emoji="🚀",
    )
    async def approve_code(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with self.lock:
            if self.handled:
                await interaction.response.send_message(
                    "⚠️ 이미 처리 중입니다.", ephemeral=True,
                )
                return
            self.handled = True

        await interaction.response.defer()

        for child in self.children:
            child.disabled = True
        try:
            await interaction.edit_original_response(view=self)
        except Exception:
            pass

        target_msg = self.message or await interaction.original_response()

        try:
            from modules.code_planner import (
                approve_code as approve_code_fn,
                apply_session_to_github,
                get_session,
            )

            if not approve_code_fn(self.session_id):
                err_embed = discord.Embed(
                    title="❌ 승인 실패",
                    description="세션이 code_pending 상태가 아닙니다.",
                    color=0xE11D48,
                )
                await target_msg.edit(embed=err_embed, view=None)
                return

            # 진행 표시
            progress_embed = _build_progress_embed(
                "🚀 개쵸 — GitHub PR 생성 중...",
                f"세션 `{self.session_id}` 모든 파일을 GitHub에 commit + PR 생성 중...",
            )
            await target_msg.edit(embed=progress_embed, view=None)

            # PR 생성
            result = await apply_session_to_github(self.session_id)

            if result["success"]:
                embed = discord.Embed(
                    title="✅ PR 생성 완료",
                    description=(
                        f"**PR**: [{result['pr_number']}]({result['pr_url']})\n"
                        f"**브랜치**: `{result['branch']}`\n"
                        f"**커밋**: {result['commits_succeeded']}/{result['commits_total']} 성공\n\n"
                        f"GitHub에서 확인 후 머지하거나, "
                        f"`/code_merge {result['pr_number']}` 로 자동 머지 가능"
                    ),
                    color=0x059669,
                )

                # 🆕 R&D 포럼 채널에 자동 게시
                session = get_session(self.session_id)
                if session:
                    asyncio.create_task(_publish_code_change_to_forum(
                        interaction.client,
                        session=session,
                        pr_result=result,
                    ))
            else:
                embed = discord.Embed(
                    title="❌ PR 생성 실패",
                    description=result.get("error", "알 수 없는 오류"),
                    color=0xE11D48,
                )

            await target_msg.edit(embed=embed, view=None)

        except Exception as e:
            log.exception(f"approve_code 처리 실패: {e}")
            err_embed = discord.Embed(
                title="❌ 처리 중 오류",
                description=str(e)[:1500],
                color=0xE11D48,
            )
            try:
                await target_msg.edit(embed=err_embed, view=None)
            except Exception:
                pass

    @discord.ui.button(
        label="코드 거부",
        style=discord.ButtonStyle.danger,
        emoji="❌",
    )
    async def reject_code(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with self.lock:
            if self.handled:
                await interaction.response.send_message(
                    "⚠️ 이미 처리됨.", ephemeral=True,
                )
                return
            self.handled = True

        await interaction.response.defer()

        from modules.code_planner import reject_session
        reject_session(self.session_id, stage="code", reason="Cho 거부")

        for child in self.children:
            child.disabled = True

        embed = discord.Embed(
            title="❌ 코드 거부됨",
            description="생성된 코드가 폐기되었습니다 (GitHub에 반영되지 않음).",
            color=0xE11D48,
        )

        target_msg = self.message or await interaction.original_response()
        try:
            await target_msg.edit(embed=embed, view=self)
        except Exception as e:
            log.warning(f"reject_code 메시지 갱신 실패: {e}")

    @discord.ui.button(
        label="파일별 Diff",
        style=discord.ButtonStyle.secondary,
        emoji="📋",
    )
    async def show_diff(self, interaction: discord.Interaction, button: discord.ui.Button):
        from modules.code_planner import get_session
        session = get_session(self.session_id)
        if not session:
            await interaction.response.send_message("세션 없음", ephemeral=True)
            return

        proposals = session.get("file_proposals", [])
        if not proposals:
            await interaction.response.send_message(
                "생성된 파일이 없습니다.", ephemeral=True,
            )
            return

        # 각 파일 요약 + 첫 번째 diff
        first = proposals[0]
        diff = first.get("diff", "")[:3500] or first.get("new_content", "")[:3500]

        embed = discord.Embed(
            title=f"📋 {first['path']}",
            description=f"```diff\n{diff}\n```",
            color=0x4F46E5,
        )
        footer_text = (
            f"전체 {len(proposals)}개 파일 중 첫 번째 · "
            "전체 보려면 PR 생성 후 GitHub 확인"
        )
        embed.set_footer(text=footer_text)
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ═══════════════════════════════════════════════════════════════════
# R&D 포럼 자동 게시
# ═══════════════════════════════════════════════════════════════════

async def _publish_code_change_to_forum(
    bot: discord.Client,
    *,
    session: dict,
    pr_result: dict,
) -> bool:
    """코드 변경을 R&D 포럼 채널에 자동 게시."""
    try:
        from modules.code_publisher import publish_code_session
        return await publish_code_session(bot, session=session, pr_result=pr_result)
    except Exception as e:
        log.warning(f"R&D 포럼 게시 실패: {e}")
        return False
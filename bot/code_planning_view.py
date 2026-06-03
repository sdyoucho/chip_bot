"""
bot/code_planning_view.py
개쵸 자동 코드 변경의 2단계 승인 UI.

Stage 1: 계획 승인
Stage 2: 코드 승인 → PR 생성
"""

import asyncio
import logging
from datetime import datetime

import discord

log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# Stage 1: 계획 승인 View
# ═══════════════════════════════════════════════════════════════════

class PlanApprovalView(discord.ui.View):
    """변경 계획 1차 승인 UI."""

    def __init__(self, session_id: str, owner_id: int, timeout: float = 600):
        super().__init__(timeout=timeout)
        self.session_id = session_id
        self.owner_id = owner_id
        self.handled = False

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
        label="계획 승인 → 코드 생성",
        style=discord.ButtonStyle.success,
        emoji="✅",
    )
    async def approve_plan(self, interaction: discord.Interaction, button: discord.ui.Button):
        from modules.code_planner import approve_plan, generate_code_for_session, get_session
        from bot.commands import _send_response, _send_error
        from utils.message_splitter import edit_long_embed

        await interaction.response.defer(thinking=True)
        self.handled = True

        if not approve_plan(self.session_id):
            await _send_error(
                interaction, error_title="승인 실패",
                error="세션이 plan_pending 상태가 아님",
            )
            return

        # 진행 메시지
        progress_embed = discord.Embed(
            title="🔧 개쵸 — 코드 생성 중...",
            description=f"세션 `{self.session_id}` 파일별 코드 생성 진행 중...\n잠시만 기다려주세요 (1~3분).",
            color=0xD97706,
        )
        progress_msg = await interaction.followup.send(embed=progress_embed)

        # 백그라운드 코드 생성 실행
        result = await generate_code_for_session(self.session_id)

        if not result["success"]:
            err_embed = discord.Embed(
                title="❌ 코드 생성 실패",
                description=result.get("error", "알 수 없는 오류"),
                color=0xE11D48,
            )
            await progress_msg.edit(embed=err_embed)
            return

        # 코드 생성 완료 → 코드 승인 View 표시
        session = get_session(self.session_id)
        await self._show_code_review(progress_msg, session, interaction)

        # 버튼 비활성화
        for child in self.children:
            child.disabled = True
        await interaction.edit_original_response(view=self)

    @discord.ui.button(
        label="계획 거부",
        style=discord.ButtonStyle.danger,
        emoji="❌",
    )
    async def reject_plan(self, interaction: discord.Interaction, button: discord.ui.Button):
        from modules.code_planner import reject_session
        self.handled = True
        reject_session(self.session_id, stage="plan", reason="Cho 거부")

        for child in self.children:
            child.disabled = True

        embed = discord.Embed(
            title="❌ 계획 거부됨",
            description=f"세션 `{self.session_id}` 폐기.",
            color=0xE11D48,
        )
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(embed=embed, ephemeral=True)

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
        for i, f in enumerate(files[:10], 1):
            action_emoji = "🆕" if f.get("action") == "create" else "✏️"
            lines.append(
                f"{action_emoji} **`{f['path']}`**\n"
                f"   └ {f.get('purpose', '')[:120]}\n"
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

    async def _show_code_review(
        self,
        progress_msg: discord.Message,
        session: dict,
        interaction: discord.Interaction,
    ):
        """코드 생성 완료 → 2단계 승인 UI 표시."""
        proposals = session.get("file_proposals", [])
        errors = session.get("generation_errors", [])

        total_lines = sum(p.get("lines_changed", 0) for p in proposals)

        # 요약 Embed
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

        review_embed.set_footer(text="⚠️ 코드 검토 후 승인하시면 GitHub PR이 생성됩니다 (10분 후 자동 만료)")

        code_view = CodeReviewView(
            session_id=session["id"],
            owner_id=self.owner_id,
            timeout=600,
        )

        await progress_msg.edit(embed=review_embed, view=code_view)


# ═══════════════════════════════════════════════════════════════════
# Stage 2: 코드 승인 View
# ═══════════════════════════════════════════════════════════════════

class CodeReviewView(discord.ui.View):
    """생성된 코드 2차 승인 + GitHub PR 생성."""

    def __init__(self, session_id: str, owner_id: int, timeout: float = 600):
        super().__init__(timeout=timeout)
        self.session_id = session_id
        self.owner_id = owner_id
        self.handled = False

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
        from modules.code_planner import approve_code, apply_session_to_github

        await interaction.response.defer(thinking=True)
        self.handled = True

        if not approve_code(self.session_id):
            embed = discord.Embed(
                title="❌ 승인 실패",
                description="세션이 code_pending 상태가 아님",
                color=0xE11D48,
            )
            await interaction.followup.send(embed=embed)
            return

        # PR 생성
        result = await apply_session_to_github(self.session_id)

        for child in self.children:
            child.disabled = True
        await interaction.edit_original_response(view=self)

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
        else:
            embed = discord.Embed(
                title="❌ PR 생성 실패",
                description=result.get("error", "알 수 없는 오류"),
                color=0xE11D48,
            )
        await interaction.followup.send(embed=embed)

    @discord.ui.button(
        label="코드 거부",
        style=discord.ButtonStyle.danger,
        emoji="❌",
    )
    async def reject_code(self, interaction: discord.Interaction, button: discord.ui.Button):
        from modules.code_planner import reject_session
        self.handled = True
        reject_session(self.session_id, stage="code", reason="Cho 거부")

        for child in self.children:
            child.disabled = True

        embed = discord.Embed(
            title="❌ 코드 거부됨",
            description="모든 변경이 폐기됩니다.",
            color=0xE11D48,
        )
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(embed=embed, ephemeral=True)

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

        # 첫 번째 파일만 보여주기 (Discord 길이 제한)
        first = proposals[0]
        diff = first.get("diff", "")[:3500] or first.get("new_content", "")[:3500]

        embed = discord.Embed(
            title=f"📋 {first['path']}",
            description=f"```diff\n{diff}\n```",
            color=0x4F46E5,
        )
        embed.set_footer(
            text=f"전체 {len(proposals)}개 파일 중 첫 번째 · "
                 f"전체 보려면 PR 생성 후 GitHub 확인"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
"""
bot/commands.py
Discord 슬래시 커맨드 등록 + 헬퍼 함수.

표준 패턴 (모든 신규 커맨드/봇 적용):
    @bot.tree.command(...)
    @is_cho()
    async def cmd_xxx(interaction, ...):
        await interaction.response.defer(thinking=True)
        try:
            embed = await some_module.handle()
            await _send_response(interaction, embed, query="/xxx", attach_files=True)
        except Exception as e:
            await _send_error(interaction, error_title="XXX 오류", error=e)

주의:
    이 파일은 반드시 `python -c "import ast; ast.parse(open('bot/commands.py').read())"`
    검증을 통과해야 합니다. f-string은 멀티라인(f\"\"\"...\"\"\") 또는
    명시적 \\n 결합으로 작성하세요.
"""

# ═══════════════════════════════════════════════════════════════════
# Imports (반드시 파일 최상단)
# ═══════════════════════════════════════════════════════════════════
import asyncio
import io
import json
import logging
import os
import time
import traceback
from datetime import datetime
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from bot.embeds import (
    embed_error, embed_info, embed_success,
    embed_unknown_command,
)

log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# 권한 체크 데코레이터 (⚠️ 단 하나만 존재해야 함!)
# ═══════════════════════════════════════════════════════════════════
def is_cho():
    """Cho만 명령 실행 가능하도록 체크. CHO_USER_ID는 런타임에 매번 조회."""
    async def predicate(interaction: discord.Interaction) -> bool:
        cho_id_str = os.getenv("CHO_USER_ID", "").strip()
        if not cho_id_str.isdigit():
            await interaction.response.send_message(
                embed=embed_error(
                    "설정 오류",
                    "CHO_USER_ID 미설정. `/config_discord`로 설정해주세요.",
                ),
                ephemeral=True,
            )
            return False
        if interaction.user.id != int(cho_id_str):
            await interaction.response.send_message(
                embed=embed_error("접근 불가", "이 봇은 오퍼레이터 전용입니다."),
                ephemeral=True,
            )
            return False
        return True
    return app_commands.check(predicate)


# ═══════════════════════════════════════════════════════════════════
# /ask 응답 헬퍼 함수들 (모듈 레벨)
# ═══════════════════════════════════════════════════════════════════

async def _dm_fallback(
    interaction: discord.Interaction,
    embed: discord.Embed,
) -> bool:
    """Interaction 만료 시 Cho에게 DM으로 전송."""
    from utils.message_splitter import send_long_embed
    try:
        await interaction.user.send(
            content="⚠️ 응답이 지연되어 DM으로 전달합니다.",
        )
        return await send_long_embed(interaction.user, embed)
    except Exception as e:
        log.error(f"DM 폴백 실패: {e}")
        return False


async def _safe_send_embed(
    interaction: discord.Interaction,
    embed: discord.Embed,
) -> bool:
    """followup.send 안전 래퍼 (자동 분할 지원)."""
    from utils.message_splitter import send_long_embed
    try:
        return await send_long_embed(interaction, embed)
    except discord.NotFound:
        log.warning("Interaction 만료 — DM 폴백 시도")
        return await _dm_fallback(interaction, embed)
    except Exception as e:
        log.exception(f"_safe_send_embed 예외: {e}")
        return False


async def _safe_followup(
    interaction: discord.Interaction,
    embed: discord.Embed,
    ephemeral: bool = False,
) -> None:
    """오류 안전 followup (예외 시 무시, 자동 분할 지원)."""
    from utils.message_splitter import send_long_embed
    try:
        await send_long_embed(interaction, embed, ephemeral=ephemeral)
    except Exception as e:
        log.warning(f"_safe_followup 실패: {e}")


def _build_fallback_embed(query: str, agent_results: dict) -> discord.Embed:
    """응답 생성 실패 시 안내 Embed."""
    query_preview = query[:200]
    embed = discord.Embed(
        title="🤔 답변을 생성하기 어려웠어요",
        description=(
            f"요청: `{query_preview}`\n\n"
            "AI가 응답을 생성했지만 적절한 형태로 정리되지 못했습니다."
        ),
        color=0xEAB308,
    )
    if agent_results:
        attempted = ", ".join(f"`{n}`" for n in agent_results.keys())
        embed.add_field(name="🔧 호출된 에이전트", value=attempted, inline=False)

    embed.add_field(
        name="💡 다시 시도해보실 점",
        value=(
            "• **더 구체적인 질문**으로 재시도 (스트리머/기간/형식 명시)\n"
            "• **전용 커맨드** 사용 (`/money`, `/schedule` 등)\n"
            "• `/rawdata ephemeral` → 파이프라인 확인"
        ),
        inline=False,
    )
    embed.add_field(
        name="📋 설명이 부족할 수 있는 부분",
        value=(
            "• 어떤 **스트리머**에 대한 질문인지\n"
            "• 어떤 **기간**\n"
            "• 원하는 **출력 형식**"
        ),
        inline=False,
    )
    embed.set_footer(text="개쵸(/rnd_diagnose)로 시스템 이슈 신고 가능")
    return embed


# ═══════════════════════════════════════════════════════════════════
# 🌟 통합 응답 헬퍼 — 모든 커맨드/봇에서 사용
# ═══════════════════════════════════════════════════════════════════

async def _send_response(
    interaction: discord.Interaction,
    embed: Optional[discord.Embed],
    *,
    query: str = "",
    attach_files: bool = True,
    ephemeral: bool = False,
    error_title: str = "오류",
) -> bool:
    """
    모든 슬래시 커맨드의 표준 응답 전송 헬퍼.

    동작:
    1. embed가 None이면 안내 Embed 생성
    2. 1,400자 초과 시 자동 분할 (Embed 여러 개로)
    3. 1,500자 초과 시 .md 파일 자동 첨부
    4. 모든 예외 흡수 + 자동 폴백
    """
    from utils.message_splitter import send_long_embed

    if embed is None:
        embed = discord.Embed(
            title=f"⚠️ {error_title}",
            description="응답을 생성하지 못했습니다.",
            color=0xEAB308,
        )

    try:
        return await send_long_embed(
            interaction,
            embed,
            query=query,
            attach_files=attach_files,
            ephemeral=ephemeral,
        )
    except discord.NotFound:
        log.warning(f"Interaction 만료 — DM 폴백 (query={query})")
        try:
            return await _dm_fallback(interaction, embed)
        except Exception as e2:
            log.error(f"DM 폴백 실패: {e2}")
            return False
    except Exception as e:
        log.exception(f"_send_response 실패: {e}")
        try:
            await interaction.followup.send(
                embed=embed_error("응답 전송 실패", str(e)[:1500]),
                ephemeral=True,
            )
        except Exception:
            pass
        return False


async def _send_error(
    interaction: discord.Interaction,
    *,
    error_title: str,
    error,
    log_traceback: bool = True,
) -> None:
    """표준 에러 응답 전송. 모든 커맨드의 except 블록에서 사용."""
    error_str = str(error) if not isinstance(error, str) else error

    if log_traceback and isinstance(error, Exception):
        log.exception(f"[{error_title}] {error_str}")
    else:
        log.error(f"[{error_title}] {error_str}")

    try:
        from utils.self_monitor import record_error
        record_error(
            category=error_title.replace(" ", "_").lower(),
            message=error_str,
            traceback_str=traceback.format_exc() if isinstance(error, Exception) else "",
        )
    except Exception:
        pass

    err_embed = embed_error(error_title, error_str[:1500])
    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=err_embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=err_embed, ephemeral=True)
    except Exception as e:
        log.warning(f"_send_error 응답 실패: {e}")


# ═══════════════════════════════════════════════════════════════════
# Modals
# ═══════════════════════════════════════════════════════════════════

class _AIKeysModal(discord.ui.Modal, title="AI API 키 설정"):
    """AI 관련 API 키 입력 모달."""

    openrouter = discord.ui.TextInput(
        label="OpenRouter API Key (필수)",
        placeholder="sk-or-v1-...",
        required=True,
        style=discord.TextStyle.short,
        max_length=200,
    )
    perplexity = discord.ui.TextInput(
        label="Perplexity API Key (선택)",
        placeholder="pplx-...",
        required=False,
        style=discord.TextStyle.short,
        max_length=200,
    )
    youtube = discord.ui.TextInput(
        label="YouTube Data API v3 Key",
        placeholder="AIza...",
        required=False,
        style=discord.TextStyle.short,
        max_length=200,
    )
    github_token = discord.ui.TextInput(
        label="GitHub Personal Access Token (선택)",
        placeholder="ghp_... 또는 github_pat_...",
        required=False,
        style=discord.TextStyle.short,
        max_length=200,
    )

    async def on_submit(self, interaction: discord.Interaction):
        """모달 제출 시 키 저장."""
        from utils.config_manager import set_key
        updated = []
        mapping = [
            (self.openrouter.value,   "OPENROUTER_API_KEY", "OpenRouter"),
            (self.perplexity.value,   "PERPLEXITY_API_KEY", "Perplexity"),
            (self.youtube.value,      "YOUTUBE_API_KEY",    "YouTube"),
            (self.github_token.value, "GITHUB_TOKEN",       "GitHub"),
        ]
        for val, env_key, label in mapping:
            if val.strip():
                set_key(env_key, val.strip())
                updated.append(label)

        msg = f"저장 완료: {', '.join(updated)}" if updated else "입력된 키가 없습니다."
        await interaction.response.send_message(
            embed=embed_info("🔑 AI API 키", msg), ephemeral=True
        )


class _NotionKeysModal(discord.ui.Modal, title="Notion 설정"):
    """Notion 토큰 및 DB ID 입력 모달."""

    token = discord.ui.TextInput(
        label="Notion API Token",
        placeholder="secret_...",
        required=False,
        style=discord.TextStyle.short,
        max_length=200,
    )
    streamers_db = discord.ui.TextInput(
        label="스트리머 DB ID",
        required=False, style=discord.TextStyle.short, max_length=100,
    )
    broadcast_db = discord.ui.TextInput(
        label="방송 로그 DB ID",
        required=False, style=discord.TextStyle.short, max_length=100,
    )
    report_db = discord.ui.TextInput(
        label="리포트 DB ID",
        required=False, style=discord.TextStyle.short, max_length=100,
    )
    schedule_db = discord.ui.TextInput(
        label="스케줄 DB ID",
        required=False, style=discord.TextStyle.short, max_length=100,
    )

    async def on_submit(self, interaction: discord.Interaction):
        """모달 제출 시 Notion 설정 저장."""
        from utils.config_manager import set_key
        updated = []
        mapping = [
            (self.token.value,        "NOTION_TOKEN",            "Token"),
            (self.streamers_db.value, "NOTION_STREAMERS_DB",     "Streamers DB"),
            (self.broadcast_db.value, "NOTION_BROADCAST_LOG_DB", "Broadcast DB"),
            (self.report_db.value,    "NOTION_REPORT_DB",        "Report DB"),
            (self.schedule_db.value,  "NOTION_SCHEDULE_DB",      "Schedule DB"),
        ]
        for val, env_key, label in mapping:
            if val.strip():
                set_key(env_key, val.strip())
                updated.append(label)

        msg = f"저장 완료: {', '.join(updated)}" if updated else "입력된 값이 없습니다."
        await interaction.response.send_message(
            embed=embed_info("📋 Notion 설정", msg), ephemeral=True
        )


class _DiscordKeysModal(discord.ui.Modal, title="Discord 오퍼레이터 설정"):
    """오퍼레이터 유저 ID 입력 모달."""

    cho_user_id = discord.ui.TextInput(
        label="오퍼레이터 유저 ID (CHO_USER_ID)",
        placeholder="Discord 개발자 모드 → 프로필 우클릭 → 사용자 ID 복사",
        required=True,
        style=discord.TextStyle.short,
        max_length=30,
    )

    async def on_submit(self, interaction: discord.Interaction):
        """모달 제출 시 CHO_USER_ID 저장."""
        from utils.config_manager import set_key
        val = self.cho_user_id.value.strip()
        if not val.isdigit():
            await interaction.response.send_message(
                embed=embed_error(
                    "입력 오류",
                    "유저 ID는 숫자만 입력 가능합니다.\n"
                    "Discord 설정 → 고급 → 개발자 모드 ON → 프로필 우클릭 → 사용자 ID 복사",
                ),
                ephemeral=True,
            )
            return

        set_key("CHO_USER_ID", val)
        await interaction.response.send_message(
            embed=embed_info(
                "🤖 Discord 설정",
                f"✅ CHO_USER_ID 저장 완료: `{val}`\n\n"
                "**채널 설정은 전용 커맨드를 사용해주세요**:\n"
                "• `/rawdata_channel` — Raw Data 트레이스 채널\n"
                "• `/rnd_channel` — R&D 공지 채널\n"
                "• `/forum_channel` — 해쵸 포럼 세션 채널",
            ),
            ephemeral=True,
        )


# ═══════════════════════════════════════════════════════════════════
# setup_commands — 슬래시 커맨드 등록
# ═══════════════════════════════════════════════════════════════════

async def setup_commands(bot: commands.Bot) -> None:
    """봇에 슬래시 커맨드 트리 등록."""

    # ───────────────────────────────────────────────────────────
    # /ask — 자연어 통합 (정지 버튼 + 진행 업데이트 + 자동 분할)
    # ───────────────────────────────────────────────────────────
    @bot.tree.command(name="ask", description="자연어로 무엇이든 물어보세요")
    @is_cho()
    @app_commands.describe(query="질문 또는 명령", streamer="(선택) 스트리머 이름")
    async def cmd_ask(
        interaction: discord.Interaction,
        query: str,
        streamer: str = "",
    ) -> None:
        """
        자연어 통합 엔트리포인트.

        - 라우터로 에이전트를 결정하고
        - 해쵸 오케스트레이터로 응답 생성
        - 결과는 자동 분할/파일 첨부로 전송
        """
        await interaction.response.defer(thinking=True)

        # 전체 본문을 try/except로 감싸 SyntaxError 외 모든 런타임 예외 흡수
        try:
            from utils.pipeline_logger import (
                start_trace, get_trace, is_enabled,
                get_output_mode, get_log_channel, step,
            )
            from bot.router import route
            from modules.haecho import orchestrate
            from utils.forum_publisher import publish_session
            from bot.interactive import AskProgressView, build_progress_embed
            from utils.message_splitter import edit_long_embed

            start_trace()
            t_start = time.monotonic()

            # 🆕 컨텍스트 수집
            from utils.conversation_context import (
                get_reply_context,
                detect_context_reference,
                format_context_for_prompt,
            )

            enriched_query = query
            if detect_context_reference(query):
                context = await get_reply_context(interaction, max_depth=5)
                if context:
                    context_text = format_context_for_prompt(context)
                    enriched_query = f"{query}\n\n{context_text}"
                    step("컨텍스트 수집", "ok", f"{len(context)}개 메시지")

            summary_embed: Optional[discord.Embed] = None
            agent_results: dict = {}

            view = AskProgressView(query=query, owner_id=interaction.user.id)
            progress_msg = await interaction.followup.send(
                embed=build_progress_embed(
                    query, "라우팅", "필요한 에이전트 선별 중...",
                ),
                view=view,
            )

            async def _do_work() -> None:
                """실제 라우팅 + 오케스트레이션 백그라운드 작업."""
                nonlocal summary_embed, agent_results

                # 라우터 호출
                try:
                    plan = await route(enriched_query, streamer=streamer)
                    step("라우팅", "ok", f"agents={list(plan.get('agents', {}).keys())}")
                except Exception as e:
                    log.exception(f"라우터 실패: {e}")
                    plan = {"agents": {}}
                    step("라우팅", "error", str(e)[:200])

                # 오케스트레이션
                try:
                    result = await orchestrate(
                        query=enriched_query,
                        plan=plan,
                        streamer=streamer,
                    )
                    agent_results = result.get("agent_results", {}) or {}
                    summary_embed = result.get("embed")
                except Exception as e:
                    log.exception(f"오케스트레이션 실패: {e}")
                    step("오케스트레이션", "error", str(e)[:200])
                    summary_embed = embed_error(
                        "응답 생성 실패",
                        f"내부 오류가 발생했습니다: {str(e)[:500]}",
                    )

            # 작업 실행 (정지 버튼 지원)
            work_task = asyncio.create_task(_do_work())
            try:
                while not work_task.done():
                    if view.stopped:
                        work_task.cancel()
                        await _safe_followup(
                            interaction,
                            embed_info("⏹️ 중단됨", "사용자가 작업을 중단했습니다."),
                            ephemeral=True,
                        )
                        return
                    await asyncio.sleep(0.5)
                await work_task
            except asyncio.CancelledError:
                log.info("/ask 작업이 사용자에 의해 취소되었습니다.")
                return

            # 폴백 Embed
            if summary_embed is None:
                summary_embed = _build_fallback_embed(query, agent_results)

            elapsed = time.monotonic() - t_start
            try:
                summary_embed.set_footer(
                    text=f"⏱️ {elapsed:.1f}s · /ask"
                )
            except Exception:
                pass

            # 최종 응답 전송
            try:
                await edit_long_embed(
                    progress_msg,
                    summary_embed,
                    query=query,
                    attach_files=True,
                )
            except Exception as e:
                log.warning(f"edit_long_embed 실패, followup 시도: {e}")
                await _send_response(
                    interaction,
                    summary_embed,
                    query="/ask",
                    attach_files=True,
                )

            # 트레이스 발행
            try:
                trace = get_trace()
                if is_enabled() and trace:
                    mode = get_output_mode()
                    channel_id = get_log_channel()
                    await publish_session(
                        bot=bot,
                        interaction=interaction,
                        query=query,
                        trace=trace,
                        mode=mode,
                        channel_id=channel_id,
                    )
            except Exception as e:
                log.warning(f"트레이스 발행 실패: {e}")

        except Exception as e:
            # 최상위 안전망: 사용자에게 한국어 에러 메시지 응답
            log.exception(f"/ask 처리 중 예외: {e}")
            await _send_error(
                interaction,
                error_title="/ask 오류",
                error=e,
            )
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
# 실제 전송은 utils/message_splitter.py에 위임
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
    embed = discord.Embed(
        title="🤔 답변을 생성하기 어려웠어요",
        description=(
            f"요청: `{query[:200]}`\n\n"
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
    embed: discord.Embed | None,
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

    사용 패턴:
        async def cmd_xxx(interaction, ...):
            await interaction.response.defer(thinking=True)
            try:
                embed = await some_module.handle()
                await _send_response(interaction, embed, query="/xxx", attach_files=True)
            except Exception as e:
                await _send_error(interaction, error_title="XXX 오류", error=e)
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
    """
    표준 에러 응답 전송. 모든 커맨드의 except 블록에서 사용.

    동작:
    1. 로그 기록 (traceback 포함)
    2. self_monitor에 자동 에러 수집
    3. 사용자에게 Embed로 에러 안내

    사용 패턴:
        try:
            ...
        except Exception as e:
            await _send_error(interaction, error_title="XXX 오류", error=e)
    """
    error_str = str(error) if not isinstance(error, str) else error

    # 1) 로그
    if log_traceback and isinstance(error, Exception):
        log.exception(f"[{error_title}] {error_str}")
    else:
        log.error(f"[{error_title}] {error_str}")

    # 2) 자동 에러 수집
    try:
        from utils.self_monitor import record_error
        record_error(
            category=error_title.replace(" ", "_").lower(),
            message=error_str,
            traceback_str=traceback.format_exc() if isinstance(error, Exception) else "",
        )
    except Exception:
        pass

    # 3) 사용자에게 안내
    err_embed = embed_error(error_title, error_str[:1500])
    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=err_embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=err_embed, ephemeral=True)
    except Exception as e:
        log.warning(f"_send_error 응답 실패: {e}")


# ═══════════════════════════════════════════════════════════════════
# 🆕 /rnd_diagnose 핸들러 — 봇 소스 코드 리뷰 기반 진단
# ═══════════════════════════════════════════════════════════════════

async def _handle_rnd_diagnose(
    interaction: discord.Interaction,
    issue: Optional[str] = None,
) -> None:
    """
    /rnd_diagnose 슬래시 명령 처리기.

    봇의 핵심 소스 코드를 GitHub에서 읽어와 LLM에 전달하고,
    코드 리뷰 / 개선 제안 / 수정 가능한 부분을 생성한다.

    Args:
        interaction: Discord 인터랙션 객체
        issue: (선택) 사용자가 명시한 특정 이슈/관심 영역
    """
    await interaction.response.defer(thinking=True)
    try:
        from modules import rnd
        from utils.message_splitter import send_long_embed

        # 모듈의 코드 리뷰 진단 함수 호출
        result = await rnd.diagnose_codebase(issue=issue)

        # 결과를 Embed로 변환 (문자열 또는 Embed 모두 지원)
        if isinstance(result, discord.Embed):
            embed = result
        else:
            title = "🔬 R&D 코드 리뷰 진단"
            if issue:
                title += f" — {issue[:60]}"
            embed = discord.Embed(
                title=title,
                description=str(result)[:4000] if result else "진단 결과가 비어 있습니다.",
                color=0x6366F1,
            )
            embed.set_footer(text="개쵸 R&D • 코드 리뷰 기반 개선 제안")

        await send_long_embed(
            interaction,
            embed,
            query=f"/rnd_diagnose {issue or ''}".strip(),
            attach_files=True,
        )
    except Exception as e:
        await _send_error(interaction, error_title="R&D 진단 오류", error=e)


# ═══════════════════════════════════════════════════════════════════
# Modals
# ═══════════════════════════════════════════════════════════════════

class _AIKeysModal(discord.ui.Modal, title="AI API 키 설정"):
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
        from utils.config_manager import set_key
        updated = []
        mapping = [
            (self.token.value,        "NOTION_TOKEN",           "Token"),
            (self.streamers_db.value, "NOTION_STREAMERS_DB",    "Streamers DB"),
            (self.broadcast_db.value, "NOTION_BROADCAST_LOG_DB","Broadcast DB"),
            (self.report_db.value,    "NOTION_REPORT_DB",       "Report DB"),
            (self.schedule_db.value,  "NOTION_SCHEDULE_DB",     "Schedule DB"),
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
    cho_user_id = discord.ui.TextInput(
        label="오퍼레이터 유저 ID (CHO_USER_ID)",
        placeholder="Discord 개발자 모드 → 프로필 우클릭 → 사용자 ID 복사",
        required=True,
        style=discord.TextStyle.short,
        max_length=30,
    )

    async def on_submit(self, interaction: discord.Interaction):
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

async def setup_commands(bot: commands.Bot):
    """봇에 슬래시 커맨드 트리 등록."""

    # ───────────────────────────────────────────────────────────
    # /rnd_diagnose — 봇 소스 코드 리뷰 기반 진단 / 개선 제안
    # ───────────────────────────────────────────────────────────
    @bot.tree.command(
        name="rnd_diagnose",
        description="봇 소스 코드 리뷰 기반 진단 및 개선 제안",
    )
    @is_cho()
    @app_commands.describe(
        issue="(선택) 특정 이슈/관심 영역 (예: 메모리 누수, /ask 성능, rnd.py 오류 등)",
    )
    async def cmd_rnd_diagnose(
        interaction: discord.Interaction,
        issue: Optional[str] = None,
    ):
        """봇 소스를 LLM으로 코드 리뷰하여 개선/수정 가능한 부분을 제시."""
        await _handle_rnd_diagnose(interaction, issue=issue)

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
    ):
        await interaction.response.defer(thinking=True)
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
            get_reply_context, detect_context_reference, format_context_for_prompt,
        )
        
        enriched_query = query
        if detect_context_reference(query):
            context = await get_reply_context(interaction, max_depth=5)
            if context:
                context_text = format_context_for_prompt(context)
                enriched_query = f"{query}\n\n{context_text}"
                step("컨텍스트 수집", "ok", f"{len(context)}개 메시지")
        
        summary_embed = None
        agent_results = {}

        view = AskProgressView(query=query, owner_id=interaction.user.id)
        progress_msg = await interaction.followup.send(
            embed=build_progress_embed(query, "라우팅", "필요한 에이전트 선별 중..."),
            view=view,
        )

        async def _do_work():
            nonlocal summary_embed, agent_results

            # 🆕 enriched_q
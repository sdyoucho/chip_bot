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

            # 🆕 enriched_query를 router에 전달
            routing = await route(enriched_query)
            step(
                "Router (OpenRouter)", "ok",
                f"agents={[m['name'] for m in routing['modules']]} "
                f"summary={routing['needs_haecho_summary']} "
                f"urls={len(routing.get('extracted_urls', []))}",
            )

            agents_str = ", ".join(m['name'] for m in routing['modules'])
            url_count = len(routing.get("extracted_urls", []))
            status_msg = f"에이전트 호출 중: **{agents_str}**"
            if url_count > 0:
                status_msg += f"\n📎 URL {url_count}개 분석 중..."

            elapsed = int((time.monotonic() - t_start) * 1000)
            try:
                await progress_msg.edit(
                    embed=build_progress_embed(
                        query, "수집",
                        status_msg,
                        elapsed_ms=elapsed,
                    ),
                    view=view,
                )
            except Exception:
                pass

            # 🆕 enriched_query를 orchestrate에도 전달
            result = await orchestrate(enriched_query, routing, streamer)
            agent_results = result.get("agent_results", {}) or {}
            summary_embed = result.get("summary_embed")
            return summary_embed, agent_results

        work_task = asyncio.create_task(_do_work())
        view.task = work_task

        try:
            await asyncio.wait_for(work_task, timeout=240)

            if view.cancelled:
                return

            elapsed_total = int((time.monotonic() - t_start) * 1000)
            final_embed = summary_embed

            # 🛡️ summary_embed가 없으면 첫 번째 유효한 agent embed 선택
            if not final_embed and agent_results:
                for _, result_tuple in agent_results.items():
                    if isinstance(result_tuple, tuple) and result_tuple[0] is not None:
                        candidate = result_tuple[0]
                        if candidate.title or candidate.description or candidate.fields:
                            final_embed = candidate
                            break

            if not final_embed:
                final_embed = _build_fallback_embed(query, agent_results)

            view.clear_items()
            view.stop()

            # 🚀 자동 분할 편집 + .md 파일 첨부 (1,400자 단위)
            ok = await edit_long_embed(
                progress_msg,
                final_embed,
                view=None,
                interaction=interaction,
                query=query,
                attach_files=True,
            )
            if ok:
                step("응답 전송", "ok", f"총 {elapsed_total}ms")
            else:
                step("응답 전송", "fail", "edit 실패 → 새 메시지", "E012")
                await _safe_send_embed(interaction, final_embed)

            # 포럼 병렬 발행
            if summary_embed and agent_results:
                asyncio.create_task(publish_session(
                    bot,
                    query=query,
                    agent_results=agent_results,
                    summary_embed=summary_embed,
                ))

        except asyncio.TimeoutError:
            step("처리", "fail", "4분 타임아웃", "E013")
            work_task.cancel()
            timeout_embed = discord.Embed(
                title="⏱️ 응답 시간 초과",
                description=(
                    f"**쿼리**: `{query[:200]}`\n\n"
                    "작업이 4분 내에 완료되지 않아 중단되었습니다.\n\n"
                    "**원인 가능성**:\n"
                    "• LLM 응답 지연 (OpenRouter 혼잡)\n"
                    "• 요청 범위가 너무 광범위\n"
                    "• 네트워크 이슈\n\n"
                    "**해결책**:\n"
                    "• 쿼리를 더 구체적으로 재작성\n"
                    "• `/rnd_health`로 봇 상태 확인\n"
                    "• 잠시 후 재시도"
                ),
                color=0xF97316,
            )
            try:
                await edit_long_embed(
                    progress_msg,
                    timeout_embed,
                    view=None,
                    interaction=interaction,
                    query=query,
                    attach_files=False,
                )
            except Exception:
                await _safe_followup(interaction, embed=timeout_embed)

        except asyncio.CancelledError:
            log.info(f"/ask 취소됨 (쿼리: {query[:40]})")
            return

        except Exception as e:
            log.exception(f"/ask 오류: {e}")
            step("처리", "fail", str(e)[:100], "E000")

            try:
                from utils.self_monitor import record_error
                record_error(
                    category="cmd_ask",
                    message=str(e),
                    traceback_str=traceback.format_exc(),
                )
            except Exception:
                pass

            err_embed = embed_error("오류", str(e)[:1500])
            try:
                await edit_long_embed(
                    progress_msg,
                    err_embed,
                    view=None,
                    interaction=interaction,
                    query=query,
                    attach_files=False,
                )
            except Exception:
                await _safe_followup(interaction, embed=err_embed)

        finally:
            if is_enabled():
                trace = get_trace()
                if trace:
                    mode = get_output_mode()
                    if mode in ("ephemeral", "both"):
                        await _safe_followup(
                            interaction,
                            embed=trace.to_embed(query=query),
                            ephemeral=True,
                        )
                    if mode in ("channel", "both"):
                        ch_id = get_log_channel()
                        if ch_id:
                            ch = bot.get_channel(ch_id)
                            if ch:
                                try:
                                    await ch.send(embed=trace.to_embed(
                                        query=query,
                                        user=str(interaction.user),
                                        for_channel=True,
                                    ))
                                except Exception as ch_err:
                                    log.warning(f"트레이스 채널 전송 실패: {ch_err}")

    # ───────────────────────────────────────────────────────────
    # 모니터링 / 리포트 / 유튜브 / 스케줄 / 자금
    # ───────────────────────────────────────────────────────────
    @bot.tree.command(name="monitor", description="스트리머 방송 현황")
    @is_cho()
    @app_commands.describe(streamer="스트리머 이름 (미입력 시 전체)")
    async def cmd_monitor(interaction: discord.Interaction, streamer: str = "all"):
        await interaction.response.defer(thinking=True)
        try:
            from modules.chzzk_monitor import get_current_status
            embed = await get_current_status(streamer)
            await _send_response(
                interaction, embed,
                query=f"/monitor {streamer}",
                attach_files=True,
            )
        except Exception as e:
            await _send_error(interaction, error_title="모니터링 오류", error=e)

    @bot.tree.command(name="report", description="주간 분석 리포트")
    @is_cho()
    @app_commands.describe(streamer="스트리머 이름 (미입력 시 전체)")
    async def cmd_report(interaction: discord.Interaction, streamer: str = "all"):
        await interaction.response.defer(thinking=True)
        try:
            from modules.weekly_report import generate_report
            embed = await generate_report(streamer)
            await _send_response(
                interaction, embed,
                query=f"/report {streamer}",
                attach_files=True,
            )
        except Exception as e:
            await _send_error(interaction, error_title="리포트 오류", error=e)

    @bot.tree.command(name="youtube", description="유튜브 채널 통계")
    @is_cho()
    @app_commands.describe(streamer="스트리머 이름")
    async def cmd_youtube(interaction: discord.Interaction, streamer: str):
        await interaction.response.defer(thinking=True)
        try:
            from modules.youtube_analytics import get_channel_stats
            embed = await get_channel_stats(streamer)
            await _send_response(
                interaction, embed,
                query=f"/youtube {streamer}",
                attach_files=True,
            )
        except Exception as e:
            await _send_error(interaction, error_title="유튜브 오류", error=e)

    @bot.tree.command(name="schedule", description="스케줄 조회")
    @is_cho()
    @app_commands.describe(query="조회할 기간")
    async def cmd_schedule(interaction: discord.Interaction, query: str = "이번주"):
        await interaction.response.defer(thinking=True)
        try:
            from modules.schedule import handle_schedule
            embed = await handle_schedule(query)
            await _send_response(
                interaction, embed,
                query=f"/schedule {query}",
                attach_files=True,
            )
        except Exception as e:
            await _send_error(interaction, error_title="스케줄 오류", error=e)

    @bot.tree.command(name="money", description="자금 현황 및 API 비용")
    @is_cho()
    async def cmd_money(interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        try:
            from modules.money import get_financial_summary
            embed = await get_financial_summary()
            await _send_response(
                interaction, embed,
                query="/money",
                attach_files=True,
            )
        except Exception as e:
            await _send_error(interaction, error_title="자금 오류", error=e)

    @bot.tree.command(name="settlement", description="월말정산 + 다음 달 예상")
    @is_cho()
    async def cmd_settlement(interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        try:
            from modules.money import monthly_settlement
            embed = await monthly_settlement()
            await _send_response(
                interaction, embed,
                query="/settlement",
                attach_files=True,
            )
        except Exception as e:
            await _send_error(interaction, error_title="정산 오류", error=e)

    @bot.tree.command(name="credit_settings", description="크레딧 알림 설정 조회 (월 한도/임계치)")
    @is_cho()
    async def cmd_credit_settings(interaction: discord.Interaction):
        from utils.credit_config import get_monthly_limit, get_thresholds
        from utils.cost_tracker import get_monthly_total

        monthly_limit = get_monthly_limit()
        month_total = await get_monthly_total()
        ratio = month_total / monthly_limit if monthly_limit else 0
        thresholds_label = ", ".join(f"{int(t*100)}%" for t in get_thresholds())

        embed = discord.Embed(title="⚙️ 인쵸 — 크레딧 알림 설정", color=0x4F46E5)
        embed.add_field(name="월 한도", value=f"${monthly_limit:.2f}", inline=True)
        embed.add_field(name="이번 달 사용", value=f"${month_total:.3f} ({ratio*100:.1f}%)", inline=True)
        embed.add_field(name="알림 임계치", value=thresholds_label, inline=False)
        embed.set_footer(text="변경: /credit_limit · /credit_thresholds")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @bot.tree.command(name="credit_limit", description="월 크레딧 한도(USD) 설정")
    @is_cho()
    @app_commands.describe(amount="월 한도 (USD, 예: 50)")
    async def cmd_credit_limit(interaction: discord.Interaction, amount: float):
        from utils.credit_config import set_monthly_limit
        try:
            set_monthly_limit(amount)
            embed = embed_success("월 한도 변경 완료", f"이번 달부터 한도 `${amount:.2f}` 적용")
        except ValueError as e:
            embed = embed_error("변경 실패", str(e))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @bot.tree.command(name="credit_thresholds", description="크레딧 알림 임계치(%) 설정")
    @is_cho()
    @app_commands.describe(thresholds="쉼표로 구분된 퍼센트 (예: 50,70,90)")
    async def cmd_credit_thresholds(interaction: discord.Interaction, thresholds: str):
        from utils.credit_config import set_thresholds
        try:
            values = [float(t.strip()) / 100 for t in thresholds.split(",") if t.strip()]
            set_thresholds(values)
            label = ", ".join(f"{int(v*100)}%" for v in sorted(values))
            embed = embed_success("임계치 변경 완료", f"알림 임계치 → {label}")
        except ValueError as e:
            embed = embed_error("변경 실패", f"퍼센트 숫자를 쉼표로 구분해 입력하세요 (예: 50,70,90).\n{e}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ───────────────────────────────────────────────────────────
    # 스트리머 관리
    # ───────────────────────────────────────────────────────────
    @bot.tree.command(name="streamer_add", description="신규 스트리머 등록")
    @is_cho()
    @app_commands.describe(
        name="스트리머 이름",
        chzzk_url="치지직 채널 URL",
        youtube_url="유튜브 채널 URL",
        soop_url="SOOP 채널 URL",
    )
    async def cmd_streamer_add(
        interaction: discord.Interaction,
        name: str,
        chzzk_url: str = "",
        youtube_url: str = "",
        soop_url: str = "",
    ):
        await interaction.response.defer(thinking=True)
        try:
            from utils.notion_client import register_streamer
            await register_streamer(name, chzzk_url, youtube_url, soop_url)
            embed = embed_info(
                f"✅ {name} 등록 완료",
                f"치지직: {chzzk_url or '미등록'}\n"
                f"유튜브: {youtube_url or '미등록'}\n"
                f"SOOP: {soop_url or '미등록'}",
            )
            await _send_response(
                interaction, embed,
                query=f"/streamer_add {name}",
                attach_files=False,
            )
        except Exception as e:
            await _send_error(interaction, error_title="등록 오류", error=e)

    @bot.tree.command(name="streamer_list", description="등록된 스트리머 목록")
    @is_cho()
    async def cmd_streamer_list(interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        try:
            from utils.notion_client import list_streamers
            streamers = await list_streamers()
            if not streamers:
                embed = embed_info("스트리머 목록", "등록된 스트리머가 없습니다.")
            else:
                lines = "\n".join(f"• {s['name']}" for s in streamers)
                embed = embed_info(f"스트리머 목록 ({len(streamers)}명)", lines)
            await _send_response(
                interaction, embed,
                query="/streamer_list",
                attach_files=False,
            )
        except Exception as e:
            await _send_error(interaction, error_title="목록 오류", error=e)

    # ───────────────────────────────────────────────────────────
    # 설정 — API/Notion/Discord
    # ───────────────────────────────────────────────────────────
    @bot.tree.command(name="config_ai", description="AI API 키 설정")
    @is_cho()
    async def cmd_config_ai(interaction: discord.Interaction):
        await interaction.response.send_modal(_AIKeysModal())

    @bot.tree.command(name="config_notion", description="Notion 설정")
    @is_cho()
    async def cmd_config_notion(interaction: discord.Interaction):
        await interaction.response.send_modal(_NotionKeysModal())

    @bot.tree.command(name="config_discord", description="Discord 오퍼레이터 설정")
    async def cmd_config_discord(interaction: discord.Interaction):
        """⚠️ is_cho 데코레이터 없음 — 초기 설정 시 CHO_USER_ID가 없어도 호출 가능."""
        cho_id_str = os.getenv("CHO_USER_ID", "").strip()
        if cho_id_str.isdigit() and interaction.user.id != int(cho_id_str):
            await interaction.response.send_message(
                embed=embed_error("접근 불가", "이 봇은 오퍼레이터 전용입니다."),
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(_DiscordKeysModal())

    @bot.tree.command(name="config_status", description="현재 API 키 설정 현황")
    @is_cho()
    async def cmd_config_status(interaction: discord.Interaction):
        from utils.config_manager import get_status
        status = get_status()

        embed = discord.Embed(title="⚙️ API 설정 현황", color=0x4F46E5)

        groups: dict[str, list[str]] = {}
        for key, info in status.items():
            g = info["group"]
            icon = "✅" if info["set"] else "❌"
            groups.setdefault(g, []).append(f"{icon} `{key}`\n　{info['desc']}")

        for group, lines in groups.items():
            embed.add_field(name=group, value="\n".join(lines), inline=False)

        embed.set_footer(text="변경: /config_ai · /config_notion · /config_discord")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ───────────────────────────────────────────────────────────
    # Raw Data 트레이스
    # ───────────────────────────────────────────────────────────
    @bot.tree.command(name="rawdata", description="Raw Data 트레이스 출력 모드")
    @is_cho()
    @app_commands.choices(mode=[
        app_commands.Choice(name="off — 비활성", value="off"),
        app_commands.Choice(name="ephemeral — 나에게만", value="ephemeral"),
        app_commands.Choice(name="channel — 채널 기록", value="channel"),
        app_commands.Choice(name="both — 둘 다", value="both"),
    ])
    async def cmd_rawdata(interaction: discord.Interaction, mode: str):
        from utils.pipeline_logger import (
            set_output_mode, get_output_mode, get_log_channel,
        )
        set_output_mode(mode)  # type: ignore

        ch_id = get_log_channel()
        ch_mention = f"<#{ch_id}>" if ch_id else "❌ 미설정"

        MODE_INFO = {
            "off": ("🔬 Raw Data OFF", 0x94A3B8, "트레이스 비활성화됨"),
            "ephemeral": ("🔬 Ephemeral 모드", 0x4F46E5,
                          "트레이스가 나에게만 보이는 임시 메시지로 표시"),
            "channel": ("🔬 Channel 모드", 0x059669,
                        f"트레이스가 채널에 영구 기록: {ch_mention}"),
            "both": ("🔬 Both 모드", 0xD97706,
                     f"임시 + 채널 양쪽 전송: {ch_mention}"),
        }
        title, color, desc = MODE_INFO[mode]
        if mode in ("channel", "both") and not ch_id:
            desc += "\n\n⚠️ 로그 채널 미설정 — `/rawdata_channel`로 지정"

        embed = discord.Embed(title=title, description=desc, color=color)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @bot.tree.command(name="rawdata_channel", description="Raw Data 기록 채널 설정")
    @is_cho()
    @app_commands.describe(channel="채널 (비우면 해제)")
    async def cmd_rawdata_channel(
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ):
        from utils.pipeline_logger import set_log_channel, get_output_mode
        from utils.config_manager import set_key

        if channel:
            set_log_channel(channel.id)
            set_key("LOG_RAW_CHANNEL_ID", str(channel.id))
            embed = discord.Embed(
                title="📋 Raw Data 채널 설정 완료",
                description=f"채널: {channel.mention}\n모드: `{get_output_mode()}`",
                color=0x059669,
            )
        else:
            set_log_channel(None)
            set_key("LOG_RAW_CHANNEL_ID", "")
            embed = discord.Embed(
                title="📋 Raw Data 채널 해제",
                color=0x94A3B8,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ───────────────────────────────────────────────────────────
    # 모델 티어링 관리
    # ───────────────────────────────────────────────────────────
    @bot.tree.command(name="model_status", description="현재 모델 티어링 조회")
    @is_cho()
    async def cmd_model_status(interaction: discord.Interaction):
        from utils.openrouter_client import get_current_config
        cfg = get_current_config()

        embed = discord.Embed(title="🧠 AI 모델 티어링", color=0x4F46E5)
        tier_lines = "\n".join(
            f"• **`{tier}`** → `{model}`"
            for tier, model in cfg["tiers"].items()
        )
        embed.add_field(name="📐 티어 매핑", value=tier_lines, inline=False)

        agent_lines = "\n".join(
            f"• **{agent}** → `{tier}`"
            for agent, tier in cfg["agents"].items()
        )
        embed.add_field(name="🤖 에이전트 매핑", value=agent_lines, inline=False)
        embed.set_footer(text="변경: /model_set · /model_agent · 초기화: /model_reset")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @bot.tree.command(name="model_set", description="티어의 모델 변경")
    @is_cho()
    @app_commands.describe(tier="티어", model="새 모델 ID")
    @app_commands.choices(tier=[
        app_commands.Choice(name="router", value="router"),
        app_commands.Choice(name="light", value="light"),
        app_commands.Choice(name="standard", value="standard"),
        app_commands.Choice(name="premium", value="premium"),
        app_commands.Choice(name="research", value="research"),
        app_commands.Choice(name="vision", value="vision"),
    ])
    async def cmd_model_set(interaction: discord.Interaction, tier: str, model: str):
        from utils.openrouter_client import set_tier_model
        try:
            set_tier_model(tier, model.strip(), persist=True)
            embed = embed_success(
                "모델 변경 완료",
                f"티어 `{tier}` → `{model.strip()}`",
            )
        except ValueError as e:
            embed = embed_error("변경 실패", str(e))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @bot.tree.command(name="model_agent", description="에이전트 티어 변경")
    @is_cho()
    @app_commands.describe(agent="에이전트", tier="새 티어")
    @app_commands.choices(
        agent=[
            app_commands.Choice(name="해쵸", value="haecho"),
            app_commands.Choice(name="기쵸", value="gihyo"),
            app_commands.Choice(name="인쵸", value="inchyo"),
            app_commands.Choice(name="분쵸", value="bunchyo"),
            app_commands.Choice(name="스쵸", value="sochyo"),
            app_commands.Choice(name="모쵸", value="mochyo"),
            app_commands.Choice(name="개쵸", value="gaechyo"),
            app_commands.Choice(name="디쵸", value="dichyo"),
        ],
        tier=[
            app_commands.Choice(name="router", value="router"),
            app_commands.Choice(name="light", value="light"),
            app_commands.Choice(name="standard", value="standard"),
            app_commands.Choice(name="premium", value="premium"),
            app_commands.Choice(name="research", value="research"),
            app_commands.Choice(name="vision", value="vision"),
        ],
    )
    async def cmd_model_agent(interaction: discord.Interaction, agent: str, tier: str):
        from utils.openrouter_client import set_agent_tier, MODEL_TIERS
        try:
            set_agent_tier(agent, tier, persist=True)
            embed = embed_success(
                "에이전트 티어 변경",
                f"**{agent}** → `{tier}` (`{MODEL_TIERS[tier]}`)",
            )
        except ValueError as e:
            embed = embed_error("변경 실패", str(e))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @bot.tree.command(name="model_reset", description="모델 설정 초기화")
    @is_cho()
    async def cmd_model_reset(interaction: discord.Interaction):
        from utils.model_config import reset_overrides
        reset_overrides()
        await interaction.response.send_message(
            embed=embed_info(
                "🔄 모델 설정 초기화",
                "오버라이드 제거됨. 봇 재시작 시 기본값으로 복귀.",
            ),
            ephemeral=True,
        )

    # ───────────────────────────────────────────────────────────
    # 고정비 관리
    # ───────────────────────────────────────────────────────────
    @bot.tree.command(name="fixedcost_list", description="고정비 납부 일정 목록")
    @is_cho()
    async def cmd_fixedcost_list(interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        try:
            from modules.fixed_costs import list_fixed_costs
            embed = await list_fixed_costs()
            await _send_response(
                interaction, embed,
                query="/fixedcost_list",
                attach_files=False,
            )
        except Exception as e:
            await _send_error(interaction, error_title="고정비 목록 오류", error=e)

    @bot.tree.command(name="fixedcost_add", description="고정비 등록")
    @is_cho()
    @app_commands.describe(
        name="서비스 이름",
        amount_krw="월 금액 (원)",
        pay_day="매월 납부일 (1~31)",
    )
    async def cmd_fixedcost_add(
        interaction: discord.Interaction,
        name: str, amount_krw: int, pay_day: int,
    ):
        from modules.fixed_costs import add_cost
        if not (1 <= pay_day <= 31):
            await interaction.response.send_message(
                embed=embed_error("입력 오류", "납부일은 1~31 사이"),
                ephemeral=True,
            )
            return
        msg = add_cost(name, amount_krw, pay_day)
        await interaction.response.send_message(
            embed=embed_info("💳 고정비 등록", msg), ephemeral=True,
        )

    @bot.tree.command(name="fixedcost_remove", description="고정비 삭제")
    @is_cho()
    @app_commands.describe(name="삭제할 서비스 이름")
    async def cmd_fixedcost_remove(interaction: discord.Interaction, name: str):
        from modules.fixed_costs import remove_cost
        await interaction.response.send_message(
            embed=embed_info("💳 고정비 삭제", remove_cost(name)),
            ephemeral=True,
        )

    @bot.tree.command(name="fixedcost_paid", description="고정비 납부 완료 기록")
    @is_cho()
    @app_commands.describe(name="납부한 서비스")
    async def cmd_fixedcost_paid(interaction: discord.Interaction, name: str):
        from modules.fixed_costs import mark_paid
        await interaction.response.send_message(
            embed=embed_info("💳 납부 완료", mark_paid(name)),
            ephemeral=True,
        )

    # ───────────────────────────────────────────────────────────
    # 스케줄 등록/수정/삭제
    # ───────────────────────────────────────────────────────────
    @bot.tree.command(name="schedule_add", description="스케줄 등록")
    @is_cho()
    @app_commands.describe(
        title="제목",
        date="날짜 (예: 2026-05-15 또는 2026-05-15 14:00)",
        memo="메모",
    )
    async def cmd_schedule_add(
        interaction: discord.Interaction,
        title: str, date: str, memo: str = "",
    ):
        await interaction.response.defer(thinking=True)
        try:
            from modules.schedule import add_schedule
            embed = await add_schedule(title, date, memo)
            await _send_response(
                interaction, embed,
                query=f"/schedule_add {title}",
                attach_files=False,
            )
        except Exception as e:
            await _send_error(interaction, error_title="스케줄 등록 오류", error=e)

    @bot.tree.command(name="schedule_edit", description="스케줄 수정")
    @is_cho()
    @app_commands.describe(
        short_id="8자리 ID",
        title="새 제목",
        date="새 날짜",
    )
    async def cmd_schedule_edit(
        interaction: discord.Interaction,
        short_id: str, title: str = "", date: str = "",
    ):
        await interaction.response.defer(thinking=True)
        try:
            from modules.schedule import update_schedule
            embed = await update_schedule(short_id, title, date)
            await _send_response(
                interaction, embed,
                query=f"/schedule_edit {short_id}",
                attach_files=False,
            )
        except Exception as e:
            await _send_error(interaction, error_title="스케줄 수정 오류", error=e)

    @bot.tree.command(name="schedule_remove", description="스케줄 삭제")
    @is_cho()
    @app_commands.describe(short_id="삭제할 8자리 ID")
    async def cmd_schedule_remove(interaction: discord.Interaction, short_id: str):
        await interaction.response.defer(thinking=True)
        try:
            from modules.schedule import delete_schedule
            embed = await delete_schedule(short_id)
            await _send_response(
                interaction, embed,
                query=f"/schedule_remove {short_id}",
                attach_files=False,
            )
        except Exception as e:
            await _send_error(interaction, error_title="스케줄 삭제 오류", error=e)

    # ───────────────────────────────────────────────────────────
    # 개쵸 R&D
    # ───────────────────────────────────────────────────────────
    @bot.tree.command(name="rnd_health", description="봇 자가 건강 진단")
    @is_cho()
    async def cmd_rnd_health(interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        try:
            from modules.rnd import run_health_check, post_to_rnd_channel
            embed = await run_health_check(bot)
            await _send_response(
                interaction, embed,
                query="/rnd_health",
                attach_files=True,
            )
            asyncio.create_task(post_to_rnd_channel(
                bot, category="health",
                title=f"수동 건강 체크 ({interaction.user.name})",
                content="`/rnd_health` 실행",
            ))
        except Exception as e:
            await _send_error(interaction, error_title="건강 진단 오류", error=e)

    @bot.tree.command(name="rnd_diagnose", description="이슈 진단")
    @is_cho()
    @app_commands.describe(issue="문제 설명")
    async def cmd_rnd_diagnose(interaction: discord.Interaction, issue: str):
        await interaction.response.defer(thinking=True)
        try:
            from modules.rnd import diagnose_issue, post_to_rnd_channel
            embed = await diagnose_issue(issue)
            await _send_response(
                interaction, embed,
                query=f"/rnd_diagnose {issue}",
                attach_files=True,
            )
            asyncio.create_task(post_to_rnd_channel(
                bot, category="issue",
                title=issue[:80],
                content=(embed.description or "")[:3000],
                author=interaction.user.name,
            ))
        except Exception as e:
            await _send_error(interaction, error_title="진단 오류", error=e)

    @bot.tree.command(name="rnd_design", description="신규 봇 설계서")
    @is_cho()
    @app_commands.describe(requirements="요구사항")
    async def cmd_rnd_design(interaction: discord.Interaction, requirements: str):
        await interaction.response.defer(thinking=True)
        try:
            from modules.rnd import design_new_bot, post_to_rnd_channel
            embed = await design_new_bot(requirements)
            await _send_response(
                interaction, embed,
                query=f"/rnd_design {requirements[:50]}",
                attach_files=True,
            )
            asyncio.create_task(post_to_rnd_channel(
                bot, category="design",
                title=f"신규 봇 설계: {requirements[:50]}",
                content=(embed.description or "")[:3500],
                author=interaction.user.name,
            ))
        except Exception as e:
            await _send_error(interaction, error_title="설계서 오류", error=e)

    @bot.tree.command(name="rnd_errors", description="최근 에러 요약")
    @is_cho()
    async def cmd_rnd_errors(interaction: discord.Interaction):
        from utils.self_monitor import get_error_summary, get_recent_errors
        summary = get_error_summary()
        recent = get_recent_errors(minutes=60)

        embed = discord.Embed(
            title="📊 개쵸 — 에러 리포트 (60분)",
            color=0x06B6D4,
        )
        if not recent:
            embed.description = "✅ 최근 1시간 내 에러 없음"
        else:
            summary_text = "\n".join(
                f"• **{cat}**: {cnt}회"
                for cat, cnt in sorted(summary.items(), key=lambda x: -x[1])[:10]
            )
            embed.add_field(name="카테고리별", value=summary_text, inline=False)

            recent_text = "\n".join(
                f"`{datetime.fromtimestamp(e['time']):%H:%M}` [{e['category']}] {e['message'][:80]}"
                for e in recent[-10:]
            )
            embed.add_field(name="최근 10건", value=recent_text[:1024], inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @bot.tree.command(name="rnd_announce", description="R&D 채널 공지")
    @is_cho()
    @app_commands.describe(
        category="공지 유형",
        title="제목",
        content="내용",
    )
    @app_commands.choices(category=[
        app_commands.Choice(name="🚀 업데이트", value="update"),
        app_commands.Choice(name="🔧 유지보수", value="maintenance"),
        app_commands.Choice(name="✨ 신규 기능", value="feature"),
        app_commands.Choice(name="⚠️ 이슈/장애", value="issue"),
    ])
    async def cmd_rnd_announce(
        interaction: discord.Interaction,
        category: str, title: str, content: str,
    ):
        from modules.rnd import post_to_rnd_channel
        ok = await post_to_rnd_channel(
            bot, category=category, title=title, content=content,
            author=interaction.user.name,
        )
        embed = (
            embed_success("공지 완료", "R&D 채널에 게시됨")
            if ok else
            embed_error(
                "공지 실패",
                "R&D 채널 미설정. `/rnd_channel`로 설정해주세요.",
            )
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @bot.tree.command(name="rnd_channel", description="R&D 공지 채널 설정")
    @is_cho()
    @app_commands.describe(channel="R&D 채널 (비우면 해제)")
    async def cmd_rnd_channel(
        interaction: discord.Interaction,
        channel: discord.TextChannel | discord.ForumChannel | None = None,
    ):
        from utils.config_manager import set_key

        if channel:
            perms = channel.permissions_for(interaction.guild.me) if interaction.guild else None
            if perms and not perms.send_messages:
                await interaction.response.send_message(
                    embed=embed_error(
                        "권한 부족",
                        f"{channel.mention}에 메시지 권한 없음",
                    ),
                    ephemeral=True,
                )
                return

            set_key("RND_CHANNEL_ID", str(channel.id))
            channel_type = "포럼" if isinstance(channel, discord.ForumChannel) else "텍스트"
            embed = discord.Embed(
                title="🔧 R&D 채널 설정 완료",
                description=(
                    f"채널: {channel.mention} ({channel_type})\n\n"
                    "**자동 게시**:\n"
                    "• 재배포 시 업데이트\n"
                    "• 매일 08:00 건강 리포트\n"
                    "• 에러 5회 초과 알림\n"
                    "• `/rnd_*` 명령 결과"
                ),
                color=0x06B6D4,
            )

            try:
                from modules.rnd import post_to_rnd_channel
                await post_to_rnd_channel(
                    bot, category="feature",
                    title="R&D 채널 연결 완료",
                    content=f"설정: {interaction.user.mention}",
                    author=interaction.user.name,
                )
            except Exception as e:
                log.warning(f"테스트 메시지 실패: {e}")
        else:
            set_key("RND_CHANNEL_ID", "")
            embed = discord.Embed(
                title="🔧 R&D 채널 해제",
                color=0x94A3B8,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @bot.tree.command(name="forum_channel", description="해쵸 포럼 세션 채널 설정")
    @is_cho()
    @app_commands.describe(channel="포럼 채널 (비우면 해제)")
    async def cmd_forum_channel(
        interaction: discord.Interaction,
        channel: discord.ForumChannel | None = None,
    ):
        from utils.config_manager import set_key

        if channel:
            perms = channel.permissions_for(interaction.guild.me) if interaction.guild else None
            if perms and not (perms.send_messages and perms.create_public_threads):
                await interaction.response.send_message(
                    embed=embed_error(
                        "권한 부족",
                        "스레드 생성 권한 필요",
                    ),
                    ephemeral=True,
                )
                return
            set_key("FORUM_CHANNEL_ID", str(channel.id))
            embed = discord.Embed(
                title="🎯 해쵸 포럼 설정 완료",
                description=f"채널: {channel.mention}",
                color=0x1E293B,
            )
        else:
            set_key("FORUM_CHANNEL_ID", "")
            embed = discord.Embed(title="🎯 포럼 해제", color=0x94A3B8)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @bot.tree.command(name="rnd_forum_channel", description="개쵸 코드 변경 포럼 채널 설정")
    @is_cho()
    @app_commands.describe(channel="포럼 채널 (비우면 해제)")
    async def cmd_rnd_forum_channel(
        interaction: discord.Interaction,
        channel: discord.ForumChannel | None = None,
    ):
        from utils.config_manager import set_key

        if channel:
            perms = channel.permissions_for(interaction.guild.me) if interaction.guild else None
            if perms and not (perms.send_messages and perms.create_public_threads):
                await interaction.response.send_message(
                    embed=embed_error(
                        "권한 부족",
                        "스레드 생성 권한 필요 (Create Public Threads)",
                    ),
                    ephemeral=True,
                )
                return

            set_key("RND_FORUM_CHANNEL_ID", str(channel.id))
            embed = discord.Embed(
                title="🔧 개쵸 R&D 포럼 설정 완료",
                description=(
                    f"채널: {channel.mention}\n\n"
                    "**자동 게시 항목**:\n"
                    "• `/code_propose` → PR 생성 시 변경 내역 자동 스레드\n"
                    "• 각 파일별 diff + 변경 요약\n"
                    "• PR 링크 + 머지 명령 안내\n\n"
                    "**테스트**: `/code_propose docstring 추가` 같은 작은 변경으로 확인"
                ),
                color=0x06B6D4,
            )
        else:
            set_key("RND_FORUM_CHANNEL_ID", "")
            embed = discord.Embed(
                title="🔧 R&D 포럼 해제",
                color=0x94A3B8,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ───────────────────────────────────────────────────────────
    # 🤖 개쵸 — 자동 코드 변경 (자연어 요청 → 자동 분석)
    # ───────────────────────────────────────────────────────────
    @bot.tree.command(
        name="code_propose",
        description="자연어로 코드 변경 요청 (개쵸가 알아서 분석 + 수정)",
    )
    @is_cho()
    @app_commands.describe(
        request="변경 요청 (예: 디쵸 figma 연동, /money 응답 개선, ...)",
        use_context="이전 대화 자동 참조 (기본 True)",
    )
    async def cmd_code_propose(
        interaction: discord.Interaction,
        request: str,
        use_context: bool = True,
    ):
        await interaction.response.defer(thinking=True)
        try:
            from modules.code_planner import create_planning_session
            from bot.code_planning_view import PlanApprovalView

            # 🆕 이전 대화 컨텍스트 자동 수집
            conversation_context = ""
            context_info = ""

            if use_context:
                try:
                    from utils.conversation_context import (
                        get_reply_context,
                        format_context_for_prompt,
                        detect_context_reference,
                    )

                    # "방금", "이전", "그거" 등 참조 표현 감지
                    has_reference = detect_context_reference(request)

                    # 참조 표현이 있거나 use_context=True이면 컨텍스트 수집
                    context_msgs = await get_reply_context(
                        interaction,
                        max_depth=5 if has_reference else 3,
                    )

                    if context_msgs:
                        conversation_context = format_context_for_prompt(context_msgs)
                        context_info = (
                            f" · 컨텍스트 {len(context_msgs)}개 메시지 참조"
                            + (" (참조 표현 감지)" if has_reference else "")
                        )
                        log.info(
                            f"/code_propose 컨텍스트: {len(context_msgs)}개 메시지 "
                            f"({len(conversation_context):,}자)"
                        )
                except Exception as e:
                    log.warning(f"컨텍스트 수집 실패 (무시): {e}")

            # 진행 메시지
            progress_embed = discord.Embed(
                title="🔍 개쵸 — 분석 중...",
                description=(
                    f"**요청**: {request[:200]}\n"
                    f"{context_info}\n\n"
                    "1️⃣ 의도 분석 → 2️⃣ 코드베이스 스캔 → 3️⃣ 변경 계획 수립\n"
                    "(약 30~60초 소요)"
                ),
                color=0xD97706,
            )
            progress_msg = await interaction.followup.send(embed=progress_embed)

            # 분석 파이프라인 실행 (컨텍스트 포함)
            result = await create_planning_session(
                user_request=request,
                requester=interaction.user.name,
                conversation_context=conversation_context,
            )

            if not result["success"]:
                err_embed = discord.Embed(
                    title="❌ 분석 실패",
                    description=result.get("error", "알 수 없는 오류"),
                    color=0xE11D48,
                )
                await progress_msg.edit(embed=err_embed)
                return

            session = result["session"]
            plan = session["plan"]
            intent = session["intent"]

            # 1차 승인 Embed
            plan_embed = discord.Embed(
                title=f"🤖 개쵸 — 변경 계획 (`{session['id']}`)",
                description=(
                    f"**요청**: {request[:300]}\n"
                    + (f"{context_info}\n" if context_info else "")
                    + f"\n### 💡 의도 분석\n"
                    f"• **의도**: {intent.get('intent', '')[:200]}\n"
                    f"• **스코프**: `{intent.get('scope', '?')}`\n"
                    f"• **리스크**: `{intent.get('risk', '?')}`\n"
                    f"• **대상**: `{intent.get('target_agent', '미지정')}`\n\n"
                    f"### 📋 변경 계획\n"
                    f"{plan.get('plan_summary', '')[:1000]}"
                ),
                color=0xD97706,
            )

            files = plan.get("files", [])
            if files:
                file_lines = []
                for f in files[:10]:
                    action_emoji = "🆕" if f.get("action") == "create" else "✏️"
                    file_lines.append(
                        f"{action_emoji} `{f['path']}` "
                        f"(~{f.get('estimated_lines', '?')}줄)\n"
                        f"   └ {f.get('purpose', '')[:100]}"
                    )
                plan_embed.add_field(
                    name=f"📂 변경 예정 파일 ({len(files)}개)",
                    value="\n\n".join(file_lines)[:1024],
                    inline=False,
                )

            deps = plan.get("requires_dependencies", [])
            if deps:
                plan_embed.add_field(
                    name="📦 추가 패키지",
                    value="\n".join(f"• `{d}`" for d in deps),
                    inline=False,
                )

            plan_embed.set_footer(
                text=f"예상 라인: {plan.get('estimated_total_lines', '?')}줄 · "
                     f"비용: ${session['total_cost']:.5f}",
            )

            view = PlanApprovalView(
                session_id=session["id"],
                owner_id=interaction.user.id,
                message=progress_msg,
                timeout=600,
            )

            await progress_msg.edit(embed=plan_embed, view=view)

        except Exception as e:
            await _send_error(interaction, error_title="코드 분석 오류", error=e)

    @bot.tree.command(name="code_sessions", description="최근 코드 변경 세션 목록")
    @is_cho()
    async def cmd_code_sessions(interaction: discord.Interaction):
        from modules.code_planner import list_sessions
        sessions = list_sessions(limit=15)

        if not sessions:
            embed = embed_info("📋 코드 세션", "최근 세션이 없습니다.")
        else:
            lines = []
            for s in sessions:
                emoji = {
                    "plan_pending": "🟡",
                    "plan_approved": "🔵",
                    "generating": "🔄",
                    "code_pending": "🟠",
                    "code_approved": "🟢",
                    "applying": "🚀",
                    "applied": "✅",
                    "failed": "❌",
                }.get(s["status"], "❓")
                rejected = s["status"].startswith("rejected")
                if rejected:
                    emoji = "❌"

                lines.append(
                    f"{emoji} `{s['id']}` — {s['user_request'][:60]}\n"
                    f"   └ 상태: `{s['status']}` · 비용: ${s.get('total_cost', 0):.4f}"
                )
            embed = discord.Embed(
                title="📋 최근 코드 변경 세션",
                description="\n\n".join(lines),
                color=0x4F46E5,
            )
        await _send_response(interaction, embed, query="/code_sessions", ephemeral=True)

    @bot.tree.command(name="code_diagnose", description="GitHub 연동 상태 진단")
    @is_cho()
    async def cmd_code_diagnose(interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            from utils.github_client import diagnose_github_access, _get_repo_info

            diag = await diagnose_github_access()
            info = _get_repo_info()

            # 상태 결정
            critical_issues = [i for i in diag["issues"] if i.startswith("❌")]
            if critical_issues:
                color = 0xE11D48
                status = "❌ 문제 있음"
            elif diag["issues"]:
                color = 0xEAB308
                status = "⚠️ 경고"
            else:
                color = 0x059669
                status = "✅ 정상"

            embed = discord.Embed(
                title=f"🔍 GitHub 연동 진단 — {status}",
                color=color,
            )

            # 1) 토큰 상태
            token_lines = [
                f"**설정됨**: {'✅' if diag['token_set'] else '❌'}",
                f"**유효함**: {'✅' if diag['token_valid'] else '❌'}",
            ]
            if diag["token_set"]:
                token_lines.append(f"**Preview**: `{diag['token_preview']}`")
            if diag["user_login"]:
                token_lines.append(f"**사용자**: `{diag['user_login']}`")
            if diag["scopes"]:
                token_lines.append(f"**스코프**: `{', '.join(diag['scopes'])}`")

            embed.add_field(
                name="🔑 토큰 상태",
                value="\n".join(token_lines),
                inline=False,
            )

            # 2) 레포 상태
            repo_lines = [
                f"**경로**: `{info['owner']}/{info['repo']}`",
                f"**브랜치**: `{info['branch']}`",
                f"**접근 가능**: {'✅' if diag['repo_accessible'] else '❌'}",
            ]
            if diag["repo_full_name"]:
                repo_lines.append(f"**확인된 이름**: `{diag['repo_full_name']}`")
            perms = diag.get("repo_permissions", {})
            if perms:
                perm_emoji = lambda v: "✅" if v else "❌"
                repo_lines.append(
                    f"**권한**: "
                    f"pull {perm_emoji(perms.get('pull'))} | "
                    f"push {perm_emoji(perms.get('push'))} | "
                    f"admin {perm_emoji(perms.get('admin'))}"
                )

            embed.add_field(
                name="📦 레포 상태",
                value="\n".join(repo_lines),
                inline=False,
            )

            # 3) Rate limit
            embed.add_field(
                name="⏱️ Rate Limit",
                value=(
                    f"잔여: **{diag['rate_limit_remaining']}** / "
                    f"{diag['rate_limit_max']}"
                ),
                inline=True,
            )

            # 4) 문제점
            if diag["issues"]:
                embed.add_field(
                    name=f"🚨 발견된 문제 ({len(diag['issues'])}개)",
                    value="\n".join(diag["issues"])[:1024],
                    inline=False,
                )

            # 5) 권장 조치
            if diag["recommendations"]:
                embed.add_field(
                    name="💡 권장 조치",
                    value="\n".join(f"• {r}" for r in diag["recommendations"])[:1024],
                    inline=False,
                )

            embed.set_footer(text="문제 해결 후 /code_propose 재시도")
            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            await _send_error(interaction, error_title="진단 오류", error=e)

    # ───────────────────────────────────────────────────────────
    # 시스템
    # ───────────────────────────────────────────────────────────
    @bot.tree.command(name="reboot", description="봇 재부팅")
    @is_cho()
    @app_commands.describe(reason="재부팅 사유")
    async def cmd_reboot(interaction: discord.Interaction, reason: str = "수동 재부팅"):
        from utils.restart_manager import request_restart
        await interaction.response.send_message(
            embed=embed_info("🔄 재부팅 시작", f"사유: {reason}\n약 30초 후 재접속"),
        )
        asyncio.create_task(request_restart(bot, reason=reason, delay_seconds=5))

    @bot.tree.command(name="uptime", description="봇 가동 시간")
    @is_cho()
    async def cmd_uptime(interaction: discord.Interaction):
        from utils.restart_manager import get_uptime, get_start_time
        embed = discord.Embed(title="⏱️ 봇 가동 현황", color=0x4F46E5)
        embed.add_field(name="가동 시간", value=get_uptime(), inline=False)
        embed.add_field(
            name="시작 시각",
            value=f"<t:{int(get_start_time().timestamp())}:F>",
            inline=False,
        )
        embed.add_field(name="서버 수", value=f"{len(bot.guilds)}개", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ───────────────────────────────────────────────────────────
    # 재부팅 스케줄 관리
    # ───────────────────────────────────────────────────────────
    @bot.tree.command(name="restart_schedule", description="자동 재부팅 시각 변경")
    @is_cho()
    @app_commands.describe(
        hour="시 (0~23, 비우면 현재 설정 조회)",
        minute="분 (0~59, 기본 0)",
    )
    async def cmd_restart_schedule(
        interaction: discord.Interaction,
        hour: int | None = None,
        minute: int = 0,
    ):
        from utils.restart_manager import (
            reschedule_auto_restart, get_restart_schedule,
        )

        # 인자 없으면 현재 설정 조회
        if hour is None:
            schedule = get_restart_schedule()
            embed = discord.Embed(
                title="⏰ 자동 재부팅 스케줄",
                color=0x4F46E5,
            )
            embed.add_field(
                name="📅 현재 설정",
                value=f"매일 **{schedule['hour']:02d}:{schedule['minute']:02d}**",
                inline=False,
            )
            if schedule["next_run"]:
                embed.add_field(
                    name="⏭️ 다음 실행",
                    value=f"<t:{int(schedule['next_run'].timestamp())}:F>",
                    inline=False,
                )
            embed.add_field(
                name="🔧 스케줄러",
                value="✅ 동작 중" if schedule["scheduler_running"] else "❌ 정지",
                inline=False,
            )
            embed.set_footer(text="변경: /restart_schedule hour:N minute:N")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # 변경
        result = reschedule_auto_restart(hour, minute)

        if result["success"]:
            embed = discord.Embed(
                title="✅ 재부팅 스케줄 변경",
                description=result["message"],
                color=0x059669,
            )
            if result["next_run"]:
                embed.add_field(
                    name="⏭️ 다음 실행",
                    value=f"<t:{int(result['next_run'].timestamp())}:F>",
                    inline=False,
                )
        else:
            embed = discord.Embed(
                title="❌ 변경 실패",
                description=result["message"],
                color=0xE11D48,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ───────────────────────────────────────────────────────────
    # 📚 기쵸 러닝 시스템
    # ───────────────────────────────────────────────────────────
    @bot.tree.command(name="gicho_learn_add", description="기쵸 학습 항목 등록")
    @is_cho()
    @app_commands.describe(
        subject="학습 주제",
        sources="소스 URL들 (쉼표로 구분)",
        category="카테고리",
        auto_approve="등록 즉시 자동 학습 시작 (기본 False)",
    )
    @app_commands.choices(category=[
        app_commands.Choice(name=cat, value=cat)
        for cat in ["콘텐츠_트렌드", "기획_기법", "협업_사례",
                    "썸네일_분석", "제목_분석", "스트리밍_기술",
                    "스폰서십", "기타"]
    ])
    async def cmd_gicho_learn_add(
        interaction: discord.Interaction,
        subject: str,
        sources: str,
        category: str = "기타",
        auto_approve: bool = False,
    ):
        await interaction.response.defer(thinking=True)
        try:
            from modules.gicho_learning import create_learning_item, execute_learning

            urls = [u.strip() for u in sources.split(",") if u.strip()]
            if not urls:
                await _send_error(interaction, error_title="등록 실패", error="유효한 URL이 없음")
                return

            item = create_learning_item(
                subject=subject,
                category=category,
                sources=urls,
                requested_by=interaction.user.name,
                auto_approve=auto_approve,
            )

            status_text = "자동 학습 시작" if auto_approve else "승인 대기"
            embed = discord.Embed(
                title=f"📚 학습 항목 등록 (`{item['id']}`)",
                description=(
                    f"**주제**: {subject}\n"
                    f"**카테고리**: {category}\n"
                    f"**소스**: {len(urls)}개\n"
                    f"**상태**: {status_text}\n\n"
                    + ("\n".join(f"• {u}" for u in urls[:5]))
                ),
                color=0xD97706,
            )

            if auto_approve:
                # 백그라운드에서 학습 실행
                asyncio.create_task(execute_learning(item["id"]))

            await _send_response(interaction, embed, query=f"/gicho_learn_add {subject}")
        except Exception as e:
            await _send_error(interaction, error_title="학습 등록 오류", error=e)

    @bot.tree.command(name="gicho_learn_approve", description="학습 승인 + 실행")
    @is_cho()
    @app_commands.describe(item_id="학습 항목 ID (8자리)")
    async def cmd_gicho_learn_approve(interaction: discord.Interaction, item_id: str):
        await interaction.response.defer(thinking=True)
        try:
            from modules.gicho_learning import approve_item, execute_learning

            if not approve_item(item_id):
                await _send_error(
                    interaction, error_title="승인 실패",
                    error="ID를 찾을 수 없거나 이미 처리됨",
                )
                return

            # 백그라운드 학습 실행
            asyncio.create_task(execute_learning(item_id))

            embed = embed_success(
                "✅ 학습 승인됨",
                f"항목 `{item_id}` 학습이 백그라운드에서 시작됩니다.\n"
                "완료 후 `/gicho_learn_status`로 결과 확인 가능.",
            )
            await _send_response(interaction, embed, query=f"/gicho_learn_approve {item_id}")
        except Exception as e:
            await _send_error(interaction, error_title="승인 오류", error=e)

    @bot.tree.command(name="gicho_learn_status", description="학습 항목 조회")
    @is_cho()
    @app_commands.describe(item_id="(선택) 특정 ID 조회. 비우면 전체 목록")
    async def cmd_gicho_learn_status(
        interaction: discord.Interaction,
        item_id: Optional[str] = None,
    ):
        await interaction.response.defer(thinking=True)
        try:
            from modules.gicho_learning import get_item, list_items, get_stats

            if item_id:
                # 단일 항목 상세
                item = get_item(item_id)
                if not item:
                    await _send_error(
                        interaction, error_title="조회 실패",
                        error="ID를 찾을 수 없음",
                    )
                    return

                insights = json.loads(item.get("insights") or "[]")
                applications = json.loads(item.get("applications") or "[]")

                embed = discord.Embed(
                    title=f"📚 학습 항목 `{item['id']}`",
                    description=(
                        f"**주제**: {item['subject']}\n"
                        f"**카테고리**: {item['category']}\n"
                        f"**상태**: {item['status']}\n"
                        f"**비용**: ${item.get('cost_usd', 0):.4f}\n\n"
                        f"### 요약\n{item.get('summary') or '(미완료)'}"
                    ),
                    color=0xD97706,
                )

                if insights:
                    embed.add_field(
                        name="💡 핵심 인사이트",
                        value="\n".join(f"• {i[:200]}" for i in insights[:7])[:1024],
                        inline=False,
                    )
                if applications:
                    embed.add_field(
                        name="🎯 활용 방안",
                        value="\n".join(f"• {a[:200]}" for a in applications[:5])[:1024],
                        inline=False,
                    )
            else:
                # 전체 통계 + 최근 목록
                stats = get_stats()
                items = list_items(limit=10)

                embed = discord.Embed(
                    title="📚 기쵸 러닝 시스템",
                    description=(
                        f"**총 학습**: {stats['total']}개\n"
                        f"**총 비용**: ${stats['total_cost_usd']:.4f}\n\n"
                        f"**상태별**: "
                        + " · ".join(f"{k}: {v}" for k, v in stats["by_status"].items())
                    ),
                    color=0xD97706,
                )

                if items:
                    lines = []
                    for item in items:
                        emoji = {
                            "requested": "⏳",
                            "approved": "🟡",
                            "learning": "🔄",
                            "completed": "✅",
                            "rejected": "❌",
                            "failed": "🚨",
                        }.get(item["status"], "❓")
                        lines.append(
                            f"{emoji} `{item['id']}` — {item['subject'][:60]} "
                            f"[{item['category']}]"
                        )
                    embed.add_field(
                        name="📋 최근 항목",
                        value="\n".join(lines)[:1024],
                        inline=False,
                    )

            await _send_response(interaction, embed, query="/gicho_learn_status")
        except Exception as e:
            await _send_error(interaction, error_title="조회 오류", error=e)

    # ───────────────────────────────────────────────────────────
    # /help — 페이지네이션
    # ───────────────────────────────────────────────────────────
    @bot.tree.command(name="help", description="명령어 도움말 (페이지별)")
    @is_cho()
    async def cmd_help(interaction: discord.Interaction):
        from bot.help_view import HelpView
        view = HelpView(owner_id=interaction.user.id)
        await interaction.response.send_message(
            embed=view._build_embed(),
            view=view,
            ephemeral=True,
        )

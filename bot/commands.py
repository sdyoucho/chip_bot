"""
bot/commands.py
Discord 슬래시 커맨드 등록 + 헬퍼 함수.
"""

# ═══════════════════════════════════════════════════════════════════
# Imports (반드시 파일 최상단)
# ═══════════════════════════════════════════════════════════════════
import asyncio
import io
import logging
import os
import time
import traceback
from datetime import datetime

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
        # DM 채널로 전송 (분할 포함)
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

    async def on_submit(self, interaction: discord.Interaction):
        from utils.config_manager import set_key
        updated = []
        mapping = [
            (self.openrouter.value, "OPENROUTER_API_KEY", "OpenRouter"),
            (self.perplexity.value, "PERPLEXITY_API_KEY", "Perplexity"),
            (self.youtube.value,    "YOUTUBE_API_KEY",    "YouTube"),
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
    # /ask — 자연어 통합 (정지 버튼 + 진행 업데이트)
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

        start_trace()
        t_start = time.monotonic()
        summary_embed = None
        agent_results = {}

        view = AskProgressView(query=query, owner_id=interaction.user.id)
        progress_msg = await interaction.followup.send(
            embed=build_progress_embed(query, "라우팅", "필요한 에이전트 선별 중..."),
            view=view,
        )

        async def _do_work():
            nonlocal summary_embed, agent_results
            routing = await route(query)
            step(
                "Router (OpenRouter)", "ok",
                f"agents={[m['name'] for m in routing['modules']]} "
                f"summary={routing['needs_haecho_summary']}",
            )

            agents_str = ", ".join(m['name'] for m in routing['modules'])
            elapsed = int((time.monotonic() - t_start) * 1000)
            try:
                await progress_msg.edit(
                    embed=build_progress_embed(
                        query, "수집",
                        f"에이전트 호출 중: **{agents_str}**",
                        elapsed_ms=elapsed,
                    ),
                    view=view,
                )
            except Exception:
                pass

            result = await orchestrate(query, routing, streamer)
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

            if not final_embed and agent_results:
                for _, result_tuple in agent_results.items():
                    if isinstance(result_tuple, tuple) and result_tuple[0]:
                        if _is_embed_valid(result_tuple[0]):
                            final_embed = result_tuple[0]
                            break

            if not final_embed:
                final_embed = _build_fallback_embed(query, agent_results)

            view.clear_items()
            view.stop()

            # ✅ 새 코드 (자동 분할 지원)
            from utils.message_splitter import edit_long_embed

            # View 제거 + 자동 분할 편집
            ok = await edit_long_embed(progress_msg, final_embed, view=None)
            if ok:
                step("응답 전송", "ok", f"총 {elapsed_total}ms")
            else:
                step("응답 전송", "fail", "edit 실패 → 새 메시지", "E012")
                await _safe_send_embed(interaction, final_embed)

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
                from utils.message_splitter import edit_long_embed
                await edit_long_embed(progress_msg, timeout_embed, view=None)
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
                from utils.message_splitter import edit_long_embed
                await edit_long_embed(progress_msg, err_embed, view=None)
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
            await interaction.followup.send(embed=await get_current_status(streamer))
        except Exception as e:
            log.exception(e)
            await interaction.followup.send(embed=embed_error("모니터링 오류", str(e)))

    @bot.tree.command(name="report", description="주간 분석 리포트")
    @is_cho()
    @app_commands.describe(streamer="스트리머 이름 (미입력 시 전체)")
    async def cmd_report(interaction: discord.Interaction, streamer: str = "all"):
        await interaction.response.defer(thinking=True)
        try:
            from modules.weekly_report import generate_report
            await interaction.followup.send(embed=await generate_report(streamer))
        except Exception as e:
            log.exception(e)
            await interaction.followup.send(embed=embed_error("리포트 오류", str(e)))

    @bot.tree.command(name="youtube", description="유튜브 채널 통계")
    @is_cho()
    @app_commands.describe(streamer="스트리머 이름")
    async def cmd_youtube(interaction: discord.Interaction, streamer: str):
        await interaction.response.defer(thinking=True)
        try:
            from modules.youtube_analytics import get_channel_stats
            await interaction.followup.send(embed=await get_channel_stats(streamer))
        except Exception as e:
            log.exception(e)
            await interaction.followup.send(embed=embed_error("유튜브 오류", str(e)))

    @bot.tree.command(name="schedule", description="스케줄 조회")
    @is_cho()
    @app_commands.describe(query="조회할 기간")
    async def cmd_schedule(interaction: discord.Interaction, query: str = "이번주"):
        await interaction.response.defer(thinking=True)
        try:
            from modules.schedule import handle_schedule
            await interaction.followup.send(embed=await handle_schedule(query))
        except Exception as e:
            log.exception(e)
            await interaction.followup.send(embed=embed_error("스케줄 오류", str(e)))

    @bot.tree.command(name="money", description="자금 현황 및 API 비용")
    @is_cho()
    async def cmd_money(interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        try:
            from modules.money import get_financial_summary
            await interaction.followup.send(embed=await get_financial_summary())
        except Exception as e:
            log.exception(e)
            await interaction.followup.send(embed=embed_error("자금 오류", str(e)))

    @bot.tree.command(name="settlement", description="월말정산 + 다음 달 예상")
    @is_cho()
    async def cmd_settlement(interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        from modules.money import monthly_settlement
        await interaction.followup.send(embed=await monthly_settlement())

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
            await interaction.followup.send(embed=embed_info(
                f"✅ {name} 등록 완료",
                f"치지직: {chzzk_url or '미등록'}\n"
                f"유튜브: {youtube_url or '미등록'}\n"
                f"SOOP: {soop_url or '미등록'}",
            ))
        except Exception as e:
            log.exception(e)
            await interaction.followup.send(embed=embed_error("등록 오류", str(e)))

    @bot.tree.command(name="streamer_list", description="등록된 스트리머 목록")
    @is_cho()
    async def cmd_streamer_list(interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        try:
            from utils.notion_client import list_streamers
            streamers = await list_streamers()
            if not streamers:
                await interaction.followup.send(embed=embed_info(
                    "스트리머 목록", "등록된 스트리머가 없습니다.",
                ))
                return
            lines = "\n".join(f"• {s['name']}" for s in streamers)
            await interaction.followup.send(embed=embed_info(
                f"스트리머 목록 ({len(streamers)}명)", lines,
            ))
        except Exception as e:
            log.exception(e)
            await interaction.followup.send(embed=embed_error("목록 오류", str(e)))

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
        # 대신 수동으로 "이미 설정됐으면 설정자만 허용" 체크
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
        from modules.fixed_costs import list_fixed_costs
        await interaction.followup.send(embed=await list_fixed_costs())

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
        from modules.schedule import add_schedule
        await interaction.followup.send(embed=await add_schedule(title, date, memo))

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
        from modules.schedule import update_schedule
        await interaction.followup.send(
            embed=await update_schedule(short_id, title, date),
        )

    @bot.tree.command(name="schedule_remove", description="스케줄 삭제")
    @is_cho()
    @app_commands.describe(short_id="삭제할 8자리 ID")
    async def cmd_schedule_remove(interaction: discord.Interaction, short_id: str):
        await interaction.response.defer(thinking=True)
        from modules.schedule import delete_schedule
        await interaction.followup.send(embed=await delete_schedule(short_id))

    # ───────────────────────────────────────────────────────────
    # 개쵸 R&D
    # ───────────────────────────────────────────────────────────
    @bot.tree.command(name="rnd_health", description="봇 자가 건강 진단")
    @is_cho()
    async def cmd_rnd_health(interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        from modules.rnd import run_health_check, post_to_rnd_channel
        embed = await run_health_check(bot)
        await interaction.followup.send(embed=embed)
        asyncio.create_task(post_to_rnd_channel(
            bot, category="health",
            title=f"수동 건강 체크 ({interaction.user.name})",
            content="`/rnd_health` 실행",
        ))

    @bot.tree.command(name="rnd_diagnose", description="이슈 진단")
    @is_cho()
    @app_commands.describe(issue="문제 설명")
    async def cmd_rnd_diagnose(interaction: discord.Interaction, issue: str):
        await interaction.response.defer(thinking=True)
        from modules.rnd import diagnose_issue, post_to_rnd_channel
        embed = await diagnose_issue(issue)
        await interaction.followup.send(embed=embed)
        asyncio.create_task(post_to_rnd_channel(
            bot, category="issue",
            title=issue[:80],
            content=(embed.description or "")[:3000],
            author=interaction.user.name,
        ))

    @bot.tree.command(name="rnd_design", description="신규 봇 설계서")
    @is_cho()
    @app_commands.describe(requirements="요구사항")
    async def cmd_rnd_design(interaction: discord.Interaction, requirements: str):
        await interaction.response.defer(thinking=True)
        from modules.rnd import design_new_bot, post_to_rnd_channel
        embed = await design_new_bot(requirements)
        await interaction.followup.send(embed=embed)
        asyncio.create_task(post_to_rnd_channel(
            bot, category="design",
            title=f"신규 봇 설계: {requirements[:50]}",
            content=(embed.description or "")[:3500],
            author=interaction.user.name,
        ))

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
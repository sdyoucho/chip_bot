"""
bot/commands.py
모든 슬래시 커맨드 정의.
Cho(오퍼레이터)만 사용 가능 — CHO_USER_ID 체크.
"""

import logging
import os

import discord
from discord import app_commands
from discord.ext import commands

from bot.router import route
from bot.embeds import (
    embed_error, embed_info, embed_thinking,
    embed_unknown_command
)

log = logging.getLogger(__name__)

CHO_USER_ID = int(os.getenv("CHO_USER_ID", "0"))


# ── /config 모달 정의 ────────────────────────────────────────────────

class _AIKeysModal(discord.ui.Modal, title="AI API 키 설정"):
    openrouter = discord.ui.TextInput(
        label="OpenRouter API Key  (필수 — 모든 LLM 통합)",
        placeholder="sk-or-v1-...",
        required=True,
        style=discord.TextStyle.short,
        max_length=200,
    )
    perplexity = discord.ui.TextInput(
        label="Perplexity API Key  (선택 — 분쵸 직접 호출)",
        placeholder="pplx-... (OpenRouter로 대체하려면 비워두세요)",
        required=False,
        style=discord.TextStyle.short,
        max_length=200,
    )
    youtube = discord.ui.TextInput(
        label="YouTube Data API v3 Key  (유튜브 통계용)",
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
        placeholder="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        required=False,
        style=discord.TextStyle.short,
        max_length=100,
    )
    broadcast_db = discord.ui.TextInput(
        label="방송 로그 DB ID",
        placeholder="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        required=False,
        style=discord.TextStyle.short,
        max_length=100,
    )
    report_db = discord.ui.TextInput(
        label="리포트 DB ID",
        placeholder="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        required=False,
        style=discord.TextStyle.short,
        max_length=100,
    )
    schedule_db = discord.ui.TextInput(
        label="스케줄 DB ID",
        placeholder="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        required=False,
        style=discord.TextStyle.short,
        max_length=100,
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


class _DiscordKeysModal(discord.ui.Modal, title="Discord 설정"):
    guild_id = discord.ui.TextInput(
        label="Discord Guild ID",
        required=False, max_length=30,
    )
    cho_user_id = discord.ui.TextInput(
        label="오퍼레이터 유저 ID",
        required=False, max_length=30,
    )
    forum_channel_id = discord.ui.TextInput(
        label="포럼 채널 ID (해쵸 세션 기록용)",
        required=False, max_length=30,
    )

    async def on_submit(self, interaction: discord.Interaction):
        from utils.config_manager import set_key
        updated = []
        fields = [
            (self.guild_id.value,         "DISCORD_GUILD_ID",  "Guild ID"),
            (self.cho_user_id.value,      "CHO_USER_ID",       "CHO_USER_ID"),
            (self.forum_channel_id.value, "FORUM_CHANNEL_ID",  "Forum Channel"),
        ]
        for val, env_key, label in fields:
            if val.strip():
                set_key(env_key, val.strip())
                updated.append(label)

        msg = f"저장 완료: {', '.join(updated)}" if updated else "입력된 값이 없습니다."
        await interaction.response.send_message(
            embed=embed_info("🤖 Discord 설정", msg), ephemeral=True
        )


def is_cho():
    """Cho만 명령 실행 가능하도록 체크."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.id != CHO_USER_ID:
            await interaction.response.send_message(
                embed=embed_error("접근 불가", "이 봇은 오퍼레이터 전용입니다."),
                ephemeral=True
            )
            return False
        return True
    return app_commands.check(predicate)


async def setup_commands(bot: commands.Bot):
    """봇에 슬래시 커맨드 트리 등록."""

    # ── /ask — 자연어 통합 명령 ─────────────────────────────────────
    @bot.tree.command(name="ask", description="자연어로 무엇이든 물어보세요")
    @is_cho()
    @app_commands.describe(query="질문 또는 명령", streamer="(선택) 스트리머 이름")
    async def cmd_ask(interaction: discord.Interaction, query: str, streamer: str = ""):
        await interaction.response.defer(thinking=True)
        from utils.pipeline_logger import (
        start_trace, get_trace, is_enabled,
        get_output_mode, get_log_channel, step,
        )
        from bot.router import route
        from modules.haecho import orchestrate
        from utils.forum_publisher import publish_session

        start_trace()
        try:
            # ① Router (다중 선택)
            routing = await route(query)
            step("Router (Gemini Flash)", "ok",
                 f"agents={[m['name'] for m in routing['modules']]} "
                f"summary={routing['needs_haecho_summary']}")

            # ② 오케스트레이션 (필요 agent만 병렬)
            result = await orchestrate(query, routing, streamer)
            agent_results = result["agent_results"]
            summary_embed = result["summary_embed"]

            # ③ Discord 응답 (요약 우선, 없으면 첫 agent 결과)
            if summary_embed:
                await interaction.followup.send(embed=summary_embed)
            elif agent_results:
                first = next(iter(agent_results.values()))
                await interaction.followup.send(embed=first[0])
            else:
                await interaction.followup.send(embed=embed_unknown_command(query))

            # ④ Forum 병렬 발행 (raw + summary 분리 기록)
            if summary_embed and agent_results:
                asyncio.create_task(publish_session(
                    bot,
                    query=query,
                    agent_results=agent_results,
                    summary_embed=summary_embed,
                ))

        except Exception as e:
            log.exception(f"/ask 오류: {e}")
            step("처리", "fail", str(e)[:100], "E000")
            await interaction.followup.send(embed=embed_error("오류", str(e)))

        finally:
            # Raw Data 트레이스 (기존 로직 그대로)
            if is_enabled():
                trace = get_trace()
                if trace:
                    mode = get_output_mode()
                    if mode in ("ephemeral", "both"):
                        await interaction.followup.send(
                            embed=trace.to_embed(query=query),
                            ephemeral=True,
                        )
                    if mode in ("channel", "both"):
                        ch_id = get_log_channel()
                        if ch_id:
                            ch = bot.get_channel(ch_id)
                            if ch:
                                await ch.send(embed=trace.to_embed(
                                    query=query, user=str(interaction.user),
                                    for_channel=True,
                                ))


    # ── /monitor — 방송 현황 ─────────────────────────────────────────
    @bot.tree.command(name="monitor", description="스트리머 방송 현황 확인")
    @is_cho()
    @app_commands.describe(streamer="스트리머 이름 (미입력 시 전체)")
    async def cmd_monitor(interaction: discord.Interaction, streamer: str = "all"):
        await interaction.response.defer(thinking=True)
        try:
            from modules.chzzk_monitor import get_current_status
            data = await get_current_status(streamer)
            await interaction.followup.send(embed=data)
        except Exception as e:
            log.exception(e)
            await interaction.followup.send(embed=embed_error("모니터링 오류", str(e)))

    # ── /report — 주간 리포트 ───────────────────────────────────────
    @bot.tree.command(name="report", description="주간 분석 리포트 즉시 생성")
    @is_cho()
    @app_commands.describe(streamer="스트리머 이름 (미입력 시 전체)")
    async def cmd_report(interaction: discord.Interaction, streamer: str = "all"):
        await interaction.response.defer(thinking=True)
        try:
            from modules.weekly_report import generate_report
            embed = await generate_report(streamer)
            await interaction.followup.send(embed=embed)
        except Exception as e:
            log.exception(e)
            await interaction.followup.send(embed=embed_error("리포트 오류", str(e)))

    # ── /youtube — 유튜브 통계 ──────────────────────────────────────
    @bot.tree.command(name="youtube", description="유튜브 채널 통계 조회")
    @is_cho()
    @app_commands.describe(streamer="스트리머 이름")
    async def cmd_youtube(interaction: discord.Interaction, streamer: str):
        await interaction.response.defer(thinking=True)
        try:
            from modules.youtube_analytics import get_channel_stats
            embed = await get_channel_stats(streamer)
            await interaction.followup.send(embed=embed)
        except Exception as e:
            log.exception(e)
            await interaction.followup.send(embed=embed_error("유튜브 오류", str(e)))

    # ── /schedule — 스케줄 ──────────────────────────────────────────
    @bot.tree.command(name="schedule", description="스케줄 확인 또는 등록")
    @is_cho()
    @app_commands.describe(query="조회할 기간 또는 등록 내용")
    async def cmd_schedule(interaction: discord.Interaction, query: str = "이번주"):
        await interaction.response.defer(thinking=True)
        try:
            from modules.schedule import handle_schedule
            embed = await handle_schedule(query)
            await interaction.followup.send(embed=embed)
        except Exception as e:
            log.exception(e)
            await interaction.followup.send(embed=embed_error("스케줄 오류", str(e)))

    # ── /money — 자금 현황 ──────────────────────────────────────────
    @bot.tree.command(name="money", description="자금 현황 및 API 비용 조회")
    @is_cho()
    async def cmd_money(interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        try:
            from modules.money import get_financial_summary
            embed = await get_financial_summary()
            await interaction.followup.send(embed=embed)
        except Exception as e:
            log.exception(e)
            await interaction.followup.send(embed=embed_error("자금 오류", str(e)))

    # ── /streamer add — 신규 스트리머 등록 ─────────────────────────
    @bot.tree.command(name="streamer_add", description="신규 스트리머 등록")
    @is_cho()
    @app_commands.describe(
        name="스트리머 이름",
        chzzk_url="치지직 채널 URL (없으면 생략)",
        youtube_url="유튜브 채널 URL (없으면 생략)",
        soop_url="SOOP 채널 URL (없으면 생략)",
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
            result = await register_streamer(name, chzzk_url, youtube_url, soop_url)
            embed = embed_info(
                f"✅ {name} 등록 완료",
                f"치지직: {chzzk_url or '미등록'}\n유튜브: {youtube_url or '미등록'}\nSOOP: {soop_url or '미등록'}"
            )
            await interaction.followup.send(embed=embed)
        except Exception as e:
            log.exception(e)
            await interaction.followup.send(embed=embed_error("등록 오류", str(e)))

    # ── /streamer_list — 목록 ───────────────────────────────────────
    @bot.tree.command(name="streamer_list", description="등록된 스트리머 목록")
    @is_cho()
    async def cmd_streamer_list(interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        try:
            from utils.notion_client import list_streamers
            streamers = await list_streamers()
            if not streamers:
                await interaction.followup.send(embed=embed_info("스트리머 목록", "등록된 스트리머가 없습니다."))
                return
            lines = "\n".join([f"• {s['name']}" for s in streamers])
            await interaction.followup.send(embed=embed_info(f"스트리머 목록 ({len(streamers)}명)", lines))
        except Exception as e:
            log.exception(e)
            await interaction.followup.send(embed=embed_error("목록 오류", str(e)))

    # ── /config ai — AI API 키 설정 ────────────────────────────────
    @bot.tree.command(name="config_ai", description="AI API 키 설정 (Gemini·Anthropic·OpenAI·Perplexity)")
    @is_cho()
    async def cmd_config_ai(interaction: discord.Interaction):
        await interaction.response.send_modal(_AIKeysModal())

    # ── /config notion — Notion 설정 ────────────────────────────────
    @bot.tree.command(name="config_notion", description="Notion 토큰 및 DB ID 설정")
    @is_cho()
    async def cmd_config_notion(interaction: discord.Interaction):
        await interaction.response.send_modal(_NotionKeysModal())

    # ── /config discord — Discord 설정 ──────────────────────────────
    @bot.tree.command(name="config_discord", description="Discord Guild ID · 오퍼레이터 ID 설정")
    @is_cho()
    async def cmd_config_discord(interaction: discord.Interaction):
        await interaction.response.send_modal(_DiscordKeysModal())

    # ── /config_status — 현재 설정 확인 ────────────────────────────
    @bot.tree.command(name="config_status", description="현재 API 키 설정 현황 확인")
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

        embed.set_footer(text="키 변경: /config_ai · /config_notion · /config_discord")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /rawdata — 출력 모드 설정 ──────────────────────────────────
    @bot.tree.command(name="rawdata", description="Raw Data 파이프라인 트레이스 출력 모드 설정")
    @is_cho()
    @app_commands.describe(mode="출력 방식 선택")
    @app_commands.choices(mode=[
        app_commands.Choice(name="off      — 비활성 (기본값)", value="off"),
        app_commands.Choice(name="ephemeral — 나에게만 보이는 임시 메시지", value="ephemeral"),
        app_commands.Choice(name="channel   — 지정 채널에 영구 기록", value="channel"),
        app_commands.Choice(name="both      — 임시 + 채널 동시 전송", value="both"),
    ])
    async def cmd_rawdata(interaction: discord.Interaction, mode: str):
        from utils.pipeline_logger import (
            set_output_mode, get_output_mode, get_log_channel,
        )
        set_output_mode(mode)  # type: ignore[arg-type]

        ch_id = get_log_channel()
        ch_mention = f"<#{ch_id}>" if ch_id else "❌ 미설정 (`/rawdata_channel`로 지정)"

        MODE_INFO = {
            "off": ("🔬 Raw Data OFF", 0x94A3B8,
                    "파이프라인 트레이스가 비활성화되었습니다."),
            "ephemeral": ("🔬 Raw Data — Ephemeral 모드", 0x4F46E5,
                          "트레이스가 **나에게만 보이는 임시 메시지**로 표시됩니다.\n"
                          "창을 닫으면 기록이 사라집니다."),
            "channel": ("🔬 Raw Data — Channel 모드", 0x059669,
                        f"트레이스가 **지정 채널에 영구 기록**됩니다.\n"
                        f"로그 채널: {ch_mention}"),
            "both": ("🔬 Raw Data — Both 모드", 0xD97706,
                     f"트레이스가 **임시 메시지 + 채널** 양쪽으로 전송됩니다.\n"
                     f"로그 채널: {ch_mention}"),
        }
        title, color, desc = MODE_INFO[mode]

        if mode in ("channel", "both") and not ch_id:
            desc += "\n\n⚠️ 로그 채널이 설정되지 않았습니다. `/rawdata_channel`로 먼저 지정하세요."
            color = 0xD97706

        embed = discord.Embed(title=title, description=desc, color=color)
        embed.set_footer(text="트레이스 항목: 전처리·AI 호출·후처리 단계, 소요 ms, 오류코드 E001–E012")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /rawdata_channel — 로그 채널 설정 ──────────────────────────
    @bot.tree.command(name="rawdata_channel", description="Raw Data 트레이스를 기록할 Discord 채널 설정")
    @is_cho()
    @app_commands.describe(channel="트레이스를 전송할 텍스트 채널 (비우면 설정 해제)")
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
                title="📋 Raw Data 로그 채널 설정 완료",
                description=(
                    f"트레이스 기록 채널: {channel.mention}\n\n"
                    f"현재 모드: `{get_output_mode()}`\n"
                    "채널 기록을 사용하려면 `/rawdata channel` 또는 `/rawdata both`로 설정하세요."
                ),
                color=0x059669,
            )
        else:
            set_log_channel(None)
            set_key("LOG_RAW_CHANNEL_ID", "")
            embed = discord.Embed(
                title="📋 Raw Data 로그 채널 해제",
                description="채널 설정이 해제되었습니다. ephemeral 모드만 사용 가능합니다.",
                color=0x94A3B8,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ── /settlement — 월말정산 수동 실행 ─────────────────────────────
    @bot.tree.command(name="settlement", description="이번 달 AI 지출 정산 + 다음 달 예상")
    @is_cho()
    async def cmd_settlement(interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        from modules.money import monthly_settlement
        embed = await monthly_settlement()
        await interaction.followup.send(embed=embed)


    # ── /model_status — 현재 모델 설정 조회 ────────────────────────
    @bot.tree.command(name="model_status", description="현재 AI 모델 티어링 및 에이전트 매핑 조회")
    @is_cho()
    async def cmd_model_status(interaction: discord.Interaction):
        from utils.openrouter_client import get_current_config
        cfg = get_current_config()

        embed = discord.Embed(
            title="🧠 AI 모델 티어링 현황",
            color=0x4F46E5,
        )

        # 티어별
        tier_lines = "\n".join(
            f"• **`{tier}`** → `{model}`"
            for tier, model in cfg["tiers"].items()
        )
        embed.add_field(name="📐 티어 매핑", value=tier_lines, inline=False)

        # 에이전트별
        agent_lines = "\n".join(
            f"• **{agent}** → `{tier}` (`{cfg['tiers'].get(tier, '?')}`)"
            for agent, tier in cfg["agents"].items()
        )
        embed.add_field(name="🤖 에이전트 매핑", value=agent_lines, inline=False)

        embed.set_footer(text="변경: /model_set · /model_agent · 초기화: /model_reset")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /model_set — 티어의 모델 변경 ───────────────────────────────
    @bot.tree.command(name="model_set", description="특정 티어의 AI 모델을 변경")
    @is_cho()
    @app_commands.describe(
        tier="변경할 티어",
        model="새 모델 ID (예: openai/gpt-4o, anthropic/claude-opus-4.7)",
    )
    @app_commands.choices(tier=[
        app_commands.Choice(name="router — 라우팅 판단", value="router"),
        app_commands.Choice(name="light — 단순 Q&A", value="light"),
        app_commands.Choice(name="standard — 기획·R&D", value="standard"),
        app_commands.Choice(name="premium — 해쵸 종합", value="premium"),
        app_commands.Choice(name="research — 분쵸 리서치", value="research"),
        app_commands.Choice(name="vision — 디쵸 디자인", value="vision"),
    ])
    async def cmd_model_set(interaction: discord.Interaction, tier: str, model: str):
        from utils.openrouter_client import set_tier_model
        try:
            set_tier_model(tier, model.strip(), persist=True)
            embed = embed_success(
                "모델 변경 완료",
                f"티어 `{tier}` → `{model.strip()}`\n"
                "이후 모든 요청에 즉시 적용됩니다.",
            )
        except ValueError as e:
            embed = embed_error("변경 실패", str(e))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /model_agent — 에이전트의 기본 티어 변경 ───────────────────
    @bot.tree.command(name="model_agent", description="에이전트의 기본 티어를 변경")
    @is_cho()
    @app_commands.describe(agent="변경할 에이전트", tier="새 티어")
    @app_commands.choices(
        agent=[
            app_commands.Choice(name="해쵸 (haecho)", value="haecho"),
            app_commands.Choice(name="기쵸 (gihyo)", value="gihyo"),
            app_commands.Choice(name="인쵸 (inchyo)", value="inchyo"),
            app_commands.Choice(name="분쵸 (bunchyo)", value="bunchyo"),
            app_commands.Choice(name="스쵸 (sochyo)", value="sochyo"),
            app_commands.Choice(name="모쵸 (mochyo)", value="mochyo"),
            app_commands.Choice(name="개쵸 (gaechyo)", value="gaechyo"),
            app_commands.Choice(name="디쵸 (dichyo)", value="dichyo"),
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
                f"**{agent}** → `{tier}` (`{MODEL_TIERS[tier]}`)\n"
                "이후 모든 요청에 즉시 적용됩니다.",
            )
        except ValueError as e:
            embed = embed_error("변경 실패", str(e))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /model_reset — 오버라이드 초기화 ───────────────────────────
    @bot.tree.command(name="model_reset", description="모델 설정을 기본값으로 초기화 (재부팅 권고)")
    @is_cho()
    async def cmd_model_reset(interaction: discord.Interaction):
        from utils.model_config import reset_overrides
        reset_overrides()
        embed = embed_info(
            "🔄 모델 설정 초기화",
            "저장된 오버라이드가 제거되었습니다.\n"
            "**봇을 재시작**해야 코드에 정의된 기본값으로 완전히 복귀됩니다.\n"
            "(현재 프로세스는 마지막 설정을 메모리에 유지)",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


    # ── /help ────────────────────────────────────────────────────────
    @bot.tree.command(name="help", description="사용 가능한 명령 목록")
    @is_cho()
    async def cmd_help(interaction: discord.Interaction):
        embed = discord.Embed(
            title="🤖 Cho's 매니지먼트 봇 — 명령 목록",
            description="오퍼레이터 전용 — Cho만 사용 가능",
            color=0x4F46E5,
        )

        # ── 핵심 운영 ──
        core = [
            ("/ask `[질문]` `[스트리머?]`",  "자연어 통합 명령 — 해쵸가 필요 agent 자동 선별"),
            ("/monitor `[스트리머]`",          "실시간 방송 현황 (모쵸)"),
            ("/report `[스트리머]`",           "주간 분석 리포트 (분쵸)"),
            ("/youtube `[스트리머]`",          "유튜브 채널 통계 (분쵸)"),
            ("/schedule `[기간]`",             "스케줄 조회 (스쵸)"),
        ]
        embed.add_field(
            name="📌 핵심 운영",
            value="\n".join(f"**{c}** — {d}" for c, d in core),
            inline=False,
        )

        # ── 자금 / 토큰 ──
        money_cmds = [
            ("/money",        "현재 자금·크레딧 현황 (인쵸)"),
            ("/settlement",   "이번 달 월말정산 + 다음 달 예상"),
        ]
        embed.add_field(
            name="💰 자금 / 토큰",
            value="\n".join(f"**{c}** — {d}" for c, d in money_cmds),
            inline=False,
        )

        # ── 스트리머 관리 ──
        sm = [
            ("/streamer_add",  "신규 스트리머 등록"),
            ("/streamer_list", "등록 스트리머 목록"),
        ]
        embed.add_field(
            name="👥 스트리머",
            value="\n".join(f"**{c}** — {d}" for c, d in sm),
            inline=False,
        )

        # ── 설정 ──
        cfg = [
            ("/config_ai",      "AI API 키 입력 (Gemini·Claude·GPT·Perplexity·OpenRouter)"),
            ("/config_notion",  "Notion 토큰 + DB ID"),
            ("/config_discord", "Guild ID · 오퍼레이터 ID · 포럼 채널 ID"),
            ("/config_status",  "현재 API 키 설정 현황"),
        ]
        embed.add_field(
            name="⚙️ 설정",
            value="\n".join(f"**{c}** — {d}" for c, d in cfg),
            inline=False,
        )

        # ── 모델 티어링 ──
        model_cmds = [
            ("/model_status",  "현재 티어링·에이전트 매핑 조회"),
            ("/model_set",     "티어의 모델 변경 (예: light → gpt-4o-mini)"),
            ("/model_agent",   "에이전트의 기본 티어 변경"),
            ("/model_reset",   "모델 설정 초기화 (재부팅 권고)"),
        ]
        embed.add_field(
            name="🧠 모델 관리",
            value="\n".join(f"**{c}** — {d}" for c, d in model_cmds),
            inline=False,
        )

        # ── 관찰성 ──
        obs = [
            ("/rawdata `[모드]`",   "파이프라인 트레이스: off / ephemeral / channel / both"),
            ("/rawdata_channel",   "트레이스 영구 기록 채널 지정"),
        ]
        embed.add_field(
            name="🔬 관찰성",
            value="\n".join(f"**{c}** — {d}" for c, d in obs),
            inline=False,
        )

        embed.set_footer(
            text="현재 모델: router·light=gpt-5.4-nano · standard·premium=opus 4.7 · "
                 "research=sonar-pro · vision=gpt-4o"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def _streamer_list_embed() -> discord.Embed:
    from utils.notion_client import list_streamers
    streamers = await list_streamers()
    if not streamers:
        return embed_info("스트리머 목록", "등록된 스트리머가 없습니다.")
    lines = "\n".join([f"• {s['name']}" for s in streamers])
    return embed_info(f"스트리머 목록 ({len(streamers)}명)", lines)


async def _dispatch(module: str, query: str, interaction) -> discord.Embed | None:
    """라우팅 결과에 따라 적절한 모듈 호출."""
    dispatch_map = {
        "haecho":       lambda: __import__('modules.haecho', fromlist=['brief']).brief(query),
        "monitor":      lambda: __import__('modules.chzzk_monitor', fromlist=['get_current_status']).get_current_status("all"),
        "youtube":      lambda: __import__('modules.youtube_analytics', fromlist=['get_channel_stats']).get_channel_stats("all"),
        "report":       lambda: __import__('modules.weekly_report', fromlist=['generate_report']).generate_report("all"),
        "competitor":   lambda: __import__('modules.competitor_analysis', fromlist=['run_analysis']).run_analysis(),
        "suggest":      lambda: __import__('modules.content_suggest', fromlist=['generate_suggestions']).generate_suggestions(query),
        "schedule":     lambda: __import__('modules.schedule', fromlist=['handle_schedule']).handle_schedule(query),
        "money":        lambda: __import__('modules.money', fromlist=['get_financial_summary']).get_financial_summary(),
        "planning":     lambda: __import__('modules.planning', fromlist=['create_document']).create_document(query),
        "rnd":          lambda: __import__('modules.rnd', fromlist=['handle_query']).handle_query(query),
        "design":       lambda: __import__('modules.design', fromlist=['handle_query']).handle_query(query),
        "streamer_add": lambda: embed_info("스트리머 등록", "/streamer_add 커맨드를 사용해주세요."),
        "streamer_list": lambda: _streamer_list_embed(),
    }
    handler = dispatch_map.get(module)
    if handler:
        return await handler()
    return None
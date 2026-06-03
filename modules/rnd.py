"""
modules/rnd.py
개쵸 — R&D 총괄.

역할:
1. Q&A: 기술 질문 응답 (기존 기능)
2. 자가 진단: 봇 건강 상태 체크 (/rnd_health)
3. 코드 리뷰: 로그·오류 분석 (/rnd_diagnose)
4. 업데이트 공지: R&D 채널에 업데이트 현황 자동 게시
5. 신규 봇 설계: Claude Opus로 신규 봇 스펙 초안 생성

OpenRouter: standard 티어 (Claude Opus 4.7)
"""

import asyncio
import logging
import os
import platform
import sys
import time
from datetime import datetime
from pathlib import Path

import discord

from utils.openrouter_client import chat

log = logging.getLogger(__name__)

SYSTEM_QA = (
    "당신은 '개쵸'입니다. Python·Discord.py·Notion API·YouTube API·"
    "스트리밍 플랫폼 연동·Railway 배포·OpenRouter에 특화된 시니어 개발자입니다. "
    "Cho의 매니지먼트 봇 시스템 유지보수·신규 기능 개발·신규 봇 생성에 대해 답변합니다. "
    "답변은 다음 형식:\n"
    "1. 요약 (1~2줄)\n"
    "2. 원인/분석\n"
    "3. 구체적 해결 방법 (코드 포함 가능)\n"
    "4. 추가 고려사항"
)

SYSTEM_BOT_DESIGN = (
    "당신은 '개쵸'입니다. 신규 Discord 봇 설계 전문가로서, "
    "Cho가 원하는 봇의 요구사항을 듣고 다음 형식의 설계서를 작성합니다:\n"
    "## 봇 이름·역할\n## 핵심 기능 리스트 (5~10개)\n"
    "## 사용할 기술 스택\n## 예상 OpenRouter 티어\n"
    "## 필요한 외부 API·환경변수\n## 디렉터리 구조\n"
    "## 예상 월 비용\n## 개발 우선순위 (Phase 1~3)\n"
    "한국어로 작성하고, 실행 가능한 수준의 구체적 스펙으로 작성하세요."
)


# ── 1. 기본 Q&A ─────────────────────────────────────────────────────
async def handle_query(query: str) -> discord.Embed:
    """R&D 자연어 질문 처리."""
    try:
        result = await chat(
            messages=[
                {"role": "system", "content": SYSTEM_QA},
                {"role": "user", "content": query},
            ],
            agent="gaechyo",
            max_tokens=1500,
            temperature=0.4,
        )
        embed = discord.Embed(
            title="🔧 개쵸 — R&D",
            description=result["content"][:3500],
            color=0x06B6D4,
        )
        embed.set_footer(
            text=f"{result['model'].split('/')[-1]} · ${result['cost']:.5f}"
        )
        return embed
    except Exception as e:
        from bot.embeds import embed_error
        return embed_error("R&D 오류", str(e))


# ── 2. 봇 건강 상태 체크 ───────────────────────────────────────────
async def run_health_check(bot: discord.Client) -> discord.Embed:
    """
    봇의 현재 상태를 진단.
    - 가동 시간
    - 연결된 서버 수
    - OpenRouter 크레딧
    - 필수 환경변수
    - 최근 로그 에러 횟수 (가능하면)
    """
    from utils.restart_manager import get_uptime, get_start_time
    from utils.openrouter_client import get_remaining_credits

    embed = discord.Embed(
        title="🩺 개쵸 — 봇 건강 진단",
        color=0x06B6D4,
        timestamp=datetime.now(),
    )

    # 기본 정보
    embed.add_field(
        name="⏱️ 가동 시간",
        value=get_uptime(),
        inline=True,
    )
    embed.add_field(
        name="🌐 연결 서버",
        value=f"{len(bot.guilds)}개",
        inline=True,
    )
    embed.add_field(
        name="📡 지연 시간",
        value=f"{bot.latency * 1000:.0f}ms",
        inline=True,
    )

    # 시스템 정보
    embed.add_field(
        name="💻 Python",
        value=platform.python_version(),
        inline=True,
    )
    embed.add_field(
        name="🖥️ 플랫폼",
        value=platform.system(),
        inline=True,
    )

    # OpenRouter 크레딧
    try:
        credits = await get_remaining_credits()
        usage_ratio = credits["usage_ratio"]
        credit_icon = "🟢" if usage_ratio < 0.5 else "🟠" if usage_ratio < 0.9 else "🔴"
        embed.add_field(
            name="💰 OpenRouter",
            value=(
                f"{credit_icon} 사용 {usage_ratio*100:.1f}%\n"
                f"잔여 ${credits['remaining']:.3f}"
            ),
            inline=True,
        )
    except Exception as e:
        embed.add_field(name="💰 OpenRouter", value=f"❌ {e}", inline=True)

    # 필수 환경변수 체크
    required_vars = [
        "DISCORD_TOKEN", "OPENROUTER_API_KEY", "CHO_USER_ID",
        "NOTION_TOKEN", "NOTION_STREAMERS_DB",
    ]
    missing = [v for v in required_vars if not os.getenv(v, "").strip()]
    env_status = "✅ 모두 설정됨" if not missing else f"❌ 누락: {', '.join(missing)}"
    embed.add_field(name="🔑 환경변수", value=env_status, inline=False)

    # 데이터 디렉터리 상태
    data_dir = Path("/data") if Path("/data").exists() else Path("./data")
    data_status = f"✅ `{data_dir}` 사용 가능" if data_dir.exists() and os.access(data_dir, os.W_OK) else f"⚠️ `{data_dir}` 쓰기 불가"
    embed.add_field(name="💾 데이터 저장소", value=data_status, inline=False)

    # 전반적 진단
    has_issue = missing or (credits.get("usage_ratio", 0) >= 0.9 if 'credits' in locals() else False)
    if has_issue:
        embed.color = 0xF97316
        embed.description = "⚠️ **주의 필요** — 아래 항목 확인"
    else:
        embed.description = "✅ **정상 작동 중**"

    embed.set_footer(text=f"개쵸 자가진단 · {get_start_time():%Y-%m-%d %H:%M} 시작")
    return embed


# ── 3. 로그/이슈 진단 ──────────────────────────────────────────────
async def diagnose_issue(issue_description: str) -> discord.Embed:
    """
    사용자가 설명한 이슈를 Claude Opus로 진단.
    예: /rnd_diagnose "/ask 커맨드가 응답이 없음"
    """
    prompt = f"""다음 이슈에 대한 진단과 해결책을 제시해주세요:

**이슈**: {issue_description}

다음 정보를 포함해 답변:
1. 가능한 원인 (상위 3개)
2. 각 원인별 확인 방법
3. 예상 해결 방법
4. 예방 조치

시스템 컨텍스트:
- Python 3.12 / discord.py 2.3.2
- Railway 배포
- OpenRouter 통합 (gpt-5.4-nano, claude-opus-4.7)
- Notion API + APScheduler 사용
"""
    try:
        result = await chat(
            messages=[
                {"role": "system", "content": SYSTEM_QA},
                {"role": "user", "content": prompt},
            ],
            agent="gaechyo",
            max_tokens=1800,
            temperature=0.3,
        )
        embed = discord.Embed(
            title="🔬 개쵸 — 이슈 진단",
            description=result["content"][:3500],
            color=0xF97316,
        )
        embed.add_field(
            name="🎯 이슈",
            value=f"`{issue_description[:200]}`",
            inline=False,
        )
        embed.set_footer(
            text=f"{result['model'].split('/')[-1]} · ${result['cost']:.5f}"
        )
        return embed
    except Exception as e:
        from bot.embeds import embed_error
        return embed_error("진단 실패", str(e))


# ── 4. 신규 봇 설계 ─────────────────────────────────────────────────
async def design_new_bot(requirements: str) -> discord.Embed:
    """
    신규 봇 요구사항 → Claude Opus가 설계서 작성.
    결과는 R&D 채널에도 자동 게시 (옵션).
    """
    try:
        result = await chat(
            messages=[
                {"role": "system", "content": SYSTEM_BOT_DESIGN},
                {"role": "user", "content": f"봇 요구사항:\n{requirements}"},
            ],
            agent="gaechyo",
            tier="premium",   # 설계는 premium 사용
            max_tokens=3000,
            temperature=0.6,
        )
        embed = discord.Embed(
            title="📐 개쵸 — 신규 봇 설계서",
            description=result["content"][:3500],
            color=0x8B5CF6,
        )
        embed.add_field(
            name="🎯 요구사항",
            value=f"```\n{requirements[:800]}\n```",
            inline=False,
        )
        embed.set_footer(
            text=f"{result['model'].split('/')[-1]} · ${result['cost']:.5f} · "
                 f"설계서 (실제 구현은 Phase별 별도 진행)"
        )
        return embed
    except Exception as e:
        from bot.embeds import embed_error
        return embed_error("설계 실패", str(e))


# ── 5. R&D 채널 공지 ───────────────────────────────────────────────
async def post_to_rnd_channel(
    bot: discord.Client,
    *,
    category: str,       # "update" | "maintenance" | "feature" | "issue"
    title: str,
    content: str,
    author: str = "개쵸",
) -> bool:
    """
    R&D 개발 채널에 자동 공지.
    RND_CHANNEL_ID 환경변수 필요.
    """
    ch_id = os.getenv("RND_CHANNEL_ID", "").strip()
    if not ch_id.isdigit():
        log.info("RND_CHANNEL_ID 미설정 — R&D 채널 공지 생략")
        return False

    channel = bot.get_channel(int(ch_id))
    if not channel:
        log.warning(f"RND 채널 찾을 수 없음: {ch_id}")
        return False

    CATEGORY_META = {
        "update":      ("🚀", "업데이트",       0x3B82F6),
        "maintenance": ("🔧", "유지보수",       0x10B981),
        "feature":     ("✨", "신규 기능",     0x8B5CF6),
        "issue":       ("⚠️", "이슈/장애",    0xF97316),
        "health":      ("🩺", "건강 체크",     0x06B6D4),
        "design":      ("📐", "봇 설계서",     0xEC4899),
    }
    icon, label, color = CATEGORY_META.get(category, ("📝", category, 0x6B7280))

    embed = discord.Embed(
        title=f"{icon} [{label}] {title}",
        description=content[:4000],
        color=color,
        timestamp=datetime.now(),
    )
    embed.set_footer(text=f"개쵸 R&D · {author}")

    try:
        # 포럼이면 스레드 생성, 일반 채널이면 메시지 전송
        if isinstance(channel, discord.ForumChannel):
            await channel.create_thread(
                name=f"[{label}] {title}"[:100],
                embed=embed,
            )
        else:
            await channel.send(embed=embed)
        log.info(f"R&D 채널 공지 완료: [{category}] {title}")
        return True
    except Exception as e:
        log.error(f"R&D 채널 공지 실패: {e}")
        return False


# ── 6. 정기 건강 리포트 (매일 08:00) ─────────────────────────────
async def daily_health_report(bot: discord.Client) -> None:
    """매일 08시 봇 상태를 R&D 채널에 공지."""
    try:
        embed = await run_health_check(bot)
        # 이미 만들어진 embed를 바로 전달
        ch_id = os.getenv("RND_CHANNEL_ID", "").strip()
        if not ch_id.isdigit():
            return
        channel = bot.get_channel(int(ch_id))
        if not channel:
            return

        if isinstance(channel, discord.ForumChannel):
            await channel.create_thread(
                name=f"[일일 건강 체크] {datetime.now():%Y-%m-%d}",
                embed=embed,
            )
        else:
            await channel.send(embed=embed)
        log.info("개쵸 일일 건강 리포트 발송")
    except Exception as e:
        log.error(f"일일 건강 리포트 실패: {e}")


# ── 7. 업데이트 이벤트 훅 ───────────────────────────────────────────
async def notify_update(
    bot: discord.Client,
    *,
    version: str = "",
    changes: list[str] = None,
) -> None:
    """
    배포/업데이트 시 R&D 채널 자동 공지.
    bot/main.py on_ready에서 호출 가능.
    """
    changes = changes or []
    content = f"**버전**: {version or 'N/A'}\n\n**변경 사항**:\n"
    content += "\n".join(f"• {c}" for c in changes) if changes else "(상세 내역 없음)"

    await post_to_rnd_channel(
        bot,
        category="update",
        title=f"봇 재배포 완료 {version}".strip(),
        content=content,
    )
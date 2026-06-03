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
from typing import Any

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
def _check_result(name: str, ok: bool, detail: str) -> dict[str, Any]:
    """표준 체크 항목 결과 dict 생성."""
    return {"name": name, "ok": bool(ok), "detail": str(detail)}


async def _safe_check(name: str, coro_or_func, *args, **kwargs) -> dict[str, Any]:
    """
    개별 체크를 안전하게 실행.
    예외 발생 시에도 표준 결과를 반환하여 전체 리포트가 깨지지 않도록 함.
    """
    try:
        if asyncio.iscoroutinefunction(coro_or_func):
            value = await coro_or_func(*args, **kwargs)
        else:
            value = coro_or_func(*args, **kwargs)
        return _check_result(name, True, str(value) if value is not None else "OK")
    except Exception as e:
        log.exception(f"[health] {name} 체크 실패: {e}")
        return _check_result(name, False, f"{type(e).__name__}: {e}")


async def run_health_check(bot: discord.Client) -> discord.Embed:
    """
    봇의 현재 상태를 진단.
    - 가동 시간
    - 연결된 서버 수
    - OpenRouter 크레딧
    - 필수 환경변수
    - 데이터 디렉터리 상태

    개별 체크 실패 시에도 전체 리포트는 정상적으로 반환된다.
    """
    embed = discord.Embed(
        title="🩺 개쵸 — 봇 건강 진단",
        color=0x06B6D4,
        timestamp=datetime.now(),
    )

    checks: list[dict[str, Any]] = []  # 표준 결과 누적용 (요약 판정에 사용)

    # ── 가동 시간 / 시작 시각 ──────────────────────────────────
    uptime_str = "N/A"
    start_time_str = "N/A"
    try:
        from utils.restart_manager import get_uptime, get_start_time
        try:
            uptime_str = get_uptime() or "N/A"
            checks.append(_check_result("uptime", True, uptime_str))
        except Exception as e:
            log.exception(f"[health] uptime 조회 실패: {e}")
            checks.append(_check_result("uptime", False, str(e)))

        try:
            st = get_start_time()
            if st is not None:
                start_time_str = f"{st:%Y-%m-%d %H:%M}"
        except Exception as e:
            log.exception(f"[health] start_time 조회 실패: {e}")
    except Exception as e:
        log.exception(f"[health] restart_manager import 실패: {e}")
        checks.append(_check_result("uptime", False, f"import 실패: {e}"))

    embed.add_field(name="⏱️ 가동 시간", value=uptime_str, inline=True)

    # ── 연결된 서버 ────────────────────────────────────────────
    try:
        guild_count = len(bot.guilds) if bot and getattr(bot, "guilds", None) is not None else 0
        embed.add_field(name="🌐 연결 서버", value=f"{guild_count}개", inline=True)
        checks.append(_check_result("guilds", True, f"{guild_count}개"))
    except Exception as e:
        log.exception(f"[health] 서버 수 조회 실패: {e}")
        embed.add_field(name="🌐 연결 서버", value="❌ 오류", inline=True)
        checks.append(_check_result("guilds", False, str(e)))

    # ── 지연 시간 ───────────────────────────────────────────────
    try:
        latency = getattr(bot, "latency", None)
        if latency is None or latency != latency:  # NaN 체크
            raise ValueError("latency 미정의")
        embed.add_field(name="📡 지연 시간", value=f"{latency * 1000:.0f}ms", inline=True)
        checks.append(_check_result("latency", True, f"{latency * 1000:.0f}ms"))
    except Exception as e:
        log.warning(f"[health] latency 조회 실패: {e}")
        embed.add_field(name="📡 지연 시간", value="N/A", inline=True)
        checks.append(_check_result("latency", False, str(e)))

    # ── 시스템 정보 ────────────────────────────────────────────
    try:
        embed.add_field(name="💻 Python", value=platform.python_version(), inline=True)
        embed.add_field(name="🖥️ 플랫폼", value=platform.system(), inline=True)
    except Exception as e:
        log.warning(f"[health] platform 정보 조회 실패: {e}")

    # ── OpenRouter 크레딧 ──────────────────────────────────────
    credits: dict[str, Any] = {}
    try:
        from utils.openrouter_client import get_remaining_credits
        credits = await get_remaining_credits() or {}
        usage_ratio = float(credits.get("usage_ratio", 0) or 0)
        remaining = float(credits.get("remaining", 0) or 0)
        credit_icon = "🟢" if usage_ratio < 0.5 else "🟠" if usage_ratio < 0.9 else "🔴"
        embed.add_field(
            name="💰 OpenRouter",
            value=(
                f"{credit_icon} 사용 {usage_ratio * 100:.1f}%\n"
                f"잔여 ${remaining:.3f}"
            ),
            inline=True,
        )
        checks.append(_check_result(
            "openrouter",
            usage_ratio < 0.9,
            f"usage {usage_ratio*100:.1f}% / remaining ${remaining:.3f}",
        ))
    except Exception as e:
        log.exception(f"[health] OpenRouter 크레딧 조회 실패: {e}")
        embed.add_field(name="💰 OpenRouter", value=f"❌ {type(e).__name__}", inline=True)
        checks.append(_check_result("openrouter", False, str(e)))

    # ── 필수 환경변수 체크 ─────────────────────────────────────
    missing: list[str] = []
    try:
        required_vars = [
            "DISCORD_TOKEN", "OPENROUTER_API_KEY", "CHO_USER_ID",
            "NOTION_TOKEN", "NOTION_STREAMERS_DB",
        ]
        missing = [v for v in required_vars if not os.getenv(v, "").strip()]
        env_status = "✅ 모두 설정됨" if not missing else f"❌ 누락: {', '.join(missing)}"
        embed.add_field(name="🔑 환경변수", value=env_status, inline=False)
        checks.append(_check_result(
            "env_vars",
            not missing,
            "OK" if not missing else f"missing: {', '.join(missing)}",
        ))
    except Exception as e:
        log.exception(f"[health] 환경변수 체크 실패: {e}")
        embed.add_field(name="🔑 환경변수", value=f"❌ {e}", inline=False)
        checks.append(_check_result("env_vars", False, str(e)))

    # ── 데이터 디렉터리 상태 ──────────────────────────────────
    try:
        data_dir = Path("/data") if Path("/data").exists() else Path("./data")
        writable = data_dir.exists() and os.access(data_dir, os.W_OK)
        data_status = (
            f"✅ `{data_dir}` 사용 가능"
            if writable
            else f"⚠️ `{data_dir}` 쓰기 불가"
        )
        embed.add_field(name="💾 데이터 저장소", value=data_status, inline=False)
        checks.append(_check_result("data_dir", writable, str(data_dir)))
    except Exception as e:
        log.exception(f"[health] 데이터 디렉터리 체크 실패: {e}")
        embed.add_field(name="💾 데이터 저장소", value=f"❌ {e}", inline=False)
        checks.append(_check_result("data_dir", False, str(e)))

    # ── 전반적 진단 ────────────────────────────────────────────
    try:
        usage_ratio = float(credits.get("usage_ratio", 0) or 0) if credits else 0.0
        has_issue = bool(missing) or (usage_ratio >= 0.9) or any(not c["ok"] for c in checks)
        if has_issue:
            embed.color = 0xF97316
            failed = [c["name"] for c in checks if not c["ok"]]
            extra = f" (실패: {', '.join(failed)})" if failed else ""
            embed.description = f"⚠️ **주의 필요** — 아래 항목 확인{extra}"
        else:
            embed.description = "✅ **정상 작동 중**"
    except Exception as e:
        log.exception(f"[health] 종합 진단 실패: {e}")
        embed.description = "⚠️ 진단 중 일부 오류 발생"

    embed.set_footer(text=f"개쵸 자가진단 · {start_time_str} 시작")
    log.info(f"[health] 체크 완료 — {sum(1 for c in checks if c['ok'])}/{len(checks)} 정상")
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
            value=f"
"""
modules/fixed_costs.py
인쵸 보조 — 고정비 납부 일정 관리.
Notion DB 또는 JSON 파일로 납부일·금액·서비스 관리.
매일 오전 9시 D-3 이내 납부 예정 알림.
"""

import logging
import os
from datetime import date, datetime, timedelta

import discord

from utils.json_store import store_path, read_json, write_json

log = logging.getLogger(__name__)

FIXED_COSTS_FILE = store_path("fixed_costs.json")


# ── 데이터 모델 ─────────────────────────────────────────────────────
def _load() -> list[dict]:
    """
    구조: [
      {"name": "Railway Hobby", "amount_krw": 6500, "pay_day": 15, "last_paid": "2026-04-15"},
      {"name": "Claude Code Max", "amount_krw": 150000, "pay_day": 1, "last_paid": "2026-05-01"},
    ]
    """
    return read_json(FIXED_COSTS_FILE, _default_costs)


def _save(data: list[dict]) -> None:
    write_json(FIXED_COSTS_FILE, data)


def _default_costs() -> list[dict]:
    """초기 기본값."""
    return [
        {"name": "Railway Hobby",    "amount_krw": 6500,   "pay_day": 15, "last_paid": ""},
        {"name": "Claude Code Max",  "amount_krw": 150000, "pay_day": 1,  "last_paid": ""},
        {"name": "ChatGPT Plus",     "amount_krw": 30000,  "pay_day": 1,  "last_paid": ""},
    ]


def _notion_enabled() -> bool:
    return bool(os.getenv("NOTION_TOKEN") and os.getenv("NOTION_FIXED_COSTS_DB"))


# ── 외부 모듈용 공개 접근자 (money.py 등) ────────────────────────────
def get_costs() -> list[dict]:
    """전체 고정비 목록."""
    return _load()


def get_total_monthly_krw() -> int:
    """이번 달 고정비 합계."""
    return sum(c["amount_krw"] for c in _load())


# ── Embed 생성 ─────────────────────────────────────────────────────
async def list_fixed_costs() -> discord.Embed:
    """고정비 목록 + 다음 납부일 표시."""
    costs = _load()
    today = date.today()

    embed = discord.Embed(
        title="💳 인쵸 — 고정비 납부 일정",
        color=0x059669,
    )

    total = 0
    lines = []
    for c in costs:
        next_pay = _next_payment_date(c["pay_day"], today)
        d_day = (next_pay - today).days
        d_str = "**오늘**" if d_day == 0 else f"D-{d_day}"

        # 긴급도 아이콘
        if d_day <= 3:
            icon = "🔴"
        elif d_day <= 7:
            icon = "🟠"
        else:
            icon = "🟢"

        lines.append(
            f"{icon} **{c['name']}**\n"
            f"   ₩{c['amount_krw']:,} · {next_pay:%Y-%m-%d} ({d_str})"
        )
        total += c["amount_krw"]

    embed.description = "\n\n".join(lines) if lines else "등록된 고정비 없음"
    embed.add_field(
        name="💰 월 합계",
        value=f"**₩{total:,}**",
        inline=False,
    )
    embed.set_footer(text="/fixedcost_add · /fixedcost_remove · /fixedcost_paid · /fixedcost_sync")
    return embed


def _next_payment_date(pay_day: int, today: date) -> date:
    """이번 달 pay_day가 오늘 이후면 이번 달, 아니면 다음 달."""
    # 말일 예외 처리 (2월 30일 등)
    def _safe_date(y, m, d):
        import calendar
        last = calendar.monthrange(y, m)[1]
        return date(y, m, min(d, last))

    this_month = _safe_date(today.year, today.month, pay_day)
    if this_month >= today:
        return this_month
    # 다음 달
    if today.month == 12:
        return _safe_date(today.year + 1, 1, pay_day)
    return _safe_date(today.year, today.month + 1, pay_day)


# ── CRUD (로컬 JSON이 항상 우선 반영되고, Notion 연동 시 best-effort로 미러링) ──
async def add_cost(name: str, amount_krw: int, pay_day: int) -> str:
    costs = _load()
    # 중복 체크
    if any(c["name"] == name for c in costs):
        return f"⚠️ '{name}' 이미 등록됨"
    costs.append({
        "name": name,
        "amount_krw": amount_krw,
        "pay_day": pay_day,
        "last_paid": "",
    })
    _save(costs)

    if _notion_enabled():
        try:
            from utils.notion_client import add_fixed_cost
            await add_fixed_cost(name, amount_krw, pay_day)
        except Exception as e:
            log.warning(f"Notion 고정비 등록 동기화 실패: {e}")

    return f"✅ '{name}' 등록: ₩{amount_krw:,} / 매월 {pay_day}일"


async def remove_cost(name: str) -> str:
    costs = _load()
    new_costs = [c for c in costs if c["name"] != name]
    if len(new_costs) == len(costs):
        return f"⚠️ '{name}' 찾을 수 없음"
    _save(new_costs)

    if _notion_enabled():
        try:
            from utils.notion_client import archive_fixed_cost
            await archive_fixed_cost(name)
        except Exception as e:
            log.warning(f"Notion 고정비 삭제 동기화 실패: {e}")

    return f"✅ '{name}' 삭제됨"


async def mark_paid(name: str) -> str:
    costs = _load()
    for c in costs:
        if c["name"] == name:
            paid_date = date.today().isoformat()
            c["last_paid"] = paid_date
            _save(costs)

            if _notion_enabled():
                try:
                    from utils.notion_client import mark_fixed_cost_paid
                    await mark_fixed_cost_paid(name, paid_date)
                except Exception as e:
                    log.warning(f"Notion 고정비 납부 동기화 실패: {e}")

            return f"✅ '{name}' 납부 완료 기록 ({paid_date})"
    return f"⚠️ '{name}' 찾을 수 없음"


async def sync_from_notion() -> str:
    """Notion DB 내용을 로컬 캐시로 가져옴 (Notion에서 직접 수정한 내용 반영)."""
    if not _notion_enabled():
        return "⚠️ NOTION_FIXED_COSTS_DB 미설정 — 로컬 데이터만 사용 중"
    from utils.notion_client import list_fixed_costs as notion_list
    try:
        remote = await notion_list()
    except Exception as e:
        return f"❌ Notion 동기화 실패: {e}"

    costs = [
        {
            "name": c["name"],
            "amount_krw": c["amount_krw"],
            "pay_day": c["pay_day"],
            "last_paid": c["last_paid"],
        }
        for c in remote
    ]
    _save(costs)
    return f"✅ Notion → 로컬 동기화 완료 ({len(costs)}건)"


# ── 알림 (매일 9시) ─────────────────────────────────────────────────
async def check_upcoming_payments(bot: discord.Client) -> None:
    """D-3 이내 납부 예정 항목을 Cho에게 DM."""
    costs = _load()
    today = date.today()
    cho_id = int(os.getenv("CHO_USER_ID", "0"))
    if not cho_id:
        return

    upcoming = []
    for c in costs:
        next_pay = _next_payment_date(c["pay_day"], today)
        d_day = (next_pay - today).days
        if 0 <= d_day <= 3:
            upcoming.append((c, d_day))

    if not upcoming:
        return

    try:
        user = await bot.fetch_user(cho_id)
        embed = discord.Embed(
            title="🔔 인쵸 — 고정비 납부 알림",
            description="다음 3일 이내 납부 예정:",
            color=0xF97316,
        )
        for c, d_day in upcoming:
            d_str = "오늘" if d_day == 0 else f"{d_day}일 후"
            embed.add_field(
                name=f"• {c['name']}",
                value=f"₩{c['amount_krw']:,} · {d_str}",
                inline=False,
            )
        await user.send(embed=embed)
        log.info(f"고정비 납부 알림 발송: {len(upcoming)}건")
    except Exception as e:
        log.error(f"고정비 알림 실패: {e}")
"""
modules/schedule.py
스쵸 — Notion 캘린더 조회 + 등록/수정/삭제.
LLM 호출 없음 (단순 DB 조회/쓰기).
"""

import logging
from datetime import datetime, timedelta

import discord

log = logging.getLogger(__name__)


# ── 조회 (기존 기능) ────────────────────────────────────────────────
async def handle_schedule(query: str = "") -> discord.Embed:
    """이번 주 스케줄 조회."""
    from utils.notion_client import get_client, _db

    today = datetime.now()
    start = today - timedelta(days=today.weekday())
    end = start + timedelta(days=6)

    try:
        client = get_client()
        res = await client.databases.query(
            database_id=_db("SCHEDULE"),
            filter={
                "and": [
                    {"property": "날짜", "date": {"on_or_after": start.date().isoformat()}},
                    {"property": "날짜", "date": {"on_or_before": end.date().isoformat()}},
                ]
            },
            sorts=[{"property": "날짜", "direction": "ascending"}],
        )
        items = res.get("results", [])
    except Exception as e:
        log.error(f"스케줄 조회 오류: {e}")
        items = []

    if not items:
        return discord.Embed(
            title="📅 스쵸 — 이번 주 스케줄",
            description="등록된 일정 없음\n\n`/schedule_add`로 일정을 등록해주세요.",
            color=0x0EA5E9,
        )

    lines = []
    for item in items:
        props = item["properties"]
        title_obj = props.get("제목", {}).get("title", [])
        title = title_obj[0]["text"]["content"] if title_obj else "?"
        date_obj = props.get("날짜", {}).get("date", {})
        date_str = date_obj.get("start", "?") if date_obj else "?"
        # 페이지 ID 축약 표시 (삭제 시 사용)
        short_id = item["id"].replace("-", "")[:8]
        lines.append(f"• `{short_id}` {date_str} — {title}")

    return discord.Embed(
        title=f"📅 스쵸 — 이번 주 스케줄 ({start.strftime('%m/%d')} ~ {end.strftime('%m/%d')})",
        description="\n".join(lines),
        color=0x0EA5E9,
    )


# ── 등록 ───────────────────────────────────────────────────────────
async def add_schedule(title: str, date_str: str, memo: str = "") -> discord.Embed:
    """
    스케줄 등록.
    date_str: "2026-05-15" 또는 "2026-05-15 14:00" 형식
    """
    from utils.notion_client import get_client, _db

    # 날짜 파싱
    try:
        if len(date_str) > 10:
            dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M")
            notion_date = dt.isoformat()
        else:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            notion_date = dt.date().isoformat()
    except ValueError:
        return discord.Embed(
            title="❌ 스쵸 — 등록 실패",
            description=f"날짜 형식 오류: `{date_str}`\n"
                        f"예: `2026-05-15` 또는 `2026-05-15 14:00`",
            color=0xE11D48,
        )

    try:
        client = get_client()
        page = await client.pages.create(
            parent={"database_id": _db("SCHEDULE")},
            properties={
                "제목": {"title": [{"text": {"content": title}}]},
                "날짜": {"date": {"start": notion_date}},
            },
            children=[{
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": [{"text": {"content": memo}}]},
            }] if memo else [],
        )
        log.info(f"스케줄 등록: {title} / {notion_date}")
        return discord.Embed(
            title="✅ 스쵸 — 스케줄 등록 완료",
            description=(
                f"**제목**: {title}\n"
                f"**날짜**: {notion_date}\n"
                f"**메모**: {memo or '(없음)'}"
            ),
            color=0x059669,
        )
    except Exception as e:
        log.error(f"스케줄 등록 오류: {e}")
        return discord.Embed(
            title="❌ 스쵸 — 등록 실패",
            description=str(e),
            color=0xE11D48,
        )


# ── 수정 ───────────────────────────────────────────────────────────
async def update_schedule(short_id: str, title: str = "", date_str: str = "") -> discord.Embed:
    """
    스케줄 수정.
    short_id: 조회 시 표시된 8자리 ID
    """
    from utils.notion_client import get_client, _db

    try:
        client = get_client()
        # short_id로 페이지 찾기
        page_id = await _find_page_by_short_id(client, short_id)
        if not page_id:
            return discord.Embed(
                title="❌ 스쵸 — 수정 실패",
                description=f"ID `{short_id}` 일정을 찾을 수 없습니다.",
                color=0xE11D48,
            )

        # 수정할 속성만 업데이트
        props = {}
        if title:
            props["제목"] = {"title": [{"text": {"content": title}}]}
        if date_str:
            try:
                if len(date_str) > 10:
                    dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M")
                    notion_date = dt.isoformat()
                else:
                    dt = datetime.strptime(date_str, "%Y-%m-%d")
                    notion_date = dt.date().isoformat()
                props["날짜"] = {"date": {"start": notion_date}}
            except ValueError:
                return discord.Embed(
                    title="❌ 스쵸 — 수정 실패",
                    description=f"날짜 형식 오류: `{date_str}`",
                    color=0xE11D48,
                )

        if not props:
            return discord.Embed(
                title="⚠️ 스쵸 — 수정할 내용 없음",
                description="제목이나 날짜 중 하나 이상 입력해주세요.",
                color=0xEAB308,
            )

        await client.pages.update(page_id=page_id, properties=props)
        log.info(f"스케줄 수정: {short_id}")
        return discord.Embed(
            title="✅ 스쵸 — 스케줄 수정 완료",
            description=f"ID `{short_id}` 업데이트됨",
            color=0x059669,
        )
    except Exception as e:
        log.error(f"스케줄 수정 오류: {e}")
        return discord.Embed(
            title="❌ 스쵸 — 수정 실패",
            description=str(e),
            color=0xE11D48,
        )


# ── 삭제 (아카이브) ────────────────────────────────────────────────
async def delete_schedule(short_id: str) -> discord.Embed:
    """스케줄 삭제 (Notion은 실제로 archive)."""
    from utils.notion_client import get_client, _db

    try:
        client = get_client()
        page_id = await _find_page_by_short_id(client, short_id)
        if not page_id:
            return discord.Embed(
                title="❌ 스쵸 — 삭제 실패",
                description=f"ID `{short_id}` 일정을 찾을 수 없습니다.",
                color=0xE11D48,
            )
        await client.pages.update(page_id=page_id, archived=True)
        log.info(f"스케줄 삭제(archive): {short_id}")
        return discord.Embed(
            title="✅ 스쵸 — 스케줄 삭제 완료",
            description=f"ID `{short_id}` 아카이브됨",
            color=0x059669,
        )
    except Exception as e:
        return discord.Embed(
            title="❌ 스쵸 — 삭제 실패",
            description=str(e),
            color=0xE11D48,
        )


async def _find_page_by_short_id(client, short_id: str) -> str | None:
    """이번 주 스케줄 중 short_id와 일치하는 페이지 ID 반환."""
    from utils.notion_client import _db
    today = datetime.now()
    start = today - timedelta(days=today.weekday())
    end = start + timedelta(days=30)  # 앞으로 1달까지 검색

    res = await client.databases.query(
        database_id=_db("SCHEDULE"),
        filter={
            "and": [
                {"property": "날짜", "date": {"on_or_after": start.date().isoformat()}},
                {"property": "날짜", "date": {"on_or_before": end.date().isoformat()}},
            ]
        },
    )
    for item in res.get("results", []):
        if item["id"].replace("-", "")[:8] == short_id:
            return item["id"]
    return None
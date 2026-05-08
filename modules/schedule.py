"""
modules/schedule.py
스케줄 관리 — Notion 캘린더 연동.
Gemini Flash-Lite 사용 (최저 비용).
"""

import asyncio
import logging
import os

import discord
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
_model = genai.GenerativeModel("gemini-2.0-flash-exp")   # Flash-Lite 대용


async def handle_schedule(query: str) -> discord.Embed:
    """스케줄 조회 또는 등록."""
    from utils.notion_client import get_client, _db
    from datetime import datetime, timedelta

    # 현재 주 날짜 범위 계산
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
        embed = discord.Embed(
            title="📅 이번 주 스케줄",
            description="등록된 일정 없음",
            color=0x0EA5E9,
        )
        return embed

    lines = []
    for item in items:
        props = item["properties"]
        title_obj = props.get("제목", {}).get("title", [])
        title = title_obj[0]["text"]["content"] if title_obj else "?"
        date_obj = props.get("날짜", {}).get("date", {})
        date = date_obj.get("start", "?") if date_obj else "?"
        lines.append(f"• {date} — {title}")

    embed = discord.Embed(
        title=f"📅 이번 주 스케줄 ({start.strftime('%m/%d')} ~ {end.strftime('%m/%d')})",
        description="\n".join(lines),
        color=0x0EA5E9,
    )
    return embed

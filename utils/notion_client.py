"""
utils/notion_client.py
Notion API 읽기·쓰기 공통 레이어.
모든 모듈이 이 파일을 통해 Notion과 통신합니다.
"""

import logging
import os
from datetime import datetime

from notion_client import AsyncClient
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)

_client = None


def get_client() -> AsyncClient:
    global _client
    if _client is None:
        token = os.getenv("NOTION_TOKEN")
        if not token:
            raise ValueError(".env에 NOTION_TOKEN이 없습니다")
        _client = AsyncClient(auth=token)
    return _client


# ── DB ID ────────────────────────────────────────────────────────────
def _db(name: str) -> str:
    key = f"NOTION_{name.upper()}_DB"
    val = os.getenv(key)
    if not val:
        raise ValueError(f".env에 {key}가 없습니다")
    return val


# ── 스트리머 관리 ────────────────────────────────────────────────────
async def register_streamer(
    name: str,
    chzzk_url: str = "",
    youtube_url: str = "",
    soop_url: str = "",
) -> dict:
    """스트리머 프로필을 Notion DB에 등록."""
    client = get_client()
    page = await client.pages.create(
        parent={"database_id": _db("STREAMERS")},
        properties={
            "이름": {"title": [{"text": {"content": name}}]},
            "치지직 URL": {"url": chzzk_url or None},
            "유튜브 URL": {"url": youtube_url or None},
            "SOOP URL":   {"url": soop_url or None},
            "등록일": {"date": {"start": datetime.now().isoformat()}},
            "활성": {"checkbox": True},
        },
    )
    log.info(f"스트리머 등록: {name} (page_id: {page['id']})")
    return page


async def list_streamers() -> list[dict]:
    """활성 스트리머 목록 반환."""
    client = get_client()
    res = await client.databases.query(
        database_id=_db("STREAMERS"),
        filter={"property": "활성", "checkbox": {"equals": True}},
    )
    streamers = []
    for page in res.get("results", []):
        props = page["properties"]
        name_obj = props.get("이름", {}).get("title", [])
        name = name_obj[0]["text"]["content"] if name_obj else "?"
        streamers.append({
            "id": page["id"],
            "name": name,
            "chzzk_url": (props.get("치지직 URL") or {}).get("url", ""),
            "youtube_url": (props.get("유튜브 URL") or {}).get("url", ""),
            "soop_url": (props.get("SOOP URL") or {}).get("url", ""),
        })
    return streamers


async def get_streamer(name: str) -> dict | None:
    """이름으로 스트리머 조회."""
    streamers = await list_streamers()
    for s in streamers:
        if s["name"] == name:
            return s
    return None


# ── 방송 로그 ────────────────────────────────────────────────────────
async def save_broadcast_log(
    streamer_name: str,
    platform: str,
    viewers_peak: int,
    viewers_avg: int,
    chat_count: int,
    sentiment_positive: float,
    keywords: list[str],
    summary: str,
) -> dict:
    """방송 로그를 Notion에 저장."""
    client = get_client()
    page = await client.pages.create(
        parent={"database_id": _db("BROADCAST_LOG")},
        properties={
            "스트리머": {"title": [{"text": {"content": streamer_name}}]},
            "플랫폼": {"select": {"name": platform}},
            "날짜": {"date": {"start": datetime.now().date().isoformat()}},
            "최고 시청자": {"number": viewers_peak},
            "평균 시청자": {"number": viewers_avg},
            "채팅 수": {"number": chat_count},
            "긍정 감정(%)": {"number": round(sentiment_positive * 100, 1)},
            "키워드": {"multi_select": [{"name": k} for k in keywords[:5]]},
        },
        children=[{
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"text": {"content": summary}}]},
        }],
    )
    log.info(f"방송 로그 저장: {streamer_name} / {platform}")
    return page


async def get_broadcast_logs(streamer_name: str, days: int = 7) -> list[dict]:
    """최근 N일간 방송 로그 조회."""
    from datetime import timedelta
    client = get_client()
    since = (datetime.now() - timedelta(days=days)).date().isoformat()
    res = await client.databases.query(
        database_id=_db("BROADCAST_LOG"),
        filter={
            "and": [
                {"property": "스트리머", "title": {"equals": streamer_name}},
                {"property": "날짜", "date": {"on_or_after": since}},
            ]
        },
        sorts=[{"property": "날짜", "direction": "descending"}],
    )
    logs = []
    for page in res.get("results", []):
        p = page["properties"]
        logs.append({
            "date":      (p.get("날짜") or {}).get("date", {}).get("start", ""),
            "platform":  (p.get("플랫폼") or {}).get("select", {}).get("name", ""),
            "peak":      (p.get("최고 시청자") or {}).get("number", 0),
            "avg":       (p.get("평균 시청자") or {}).get("number", 0),
            "chats":     (p.get("채팅 수") or {}).get("number", 0),
            "sentiment": (p.get("긍정 감정(%)") or {}).get("number", 0),
        })
    return logs


# ── 리포트 저장 ───────────────────────────────────────────────────────
async def save_report(streamer_name: str, period: str, content: str) -> dict:
    """주간 리포트를 Notion에 저장."""
    client = get_client()
    page = await client.pages.create(
        parent={"database_id": _db("REPORT")},
        properties={
            "제목": {"title": [{"text": {"content": f"{streamer_name} — {period} 주간 리포트"}}]},
            "스트리머": {"rich_text": [{"text": {"content": streamer_name}}]},
            "생성일": {"date": {"start": datetime.now().isoformat()}},
        },
        children=[{
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"text": {"content": content}}]},
        }],
    )
    log.info(f"리포트 저장: {streamer_name} {period}")
    return page

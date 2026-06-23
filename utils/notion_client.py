"""
utils/notion_client.py
Notion API 읽기·쓰기 공통 레이어.
모든 모듈이 이 파일을 통해 Notion과 통신합니다.
"""

import json
import logging
import os
import uuid
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


# ── 고정비 관리 ──────────────────────────────────────────────────────
async def list_fixed_costs() -> list[dict]:
    """활성 고정비 목록 (Notion DB)."""
    client = get_client()
    res = await client.databases.query(
        database_id=_db("FIXED_COSTS"),
        filter={"property": "활성", "checkbox": {"equals": True}},
    )
    costs = []
    for page in res.get("results", []):
        p = page["properties"]
        name_obj = p.get("이름", {}).get("title", [])
        name = name_obj[0]["text"]["content"] if name_obj else "?"
        last_paid = (p.get("마지막 납부일") or {}).get("date") or {}
        costs.append({
            "id": page["id"],
            "name": name,
            "amount_krw": int((p.get("금액") or {}).get("number") or 0),
            "pay_day": int((p.get("납부일") or {}).get("number") or 1),
            "last_paid": last_paid.get("start") or "",
        })
    return costs


async def add_fixed_cost(name: str, amount_krw: int, pay_day: int) -> dict:
    """고정비를 Notion DB에 등록."""
    client = get_client()
    page = await client.pages.create(
        parent={"database_id": _db("FIXED_COSTS")},
        properties={
            "이름": {"title": [{"text": {"content": name}}]},
            "금액": {"number": amount_krw},
            "납부일": {"number": pay_day},
            "활성": {"checkbox": True},
        },
    )
    log.info(f"고정비 등록(Notion): {name} / ₩{amount_krw:,} / 매월 {pay_day}일")
    return page


async def archive_fixed_cost(name: str) -> bool:
    """고정비 페이지를 archive 처리 (이름으로 검색)."""
    client = get_client()
    costs = await list_fixed_costs()
    match = next((c for c in costs if c["name"] == name), None)
    if not match:
        return False
    await client.pages.update(page_id=match["id"], archived=True)
    log.info(f"고정비 삭제(Notion archive): {name}")
    return True


async def mark_fixed_cost_paid(name: str, paid_date: str) -> bool:
    """고정비 페이지의 마지막 납부일을 업데이트 (이름으로 검색)."""
    client = get_client()
    costs = await list_fixed_costs()
    match = next((c for c in costs if c["name"] == name), None)
    if not match:
        return False
    await client.pages.update(
        page_id=match["id"],
        properties={"마지막 납부일": {"date": {"start": paid_date}}},
    )
    log.info(f"고정비 납부 기록(Notion): {name} / {paid_date}")
    return True


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


# ── 기쵸 러닝 ────────────────────────────────────────────────────────
def _rich_text(content: str) -> dict:
    return {"rich_text": [{"text": {"content": content}}]} if content else {"rich_text": []}


def _text_of(prop: dict | None) -> str:
    arr = (prop or {}).get("rich_text", [])
    return "".join(t["text"]["content"] for t in arr)


def _select_of(prop: dict | None) -> str:
    return ((prop or {}).get("select") or {}).get("name", "")


def _date_of(prop: dict | None) -> str:
    return ((prop or {}).get("date") or {}).get("start", "")


def _parse_learning_page(page: dict) -> dict:
    p = page["properties"]
    title_obj = (p.get("주제") or {}).get("title", [])
    subject = title_obj[0]["text"]["content"] if title_obj else "?"
    return {
        "id": _text_of(p.get("ID")),
        "page_id": page["id"],
        "subject": subject,
        "category": _select_of(p.get("카테고리")),
        "status": _select_of(p.get("상태")),
        "sources": json.loads(_text_of(p.get("소스")) or "[]"),
        "requested_by": _text_of(p.get("요청자")),
        "requested_at": _date_of(p.get("요청일")),
        "approved_at": _date_of(p.get("승인일")),
        "completed_at": _date_of(p.get("완료일")),
        "summary": _text_of(p.get("요약")),
        "insights": json.loads(_text_of(p.get("인사이트")) or "[]"),
        "applications": json.loads(_text_of(p.get("활용방안")) or "[]"),
        "cost_usd": (p.get("비용USD") or {}).get("number") or 0,
        "error_message": _text_of(p.get("에러메시지")),
    }


async def _find_learning_page(client, item_id: str) -> dict | None:
    res = await client.databases.query(
        database_id=_db("GICHO_LEARNING"),
        filter={"property": "ID", "rich_text": {"equals": item_id}},
    )
    results = res.get("results", [])
    return results[0] if results else None


async def create_learning_item(
    subject: str,
    category: str = "기타",
    sources: list[str] | None = None,
    requested_by: str = "Cho",
    auto_approve: bool = False,
) -> dict:
    """학습 요청을 Notion DB에 등록."""
    client = get_client()
    item_id = uuid.uuid4().hex[:8]
    now = datetime.now().isoformat()
    status = "approved" if auto_approve else "requested"

    properties = {
        "주제": {"title": [{"text": {"content": subject}}]},
        "ID": _rich_text(item_id),
        "카테고리": {"select": {"name": category}},
        "상태": {"select": {"name": status}},
        "소스": _rich_text(json.dumps(sources or [], ensure_ascii=False)),
        "요청자": _rich_text(requested_by),
        "요청일": {"date": {"start": now}},
    }
    if auto_approve:
        properties["승인일"] = {"date": {"start": now}}

    await client.pages.create(parent={"database_id": _db("GICHO_LEARNING")}, properties=properties)
    log.info(f"학습 항목 등록: {subject} ({item_id})")
    return {
        "id": item_id,
        "subject": subject,
        "category": category,
        "status": status,
        "sources": sources or [],
        "requested_at": now,
    }


async def approve_learning_item(item_id: str) -> bool:
    """학습 항목 승인 (requested → approved)."""
    client = get_client()
    page = await _find_learning_page(client, item_id)
    if not page or _select_of(page["properties"].get("상태")) != "requested":
        return False
    await client.pages.update(
        page_id=page["id"],
        properties={
            "상태": {"select": {"name": "approved"}},
            "승인일": {"date": {"start": datetime.now().isoformat()}},
        },
    )
    log.info(f"학습 항목 승인: {item_id}")
    return True


async def reject_learning_item(item_id: str, reason: str = "") -> bool:
    """학습 항목 거부."""
    client = get_client()
    page = await _find_learning_page(client, item_id)
    if not page:
        return False
    await client.pages.update(
        page_id=page["id"],
        properties={
            "상태": {"select": {"name": "rejected"}},
            "에러메시지": _rich_text(reason[:2000]),
        },
    )
    log.info(f"학습 항목 거부: {item_id}")
    return True


async def get_learning_item(item_id: str) -> dict | None:
    """학습 항목 조회."""
    client = get_client()
    page = await _find_learning_page(client, item_id)
    return _parse_learning_page(page) if page else None


async def list_learning_items(
    status: str | None = None,
    category: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """학습 항목 목록."""
    client = get_client()
    filters = []
    if status:
        filters.append({"property": "상태", "select": {"equals": status}})
    if category:
        filters.append({"property": "카테고리", "select": {"equals": category}})

    query_kwargs = {
        "database_id": _db("GICHO_LEARNING"),
        "sorts": [{"property": "요청일", "direction": "descending"}],
        "page_size": min(limit, 100),
    }
    if len(filters) == 1:
        query_kwargs["filter"] = filters[0]
    elif filters:
        query_kwargs["filter"] = {"and": filters}

    res = await client.databases.query(**query_kwargs)
    return [_parse_learning_page(pg) for pg in res.get("results", [])][:limit]


async def update_learning_status(item_id: str, status: str, error_message: str = "") -> None:
    """학습 항목 상태 갱신 (learning/completed/failed 등)."""
    client = get_client()
    page = await _find_learning_page(client, item_id)
    if not page:
        return
    properties = {"상태": {"select": {"name": status}}}
    if status == "completed":
        properties["완료일"] = {"date": {"start": datetime.now().isoformat()}}
    else:
        properties["에러메시지"] = _rich_text(error_message[:2000])
    await client.pages.update(page_id=page["id"], properties=properties)


async def save_learning_analysis(item_id: str, analysis: dict, cost: float = 0) -> None:
    """학습 분석 결과(요약/인사이트/활용방안) 저장."""
    client = get_client()
    page = await _find_learning_page(client, item_id)
    if not page:
        return
    current_cost = (page["properties"].get("비용USD") or {}).get("number") or 0
    await client.pages.update(
        page_id=page["id"],
        properties={
            "요약": _rich_text((analysis.get("summary") or "")[:2000]),
            "인사이트": _rich_text(json.dumps(analysis.get("insights", []), ensure_ascii=False)[:2000]),
            "활용방안": _rich_text(json.dumps(analysis.get("applications", []), ensure_ascii=False)[:2000]),
            "비용USD": {"number": current_cost + cost},
        },
    )


async def search_learning_items(query: str, limit: int = 5) -> list[dict]:
    """완료된 학습 항목 중 키워드가 포함된 항목 검색."""
    client = get_client()
    res = await client.databases.query(
        database_id=_db("GICHO_LEARNING"),
        filter={
            "and": [
                {"property": "상태", "select": {"equals": "completed"}},
                {"or": [
                    {"property": "주제", "title": {"contains": query}},
                    {"property": "요약", "rich_text": {"contains": query}},
                    {"property": "인사이트", "rich_text": {"contains": query}},
                ]},
            ]
        },
        sorts=[{"property": "완료일", "direction": "descending"}],
        page_size=limit,
    )
    return [_parse_learning_page(pg) for pg in res.get("results", [])]


async def get_learning_stats() -> dict:
    """학습 통계 (전체 개수/상태별/카테고리별/총 비용)."""
    client = get_client()
    items = []
    cursor = None
    while True:
        res = await client.databases.query(
            database_id=_db("GICHO_LEARNING"),
            start_cursor=cursor,
            page_size=100,
        )
        items.extend(_parse_learning_page(pg) for pg in res.get("results", []))
        if not res.get("has_more"):
            break
        cursor = res.get("next_cursor")

    by_status: dict[str, int] = {}
    by_category: dict[str, int] = {}
    total_cost = 0.0
    for it in items:
        by_status[it["status"]] = by_status.get(it["status"], 0) + 1
        by_category[it["category"]] = by_category.get(it["category"], 0) + 1
        total_cost += it["cost_usd"]

    return {
        "total": len(items),
        "by_status": by_status,
        "by_category": by_category,
        "total_cost_usd": round(total_cost, 4),
    }

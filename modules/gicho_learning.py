"""
modules/gicho_learning.py
기쵸 러닝 시스템 — 콘텐츠 트렌드/기획 기법 자율 학습.
"""

import asyncio
import json
import logging
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from utils.openrouter_client import chat
from utils.url_analyzer import fetch_url_content

log = logging.getLogger(__name__)

# SQLite DB 경로 (Railway는 /tmp 가 휘발성이라 영구 저장 필요)
DB_DIR = Path(os.getenv("LEARNING_DB_DIR", "data"))
DB_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DB_DIR / "gicho_learning.db"

# 학습 카테고리
CATEGORIES = [
    "콘텐츠_트렌드",
    "기획_기법",
    "협업_사례",
    "썸네일_분석",
    "제목_분석",
    "스트리밍_기술",
    "스폰서십",
    "기타",
]

# 학습 상태
STATUSES = ["requested", "approved", "learning", "completed", "rejected", "failed"]


import os


# ═══════════════════════════════════════════════════════════════════
# DB 초기화
# ═══════════════════════════════════════════════════════════════════

def _init_db():
    """SQLite 테이블 생성."""
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS learning_items (
                id TEXT PRIMARY KEY,
                subject TEXT NOT NULL,
                category TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'requested',
                sources TEXT NOT NULL,
                requested_by TEXT,
                requested_at TEXT NOT NULL,
                approved_at TEXT,
                completed_at TEXT,
                summary TEXT,
                insights TEXT,
                applications TEXT,
                cost_usd REAL DEFAULT 0,
                error_message TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON learning_items(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_category ON learning_items(category)")
        conn.commit()
    finally:
        conn.close()


_init_db()


# ═══════════════════════════════════════════════════════════════════
# CRUD
# ═══════════════════════════════════════════════════════════════════

def create_learning_item(
    subject: str,
    category: str = "기타",
    sources: list[str] = None,
    requested_by: str = "Cho",
    auto_approve: bool = False,
) -> dict:
    """학습 요청 등록."""
    if category not in CATEGORIES:
        category = "기타"

    item_id = str(uuid.uuid4())[:8]
    sources_json = json.dumps(sources or [], ensure_ascii=False)
    now = datetime.now().isoformat()

    status = "approved" if auto_approve else "requested"
    approved_at = now if auto_approve else None

    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("""
            INSERT INTO learning_items
            (id, subject, category, status, sources, requested_by,
             requested_at, approved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (item_id, subject, category, status, sources_json, requested_by, now, approved_at))
        conn.commit()
    finally:
        conn.close()

    return {
        "id": item_id,
        "subject": subject,
        "category": category,
        "status": status,
        "sources": sources or [],
        "requested_at": now,
    }


def approve_item(item_id: str) -> bool:
    """학습 항목 승인."""
    now = datetime.now().isoformat()
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute("""
            UPDATE learning_items
            SET status = 'approved', approved_at = ?
            WHERE id = ? AND status = 'requested'
        """, (now, item_id))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def reject_item(item_id: str, reason: str = "") -> bool:
    """학습 항목 거부."""
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute("""
            UPDATE learning_items
            SET status = 'rejected', error_message = ?
            WHERE id = ?
        """, (reason, item_id))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def get_item(item_id: str) -> Optional[dict]:
    """학습 항목 조회."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM learning_items WHERE id = ?", (item_id,),
        ).fetchone()
        if row:
            item = dict(row)
            item["sources"] = json.loads(item["sources"] or "[]")
            return item
        return None
    finally:
        conn.close()


def list_items(
    status: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 20,
) -> list[dict]:
    """학습 항목 목록."""
    sql = "SELECT * FROM learning_items WHERE 1=1"
    params = []
    if status:
        sql += " AND status = ?"
        params.append(status)
    if category:
        sql += " AND category = ?"
        params.append(category)
    sql += " ORDER BY requested_at DESC LIMIT ?"
    params.append(limit)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(sql, params).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["sources"] = json.loads(item["sources"] or "[]")
            items.append(item)
        return items
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════
# 학습 실행
# ═══════════════════════════════════════════════════════════════════

LEARNING_SYSTEM_PROMPT = """당신은 기쵸의 학습 분석가입니다.
주어진 소스 자료들을 분석하여 핵심 인사이트를 추출합니다.

응답 형식 (JSON):
{
  "summary": "전체 내용 5~10줄 요약",
  "insights": [
    "핵심 인사이트 1",
    "핵심 인사이트 2",
    "..."
  ],
  "applications": [
    "이 인사이트를 어떻게 활용할 수 있는지 1",
    "활용법 2",
    "..."
  ],
  "keywords": ["키워드1", "키워드2", "..."]
}

JSON만 출력. 마크다운 코드 블록 사용하지 마세요."""


async def execute_learning(item_id: str) -> dict:
    """학습 실행. 비동기 백그라운드 호출."""
    item = get_item(item_id)
    if not item:
        return {"success": False, "error": "항목을 찾을 수 없음"}

    if item["status"] not in ("approved",):
        return {"success": False, "error": f"실행 불가 상태: {item['status']}"}

    # 상태 갱신
    _update_status(item_id, "learning")

    try:
        # 1) 모든 소스 URL fetch
        log.info(f"학습 시작 ({item_id}): {item['subject']}")
        sources = item["sources"]
        fetch_results = await asyncio.gather(
            *[fetch_url_content(url) for url in sources],
            return_exceptions=True,
        )

        # 2) 콘텐츠 합치기
        source_contents = []
        for url, result in zip(sources, fetch_results):
            if isinstance(result, Exception) or result.get("error"):
                continue
            source_contents.append(
                f"### {result.get('title', '제목 없음')}\n"
                f"URL: {url}\n"
                f"{result.get('content', '')[:4000]}"
            )

        if not source_contents:
            _update_status(item_id, "failed", "유효한 소스가 없음")
            return {"success": False, "error": "유효한 소스가 없음"}

        combined = "\n\n---\n\n".join(source_contents)

        # 3) AI 분석
        user_msg = (
            f"학습 주제: {item['subject']}\n"
            f"카테고리: {item['category']}\n\n"
            f"--- 소스 자료 ---\n{combined[:30000]}\n"
        )

        result = await chat(
            messages=[
                {"role": "system", "content": LEARNING_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            agent="gihyo",
            tier="standard",
            max_tokens=4000,
            temperature=0.4,
        )

        # 4) JSON 파싱
        try:
            analysis = json.loads(result["content"])
        except json.JSONDecodeError:
            # 코드 블록이 포함됐을 수 있음
            import re
            m = re.search(r"\{.*\}", result["content"], re.DOTALL)
            if m:
                analysis = json.loads(m.group())
            else:
                _update_status(item_id, "failed", "JSON 파싱 실패")
                return {"success": False, "error": "AI 응답 파싱 실패"}

        # 5) DB 저장
        _save_analysis(item_id, analysis, cost=result.get("cost", 0))
        _update_status(item_id, "completed")

        log.info(f"학습 완료 ({item_id})")
        return {"success": True, "analysis": analysis, "item_id": item_id}

    except Exception as e:
        log.exception(f"학습 실패 ({item_id})")
        _update_status(item_id, "failed", str(e)[:500])
        return {"success": False, "error": str(e)}


def _update_status(item_id: str, status: str, error_message: str = ""):
    conn = sqlite3.connect(DB_PATH)
    try:
        if status == "completed":
            conn.execute(
                "UPDATE learning_items SET status = ?, completed_at = ? WHERE id = ?",
                (status, datetime.now().isoformat(), item_id),
            )
        else:
            conn.execute(
                "UPDATE learning_items SET status = ?, error_message = ? WHERE id = ?",
                (status, error_message, item_id),
            )
        conn.commit()
    finally:
        conn.close()


def _save_analysis(item_id: str, analysis: dict, cost: float = 0):
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("""
            UPDATE learning_items
            SET summary = ?,
                insights = ?,
                applications = ?,
                cost_usd = cost_usd + ?
            WHERE id = ?
        """, (
            analysis.get("summary", ""),
            json.dumps(analysis.get("insights", []), ensure_ascii=False),
            json.dumps(analysis.get("applications", []), ensure_ascii=False),
            cost,
            item_id,
        ))
        conn.commit()
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════
# 학습 결과 활용 (다른 모듈에서 호출)
# ═══════════════════════════════════════════════════════════════════

async def search_learning(query: str, limit: int = 5) -> list[dict]:
    """
    학습 DB에서 관련 항목 검색 (간단한 키워드 매칭).
    /ask 기획 호출 시 자동으로 호출되어 컨텍스트 enrichment에 사용.
    """
    sql = """
        SELECT * FROM learning_items
        WHERE status = 'completed'
          AND (subject LIKE ? OR summary LIKE ? OR insights LIKE ?)
        ORDER BY completed_at DESC
        LIMIT ?
    """
    pattern = f"%{query}%"
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(sql, (pattern, pattern, pattern, limit)).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["sources"] = json.loads(item["sources"] or "[]")
            item["insights"] = json.loads(item["insights"] or "[]")
            item["applications"] = json.loads(item["applications"] or "[]")
            items.append(item)
        return items
    finally:
        conn.close()


def get_stats() -> dict:
    """학습 통계."""
    conn = sqlite3.connect(DB_PATH)
    try:
        total = conn.execute("SELECT COUNT(*) FROM learning_items").fetchone()[0]
        by_status = dict(conn.execute(
            "SELECT status, COUNT(*) FROM learning_items GROUP BY status"
        ).fetchall())
        by_category = dict(conn.execute(
            "SELECT category, COUNT(*) FROM learning_items GROUP BY category"
        ).fetchall())
        total_cost = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM learning_items"
        ).fetchone()[0]
        return {
            "total": total,
            "by_status": by_status,
            "by_category": by_category,
            "total_cost_usd": round(total_cost, 4),
        }
    finally:
        conn.close()
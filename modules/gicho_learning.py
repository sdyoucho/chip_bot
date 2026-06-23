"""
modules/gicho_learning.py
기쵸 러닝 시스템 — 콘텐츠 트렌드/기획 기법 자율 학습.
저장소: Notion DB (NOTION_GICHO_LEARNING_DB).
"""

import asyncio
import json
import logging
from typing import Optional

from utils.notion_client import (
    create_learning_item as _create_learning_item,
    approve_learning_item,
    reject_learning_item,
    get_learning_item,
    list_learning_items,
    update_learning_status,
    save_learning_analysis,
    search_learning_items,
    get_learning_stats,
)
from utils.openrouter_client import chat
from utils.url_analyzer import fetch_url_content

log = logging.getLogger(__name__)

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


# ═══════════════════════════════════════════════════════════════════
# CRUD (Notion DB 위임)
# ═══════════════════════════════════════════════════════════════════

async def create_learning_item(
    subject: str,
    category: str = "기타",
    sources: list[str] = None,
    requested_by: str = "Cho",
    auto_approve: bool = False,
) -> dict:
    """학습 요청 등록."""
    if category not in CATEGORIES:
        category = "기타"
    return await _create_learning_item(
        subject=subject,
        category=category,
        sources=sources,
        requested_by=requested_by,
        auto_approve=auto_approve,
    )


async def approve_item(item_id: str) -> bool:
    """학습 항목 승인."""
    return await approve_learning_item(item_id)


async def reject_item(item_id: str, reason: str = "") -> bool:
    """학습 항목 거부."""
    return await reject_learning_item(item_id, reason)


async def get_item(item_id: str) -> Optional[dict]:
    """학습 항목 조회."""
    return await get_learning_item(item_id)


async def list_items(
    status: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 20,
) -> list[dict]:
    """학습 항목 목록."""
    return await list_learning_items(status=status, category=category, limit=limit)


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
    item = await get_learning_item(item_id)
    if not item:
        return {"success": False, "error": "항목을 찾을 수 없음"}

    if item["status"] not in ("approved",):
        return {"success": False, "error": f"실행 불가 상태: {item['status']}"}

    await update_learning_status(item_id, "learning")

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
            await update_learning_status(item_id, "failed", "유효한 소스가 없음")
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
                await update_learning_status(item_id, "failed", "JSON 파싱 실패")
                return {"success": False, "error": "AI 응답 파싱 실패"}

        # 5) DB 저장
        await save_learning_analysis(item_id, analysis, cost=result.get("cost", 0))
        await update_learning_status(item_id, "completed")

        log.info(f"학습 완료 ({item_id})")
        return {"success": True, "analysis": analysis, "item_id": item_id}

    except Exception as e:
        log.exception(f"학습 실패 ({item_id})")
        await update_learning_status(item_id, "failed", str(e)[:500])
        return {"success": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════
# 학습 결과 활용 (다른 모듈에서 호출)
# ═══════════════════════════════════════════════════════════════════

async def search_learning(query: str, limit: int = 5) -> list[dict]:
    """
    학습 DB에서 관련 항목 검색.
    /ask 기획 호출 시 자동으로 호출되어 컨텍스트 enrichment에 사용.
    """
    return await search_learning_items(query, limit=limit)


async def get_stats() -> dict:
    """학습 통계."""
    return await get_learning_stats()

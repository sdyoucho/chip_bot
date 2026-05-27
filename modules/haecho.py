"""
modules/haecho.py
해쵸 — Router 결과 기반 동적 오케스트레이터.

모델 사용:
- light (gpt-5.4-nano): 각 agent raw 결과를 사전 요약·정규화 (빠르게)
- premium (opus 4.7): 정규화된 결과를 최종 종합 브리핑

🆕 v2 변경사항:
- URL 콘텐츠 분석 통합 (utils.url_analyzer)
- enriched_query 처리 (컨텍스트 + URL)
- max_tokens 증가 (900 → 8000)
- URL 분석 실패 안내 추가
"""

import asyncio
import logging

import discord

from utils.openrouter_client import chat
from utils.pipeline_logger import step

log = logging.getLogger(__name__)

SUMMARY_SYSTEM = """당신은 Cho의 매니지먼트 총괄 AI '해쵸'입니다.
아래 역할 AI들의 결과를 종합해 Cho에게 브리핑합니다.

핵심 원칙:
- 간결하되 실행 가능한 정보만
- 우선순위 순으로 정렬
- 각 항목 끝에 담당 에이전트 명시 [기쵸], [분쵸] 식
- 중복 정보는 한 번만 언급
- 응답을 중간에 끊지 말고 완결성 있게 작성
- 최소 1,500자 이상, 필요 시 상세히 작성

응답 형식:
## 🎯 핵심 요약
(3~5줄)

## 📌 주요 내용
(에이전트별 정리)

## ⚡ 우선순위 / 액션 아이템
(번호 매겨서)

## 💡 추가 고려사항
(있으면)

참조 자료 (URL)가 있다면:
- 출처를 명확히 표시 ([출처: example.com])
- 자료 내용을 분석에 활용
- 자료와 다른 의견이 있다면 명시
"""

PRECONDENSE_SYSTEM = """당신은 사전 정리 도우미입니다.
전달된 에이전트 원문을 3~5줄 bullet point로 요약하세요.
핵심 수치와 고유명사는 반드시 보존하고, 수사적 표현은 제거합니다."""

URL_ENRICHMENT_SYSTEM = """당신은 URL 콘텐츠 요약가입니다.
주어진 URL의 본문을 6~10줄로 요약합니다.
핵심 정보, 인용 가능한 데이터, 출처를 명시합니다."""


# ═══════════════════════════════════════════════════════════════════
# Agent 핸들러 레지스트리
# ═══════════════════════════════════════════════════════════════════

async def _call_agent(
    name: str,
    query: str,
    streamer: str = "",
    url_context: str = "",
) -> tuple[discord.Embed, str]:
    """
    각 agent 호출 → (Embed, raw 텍스트). 실패해도 반드시 유효한 tuple 반환.

    Args:
        name: 에이전트 이름
        query: 사용자 쿼리 (enriched 가능)
        streamer: 스트리머 이름
        url_context: URL 분석 결과 (있을 시 query 뒤에 추가)
    """
    from modules import (
        chzzk_monitor, youtube_analytics, weekly_report,
        competitor_analysis, content_suggest, schedule,
        money, planning, rnd, design,
    )

    # URL 컨텍스트가 있으면 query 뒤에 추가 (각 모듈이 활용 가능)
    enriched_query = query
    if url_context:
        enriched_query = f"{query}\n\n--- 참조 URL 자료 ---\n{url_context}"

    handlers = {
        "monitor":    lambda: chzzk_monitor.get_current_status(streamer or "all"),
        "youtube":    lambda: youtube_analytics.get_channel_stats(streamer or "all"),
        "report":     lambda: weekly_report.generate_report(streamer or "all"),
        "competitor": lambda: competitor_analysis.run_analysis(streamer or "all"),
        "suggest":    lambda: content_suggest.generate_suggestions(enriched_query, streamer),
        "schedule":   lambda: schedule.handle_schedule(enriched_query),
        "money":      lambda: money.handle_query(enriched_query) if enriched_query else money.get_financial_summary(),
        "planning":   lambda: planning.create_document(enriched_query, streamer),
        "rnd":        lambda: rnd.handle_query(enriched_query),
        "design":     lambda: design.handle_query(enriched_query),
    }
    handler = handlers.get(name)
    if not handler:
        err_embed = discord.Embed(
            title=f"❓ 알 수 없는 에이전트",
            description=f"`{name}` 에이전트를 찾을 수 없습니다.",
            color=0xEAB308,
        )
        return err_embed, f"unknown agent: {name}"

    try:
        embed = await handler()
        if embed is None or not isinstance(embed, discord.Embed):
            log.warning(f"agent {name}가 유효하지 않은 값 반환: {type(embed)}")
            err_embed = discord.Embed(
                title=f"⚠️ {name} 응답 형식 오류",
                description=f"에이전트가 예상치 못한 형식을 반환했습니다.",
                color=0xEAB308,
            )
            return err_embed, str(embed)

        # raw 텍스트 추출
        raw_parts = []
        if embed.title:
            raw_parts.append(embed.title)
        if embed.description:
            raw_parts.append(embed.description)
        for f in embed.fields:
            raw_parts.append(f"[{f.name}]\n{f.value}")
        raw = "\n\n".join(raw_parts)

        return embed, raw

    except Exception as e:
        log.exception(f"agent {name} 실행 실패")
        err = discord.Embed(
            title=f"❌ {name} 실행 중 오류",
            description=(
                f"**오류 내용**: {str(e)[:500]}\n\n"
                f"질문을 조금 다르게 표현하거나, `/ask 개쵸 {name} 문제 확인`으로 "
                f"개쵸에게 이슈를 공유해주세요."
            ),
            color=0xE11D48,
        )
        return err, f"error: {e}"


# ═══════════════════════════════════════════════════════════════════
# URL 콘텐츠 enrichment
# ═══════════════════════════════════════════════════════════════════

async def _fetch_and_format_urls(urls: list) -> tuple[str, list[dict]]:
    """
    URL 리스트를 받아 콘텐츠를 가져온 후 포맷팅된 텍스트로 반환.

    Returns:
        (context_text, fetch_results)
        - context_text: 모든 URL 본문을 합친 형식화된 텍스트
        - fetch_results: 각 URL별 fetch 결과 (실패 안내용)
    """
    if not urls:
        return "", []

    from utils.url_analyzer import fetch_url_content

    # 병렬 fetch
    tasks = [fetch_url_content(url) for url in urls[:5]]  # 최대 5개
    results = await asyncio.gather(*tasks, return_exceptions=True)

    fetch_results = []
    valid_contents = []

    for url, result in zip(urls, results):
        if isinstance(result, Exception):
            fetch_results.append({
                "url": url,
                "success": False,
                "error": str(result),
            })
            continue

        if result.get("error"):
            fetch_results.append({
                "url": url,
                "success": False,
                "error": result["error"],
            })
            continue

        fetch_results.append({
            "url": url,
            "success": True,
            "title": result.get("title", ""),
            "type": result.get("type", "webpage"),
        })
        valid_contents.append(result)

    if not valid_contents:
        return "", fetch_results

    # 본문 합치기 (각 5,000자까지)
    parts = []
    for c in valid_contents:
        section = f"\n### 📎 [{c['type']}] {c['title']}\n"
        section += f"URL: {c['url']}\n"
        if c.get('description'):
            section += f"요약: {c['description'][:500]}\n"
        section += f"\n본문:\n{c['content'][:5000]}\n"
        if c.get('metadata'):
            meta = c['metadata']
            if meta.get('author'):
                section += f"\n저자: {meta['author']}"
            if meta.get('published'):
                section += f"\n발행: {meta['published']}"
        parts.append(section)

    context_text = "\n---\n".join(parts)
    return context_text, fetch_results


# ═══════════════════════════════════════════════════════════════════
# 사전 요약 (light 모델)
# ═══════════════════════════════════════════════════════════════════

async def _precondense(agent_name: str, raw: str) -> str:
    """
    light 모델로 raw 결과를 빠르게 요약.
    premium 모델의 입력 토큰을 줄이는 선처리 단계.
    """
    if len(raw) < 400:
        return raw

    try:
        result = await chat(
            messages=[
                {"role": "system", "content": PRECONDENSE_SYSTEM},
                {"role": "user", "content": f"[{agent_name}]\n{raw[:3000]}"},
            ],
            agent="haecho",
            tier="light",
            max_tokens=400,
            temperature=0.3,
            use_cache=True,
        )
        return result["content"]
    except Exception as e:
        log.warning(f"해쵸 사전 요약 실패({agent_name}): {e} → 원문 사용")
        return raw[:1200]


# ═══════════════════════════════════════════════════════════════════
# 메인 오케스트레이터
# ═══════════════════════════════════════════════════════════════════

async def orchestrate(
    query: str,
    routing: dict,
    streamer: str = "",
) -> dict:
    """
    Router 결과를 받아 필요한 agent만 병렬 호출 + URL 콘텐츠 통합.

    Args:
        query: 사용자 쿼리 (이미 컨텍스트 enrichment 된 상태로 들어옴)
        routing: route() 결과
            - modules: list of {name, priority, reason}
            - needs_haecho_summary: bool
            - extracted_urls: list[str] (선택)
        streamer: 스트리머 이름

    Returns:
        {
            "agent_results": {name: (Embed, raw_text)},
            "summary_embed": Embed | None,
            "url_fetch_results": list[dict],   # 🆕 URL 분석 결과
        }
    """
    modules = routing.get("modules", [])
    needs_summary = routing.get("needs_haecho_summary", False)
    extracted_urls = routing.get("extracted_urls", [])

    step("해쵸 오케스트레이션", "ok",
         f"agents={[m['name'] for m in modules]}, "
         f"summary={needs_summary}, "
         f"urls={len(extracted_urls)}")

    # ───────────────────────────────────────────────────────────
    # 1) URL 콘텐츠 수집 (있는 경우)
    # ───────────────────────────────────────────────────────────
    url_context = ""
    url_fetch_results = []

    if extracted_urls:
        step("URL 분석", "ok", f"{len(extracted_urls)}개 URL 수집 시작")
        url_context, url_fetch_results = await _fetch_and_format_urls(extracted_urls)

        success_count = sum(1 for r in url_fetch_results if r["success"])
        fail_count = len(url_fetch_results) - success_count
        step(
            "URL 분석",
            "ok" if success_count > 0 else "fail",
            f"성공 {success_count}/{len(url_fetch_results)}, 실패 {fail_count}",
        )

    # ───────────────────────────────────────────────────────────
    # 2) Agent 병렬 호출 (URL 컨텍스트 포함)
    # ───────────────────────────────────────────────────────────
    sem = asyncio.Semaphore(3)

    async def _bound(m):
        async with sem:
            return m["name"], await _call_agent(
                m["name"], query, streamer, url_context=url_context,
            )

    pairs = await asyncio.gather(
        *[_bound(m) for m in modules if m["name"] != "haecho"],
        return_exceptions=False,
    )
    agent_results = dict(pairs)

    # ───────────────────────────────────────────────────────────
    # 3) 종합 브리핑 (필요 시)
    # ───────────────────────────────────────────────────────────
    summary_embed = None

    # URL이 있거나 / 여러 agent 결과가 있거나 / 명시적 요청 시 summary 생성
    should_summarize = (
        needs_summary
        or len(modules) >= 2
        or bool(extracted_urls)  # 🆕 URL이 있으면 무조건 종합
    )

    if should_summarize:
        summary_embed = await _summarize(
            query, agent_results, url_context=url_context,
            url_fetch_results=url_fetch_results,
        )

    return {
        "agent_results": agent_results,
        "summary_embed": summary_embed,
        "url_fetch_results": url_fetch_results,
    }


# ═══════════════════════════════════════════════════════════════════
# 최종 종합 (premium 모델)
# ═══════════════════════════════════════════════════════════════════

async def _summarize(
    query: str,
    agent_results: dict,
    *,
    url_context: str = "",
    url_fetch_results: list = None,
) -> discord.Embed:
    """
    light로 사전 요약 → premium으로 최종 종합.
    URL 콘텐츠가 있으면 함께 분석.
    """
    url_fetch_results = url_fetch_results or []

    # Step 1: 각 agent 결과를 light 모델로 병렬 사전 요약
    step("해쵸 사전 요약(light)", "ok", f"{len(agent_results)}개 agent")
    condense_tasks = {
        name: _precondense(name, raw)
        for name, (_, raw) in agent_results.items()
    }
    condensed_list = await asyncio.gather(*condense_tasks.values())
    condensed = dict(zip(condense_tasks.keys(), condensed_list))

    # Step 2: premium 모델로 최종 종합
    context_parts = [f"[{name}]\n{text}" for name, text in condensed.items()]
    context = "\n\n".join(context_parts) if context_parts else "(에이전트 결과 없음)"

    user_msg_parts = [f"Cho 요청: {query}"]

    # URL 콘텐츠 추가
    if url_context:
        user_msg_parts.append(
            f"\n=== 📎 사용자가 첨부한 URL 자료 ===\n{url_context}\n"
        )

    user_msg_parts.append(
        f"\n=== 🤖 각 역할 AI의 정리된 결과 ===\n{context}"
    )

    user_msg = "\n".join(user_msg_parts)

    try:
        result = await chat(
            messages=[
                {"role": "system", "content": SUMMARY_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            agent="haecho",
            tier="premium",
            max_tokens=8000,   # 🆕 900 → 8000 (응답 잘림 방지)
            temperature=0.7,
            use_cache=False,
        )

        # Embed 생성
        embed_description = result["content"]

        # URL 분석 실패가 있으면 footer에 안내
        url_warnings = []
        for r in url_fetch_results:
            if not r["success"]:
                url_warnings.append(f"⚠️ {r['url']}: {r.get('error', '실패')[:50]}")

        embed = discord.Embed(
            title="🎯 해쵸 — 총괄 브리핑",
            description=embed_description,
            color=0x1E293B,
        )

        # URL 실패 안내 필드 (있을 시)
        if url_warnings:
            embed.add_field(
                name="⚠️ URL 분석 일부 실패",
                value="\n".join(url_warnings[:5])[:1000],
                inline=False,
            )

        # 성공한 URL 수 표시
        success_urls = [r for r in url_fetch_results if r["success"]]
        url_info = f" · 📎 URL {len(success_urls)}개 분석" if success_urls else ""

        embed.set_footer(
            text=f"{result['model'].split('/')[-1]} · "
                 f"${result['cost']:.5f} · "
                 f"light 사전정리 + premium 종합 · "
                 f"{len(agent_results)}개 에이전트"
                 f"{url_info}"
        )
        return embed

    except Exception as e:
        log.exception("해쵸 종합 실패")
        from bot.embeds import embed_error
        return embed_error("해쵸 종합 실패", str(e))


# ═══════════════════════════════════════════════════════════════════
# 레거시 호환 — 기존 /ask 직접 호출용
# ═══════════════════════════════════════════════════════════════════

async def brief(query: str = "", streamer_name: str = "") -> discord.Embed:
    """단독 호출용 (라우터 없이 해쵸만 쓰는 경우)."""
    routing = {
        "modules": [
            {"name": "monitor",  "priority": 1, "reason": "현황"},
            {"name": "schedule", "priority": 2, "reason": "일정"},
            {"name": "money",    "priority": 3, "reason": "자금"},
        ],
        "needs_haecho_summary": True,
        "extracted_urls": [],
    }
    result = await orchestrate(query, routing, streamer_name)
    return result["summary_embed"] or discord.Embed(
        title="🎯 해쵸", description="결과 없음", color=0x1E293B,
    )
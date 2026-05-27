"""
bot/router.py
OpenRouter 기반 라우팅 엔진 (gpt-5.4-nano 사용).
사용자 입력 → 필요한 모듈 1~N개 JSON으로 반환.

🆕 v2 변경:
- URL 자동 추출 + 라우팅 힌트
- GitHub 링크 특수 처리
- max_tokens 증가 (300 → 500)
"""

import json
import logging
import re

from utils.openrouter_client import chat

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """당신은 Cho의 매니지먼트 봇 라우터입니다.
사용자(Cho) 입력을 분석해 **필요한 모듈만** 선별하세요. 관련 없는 모듈은 절대 호출하지 마세요.

모듈 목록:
- monitor  : 모쵸 — 실시간 방송 현황, 채팅, 시청자
- youtube  : 분쵸 — 유튜브 통계
- report   : 분쵸 — 주간/월간 리포트
- competitor: 분쵸 — 경쟁 채널 트렌드
- suggest  : 기쵸 — 콘텐츠/썸네일 개선 제안
- planning : 기쵸 — 기획서, 협업 제안서
- schedule : 스쵸 — 일정 조회
- money    : 인쵸 — 자금·토큰 비용
- rnd      : 개쵸 — 개발/기술/봇 유지보수/코드 분석
- design   : 디쵸 — 디자인(Figma 포스터/PPT)
- haecho   : 해쵸 — 총괄 브리핑(다수 모듈 종합이 필요할 때)
- streamer_add / streamer_list : 스트리머 관리

URL 처리 가이드:
- GitHub 링크 (github.com/...) → rnd (코드/기술 관련)
- YouTube 링크 → youtube + suggest (콘텐츠 분석)
- 뉴스/블로그 → planning 또는 suggest (콘텐츠 참고)
- 경쟁 채널 (chzzk.naver.com, twitch.tv) → competitor

반드시 아래 JSON 형식으로만 응답:
{
  "modules": [
    {"name": "모듈명", "priority": 1, "reason": "이유"}
  ],
  "needs_haecho_summary": true,
  "confidence": 0.9
}

규칙:
1. 단일 도메인 질문이면 modules=1개
2. 복합 도메인이면 modules=2~4개, needs_haecho_summary=true
3. URL이 포함되어 있으면 needs_haecho_summary=true (종합 분석 필요)
4. 확실하지 않으면 modules=[{"name":"haecho"}], needs_haecho_summary=true

JSON만 출력."""


# ═══════════════════════════════════════════════════════════════════
# URL 분류 (라우팅 힌트용)
# ═══════════════════════════════════════════════════════════════════

URL_CATEGORIES = {
    "github": (
        ["github.com", "gist.github.com"],
        "rnd",
        "GitHub 코드/저장소 분석",
    ),
    "youtube": (
        ["youtube.com", "youtu.be"],
        "suggest",
        "YouTube 콘텐츠 분석",
    ),
    "chzzk": (
        ["chzzk.naver.com"],
        "competitor",
        "치지직 채널 분석",
    ),
    "twitch": (
        ["twitch.tv"],
        "competitor",
        "Twitch 채널 분석",
    ),
    "soop": (
        ["sooplive.co.kr", "afreecatv.com"],
        "competitor",
        "SOOP 채널 분석",
    ),
    "figma": (
        ["figma.com"],
        "design",
        "Figma 디자인 분석",
    ),
    "notion": (
        ["notion.so", "notion.site"],
        "planning",
        "Notion 문서 분석",
    ),
}


def _categorize_url(url: str) -> tuple[str, str, str] | None:
    """
    URL을 분류하여 (category, suggested_module, reason) 반환.
    매칭 안 되면 None.
    """
    url_lower = url.lower()
    for category, (domains, module, reason) in URL_CATEGORIES.items():
        if any(d in url_lower for d in domains):
            return category, module, reason
    return None


# ═══════════════════════════════════════════════════════════════════
# 메인 라우팅
# ═══════════════════════════════════════════════════════════════════

async def route(user_input: str) -> dict:
    """
    반환:
    {
        "modules": [{"name": ..., "priority": ..., "reason": ...}, ...],
        "needs_haecho_summary": bool,
        "confidence": float,
        "extracted_urls": list[str],          # 🆕
        "url_categories": dict[str, str],     # 🆕 {url: category}
    }
    """
    # 1) URL 추출
    extracted_urls = []
    url_categories = {}
    try:
        from utils.url_analyzer import extract_urls
        extracted_urls = extract_urls(user_input)

        for url in extracted_urls:
            cat = _categorize_url(url)
            if cat:
                url_categories[url] = cat[0]
    except ImportError:
        log.debug("url_analyzer 미설치 — URL 추출 건너뜀")

    # 2) LLM 라우팅
    routing_result = None
    try:
        result = await chat(
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_input},
            ],
            agent="router",
            tier="router",
            max_tokens=500,
            temperature=0.1,
            use_cache=True,
        )
        text = result["content"].strip()
        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if json_match:
            parsed = json.loads(json_match.group())
            if "modules" in parsed and isinstance(parsed["modules"], list):
                parsed.setdefault("needs_haecho_summary", False)
                parsed.setdefault("confidence", 0.7)
                routing_result = parsed
    except Exception as e:
        log.error(f"라우터 LLM 오류: {e}")

    # 3) LLM 실패 시 폴백
    if not routing_result:
        routing_result = _fallback_route(user_input)

    # 4) URL이 있으면 라우팅 보강
    if extracted_urls:
        routing_result = _enrich_routing_with_urls(
            routing_result, extracted_urls, url_categories,
        )

    # 5) 최종 결과
    routing_result["extracted_urls"] = extracted_urls
    routing_result["url_categories"] = url_categories

    log.info(
        f"라우팅: {[m['name'] for m in routing_result['modules']]} "
        f"summary={routing_result['needs_haecho_summary']} "
        f"urls={len(extracted_urls)}"
    )
    return routing_result


# ═══════════════════════════════════════════════════════════════════
# URL 기반 라우팅 보강
# ═══════════════════════════════════════════════════════════════════

def _enrich_routing_with_urls(
    routing: dict,
    urls: list[str],
    url_categories: dict[str, str],
) -> dict:
    """
    URL이 있으면 적절한 에이전트를 자동 추가 + summary 강제.
    """
    existing_modules = {m["name"] for m in routing["modules"]}

    # 각 URL 카테고리에 맞는 모듈 추가
    added_modules = []
    for url, category in url_categories.items():
        cat_info = URL_CATEGORIES.get(category)
        if not cat_info:
            continue
        _, suggested_module, reason = cat_info

        if suggested_module not in existing_modules:
            added_modules.append({
                "name": suggested_module,
                "priority": len(routing["modules"]) + len(added_modules) + 1,
                "reason": f"URL 분석: {reason}",
            })
            existing_modules.add(suggested_module)

    # 카테고리 없는 URL이 있으면 planning 추가
    uncategorized = [u for u in urls if u not in url_categories]
    if uncategorized and "planning" not in existing_modules:
        added_modules.append({
            "name": "planning",
            "priority": len(routing["modules"]) + len(added_modules) + 1,
            "reason": f"URL 분석 (일반): {len(uncategorized)}개",
        })

    routing["modules"].extend(added_modules)

    # URL이 있으면 무조건 종합 분석
    routing["needs_haecho_summary"] = True

    return routing


# ═══════════════════════════════════════════════════════════════════
# 폴백 (LLM 장애 시 키워드 기반)
# ═══════════════════════════════════════════════════════════════════

def _fallback_route(text: str) -> dict:
    """키워드 폴백 (OpenRouter 장애 시)."""
    text_lower = text.lower()
    rules = [
        (["방송", "모니터", "채팅", "시청자", "라이브"], "monitor"),
        (["유튜브", "영상", "조회수", "구독"], "youtube"),
        (["리포트", "주간", "분석", "요약"], "report"),
        (["경쟁", "비교", "트렌드"], "competitor"),
        (["썸네일", "제목", "클릭률"], "suggest"),
        (["기획서", "제안서", "협업"], "planning"),
        (["스케줄", "일정", "캘린더"], "schedule"),
        (["자금", "비용", "토큰", "요금", "돈"], "money"),
        (["개발", "코드", "기술", "github"], "rnd"),
        (["디자인", "포스터", "ppt", "figma"], "design"),
        (["전체", "총괄", "브리핑", "종합"], "haecho"),
    ]
    matched = []
    for i, (keywords, module) in enumerate(rules):
        if any(k in text_lower for k in keywords):
            matched.append({"name": module, "priority": i + 1, "reason": "키워드 폴백"})

    if not matched:
        matched = [{"name": "haecho", "priority": 1, "reason": "판단 불가 → 총괄"}]

    return {
        "modules": matched,
        "needs_haecho_summary": len(matched) >= 2,
        "confidence": 0.5,
    }
"""
bot/router.py
OpenRouter 기반 라우팅 엔진 (gpt-5.4-nano 사용).
사용자 입력 → 필요한 모듈 1~N개 JSON으로 반환.
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
- rnd      : 개쵸 — 개발/기술/봇 유지보수
- design   : 디쵸 — 디자인(Figma 포스터/PPT)
- haecho   : 해쵸 — 총괄 브리핑(다수 모듈 종합이 필요할 때)
- streamer_add / streamer_list : 스트리머 관리

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
3. 확실하지 않으면 modules=[{"name":"haecho"}], needs_haecho_summary=true

JSON만 출력."""


async def route(user_input: str) -> dict:
    """
    반환: {"modules": [...], "needs_haecho_summary": bool, "confidence": float}
    """
    try:
        result = await chat(
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_input},
            ],
            agent="router",
            tier="router",  # gpt-5.4-nano
            max_tokens=300,
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
                log.info(
                    f"라우팅: {[m['name'] for m in parsed['modules']]} "
                    f"summary={parsed['needs_haecho_summary']}"
                )
                return parsed
    except Exception as e:
        log.error(f"라우터 오류: {e}")

    return _fallback_route(user_input)


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
        (["개발", "코드", "기술"], "rnd"),
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
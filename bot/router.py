"""
bot/router.py
Gemini Flash 라우팅 엔진 (다중 선택 지원).
사용자 입력 → 필요한 모듈 1~N개를 우선순위와 함께 반환.
"""

import asyncio
import json
import logging
import os
import re

import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

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
    {"name": "모듈명", "priority": 1, "reason": "이유"},
    ...
  ],
  "needs_haecho_summary": true/false,
  "confidence": 0.0~1.0
}

규칙:
1. 질문이 단일 도메인이면 modules 배열에 1개만.
2. 여러 도메인이 얽힌 질문(예: "이번 주 전체 현황", "기획서 쓰려는데 경쟁사 트렌드도 반영해줘")은 2~4개 선택.
3. needs_haecho_summary=true이면 해쵸가 결과들을 종합 브리핑.
4. 확실하지 않으면 modules=[{"name":"haecho", ...}], needs_haecho_summary=true.

JSON만 출력."""

_model = None


def _get_model():
    global _model
    if _model is None:
        _model = genai.GenerativeModel(
            model_name="gemini-2.0-flash-exp",
            system_instruction=SYSTEM_PROMPT,
        )
    return _model


async def route(user_input: str) -> dict:
    """
    반환:
    {
      "modules": [{"name": str, "priority": int, "reason": str}, ...],
      "needs_haecho_summary": bool,
      "confidence": float
    }
    """
    model = _get_model()
    try:
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None, lambda: model.generate_content(user_input)
        )
        text = response.text.strip()
        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group())
            # 정합성 검증
            if "modules" not in result or not isinstance(result["modules"], list):
                raise ValueError("Invalid schema")
            result.setdefault("needs_haecho_summary", False)
            result.setdefault("confidence", 0.7)
            log.info(
                f"라우팅: {[m['name'] for m in result['modules']]} "
                f"(summary={result['needs_haecho_summary']})"
            )
            return result
    except Exception as e:
        log.error(f"라우터 오류: {e}")

    return _fallback_route(user_input)


def _fallback_route(text: str) -> dict:
    """키워드 기반 폴백 (다중 매칭 허용)."""
    text_lower = text.lower()
    rules = [
        (["방송", "모니터", "채팅", "시청자", "라이브"], "monitor"),
        (["유튜브", "영상", "조회수", "구독", "ctr"], "youtube"),
        (["리포트", "주간", "분석", "요약"], "report"),
        (["경쟁", "비교", "트렌드"], "competitor"),
        (["썸네일", "제목", "클릭률"], "suggest"),
        (["기획서", "제안서", "협업", "문서"], "planning"),
        (["스케줄", "일정", "캘린더", "데드라인"], "schedule"),
        (["자금", "비용", "토큰", "요금", "돈"], "money"),
        (["개발", "코드", "기술", "봇"], "rnd"),
        (["디자인", "포스터", "ppt", "figma"], "design"),
        (["전체", "총괄", "브리핑", "종합", "상황"], "haecho"),
    ]
    matched = []
    for i, (keywords, module) in enumerate(rules):
        if any(k in text_lower for k in keywords):
            matched.append({
                "name": module, "priority": i + 1,
                "reason": "키워드 매칭",
            })

    if not matched:
        matched = [{"name": "haecho", "priority": 1, "reason": "판단 불가 → 총괄"}]

    return {
        "modules": matched,
        "needs_haecho_summary": len(matched) >= 2,
        "confidence": 0.5,
    }
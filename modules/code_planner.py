"""
modules/code_planner.py
개쵸의 자동 코드 변경 계획 시스템.

워크플로:
1. 의도 분석
2. 코드베이스 스캔
3. 변경 계획 수립
4. 각 파일별 코드 생성
"""

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime
from typing import Optional

from utils.github_client import get_file_content, _get_repo_info, _headers
from utils.openrouter_client import chat
import aiohttp

log = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"

# 의도 분석 시스템 프롬프트
INTENT_SYSTEM = """당신은 개쵸 — chip_bot 시스템의 자율 코드 분석가입니다.
사용자(Cho)의 자연어 요청을 받아 어떤 코드 변경이 필요한지 분석합니다.

봇 구조 (필수 숙지):
- bot/         : Discord 커맨드, 라우터, UI
  - commands.py: 슬래시 커맨드 등록
  - router.py  : 자연어 → 모듈 라우팅
  - main.py    : 봇 진입점 (수정 금지)
- modules/     : 8개 에이전트
  - haecho     : 총괄
  - chzzk_monitor (모쵸): 치지직 모니터링
  - youtube_analytics, weekly_report, competitor_analysis (분쵸): 분석
  - content_suggest, planning (기쵸): 콘텐츠 제안/기획
  - schedule (스쵸): 스케줄
  - money (인쵸): 자금
  - rnd (개쵸): 개발/유지보수
  - design (디쵸): 디자인
- utils/       : 유틸리티
  - openrouter_client, notion_client, github_client 등

응답 형식 (JSON):
{
  "intent": "변경 의도 한 줄 요약",
  "scope": "bug_fix | feature_add | refactor | docs | config",
  "risk": "low | medium | high",
  "target_agent": "관련 에이전트 이름 (있으면)",
  "needs_new_files": true/false,
  "estimated_files": 1~10,
  "reasoning": "왜 이렇게 판단했는지 3~5줄"
}

JSON만 출력. 마크다운 코드 블록 사용 금지."""


# 계획 수립 시스템 프롬프트
PLANNER_SYSTEM = """당신은 개쵸 — 코드 변경 계획자입니다.
요청과 현재 코드베이스 구조를 보고, 어떤 파일을 어떻게 변경할지 구체적 계획을 세웁니다.

규칙:
1. 변경 가능 파일: modules/*.py, bot/*.py, utils/*.py, docs/*.md, requirements.txt
2. 변경 금지: bot/main.py, .env, secrets.*
3. 신규 파일이 필요하면 명확한 위치 + 이름 지정
4. 각 파일별 변경 사유 + 변경 작업을 구체적으로 명시
5. 변경 라인 수가 100을 초과할 것 같으면 여러 파일로 분할

응답 형식 (JSON):
{
  "plan_summary": "전체 변경 계획 요약 (3~5줄)",
  "files": [
    {
      "path": "파일 경로",
      "action": "create | modify",
      "purpose": "이 파일의 역할 / 변경 사유",
      "instruction": "구체적인 변경/생성 내용 (5~10줄)",
      "estimated_lines": 50,
      "depends_on": []
    }
  ],
  "requires_dependencies": ["새로 추가할 pip 패키지 (있으면)"],
  "estimated_total_lines": 250,
  "execution_order": ["path1", "path2", ...]
}

JSON만 출력."""


CODE_GENERATOR_SYSTEM = """당신은 개쵸 — 코드 생성자입니다.
주어진 파일의 현재 내용(있으면)과 변경 지시를 보고, 완성된 파일 전체를 출력합니다.

규칙:
1. 응답은 변경된 파일의 **전체 내용**을 코드 블록으로 출력
2. 기존 들여쓰기/스타일 유지
3. 한국어 주석 OK
4. import는 표준 → 서드파티 → 로컬 순서
5. 함수/클래스에 docstring 추가
6. 타입 힌트 명시

응답 형식:
```python
# 변경된 파일 전체
...
```

코드 블록 외에는 1~3줄 변경 요약만 작성."""


# ═══════════════════════════════════════════════════════════════════
# 임시 세션 저장소
# ═══════════════════════════════════════════════════════════════════

_PLAN_SESSIONS: dict[str, dict] = {}


def get_session(session_id: str) -> Optional[dict]:
    return _PLAN_SESSIONS.get(session_id)


def list_sessions(limit: int = 10) -> list[dict]:
    items = sorted(
        _PLAN_SESSIONS.values(),
        key=lambda s: s.get("created_at", ""),
        reverse=True,
    )
    return items[:limit]


# ═══════════════════════════════════════════════════════════════════
# Stage 1: 의도 분석
# ═══════════════════════════════════════════════════════════════════

async def analyze_intent(user_request: str) -> dict:
    """사용자 요청을 분석해 의도/스코프/리스크 판단."""
    try:
        result = await chat(
            messages=[
                {"role": "system", "content": INTENT_SYSTEM},
                {"role": "user", "content": user_request},
            ],
            agent="gaechyo",
            tier="standard",
            max_tokens=800,
            temperature=0.2,
        )
        text = result["content"].strip()

        # JSON 추출
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return {"success": False, "error": "JSON 파싱 실패"}
        intent = json.loads(m.group())
        intent["success"] = True
        intent["cost"] = result.get("cost", 0)
        return intent
    except Exception as e:
        log.exception("의도 분석 실패")
        return {"success": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════
# Stage 2: 코드베이스 스캔
# ═══════════════════════════════════════════════════════════════════

async def scan_codebase(focus_areas: list[str] = None) -> dict:
    """
    레포 디렉토리 트리 + 주요 파일 목록 수집.

    Args:
        focus_areas: 집중 조사할 경로 (예: ["modules/design.py"])
    """
    info = _get_repo_info()

    timeout = aiohttp.ClientTimeout(total=20)
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=_headers()) as session:
            # 1) 전체 트리
            tree_url = f"{GITHUB_API}/repos/{info['owner']}/{info['repo']}/git/trees/{info['branch']}?recursive=1"
            async with session.get(tree_url) as resp:
                if resp.status != 200:
                    return {"success": False, "error": f"트리 조회 실패: HTTP {resp.status}"}
                tree_data = await resp.json()

            # 2) 파이썬 파일 + 주요 파일만 필터
            relevant_files = []
            for item in tree_data.get("tree", []):
                if item["type"] != "blob":
                    continue
                path = item["path"]
                if (path.endswith(".py") or
                    path.endswith(".md") or
                    path == "requirements.txt"):
                    relevant_files.append({
                        "path": path,
                        "size": item.get("size", 0),
                    })

            # 3) focus_areas의 실제 내용 가져오기 (병렬)
            focused_contents = {}
            if focus_areas:
                tasks = [get_file_content(p) for p in focus_areas[:8]]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for path, result in zip(focus_areas[:8], results):
                    if isinstance(result, dict) and result.get("success"):
                        focused_contents[path] = result["content"]

            return {
                "success": True,
                "total_files": len(relevant_files),
                "files": relevant_files,
                "focused_contents": focused_contents,
            }
    except Exception as e:
        log.exception("코드베이스 스캔 실패")
        return {"success": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════
# Stage 3: 변경 계획 수립
# ═══════════════════════════════════════════════════════════════════

async def create_plan(
    user_request: str,
    intent: dict,
    codebase: dict,
) -> dict:
    """변경 계획 수립."""
    # 파일 트리 요약 (LLM 입력용)
    files_summary = "\n".join(
        f"  • {f['path']} ({f['size']} bytes)"
        for f in codebase.get("files", [])[:80]
    )

    user_msg = (
        f"=== Cho 요청 ===\n{user_request}\n\n"
        f"=== 의도 분석 결과 ===\n{json.dumps(intent, ensure_ascii=False, indent=2)}\n\n"
        f"=== 현재 코드베이스 구조 ===\n{files_summary}\n\n"
        f"=== 작업 ===\n"
        f"위 정보를 바탕으로 어떤 파일을 어떻게 변경할지 구체적인 계획을 JSON으로 응답해주세요."
    )

    try:
        result = await chat(
            messages=[
                {"role": "system", "content": PLANNER_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            agent="gaechyo",
            tier="premium",
            max_tokens=4000,
            temperature=0.3,
        )
        text = result["content"].strip()
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return {"success": False, "error": "JSON 파싱 실패"}

        plan = json.loads(m.group())
        plan["success"] = True
        plan["cost"] = result.get("cost", 0)
        return plan
    except Exception as e:
        log.exception("계획 수립 실패")
        return {"success": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════
# Stage 4: 세션 생성
# ═══════════════════════════════════════════════════════════════════

async def create_planning_session(
    user_request: str,
    requester: str = "Cho",
    *,
    conversation_context: str = "",   # 🆕 이전 대화 맥락
) -> dict:
    """전체 분석 파이프라인 실행 → 세션 생성.

    Args:
        user_request: 사용자 요청 (자연어)
        requester: 요청자 이름
        conversation_context: 이전 Discord 대화 컨텍스트 (선택)
    """
    session_id = str(uuid.uuid4())[:8]

    log.info(f"[{session_id}] 자동 계획 시작: {user_request[:80]}")
    if conversation_context:
        log.info(f"[{session_id}] 컨텍스트 포함: {len(conversation_context):,}자")

    # 🆕 컨텍스트가 있으면 user_request 앞에 추가하여 LLM에 전달
    enriched_request = user_request
    if conversation_context:
        enriched_request = (
            f"{conversation_context}\n\n"
            f"--- 현재 요청 ---\n{user_request}"
        )

    # Stage 1: 의도 분석 (enriched 사용)
    intent = await analyze_intent(enriched_request)
    if not intent.get("success"):
        return {"success": False, "error": f"의도 분석 실패: {intent.get('error')}"}

    log.info(f"[{session_id}] 의도: {intent.get('intent')} (scope={intent.get('scope')})")

    # Stage 2: 코드베이스 스캔 (target_agent 있으면 그 모듈도 포함)
    focus_paths = []
    target_agent = intent.get("target_agent", "")
    if target_agent:
        # 에이전트 → 파일 매핑
        agent_to_files = {
            "haecho": ["modules/haecho.py"],
            "monitor": ["modules/chzzk_monitor.py"],
            "youtube": ["modules/youtube_analytics.py"],
            "report": ["modules/weekly_report.py"],
            "competitor": ["modules/competitor_analysis.py"],
            "suggest": ["modules/content_suggest.py"],
            "planning": ["modules/planning.py"],
            "schedule": ["modules/schedule.py"],
            "money": ["modules/money.py"],
            "rnd": ["modules/rnd.py"],
            "design": ["modules/design.py"],
            "gaechyo": ["modules/rnd.py"],
            "gicho": ["modules/planning.py", "modules/content_suggest.py"],
            "mocho": ["modules/chzzk_monitor.py"],
            "dicho": ["modules/design.py"],
        }
        for key, paths in agent_to_files.items():
            if key in target_agent.lower():
                focus_paths.extend(paths)
                break

    codebase = await scan_codebase(focus_areas=focus_paths)
    if not codebase.get("success"):
        return {"success": False, "error": f"코드베이스 스캔 실패: {codebase.get('error')}"}

    log.info(f"[{session_id}] 스캔: {codebase['total_files']}개 파일")

    # Stage 3: 계획 수립
    plan = await create_plan(enriched_request, intent, codebase)
    if not plan.get("success"):
        return {"success": False, "error": f"계획 수립 실패: {plan.get('error')}"}

    log.info(f"[{session_id}] 계획: {len(plan.get('files', []))}개 파일 변경 예정")

    # 세션 저장
    session = {
        "id": session_id,
        "user_request": user_request,           # 원본 (포럼/UI 표시용)
        "enriched_request": enriched_request,   # 🆕 컨텍스트 포함 버전
        "requester": requester,
        "intent": intent,
        "codebase_summary": {
            "total_files": codebase["total_files"],
            "focused_paths": list(codebase.get("focused_contents", {}).keys()),
        },
        "plan": plan,
        "status": "plan_pending",
        "created_at": datetime.now().isoformat(),
        "total_cost": intent.get("cost", 0) + plan.get("cost", 0),
        "file_proposals": [],  # Stage 5에서 채워짐
    }
    _PLAN_SESSIONS[session_id] = session

    return {"success": True, "session": session}


# ═══════════════════════════════════════════════════════════════════
# Stage 5: 각 파일별 코드 생성 (계획 승인 후 실행)
# ═══════════════════════════════════════════════════════════════════

async def generate_code_for_session(session_id: str) -> dict:
    """승인된 계획대로 각 파일의 코드를 병렬 생성."""
    session = _PLAN_SESSIONS.get(session_id)
    if not session:
        return {"success": False, "error": "세션을 찾을 수 없음"}

    if session["status"] != "plan_approved":
        return {"success": False, "error": f"상태가 plan_approved 가 아님: {session['status']}"}

    session["status"] = "generating"
    files = session["plan"].get("files", [])

    # 안전 제한 — 동시 3개
    sem = asyncio.Semaphore(3)

    async def _gen(file_spec: dict) -> dict:
        async with sem:
            return await _generate_single_file(file_spec, session)

    results = await asyncio.gather(*[_gen(f) for f in files], return_exceptions=True)

    file_proposals = []
    total_cost = 0
    errors = []

    for file_spec, result in zip(files, results):
        if isinstance(result, Exception):
            errors.append(f"{file_spec['path']}: {result}")
            continue
        if not result.get("success"):
            errors.append(f"{file_spec['path']}: {result.get('error')}")
            continue
        file_proposals.append(result)
        total_cost += result.get("cost", 0)

    session["file_proposals"] = file_proposals
    session["total_cost"] += total_cost
    session["status"] = "code_pending"
    session["generation_errors"] = errors

    return {
        "success": True,
        "session_id": session_id,
        "file_proposals": file_proposals,
        "errors": errors,
    }


async def _generate_single_file(
    session_id: str,
    file_plan: dict,
    user_request: str,
) -> dict:
    """단일 파일에 대한 코드 생성."""
    path = file_plan.get("path", "")
    action = file_plan.get("action", "modify")
    instruction = file_plan.get("instruction", "")

    log.info(f"[{session_id}] 코드 생성 시작: {path} ({action})")

    if not path or not instruction:
        return {
            "success": False,
            "path": path,
            "error": "path 또는 instruction 누락",
        }

    # 기존 파일 내용 읽기 (modify인 경우)
    existing_content = ""
    if action == "modify":
        try:
            from utils.github_client import get_file_content
            file_info = await get_file_content(path)
            if file_info.get("success"):
                existing_content = file_info.get("content", "")
                log.info(
                    f"[{session_id}] 기존 파일 로드: {path} "
                    f"({len(existing_content):,}자)"
                )
            else:
                log.warning(
                    f"[{session_id}] 기존 파일 로드 실패: {path} "
                    f"({file_info.get('error', '?')})"
                )
                # 파일이 없으면 create로 전환
                action = "create"
        except Exception as e:
            log.warning(f"[{session_id}] 파일 읽기 예외 ({path}): {e}")
            action = "create"

    # 프롬프트 구성
    BACKTICKS_3 = "```"

    if action == "modify":
        prompt_parts = [
            "다음 파일을 수정해주세요.",
            "",
            "**경로**: " + path,
            "**요청**: " + user_request[:1000],
            "**지시사항**: " + instruction[:2000],
            "",
            "**현재 파일 내용**:",
            BACKTICKS_3 + "python",
            existing_content[:30000],
            BACKTICKS_3,
            "",
            "위 파일에 대한 **수정된 전체 코드**를 반환해주세요.",
            "반드시 다음 규칙을 따르세요:",
            "1. " + BACKTICKS_3 + "python ... " + BACKTICKS_3 + " 코드 블록 안에 전체 파일 내용을 작성",
            "2. import 누락 없도록 (typing.Optional, json 등 필요한 것 모두 import)",
            "3. 기존 기능을 모두 유지하며 요청만 반영",
            "4. syntax 오류 없이 컴파일 가능한 코드",
            "5. f-string 안에 백틱 3개 사용 금지 (변수로 분리)",
        ]
    else:  # create
        prompt_parts = [
            "다음 파일을 새로 생성해주세요.",
            "",
            "**경로**: " + path,
            "**요청**: " + user_request[:1000],
            "**지시사항**: " + instruction[:2000],
            "",
            "다음 규칙에 따라 새 파일을 생성해주세요:",
            "1. " + BACKTICKS_3 + "python ... " + BACKTICKS_3 + " 코드 블록 안에 전체 내용 작성",
            "2. 모든 필요한 import 포함",
            "3. syntax 오류 없이 컴파일 가능",
            "4. docstring 포함",
            "5. f-string 안에 백틱 3개 사용 금지",
        ]

    user_prompt = "\n".join(prompt_parts)

    # AI 호출
    try:
        from utils.openrouter_client import chat

        result = await chat(
            messages=[
                {"role": "system", "content": CODE_GENERATOR_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            agent="gaechyo",
            tier="premium",
            max_tokens=16000,
            temperature=0.2,
        )

        ai_response = result.get("content", "") or ""
        cost = float(result.get("cost", 0.0))

        log.info(
            f"[{session_id}] AI 응답: {len(ai_response):,}자, "
            f"비용 ${cost:.5f}"
        )

        if not ai_response.strip():
            return {
                "success": False,
                "path": path,
                "error": "AI 응답이 비어있음",
                "cost": cost,
            }

        # 코드 블록 추출
        new_content = _extract_code_block(ai_response)

        if not new_content:
            log.warning(
                f"[{session_id}] 코드 블록 추출 실패. AI 응답 처음 500자:\n"
                f"{ai_response[:500]}"
            )
            return {
                "success": False,
                "path": path,
                "error": (
                    "AI 응답에서 코드 블록을 찾을 수 없음. "
                    "응답 처음 500자: " + ai_response[:500]
                ),
                "cost": cost,
            }

        log.info(
            f"[{session_id}] 코드 블록 추출 성공: {path} "
            f"({len(new_content):,}자)"
        )

        # syntax 검증 (Python 파일만)
        if path.endswith(".py"):
            try:
                compile(new_content, path, "exec")
                log.info(f"[{session_id}] Syntax 검증 통과: {path}")
            except SyntaxError as e:
                log.error(
                    f"[{session_id}] AI 생성 코드 syntax 오류: {path} — "
                    f"Line {e.lineno}: {e.msg}"
                )
                return {
                    "success": False,
                    "path": path,
                    "error": (
                        "AI 생성 코드 syntax 오류 (Line " + str(e.lineno) +
                        "): " + str(e.msg)
                    ),
                    "cost": cost,
                    "new_content": new_content,  # 디버깅용 보존
                }

        # diff 생성
        diff_text = _make_diff(existing_content, new_content, path)
        lines_changed = len(diff_text.splitlines())

        # summary 추출 (AI 응답의 코드 블록 외 텍스트)
        summary = _extract_summary(ai_response)

        return {
            "success": True,
            "path": path,
            "action": action,
            "new_content": new_content,
            "diff": diff_text,
            "lines_changed": lines_changed,
            "summary": summary,
            "cost": cost,
        }

    except Exception as e:
        log.exception(f"[{session_id}] 코드 생성 예외 ({path}): {e}")
        return {
            "success": False,
            "path": path,
            "error": "예외: " + str(e),
            "cost": 0.0,
        }


def _extract_code_block(text: str) -> str:
    """
    AI 응답에서 코드 블록 추출.
    여러 패턴 지원:
    - ```python ... ```
    - ```py ... ```
    - ``` ... ```
    """
    import re

    if not text:
        return ""

    # 패턴 1: ```python ... ``` (가장 우선)
    patterns = [
        r"```python\s*\n(.*?)```",
        r"```py\s*\n(.*?)```",
        r"```\s*\n(.*?)```",
    ]

    for pattern in patterns:
        matches = re.findall(pattern, text, re.DOTALL)
        if matches:
            # 가장 긴 매치 선택 (보통 메인 코드가 가장 김)
            return max(matches, key=len).strip()

    # 패턴 매치 실패 시: 첫 ```부터 마지막 ```까지
    first_idx = text.find("```")
    if first_idx != -1:
        # 첫 ``` 다음 줄부터 시작
        start = text.find("\n", first_idx) + 1
        last_idx = text.rfind("```")
        if last_idx > start:
            extracted = text[start:last_idx].strip()
            if extracted:
                return extracted

    # 코드 블록 마커가 전혀 없으면 — AI가 코드만 반환했을 가능성
    # import나 def, class로 시작하면 코드로 간주
    lines = text.strip().splitlines()
    if lines:
        first_line = lines[0].strip()
        if first_line.startswith(("import ", "from ", "def ", "class ", "#", '"""')):
            return text.strip()

    return ""


def _extract_summary(text: str) -> str:
    """AI 응답에서 코드 블록 외 텍스트(설명) 추출."""
    import re

    if not text:
        return ""

    # 모든 코드 블록 제거
    cleaned = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    cleaned = cleaned.strip()

    # 처음 500자만 요약으로 사용
    return cleaned[:500] if cleaned else "(설명 없음)"


def _make_diff(old: str, new: str, path: str) -> str:
    """unified diff 생성."""
    import difflib

    old_lines = old.splitlines(keepends=True) if old else []
    new_lines = new.splitlines(keepends=True) if new else []

    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=path + " (before)",
        tofile=path + " (after)",
        lineterm="",
        n=3,
    )

    return "".join(diff)


# ═══════════════════════════════════════════════════════════════════
# Stage 7: GitHub PR 생성 (모든 파일을 단일 브랜치에 commit)
# ═══════════════════════════════════════════════════════════════════

async def apply_session_to_github(session_id: str) -> dict:
    """세션의 모든 파일 변경을 GitHub PR로 적용."""
    session = _PLAN_SESSIONS.get(session_id)
    if not session:
        return {"success": False, "error": "세션 없음"}

    if session["status"] != "code_approved":
        return {"success": False, "error": f"상태가 code_approved 아님: {session['status']}"}

    session["status"] = "applying"

    try:
        from utils.github_client import (
            create_branch, commit_file, create_pull_request, generate_branch_name,
        )

        # 1) 새 브랜치 생성
        branch_name = generate_branch_name(prefix="gaechyo/auto")
        branch_result = await create_branch(branch_name)
        if not branch_result["success"]:
            session["status"] = "failed"
            return {"success": False, "error": f"브랜치 생성 실패: {branch_result['error']}"}

        # 2) 각 파일 commit (순차적으로 — 같은 브랜치)
        commit_results = []
        for proposal in session["file_proposals"]:
            commit_msg = (
                f"[개쵸-auto] {proposal['path']}\n\n"
                f"세션: {session_id}\n"
                f"동작: {proposal['action']}\n"
                f"라인: {proposal['lines_changed']}"
            )
            commit_result = await commit_file(
                branch=branch_name,
                path=proposal["path"],
                new_content=proposal["new_content"],
                commit_message=commit_msg,
                existing_sha=proposal.get("file_sha") or None,
            )
            commit_results.append({
                "path": proposal["path"],
                "success": commit_result["success"],
                "error": commit_result.get("error"),
            })
            if not commit_result["success"]:
                log.warning(f"Commit 실패 ({proposal['path']}): {commit_result['error']}")

        success_count = sum(1 for r in commit_results if r["success"])

        # 3) PR 생성
        files_summary = "\n".join(
            f"- {'✅' if r['success'] else '❌'} `{r['path']}`"
            + (f" — {r['error'][:50]}" if r.get('error') else "")
            for r in commit_results
        )

        pr_body = (
            f"## 🤖 개쵸 자동 코드 변경\n\n"
            f"**요청**: {session['user_request']}\n\n"
            f"**의도**: {session['intent'].get('intent', '')}\n"
            f"**스코프**: {session['intent'].get('scope', '')}\n"
            f"**리스크**: {session['intent'].get('risk', '')}\n\n"
            f"### 📋 변경 계획 요약\n\n{session['plan'].get('plan_summary', '')}\n\n"
            f"### 📂 변경된 파일 ({success_count}/{len(commit_results)})\n\n{files_summary}\n\n"
            f"### 💰 비용\n\n${session['total_cost']:.5f}\n\n"
            f"---\n"
            f"🤖 자동 생성됨 — chip_bot 개쵸 시스템\n"
            f"**세션 ID**: `{session_id}`"
        )

        pr_result = await create_pull_request(
            head_branch=branch_name,
            title=f"[개쵸] {session['user_request'][:80]}",
            body=pr_body,
        )

        if not pr_result["success"]:
            session["status"] = "failed"
            return {"success": False, "error": f"PR 생성 실패: {pr_result['error']}"}

        session["status"] = "applied"
        session["branch"] = branch_name
        session["pr_url"] = pr_result["pr_url"]
        session["pr_number"] = pr_result["pr_number"]
        session["commit_results"] = commit_results

        return {
            "success": True,
            "branch": branch_name,
            "pr_url": pr_result["pr_url"],
            "pr_number": pr_result["pr_number"],
            "commits_succeeded": success_count,
            "commits_total": len(commit_results),
        }

    except Exception as e:
        log.exception(f"PR 적용 실패: {session_id}")
        session["status"] = "failed"
        return {"success": False, "error": f"적용 중 예외: {e}"}


# ═══════════════════════════════════════════════════════════════════
# 헬퍼
# ═══════════════════════════════════════════════════════════════════

def _extract_code_block(text: str) -> str:
    """응답에서 첫 코드 블록 추출."""
    pattern = re.compile(r"```(?:\w+)?\n(.*?)```", re.DOTALL)
    match = pattern.search(text)
    if match:
        return match.group(1).strip()
    return ""


def _extract_summary(text: str) -> str:
    """코드 블록 이후 요약."""
    pattern = re.compile(r"```.*?```\s*\n(.*)", re.DOTALL)
    match = pattern.search(text)
    if match:
        return match.group(1).strip()[:1000]
    return ""


def _calculate_diff(old: str, new: str, path: str) -> str:
    import difflib
    diff = difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        n=3,
    )
    return "".join(diff)


def _count_changed_lines(diff: str) -> int:
    count = 0
    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            count += 1
        elif line.startswith("-") and not line.startswith("---"):
            count += 1
    return count


# ═══════════════════════════════════════════════════════════════════
# 상태 변경 (Discord UI에서 호출)
# ═══════════════════════════════════════════════════════════════════

def approve_plan(session_id: str) -> bool:
    session = _PLAN_SESSIONS.get(session_id)
    if not session or session["status"] != "plan_pending":
        return False
    session["status"] = "plan_approved"
    return True


def approve_code(session_id: str) -> bool:
    session = _PLAN_SESSIONS.get(session_id)
    if not session or session["status"] != "code_pending":
        return False
    session["status"] = "code_approved"
    return True


def reject_session(session_id: str, stage: str, reason: str = "") -> bool:
    session = _PLAN_SESSIONS.get(session_id)
    if not session:
        return False
    session["status"] = f"rejected_at_{stage}"
    session["reject_reason"] = reason
    return True

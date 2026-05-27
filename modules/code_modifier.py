"""
modules/code_modifier.py
기쵸의 자동 코드 수정 시스템.

워크플로:
1. 변경 제안 생성 (AI)
2. Cho 승인 대기 (Discord 버튼)
3. GitHub PR 생성
4. 자동 머지 (선택)
"""

import asyncio
import difflib
import logging
import re
import uuid
from datetime import datetime
from typing import Optional

from utils.github_client import (
    get_file_content, create_branch, commit_file,
    create_pull_request, merge_pr, generate_branch_name,
)
from utils.openrouter_client import chat

log = logging.getLogger(__name__)

# 변경 가능 파일 패턴 (화이트리스트)
ALLOWED_PATHS = [
    re.compile(r"^modules/.*\.py$"),
    re.compile(r"^bot/.*\.py$"),
    re.compile(r"^utils/.*\.py$"),
    re.compile(r"^docs/.*\.md$"),
    re.compile(r"^requirements\.txt$"),
]

# 절대 변경 불가 (블랙리스트)
FORBIDDEN_PATHS = [
    re.compile(r"\.env"),
    re.compile(r"secrets?\."),
    re.compile(r"\.git/"),
    re.compile(r"^bot/main\.py$"),  # 진입점은 신중하게
]

# 변경 안전 제한
MAX_LINES_CHANGED = 200       # 한 번에 변경 가능한 최대 라인 수
MAX_FILES_PER_PROPOSAL = 5    # 한 제안서당 최대 파일 수

# 임시 저장소 (세션 ID → 제안 dict)
_PENDING_PROPOSALS: dict[str, dict] = {}


SYSTEM_PROMPT = """당신은 기쵸의 코드 수정 도우미입니다.
주어진 파일의 현재 내용과 요청을 보고, 변경된 파일 전체를 출력합니다.

규칙:
1. 응답은 변경된 파일의 **전체 내용**을 코드 블록으로 출력
2. 기존 들여쓰기/스타일 유지
3. 변경 사유를 주석으로 명시 (한국어 OK)
4. 안전한 변경만 (전역 변수 삭제·import 제거 등 위험한 행위 금지)
5. 변경 라인 수는 최소화

응답 형식:
```python
# 변경된 파일 전체 내용
...
```

이후 변경 요약을 3~5줄로 작성.
"""


# ═══════════════════════════════════════════════════════════════════
# 경로 검증
# ═══════════════════════════════════════════════════════════════════

def is_path_allowed(path: str) -> tuple[bool, str]:
    """파일 경로가 변경 가능한지 검증."""
    # 블랙리스트 우선
    for pattern in FORBIDDEN_PATHS:
        if pattern.search(path):
            return False, f"❌ 변경 금지 경로 ({pattern.pattern})"

    # 화이트리스트
    for pattern in ALLOWED_PATHS:
        if pattern.match(path):
            return True, "OK"

    return False, "❌ 화이트리스트에 없는 경로"


# ═══════════════════════════════════════════════════════════════════
# 변경안 생성
# ═══════════════════════════════════════════════════════════════════

async def generate_change_proposal(
    path: str,
    instruction: str,
    *,
    proposer: str = "기쵸",
) -> dict:
    """
    파일에 대한 변경안 생성.

    Returns:
        {
            "success": bool,
            "proposal_id": str,
            "path": str,
            "old_content": str,
            "new_content": str,
            "diff": str,
            "summary": str,
            "lines_changed": int,
            "error": str | None,
        }
    """
    # 1) 경로 검증
    allowed, reason = is_path_allowed(path)
    if not allowed:
        return {"success": False, "error": reason}

    # 2) 현재 파일 내용 가져오기
    file_result = await get_file_content(path)
    if not file_result["success"]:
        return {"success": False, "error": f"파일 로드 실패: {file_result['error']}"}

    old_content = file_result["content"]
    file_sha = file_result["sha"]

    # 3) AI에게 변경안 요청
    user_msg = (
        f"파일 경로: {path}\n\n"
        f"요청: {instruction}\n\n"
        f"--- 현재 파일 내용 ---\n"
        f"{old_content[:15000]}\n"
        f"--- 끝 ---\n\n"
        f"위 파일에 요청사항을 반영해 변경된 전체 파일을 출력해주세요."
    )

    try:
        result = await chat(
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            agent="gihyo",
            tier="premium",
            max_tokens=12000,
            temperature=0.3,
        )
        ai_response = result["content"]
    except Exception as e:
        return {"success": False, "error": f"AI 변경안 생성 실패: {e}"}

    # 4) 응답에서 코드 블록 추출
    new_content = _extract_code_block(ai_response)
    if not new_content:
        return {"success": False, "error": "AI 응답에서 코드 블록을 찾을 수 없음"}

    # 5) Diff 계산
    diff = _calculate_diff(old_content, new_content, path)
    lines_changed = _count_changed_lines(diff)

    # 6) 안전 제한 확인
    if lines_changed > MAX_LINES_CHANGED:
        return {
            "success": False,
            "error": (
                f"변경 라인 수 초과 ({lines_changed} > {MAX_LINES_CHANGED}). "
                "더 작은 단위로 나눠 요청해주세요."
            ),
        }

    # 7) 변경 요약 추출
    summary = _extract_summary(ai_response)

    # 8) 제안 저장
    proposal_id = str(uuid.uuid4())[:8]
    proposal = {
        "id": proposal_id,
        "path": path,
        "instruction": instruction,
        "old_content": old_content,
        "new_content": new_content,
        "diff": diff,
        "summary": summary,
        "lines_changed": lines_changed,
        "file_sha": file_sha,
        "proposer": proposer,
        "created_at": datetime.now().isoformat(),
        "status": "pending",
    }
    _PENDING_PROPOSALS[proposal_id] = proposal

    return {
        "success": True,
        **proposal,
        "error": None,
    }


def _extract_code_block(text: str) -> str:
    """응답에서 첫 번째 코드 블록 추출."""
    pattern = re.compile(r"```(?:\w+)?\n(.*?)```", re.DOTALL)
    match = pattern.search(text)
    if match:
        return match.group(1).strip()
    return ""


def _extract_summary(text: str) -> str:
    """코드 블록 이후의 요약 텍스트 추출."""
    pattern = re.compile(r"```.*?```\s*\n(.*)", re.DOTALL)
    match = pattern.search(text)
    if match:
        return match.group(1).strip()[:1000]
    return ""


def _calculate_diff(old: str, new: str, path: str) -> str:
    """unified diff 생성."""
    diff = difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        n=3,
    )
    return "".join(diff)


def _count_changed_lines(diff: str) -> int:
    """diff에서 변경된 라인 수 계산."""
    count = 0
    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            count += 1
        elif line.startswith("-") and not line.startswith("---"):
            count += 1
    return count


# ═══════════════════════════════════════════════════════════════════
# 제안 조회 / 승인 / 거부
# ═══════════════════════════════════════════════════════════════════

def get_proposal(proposal_id: str) -> Optional[dict]:
    return _PENDING_PROPOSALS.get(proposal_id)


def reject_proposal(proposal_id: str, reason: str = "") -> bool:
    proposal = _PENDING_PROPOSALS.get(proposal_id)
    if not proposal:
        return False
    proposal["status"] = "rejected"
    proposal["reject_reason"] = reason
    return True


async def approve_and_apply_proposal(
    proposal_id: str,
    *,
    auto_merge: bool = False,
) -> dict:
    """
    승인된 제안을 GitHub에 적용.

    Returns:
        {
            "success": bool,
            "branch": str,
            "pr_url": str,
            "pr_number": int,
            "merged": bool,
            "error": str | None,
        }
    """
    proposal = _PENDING_PROPOSALS.get(proposal_id)
    if not proposal:
        return {"success": False, "error": "제안을 찾을 수 없음 (만료되었거나 잘못된 ID)"}

    if proposal["status"] != "pending":
        return {"success": False, "error": f"제안 상태: {proposal['status']} (pending이어야 함)"}

    proposal["status"] = "applying"

    try:
        # 1) 새 브랜치 생성
        branch_name = generate_branch_name(prefix=f"gicho/auto")
        branch_result = await create_branch(branch_name)
        if not branch_result["success"]:
            proposal["status"] = "failed"
            return {"success": False, "error": f"브랜치 생성 실패: {branch_result['error']}"}

        # 2) 파일 commit
        commit_msg = (
            f"[기쵸-auto] {proposal['instruction'][:80]}\n\n"
            f"제안 ID: {proposal['id']}\n"
            f"변경 라인: {proposal['lines_changed']}\n"
            f"제안자: {proposal['proposer']}"
        )
        commit_result = await commit_file(
            branch=branch_name,
            path=proposal["path"],
            new_content=proposal["new_content"],
            commit_message=commit_msg,
            existing_sha=proposal["file_sha"],
        )
        if not commit_result["success"]:
            proposal["status"] = "failed"
            return {"success": False, "error": f"Commit 실패: {commit_result['error']}"}

        # 3) PR 생성
        pr_body = (
            f"## 🤖 기쵸 자동 코드 수정\n\n"
            f"**요청**: {proposal['instruction']}\n\n"
            f"**파일**: `{proposal['path']}`\n"
            f"**변경 라인**: {proposal['lines_changed']}줄\n"
            f"**제안 ID**: `{proposal['id']}`\n"
            f"**승인자**: Cho\n\n"
            f"### 변경 요약\n\n{proposal['summary'][:3000]}\n\n"
            f"### Diff\n\n```diff\n{proposal['diff'][:30000]}\n```\n\n"
            f"---\n"
            f"🤖 자동 생성됨 — chip_bot 기쵸 R&D 시스템"
        )
        pr_result = await create_pull_request(
            head_branch=branch_name,
            title=f"[기쵸-auto] {proposal['instruction'][:80]}",
            body=pr_body,
        )
        if not pr_result["success"]:
            proposal["status"] = "failed"
            return {"success": False, "error": f"PR 생성 실패: {pr_result['error']}"}

        # 4) 자동 머지 (선택)
        merged = False
        if auto_merge:
            await asyncio.sleep(2)  # PR이 안정화될 시간
            merge_result = await merge_pr(pr_result["pr_number"])
            merged = merge_result.get("success", False) and merge_result.get("merged", False)

        proposal["status"] = "applied"
        proposal["branch"] = branch_name
        proposal["pr_url"] = pr_result["pr_url"]
        proposal["pr_number"] = pr_result["pr_number"]
        proposal["merged"] = merged

        return {
            "success": True,
            "branch": branch_name,
            "pr_url": pr_result["pr_url"],
            "pr_number": pr_result["pr_number"],
            "merged": merged,
            "error": None,
        }

    except Exception as e:
        log.exception(f"제안 적용 실패: {proposal_id}")
        proposal["status"] = "failed"
        return {"success": False, "error": f"적용 중 예외: {e}"}


def list_recent_proposals(limit: int = 10) -> list[dict]:
    """최근 제안 목록."""
    items = sorted(
        _PENDING_PROPOSALS.values(),
        key=lambda p: p.get("created_at", ""),
        reverse=True,
    )
    return items[:limit]
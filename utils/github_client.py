"""
utils/github_client.py
GitHub API를 활용한 코드 읽기/쓰기/PR 관리.
"""

import base64
import logging
import os
from datetime import datetime
from typing import Optional

import aiohttp

log = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"


def _get_token() -> Optional[str]:
    """GitHub PAT 조회. 없으면 None."""
    token = os.getenv("GITHUB_TOKEN", "").strip()
    return token or None


def _get_repo_info() -> dict:
    """봇이 작동하는 레포 정보 (환경변수에서)."""
    return {
        "owner": os.getenv("GITHUB_REPO_OWNER", "sdyoucho"),
        "repo": os.getenv("GITHUB_REPO_NAME", "chip_bot"),
        "branch": os.getenv("GITHUB_DEFAULT_BRANCH", "main"),
    }


def _headers() -> dict:
    token = _get_token()
    h = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "cho-bot/1.0",
    }
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


# ═══════════════════════════════════════════════════════════════════
# 파일 읽기
# ═══════════════════════════════════════════════════════════════════

async def get_file_content(path: str, branch: Optional[str] = None) -> dict:
    """
    레포에서 특정 파일 내용 가져오기.

    Returns:
        {
            "success": bool,
            "content": str,       # 파일 본문
            "sha": str,           # 커밋용 SHA
            "size": int,
            "error": str | None,
        }
    """
    info = _get_repo_info()
    branch = branch or info["branch"]

    url = f"{GITHUB_API}/repos/{info['owner']}/{info['repo']}/contents/{path}?ref={branch}"

    timeout = aiohttp.ClientTimeout(total=15)
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=_headers()) as session:
            async with session.get(url) as resp:
                if resp.status == 404:
                    return {"success": False, "content": "", "sha": "", "size": 0,
                            "error": "파일을 찾을 수 없음"}
                if resp.status != 200:
                    return {"success": False, "content": "", "sha": "", "size": 0,
                            "error": f"GitHub API HTTP {resp.status}"}
                data = await resp.json()
    except Exception as e:
        return {"success": False, "content": "", "sha": "", "size": 0,
                "error": f"네트워크 오류: {e}"}

    if isinstance(data, list):
        return {"success": False, "content": "", "sha": "", "size": 0,
                "error": "디렉토리입니다 (파일 경로를 지정해주세요)"}

    try:
        content = base64.b64decode(data["content"]).decode("utf-8", errors="ignore")
    except Exception as e:
        return {"success": False, "content": "", "sha": "", "size": 0,
                "error": f"디코딩 실패: {e}"}

    return {
        "success": True,
        "content": content,
        "sha": data.get("sha", ""),
        "size": data.get("size", 0),
        "error": None,
    }


# ═══════════════════════════════════════════════════════════════════
# 브랜치 생성
# ═══════════════════════════════════════════════════════════════════

async def create_branch(branch_name: str, from_branch: Optional[str] = None) -> dict:
    """새 브랜치 생성. 기존 브랜치에서 분기."""
    info = _get_repo_info()
    from_branch = from_branch or info["branch"]

    # 1) 베이스 브랜치의 최신 SHA 조회
    timeout = aiohttp.ClientTimeout(total=15)
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=_headers()) as session:
            ref_url = f"{GITHUB_API}/repos/{info['owner']}/{info['repo']}/git/refs/heads/{from_branch}"
            async with session.get(ref_url) as resp:
                if resp.status != 200:
                    return {"success": False, "error": f"기준 브랜치 조회 실패: HTTP {resp.status}"}
                ref_data = await resp.json()
                base_sha = ref_data["object"]["sha"]

            # 2) 새 브랜치 생성
            create_url = f"{GITHUB_API}/repos/{info['owner']}/{info['repo']}/git/refs"
            payload = {
                "ref": f"refs/heads/{branch_name}",
                "sha": base_sha,
            }
            async with session.post(create_url, json=payload) as resp:
                if resp.status == 422:
                    return {"success": False, "error": "이미 존재하는 브랜치"}
                if resp.status != 201:
                    err_text = await resp.text()
                    return {"success": False, "error": f"브랜치 생성 실패: HTTP {resp.status} - {err_text[:200]}"}
                return {"success": True, "branch": branch_name, "base_sha": base_sha}
    except Exception as e:
        return {"success": False, "error": f"네트워크 오류: {e}"}


# ═══════════════════════════════════════════════════════════════════
# 파일 commit (생성/수정)
# ═══════════════════════════════════════════════════════════════════

async def commit_file(
    branch: str,
    path: str,
    new_content: str,
    commit_message: str,
    existing_sha: Optional[str] = None,
) -> dict:
    """
    파일 commit. existing_sha 있으면 update, 없으면 create.
    """
    info = _get_repo_info()

    url = f"{GITHUB_API}/repos/{info['owner']}/{info['repo']}/contents/{path}"

    payload = {
        "message": commit_message,
        "content": base64.b64encode(new_content.encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    if existing_sha:
        payload["sha"] = existing_sha

    timeout = aiohttp.ClientTimeout(total=30)
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=_headers()) as session:
            async with session.put(url, json=payload) as resp:
                if resp.status not in (200, 201):
                    err_text = await resp.text()
                    return {"success": False, "error": f"Commit 실패: HTTP {resp.status} - {err_text[:200]}"}
                data = await resp.json()
                return {
                    "success": True,
                    "commit_sha": data["commit"]["sha"],
                    "commit_url": data["commit"]["html_url"],
                }
    except Exception as e:
        return {"success": False, "error": f"네트워크 오류: {e}"}


# ═══════════════════════════════════════════════════════════════════
# PR 생성
# ═══════════════════════════════════════════════════════════════════

async def create_pull_request(
    head_branch: str,
    title: str,
    body: str,
    base_branch: Optional[str] = None,
) -> dict:
    """Pull Request 생성."""
    info = _get_repo_info()
    base_branch = base_branch or info["branch"]

    url = f"{GITHUB_API}/repos/{info['owner']}/{info['repo']}/pulls"
    payload = {
        "title": title[:256],
        "body": body[:65000],
        "head": head_branch,
        "base": base_branch,
    }

    timeout = aiohttp.ClientTimeout(total=20)
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=_headers()) as session:
            async with session.post(url, json=payload) as resp:
                if resp.status not in (200, 201):
                    err_text = await resp.text()
                    return {"success": False, "error": f"PR 생성 실패: HTTP {resp.status} - {err_text[:200]}"}
                data = await resp.json()
                return {
                    "success": True,
                    "pr_number": data["number"],
                    "pr_url": data["html_url"],
                    "pr_title": data["title"],
                }
    except Exception as e:
        return {"success": False, "error": f"네트워크 오류: {e}"}


# ═══════════════════════════════════════════════════════════════════
# PR 자동 머지
# ═══════════════════════════════════════════════════════════════════

async def merge_pr(pr_number: int, merge_method: str = "squash") -> dict:
    """PR 자동 머지."""
    info = _get_repo_info()
    url = f"{GITHUB_API}/repos/{info['owner']}/{info['repo']}/pulls/{pr_number}/merge"

    payload = {
        "merge_method": merge_method,  # "merge", "squash", "rebase"
    }

    timeout = aiohttp.ClientTimeout(total=30)
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=_headers()) as session:
            async with session.put(url, json=payload) as resp:
                if resp.status != 200:
                    err_text = await resp.text()
                    return {"success": False, "error": f"머지 실패: HTTP {resp.status} - {err_text[:200]}"}
                data = await resp.json()
                return {
                    "success": True,
                    "merged": data.get("merged", False),
                    "sha": data.get("sha", ""),
                }
    except Exception as e:
        return {"success": False, "error": f"네트워크 오류: {e}"}


def generate_branch_name(prefix: str = "gicho/auto") -> str:
    """자동 브랜치 이름 생성."""
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{prefix}-{timestamp}"
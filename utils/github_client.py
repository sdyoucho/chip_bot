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
    """GitHub API 헤더 (PAT 있으면 인증 추가)."""
    token = _get_token()
    h = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "cho-bot/1.0",
    }
    if token:
        h["Authorization"] = f"Bearer {token}"
        log.debug(f"GitHub API 호출 (인증됨: {token[:10]}...)")
    else:
        log.warning(
            "⚠️ GITHUB_TOKEN 미설정 — 익명 호출 (60 req/h, 쓰기 작업 불가)"
        )
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


# ═══════════════════════════════════════════════════════════════════
# 진단 함수 (디버깅용)
# ═══════════════════════════════════════════════════════════════════

async def diagnose_github_access() -> dict:
    """
    GitHub 인증 상태를 종합 진단.

    Returns:
        {
            "token_set": bool,
            "token_preview": str,         # 처음 10자만
            "token_valid": bool,
            "user_login": str,
            "rate_limit_remaining": int,
            "rate_limit_max": int,
            "repo_accessible": bool,
            "repo_full_name": str,
            "repo_permissions": dict,     # admin/push/pull
            "scopes": list[str],          # 토큰 권한 범위
            "issues": list[str],          # 발견된 문제
            "recommendations": list[str], # 권장사항
        }
    """
    result = {
        "token_set": False,
        "token_preview": "",
        "token_valid": False,
        "user_login": "",
        "rate_limit_remaining": 0,
        "rate_limit_max": 0,
        "repo_accessible": False,
        "repo_full_name": "",
        "repo_permissions": {},
        "scopes": [],
        "issues": [],
        "recommendations": [],
    }

    # 1) 토큰 존재 확인
    token = _get_token()
    if not token:
        result["issues"].append("❌ GITHUB_TOKEN 환경변수가 비어있음")
        result["recommendations"].append(
            "Discord에서 `/config_ai` 실행 → GitHub PAT 입력"
        )
        return result

    result["token_set"] = True
    result["token_preview"] = token[:10] + "..." if len(token) > 10 else token

    # 토큰 형식 검증
    if not (token.startswith("ghp_") or token.startswith("github_pat_")):
        result["issues"].append(
            f"⚠️ 토큰 형식이 비표준 (시작: {token[:10]})"
        )

    # 2) 토큰 유효성 확인 — /user 엔드포인트
    timeout = aiohttp.ClientTimeout(total=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=_headers()) as session:
            # /user 호출 (인증 필수)
            async with session.get(f"{GITHUB_API}/user") as resp:
                if resp.status == 401:
                    result["issues"].append("❌ 토큰이 유효하지 않음 (401 Unauthorized)")
                    result["recommendations"].append(
                        "GitHub Settings → Developer settings → "
                        "Personal access tokens에서 새 토큰 발급"
                    )
                    # rate limit은 익명으로도 확인 가능하니 계속 진행
                elif resp.status == 200:
                    user_data = await resp.json()
                    result["token_valid"] = True
                    result["user_login"] = user_data.get("login", "unknown")

                    # 토큰 스코프 추출
                    scopes_header = resp.headers.get("X-OAuth-Scopes", "")
                    if scopes_header:
                        result["scopes"] = [s.strip() for s in scopes_header.split(",") if s.strip()]

            # 3) Rate limit 확인
            async with session.get(f"{GITHUB_API}/rate_limit") as resp:
                if resp.status == 200:
                    rate_data = await resp.json()
                    core = rate_data.get("resources", {}).get("core", {})
                    result["rate_limit_remaining"] = core.get("remaining", 0)
                    result["rate_limit_max"] = core.get("limit", 60)

            # 4) 레포 접근 확인
            info = _get_repo_info()
            repo_url = f"{GITHUB_API}/repos/{info['owner']}/{info['repo']}"
            async with session.get(repo_url) as resp:
                if resp.status == 200:
                    repo_data = await resp.json()
                    result["repo_accessible"] = True
                    result["repo_full_name"] = repo_data.get("full_name", "")
                    result["repo_permissions"] = repo_data.get("permissions", {})
                elif resp.status == 404:
                    result["issues"].append(
                        f"❌ 레포 접근 불가: {info['owner']}/{info['repo']} "
                        "(존재하지 않거나 비공개 + 권한 없음)"
                    )
                    result["recommendations"].append(
                        "환경변수 GITHUB_REPO_OWNER, GITHUB_REPO_NAME 확인"
                    )
                elif resp.status == 401:
                    result["issues"].append("❌ 레포 접근 인증 실패")
    except asyncio.TimeoutError:
        result["issues"].append("❌ GitHub API 타임아웃 (네트워크 문제)")
    except Exception as e:
        result["issues"].append(f"❌ 예외: {e}")

    # 5) 권한 분석
    if result["token_valid"]:
        # 필요 스코프: 'repo' (private) 또는 'public_repo' (public only)
        needed = {"repo", "public_repo"}
        has_any = bool(set(result["scopes"]) & needed)

        if not result["scopes"]:
            result["issues"].append(
                "⚠️ 토큰 스코프를 확인할 수 없음 (Fine-grained PAT일 수 있음)"
            )
        elif not has_any:
            result["issues"].append(
                f"❌ 'repo' 또는 'public_repo' 스코프 없음 "
                f"(현재: {result['scopes']})"
            )
            result["recommendations"].append(
                "새 토큰 발급 시 'repo' 스코프 체크 필수"
            )

        # 레포 권한 확인
        perms = result["repo_permissions"]
        if perms and not perms.get("push", False):
            result["issues"].append(
                "❌ 레포에 push 권한 없음 (브랜치 생성 불가)"
            )
            result["recommendations"].append(
                "본인이 소유한 레포의 토큰을 사용하거나, "
                "Collaborator 권한 추가"
            )

    # Rate limit 경고
    if result["rate_limit_remaining"] < 10 and result["rate_limit_max"] > 60:
        result["issues"].append(
            f"⚠️ Rate limit 거의 소진 ({result['rate_limit_remaining']}/{result['rate_limit_max']})"
        )
    elif result["rate_limit_max"] == 60:
        result["issues"].append(
            "⚠️ Rate limit이 60 (익명 수준) — 토큰이 헤더에 실리지 않은 듯"
        )

    return result


# asyncio import 추가 (파일 상단에 이미 없다면)
import asyncio
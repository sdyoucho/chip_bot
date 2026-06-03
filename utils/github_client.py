"""
utils/github_client.py
GitHub API를 활용한 코드 읽기/쓰기/PR 관리.
"""

import asyncio
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

    timeout = aiohttp.ClientTimeout(total=15)
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=_headers()) as session:
            ref_url = f"{GITHUB_API}/repos/{info['owner']}/{info['repo']}/git/refs/heads/{from_branch}"
            async with session.get(ref_url) as resp:
                if resp.status != 200:
                    return {"success": False, "error": f"기준 브랜치 조회 실패: HTTP {resp.status}"}
                ref_data = await resp.json()
                base_sha = ref_data["object"]["sha"]

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
    """파일 commit. existing_sha 있으면 update, 없으면 create."""
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
# PR 상태 조회 (머지 전 사전 점검용)
# ═══════════════════════════════════════════════════════════════════

async def get_pull_request(pr_number: int) -> dict:
    """
    PR 정보 조회. 머지 가능 여부 사전 확인용.

    Returns:
        {
            "success": bool,
            "state": str,                # "open" | "closed"
            "merged": bool,
            "mergeable": bool | None,    # None이면 GitHub가 계산 중
            "mergeable_state": str,      # "clean" | "dirty" | "blocked" | "unstable" | "unknown"
            "head_sha": str,
            "title": str,
            "html_url": str,
            "error": str | None,
            "status_code": int,
        }
    """
    info = _get_repo_info()
    url = f"{GITHUB_API}/repos/{info['owner']}/{info['repo']}/pulls/{pr_number}"

    result: dict = {
        "success": False,
        "state": "",
        "merged": False,
        "mergeable": None,
        "mergeable_state": "unknown",
        "head_sha": "",
        "title": "",
        "html_url": "",
        "error": None,
        "status_code": 0,
    }

    timeout = aiohttp.ClientTimeout(total=15)
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=_headers()) as session:
            async with session.get(url) as resp:
                result["status_code"] = resp.status
                if resp.status == 404:
                    result["error"] = f"PR #{pr_number}을 찾을 수 없음"
                    return result
                if resp.status in (401, 403):
                    result["error"] = f"권한 부족 (HTTP {resp.status}) — 토큰 스코프 확인 필요"
                    return result
                if resp.status != 200:
                    err_text = await resp.text()
                    result["error"] = f"PR 조회 실패: HTTP {resp.status} - {err_text[:200]}"
                    return result

                try:
                    data = await resp.json()
                except Exception:
                    raw = await resp.text()
                    result["error"] = f"JSON 파싱 실패: {raw[:200]}"
                    return result

                result["success"] = True
                result["state"] = data.get("state", "")
                result["merged"] = data.get("merged", False)
                result["mergeable"] = data.get("mergeable")  # None 가능
                result["mergeable_state"] = data.get("mergeable_state", "unknown")
                result["head_sha"] = (data.get("head") or {}).get("sha", "")
                result["title"] = data.get("title", "")
                result["html_url"] = data.get("html_url", "")
                return result
    except asyncio.TimeoutError:
        result["error"] = "GitHub API 타임아웃"
        return result
    except aiohttp.ClientError as e:
        result["error"] = f"네트워크 오류: {e}"
        return result
    except Exception as e:
        result["error"] = f"예외: {e}"
        return result


# ═══════════════════════════════════════════════════════════════════
# PR 자동 머지
# ═══════════════════════════════════════════════════════════════════

async def merge_pr(
    pr_number: int,
    merge_method: str = "squash",
    precheck: bool = True,
) -> dict:
    """
    PR 자동 머지.

    Args:
        pr_number: PR 번호
        merge_method: "merge" | "squash" | "rebase"
        precheck: True면 머지 전 PR 상태(mergeable/mergeable_state) 사전 확인

    Returns:
        {
            "success": bool,         # API 호출+머지 성공 여부
            "merged": bool,          # 실제 머지되었는지
            "message": str,          # 사람이 읽을 메시지
            "sha": str | None,       # 머지 커밋 SHA
            "status_code": int,      # HTTP 상태 코드 (0이면 네트워크 오류)
        }
    """
    result: dict = {
        "success": False,
        "merged": False,
        "message": "",
        "sha": None,
        "status_code": 0,
    }

    if merge_method not in ("merge", "squash", "rebase"):
        result["message"] = f"잘못된 merge_method: {merge_method}"
        return result

    # ── 사전 점검 ──────────────────────────────────────────────
    if precheck:
        pre = await get_pull_request(pr_number)
        if not pre["success"]:
            result["status_code"] = pre.get("status_code", 0)
            result["message"] = f"PR 사전조회 실패: {pre.get('error', '알 수 없음')}"
            return result

        if pre["merged"]:
            result["success"] = True
            result["merged"] = True
            result["message"] = "이미 머지된 PR입니다"
            result["sha"] = pre.get("head_sha") or None
            result["status_code"] = 200
            return result

        if pre["state"] != "open":
            result["message"] = f"PR이 열려있지 않음 (state={pre['state']})"
            return result

        ms = pre["mergeable_state"]
        # GitHub가 mergeable 계산 중이면 mergeable=None → 그대로 시도
        if pre["mergeable"] is False:
            result["message"] = (
                f"머지 불가 (mergeable=False, state={ms}) — "
                "충돌이나 보호 규칙을 확인하세요"
            )
            return result
        if ms in ("dirty",):
            result["message"] = "머지 불가: 충돌(dirty)이 있습니다"
            return result
        if ms in ("blocked",):
            log.warning(f"PR #{pr_number}: mergeable_state=blocked (보호규칙) — 시도는 진행")

    # ── 실제 머지 호출 ─────────────────────────────────────────
    info = _get_repo_info()
    url = f"{GITHUB_API}/repos/{info['owner']}/{info['repo']}/pulls/{pr_number}/merge"
    payload = {"merge_method": merge_method}

    timeout = aiohttp.ClientTimeout(total=30)
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=_headers()) as session:
            async with session.put(url, json=payload) as resp:
                result["status_code"] = resp.status

                # JSON 파싱 (실패시 raw text)
                try:
                    data = await resp.json(content_type=None)
                    if not isinstance(data, dict):
                        data = {"_raw": data}
                except Exception:
                    raw = await resp.text()
                    data = {"_raw": raw}

                api_msg = data.get("message", "") if isinstance(data, dict) else ""

                if resp.status == 200:
                    result["success"] = True
                    result["merged"] = bool(data.get("merged", True))
                    result["sha"] = data.get("sha")
                    result["message"] = data.get("message") or "머지 성공"
                    return result

                if resp.status == 405:
                    result["message"] = (
                        f"머지 불가 (405 Method Not Allowed): "
                        f"충돌·체크 실패·보호 규칙 — {api_msg or '상세없음'}"
                    )
                    return result

                if resp.status == 409:
                    result["message"] = (
                        f"SHA 불일치 (409 Conflict): "
                        f"PR이 업데이트되었습니다 — {api_msg or '상세없음'}"
                    )
                    return result

                if resp.status in (401, 403):
                    result["message"] = (
                        f"권한 부족 (HTTP {resp.status}): "
                        f"GITHUB_TOKEN의 'repo' 스코프와 push 권한을 확인하세요 — {api_msg or ''}"
                    )
                    return result

                if resp.status == 404:
                    result["message"] = f"PR #{pr_number}을 찾을 수 없음"
                    return result

                if resp.status == 422:
                    result["message"] = (
                        f"유효성 실패 (422): merge_method/SHA 확인 — {api_msg or ''}"
                    )
                    return result

                # 기타
                raw_preview = ""
                if isinstance(data, dict):
                    raw_preview = str(data.get("_raw", "") or api_msg)[:200]
                result["message"] = f"머지 실패: HTTP {resp.status} - {raw_preview}"
                return result

    except asyncio.TimeoutError:
        result["message"] = "GitHub 머지 API 타임아웃"
        return result
    except aiohttp.ClientError as e:
        result["message"] = f"네트워크 오류: {e}"
        return result
    except Exception as e:
        log.exception(f"merge_pr 예외 (PR #{pr_number})")
        result["message"] = f"예외: {e}"
        return result


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
    result: dict = {
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
            async with session.get(f"{GITHUB_API}/user") as resp:
                if resp.status == 401:
                    result["issues"].append("❌ 토큰이 유효하지 않음 (401 Unauthorized)")
                    result["recommendations"].append(
                        "GitHub Settings → Developer settings → "
                        "Personal access tokens에서 새 토큰 발급"
                    )
                elif resp.status == 200:
                    user_data = await resp.json()
                    result["token_valid"] = True
                    result["user_login"] = user_data.get("login", "unknown")

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
            "⚠️ Rate limit이 60/h (익명 호출) — 토큰 인증 실패 가능성"
        )

    return result
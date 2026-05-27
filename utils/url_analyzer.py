"""
utils/url_analyzer.py
URL을 받아 콘텐츠를 가져와 분석하는 모듈.

🆕 v2 지원:
- 일반 웹페이지 (HTML)
- YouTube URL (제목·설명·태그)
- GitHub URL (repo / file / issue / PR)
- Twitter/X 게시물
- 뉴스 기사
- 블로그
"""

import asyncio
import base64
import logging
import os
import re
from typing import Optional
from urllib.parse import urlparse

import aiohttp

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
TIMEOUT = aiohttp.ClientTimeout(total=20)
MAX_CONTENT_LENGTH = 50000

# GitHub API
GITHUB_API = "https://api.github.com"
MAX_FILES_PER_REPO = 30   # repo 분석 시 최대 파일 수


# ═══════════════════════════════════════════════════════════════════
# 메인 API
# ═══════════════════════════════════════════════════════════════════

async def fetch_url_content(url: str) -> dict:
    """
    URL에서 콘텐츠를 가져와 정형화된 dict로 반환.

    Returns:
        {
            "url": str,
            "type": "webpage" | "youtube" | "github_repo" | "github_file" | "github_issue" | "twitter" | "error",
            "title": str,
            "description": str,
            "content": str,
            "metadata": dict,
            "error": str | None,
        }
    """
    if not _is_valid_url(url):
        return _error_response(url, "유효하지 않은 URL")

    url_type = _detect_url_type(url)

    try:
        if url_type == "youtube":
            return await _fetch_youtube(url)
        elif url_type == "github":
            return await _fetch_github(url)
        else:
            return await _fetch_webpage(url)
    except asyncio.TimeoutError:
        return _error_response(url, "타임아웃 — 20초 내 응답 없음")
    except aiohttp.ClientError as e:
        return _error_response(url, f"네트워크 오류: {e}")
    except Exception as e:
        log.exception(f"URL 분석 실패: {url}")
        return _error_response(url, f"분석 실패: {e}")


def extract_urls(text: str) -> list[str]:
    """문장에서 URL들을 추출."""
    pattern = re.compile(
        r"https?://[^\s<>\"'`]+",
        re.IGNORECASE,
    )
    return pattern.findall(text)


# ═══════════════════════════════════════════════════════════════════
# URL 타입 판별
# ═══════════════════════════════════════════════════════════════════

def _is_valid_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False


def _detect_url_type(url: str) -> str:
    domain = urlparse(url).netloc.lower()
    if "youtube.com" in domain or "youtu.be" in domain:
        return "youtube"
    if "github.com" in domain:
        return "github"
    if "twitter.com" in domain or "x.com" in domain:
        return "twitter"
    return "webpage"


def _error_response(url: str, error: str) -> dict:
    return {
        "url": url,
        "type": "error",
        "title": "",
        "description": "",
        "content": "",
        "metadata": {},
        "error": error,
    }


# ═══════════════════════════════════════════════════════════════════
# GitHub 수집 (REST API)
# ═══════════════════════════════════════════════════════════════════

def _github_headers() -> dict:
    """GitHub API 헤더 (PAT 있으면 인증 추가)."""
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "cho-bot/1.0",
    }
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _parse_github_url(url: str) -> dict:
    """
    GitHub URL을 분해. 지원 패턴:
    - https://github.com/owner/repo
    - https://github.com/owner/repo/blob/branch/path/to/file
    - https://github.com/owner/repo/tree/branch/path
    - https://github.com/owner/repo/issues/123
    - https://github.com/owner/repo/pull/123
    """
    pattern = re.compile(
        r"github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)"
        r"(?:/(?P<kind>blob|tree|issues|pull|commits))?"
        r"(?:/(?P<rest>.+))?$",
        re.IGNORECASE,
    )
    m = pattern.search(url.rstrip("/"))
    if not m:
        return {}

    owner = m.group("owner")
    repo = m.group("repo").replace(".git", "")
    kind = m.group("kind") or "repo"
    rest = m.group("rest") or ""

    info = {"owner": owner, "repo": repo, "kind": kind}

    if kind == "blob" or kind == "tree":
        parts = rest.split("/", 1)
        info["branch"] = parts[0] if parts else "main"
        info["path"] = parts[1] if len(parts) > 1 else ""
    elif kind in ("issues", "pull"):
        info["number"] = rest.split("/")[0] if rest else ""
    elif kind == "commits":
        info["sha"] = rest.split("/")[0] if rest else ""

    return info


async def _fetch_github(url: str) -> dict:
    """GitHub URL 분석 — 타입별 분기."""
    parsed = _parse_github_url(url)
    if not parsed:
        return _error_response(url, "GitHub URL 파싱 실패")

    kind = parsed.get("kind", "repo")

    try:
        if kind == "repo":
            return await _fetch_github_repo(url, parsed)
        elif kind == "blob":
            return await _fetch_github_file(url, parsed)
        elif kind == "tree":
            return await _fetch_github_tree(url, parsed)
        elif kind == "issues":
            return await _fetch_github_issue(url, parsed)
        elif kind == "pull":
            return await _fetch_github_pr(url, parsed)
        else:
            return await _fetch_github_repo(url, parsed)
    except Exception as e:
        log.exception(f"GitHub fetch 실패: {url}")
        return _error_response(url, f"GitHub 분석 실패: {e}")


async def _fetch_github_repo(url: str, parsed: dict) -> dict:
    """레포 기본 정보 + README + 디렉토리 구조 수집."""
    owner = parsed["owner"]
    repo = parsed["repo"]

    async with aiohttp.ClientSession(timeout=TIMEOUT, headers=_github_headers()) as session:
        # 1) 레포 정보
        repo_url = f"{GITHUB_API}/repos/{owner}/{repo}"
        async with session.get(repo_url) as resp:
            if resp.status == 404:
                return _error_response(url, "GitHub 레포를 찾을 수 없음 (비공개이거나 존재하지 않음)")
            if resp.status == 403:
                return _error_response(url, "GitHub API rate limit 초과 — GITHUB_TOKEN 설정 권장")
            if resp.status != 200:
                return _error_response(url, f"GitHub API HTTP {resp.status}")
            repo_info = await resp.json()

        # 2) README
        readme_content = ""
        try:
            async with session.get(f"{GITHUB_API}/repos/{owner}/{repo}/readme") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    readme_content = base64.b64decode(data["content"]).decode("utf-8", errors="ignore")
        except Exception as e:
            log.warning(f"README fetch 실패: {e}")

        # 3) 디렉토리 트리 (depth 1)
        tree_text = ""
        try:
            default_branch = repo_info.get("default_branch", "main")
            tree_url = f"{GITHUB_API}/repos/{owner}/{repo}/git/trees/{default_branch}?recursive=1"
            async with session.get(tree_url) as resp:
                if resp.status == 200:
                    tree_data = await resp.json()
                    items = tree_data.get("tree", [])[:MAX_FILES_PER_REPO * 2]
                    # 파일과 폴더 분리
                    files = [t["path"] for t in items if t["type"] == "blob"][:MAX_FILES_PER_REPO]
                    dirs = [t["path"] for t in items if t["type"] == "tree"][:15]
                    tree_text = (
                        f"\n📁 디렉토리 ({len(dirs)}개 표시):\n"
                        + "\n".join(f"  • {d}/" for d in dirs)
                        + f"\n\n📄 파일 ({len(files)}개 표시):\n"
                        + "\n".join(f"  • {f}" for f in files)
                    )
                    if tree_data.get("truncated"):
                        tree_text += "\n\n⚠️ (전체 트리가 잘림 — 일부만 표시)"
        except Exception as e:
            log.warning(f"트리 fetch 실패: {e}")

    # 본문 구성
    description_text = repo_info.get("description", "")
    stars = repo_info.get("stargazers_count", 0)
    forks = repo_info.get("forks_count", 0)
    language = repo_info.get("language", "Unknown")
    topics = repo_info.get("topics", [])

    content_parts = [
        f"# {owner}/{repo}",
        f"\n**설명**: {description_text or '(설명 없음)'}",
        f"**주 언어**: {language}",
        f"**⭐ Stars**: {stars:,} · **🍴 Forks**: {forks:,}",
    ]
    if topics:
        content_parts.append(f"**주제**: {', '.join(topics)}")
    if tree_text:
        content_parts.append(f"\n## 구조{tree_text}")
    if readme_content:
        content_parts.append(f"\n## README\n\n{readme_content[:15000]}")

    content = "\n".join(content_parts)

    return {
        "url": url,
        "type": "github_repo",
        "title": f"{owner}/{repo}",
        "description": description_text or "GitHub 저장소",
        "content": content[:MAX_CONTENT_LENGTH],
        "metadata": {
            "owner": owner,
            "repo": repo,
            "stars": stars,
            "forks": forks,
            "language": language,
            "topics": topics,
            "default_branch": repo_info.get("default_branch", "main"),
            "license": repo_info.get("license", {}).get("name") if repo_info.get("license") else None,
            "created_at": repo_info.get("created_at"),
            "updated_at": repo_info.get("updated_at"),
        },
        "error": None,
    }


async def _fetch_github_file(url: str, parsed: dict) -> dict:
    """특정 파일 내용 수집."""
    owner = parsed["owner"]
    repo = parsed["repo"]
    branch = parsed.get("branch", "main")
    path = parsed.get("path", "")

    if not path:
        return await _fetch_github_repo(url, parsed)

    async with aiohttp.ClientSession(timeout=TIMEOUT, headers=_github_headers()) as session:
        api_url = f"{GITHUB_API}/repos/{owner}/{repo}/contents/{path}?ref={branch}"
        async with session.get(api_url) as resp:
            if resp.status == 404:
                return _error_response(url, "파일을 찾을 수 없음")
            if resp.status != 200:
                return _error_response(url, f"GitHub API HTTP {resp.status}")
            data = await resp.json()

    if isinstance(data, list):
        # 디렉토리였음
        return await _fetch_github_tree(url, parsed)

    encoding = data.get("encoding", "base64")
    if encoding != "base64":
        return _error_response(url, f"지원하지 않는 인코딩: {encoding}")

    try:
        file_content = base64.b64decode(data["content"]).decode("utf-8", errors="ignore")
    except Exception as e:
        return _error_response(url, f"파일 디코딩 실패: {e}")

    file_size = data.get("size", 0)
    file_ext = path.split(".")[-1] if "." in path else ""

    # 코드 파일이면 마크다운 코드 블록으로 감싸기
    code_extensions = {
        "py", "js", "ts", "jsx", "tsx", "java", "go", "rs", "c", "cpp", "h",
        "rb", "php", "swift", "kt", "scala", "sh", "yaml", "yml", "json",
        "html", "css", "scss", "vue", "svelte",
    }
    if file_ext.lower() in code_extensions:
        lang = file_ext.lower()
        formatted_content = f"```{lang}\n{file_content[:20000]}\n```"
    else:
        formatted_content = file_content[:20000]

    return {
        "url": url,
        "type": "github_file",
        "title": f"{owner}/{repo} — {path}",
        "description": f"{path} ({file_size:,} bytes)",
        "content": formatted_content,
        "metadata": {
            "owner": owner,
            "repo": repo,
            "branch": branch,
            "path": path,
            "size": file_size,
            "file_extension": file_ext,
            "sha": data.get("sha"),
        },
        "error": None,
    }


async def _fetch_github_tree(url: str, parsed: dict) -> dict:
    """디렉토리 내용 수집."""
    owner = parsed["owner"]
    repo = parsed["repo"]
    branch = parsed.get("branch", "main")
    path = parsed.get("path", "")

    async with aiohttp.ClientSession(timeout=TIMEOUT, headers=_github_headers()) as session:
        api_url = f"{GITHUB_API}/repos/{owner}/{repo}/contents/{path}?ref={branch}"
        async with session.get(api_url) as resp:
            if resp.status != 200:
                return _error_response(url, f"디렉토리 fetch 실패: HTTP {resp.status}")
            items = await resp.json()

    if not isinstance(items, list):
        # 파일이었음
        return await _fetch_github_file(url, parsed)

    files = [i for i in items if i["type"] == "file"]
    dirs = [i for i in items if i["type"] == "dir"]

    content_lines = [
        f"# {owner}/{repo}/{path}",
        f"\n**브랜치**: {branch}\n",
        f"## 📁 디렉토리 ({len(dirs)}개)",
    ]
    for d in dirs[:30]:
        content_lines.append(f"  • {d['name']}/")

    content_lines.append(f"\n## 📄 파일 ({len(files)}개)")
    for f in files[:50]:
        size = f.get("size", 0)
        content_lines.append(f"  • {f['name']} ({size:,} bytes)")

    return {
        "url": url,
        "type": "github_tree",
        "title": f"{owner}/{repo}/{path}",
        "description": f"디렉토리 ({len(dirs)}개 폴더, {len(files)}개 파일)",
        "content": "\n".join(content_lines)[:MAX_CONTENT_LENGTH],
        "metadata": {
            "owner": owner,
            "repo": repo,
            "branch": branch,
            "path": path,
            "dir_count": len(dirs),
            "file_count": len(files),
        },
        "error": None,
    }


async def _fetch_github_issue(url: str, parsed: dict) -> dict:
    """Issue 내용 수집."""
    owner = parsed["owner"]
    repo = parsed["repo"]
    number = parsed.get("number")

    if not number:
        return _error_response(url, "Issue 번호 추출 실패")

    async with aiohttp.ClientSession(timeout=TIMEOUT, headers=_github_headers()) as session:
        api_url = f"{GITHUB_API}/repos/{owner}/{repo}/issues/{number}"
        async with session.get(api_url) as resp:
            if resp.status != 200:
                return _error_response(url, f"Issue fetch 실패: HTTP {resp.status}")
            issue = await resp.json()

        # 코멘트도 가져오기 (최대 10개)
        comments = []
        try:
            comments_url = f"{api_url}/comments?per_page=10"
            async with session.get(comments_url) as resp:
                if resp.status == 200:
                    comments = await resp.json()
        except Exception:
            pass

    title = issue.get("title", "")
    body = issue.get("body", "") or ""
    state = issue.get("state", "open")
    author = issue.get("user", {}).get("login", "unknown")
    labels = [l["name"] for l in issue.get("labels", [])]

    content_parts = [
        f"# Issue #{number}: {title}",
        f"\n**상태**: {state} | **작성자**: {author}",
    ]
    if labels:
        content_parts.append(f"**라벨**: {', '.join(labels)}")
    content_parts.append(f"\n## 내용\n\n{body[:10000]}")

    if comments:
        content_parts.append(f"\n## 💬 코멘트 ({len(comments)}개)")
        for c in comments[:5]:
            c_author = c.get("user", {}).get("login", "?")
            c_body = c.get("body", "")[:1000]
            content_parts.append(f"\n**{c_author}**:\n{c_body}")

    return {
        "url": url,
        "type": "github_issue",
        "title": f"#{number} {title}",
        "description": f"{owner}/{repo} Issue #{number}",
        "content": "\n".join(content_parts)[:MAX_CONTENT_LENGTH],
        "metadata": {
            "owner": owner,
            "repo": repo,
            "number": number,
            "state": state,
            "author": author,
            "labels": labels,
            "created_at": issue.get("created_at"),
            "comments_count": issue.get("comments", 0),
        },
        "error": None,
    }


async def _fetch_github_pr(url: str, parsed: dict) -> dict:
    """Pull Request 내용 + diff 요약."""
    owner = parsed["owner"]
    repo = parsed["repo"]
    number = parsed.get("number")

    if not number:
        return _error_response(url, "PR 번호 추출 실패")

    async with aiohttp.ClientSession(timeout=TIMEOUT, headers=_github_headers()) as session:
        # PR 정보
        api_url = f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{number}"
        async with session.get(api_url) as resp:
            if resp.status != 200:
                # PR이 아니라 issue 형태일 수 있음
                return await _fetch_github_issue(url, parsed)
            pr = await resp.json()

        # 변경된 파일 목록
        files_changed = []
        try:
            files_url = f"{api_url}/files?per_page=30"
            async with session.get(files_url) as resp:
                if resp.status == 200:
                    files_changed = await resp.json()
        except Exception:
            pass

    title = pr.get("title", "")
    body = pr.get("body", "") or ""
    state = pr.get("state", "open")
    author = pr.get("user", {}).get("login", "unknown")
    merged = pr.get("merged", False)
    additions = pr.get("additions", 0)
    deletions = pr.get("deletions", 0)

    content_parts = [
        f"# PR #{number}: {title}",
        f"\n**상태**: {'merged' if merged else state} | **작성자**: {author}",
        f"**변경**: +{additions} / -{deletions} (파일 {len(files_changed)}개)",
    ]
    content_parts.append(f"\n## 설명\n\n{body[:8000]}")

    if files_changed:
        content_parts.append(f"\n## 📂 변경된 파일")
        for f in files_changed[:20]:
            content_parts.append(
                f"  • `{f['filename']}` (+{f.get('additions', 0)} / -{f.get('deletions', 0)})"
            )

    return {
        "url": url,
        "type": "github_pr",
        "title": f"#{number} {title}",
        "description": f"{owner}/{repo} PR #{number}",
        "content": "\n".join(content_parts)[:MAX_CONTENT_LENGTH],
        "metadata": {
            "owner": owner,
            "repo": repo,
            "number": number,
            "state": state,
            "merged": merged,
            "author": author,
            "additions": additions,
            "deletions": deletions,
            "files_changed": len(files_changed),
        },
        "error": None,
    }


# ═══════════════════════════════════════════════════════════════════
# 일반 웹페이지
# ═══════════════════════════════════════════════════════════════════

async def _fetch_webpage(url: str) -> dict:
    """일반 웹페이지를 가져와 본문 추출."""
    async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
        headers = {"User-Agent": USER_AGENT}
        async with session.get(url, headers=headers, allow_redirects=True) as resp:
            if resp.status != 200:
                return _error_response(url, f"HTTP {resp.status}")

            content_type = resp.headers.get("Content-Type", "")
            if "text/html" not in content_type:
                return _error_response(url, f"비-HTML 콘텐츠: {content_type}")

            html = await resp.text()

    return _parse_html(url, html)


def _parse_html(url: str, html: str) -> dict:
    """HTML에서 메타데이터 + 본문 추출."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return _error_response(url, "BeautifulSoup 미설치")

    soup = BeautifulSoup(html, "html.parser")

    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    og_title = soup.find("meta", {"property": "og:title"})
    if og_title and og_title.get("content"):
        title = og_title["content"].strip()

    description = ""
    desc_meta = soup.find("meta", {"name": "description"})
    if desc_meta and desc_meta.get("content"):
        description = desc_meta["content"].strip()
    og_desc = soup.find("meta", {"property": "og:description"})
    if og_desc and og_desc.get("content"):
        description = og_desc["content"].strip()

    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "iframe", "noscript"]):
        tag.decompose()

    article = soup.find("article")
    if article:
        body_text = article.get_text(separator="\n", strip=True)
    else:
        main = soup.find("main") or soup.find("body")
        body_text = main.get_text(separator="\n", strip=True) if main else ""

    body_text = re.sub(r"\n{3,}", "\n\n", body_text)
    body_text = body_text[:MAX_CONTENT_LENGTH]

    metadata = {
        "domain": urlparse(url).netloc,
        "length": len(body_text),
    }

    author_meta = (
        soup.find("meta", {"name": "author"}) or
        soup.find("meta", {"property": "article:author"})
    )
    if author_meta and author_meta.get("content"):
        metadata["author"] = author_meta["content"].strip()

    date_meta = (
        soup.find("meta", {"property": "article:published_time"}) or
        soup.find("meta", {"name": "date"})
    )
    if date_meta and date_meta.get("content"):
        metadata["published"] = date_meta["content"].strip()

    return {
        "url": url,
        "type": "webpage",
        "title": title[:500],
        "description": description[:1000],
        "content": body_text,
        "metadata": metadata,
        "error": None,
    }


# ═══════════════════════════════════════════════════════════════════
# YouTube
# ═══════════════════════════════════════════════════════════════════

async def _fetch_youtube(url: str) -> dict:
    oembed_url = f"https://www.youtube.com/oembed?url={url}&format=json"

    async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
        try:
            async with session.get(oembed_url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    title = data.get("title", "")
                    author = data.get("author_name", "")
                else:
                    title = author = ""
        except Exception:
            title = author = ""

        headers = {"User-Agent": USER_AGENT}
        try:
            async with session.get(url, headers=headers) as resp:
                html = await resp.text() if resp.status == 200 else ""
        except Exception:
            html = ""

    description = _extract_youtube_description(html)
    video_id = _extract_youtube_video_id(url)

    return {
        "url": url,
        "type": "youtube",
        "title": title,
        "description": description[:2000],
        "content": description[:MAX_CONTENT_LENGTH],
        "metadata": {
            "video_id": video_id,
            "author": author,
            "thumbnail": f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg" if video_id else "",
        },
        "error": None,
    }


def _extract_youtube_video_id(url: str) -> str:
    patterns = [
        r"(?:v=|/v/|youtu\.be/|/embed/)([a-zA-Z0-9_-]{11})",
        r"youtube\.com/shorts/([a-zA-Z0-9_-]{11})",
    ]
    for pattern in patterns:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return ""


def _extract_youtube_description(html: str) -> str:
    if not html:
        return ""

    m = re.search(r'<meta name="description" content="([^"]+)"', html)
    if m:
        return m.group(1).replace("&quot;", '"').replace("&amp;", "&")

    m = re.search(r'"shortDescription":"([^"]+)"', html)
    if m:
        return m.group(1).encode().decode("unicode_escape", errors="ignore")

    return ""
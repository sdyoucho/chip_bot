"""
modules/code_publisher.py
개쵸 코드 변경을 R&D 포럼 채널에 자동 게시.

🆕 v3 변경:
- utils/message_splitter.py 기능 100% 활용
- send_long_text(), send_long_embed() 사용
- smart_split_text()로 마크다운/코드블록 보호 분할
- 거대 diff는 .md/.diff 파일 자동 첨부
"""

import io
import logging
import os
from datetime import datetime
from typing import Optional

import discord

from utils.message_splitter import (
    smart_split_text,
    SAFE_CHUNK_LENGTH,
    ATTACH_FILE_THRESHOLD,
    send_long_text,
    send_long_embed,
)

log = logging.getLogger(__name__)

# 게시 길이 임계치
MAX_THREAD_NAME = 100
MAX_DIFF_INLINE = 1500       # 이 이상이면 파일/Gist
MAX_DIFF_GIST = 80000        # 이 이상이면 Gist


# ═══════════════════════════════════════════════════════════════════
# 채널 조회
# ═══════════════════════════════════════════════════════════════════

def _get_rnd_forum_channel(bot: discord.Client) -> Optional[discord.ForumChannel]:
    """R&D 포럼 채널 조회."""
    ch_id_str = os.getenv("RND_FORUM_CHANNEL_ID", "").strip()
    if not ch_id_str.isdigit():
        return None
    channel = bot.get_channel(int(ch_id_str))
    if not isinstance(channel, discord.ForumChannel):
        log.warning(f"RND_FORUM_CHANNEL_ID가 포럼 채널이 아님: {type(channel).__name__}")
        return None
    return channel


# ═══════════════════════════════════════════════════════════════════
# GitHub Gist 업로드 (매우 큰 파일용)
# ═══════════════════════════════════════════════════════════════════

async def _upload_to_gist(
    filename: str,
    content: str,
    description: str = "chip_bot auto code change",
) -> Optional[str]:
    """매우 긴 콘텐츠를 GitHub Gist로 업로드."""
    try:
        import aiohttp
        from utils.github_client import _get_token

        token = _get_token()
        if not token:
            log.warning("Gist 업로드 불가 — GitHub Token 없음")
            return None

        url = "https://api.github.com/gists"
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "cho-bot/1.0",
        }
        payload = {
            "description": description[:200],
            "public": False,
            "files": {filename: {"content": content[:1_000_000]}},
        }

        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.post(url, json=payload) as resp:
                if resp.status != 201:
                    log.warning(f"Gist 업로드 실패: HTTP {resp.status}")
                    return None
                data = await resp.json()
                return data.get("html_url")
    except Exception as e:
        log.warning(f"Gist 업로드 예외: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════
# 메인 API
# ═══════════════════════════════════════════════════════════════════

async def publish_code_session(
    bot: discord.Client,
    *,
    session: dict,
    pr_result: dict,
) -> bool:
    """코드 변경 세션을 R&D 포럼 스레드로 게시."""
    channel = _get_rnd_forum_channel(bot)
    if not channel:
        log.info("R&D 포럼 채널 미설정 — 게시 건너뜀")
        return False

    try:
        thread_name = _build_thread_name(session, pr_result)
        first_message = _build_first_message(session, pr_result)

        # 첫 메시지가 2,000자 초과해도 thread.create는 한 번에 보낼 수 없으니
        # 안전한 길이로 잘라서 시작 후 나머지는 추가 전송
        first_message_short = first_message[:1900]
        first_message_rest = first_message[1900:] if len(first_message) > 1900 else ""

        # 스레드 생성
        thread, _ = await channel.create_thread(
            name=thread_name,
            content=first_message_short,
            auto_archive_duration=10080,  # 7일
        )

        # 첫 메시지의 나머지가 있으면 추가 게시
        if first_message_rest:
            await send_long_text(thread, first_message_rest)

        # 각 파일 게시
        await _post_file_changes(thread, session)

        # 푸터 게시
        await _post_footer(thread, pr_result)

        log.info(f"R&D 포럼 게시 완료: {thread.id} ({thread_name})")
        return True

    except discord.Forbidden:
        log.error("R&D 포럼 채널 권한 없음 (Create Public Threads 필요)")
        return False
    except Exception as e:
        log.exception(f"R&D 포럼 게시 실패: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════
# 스레드 이름/메시지 빌더
# ═══════════════════════════════════════════════════════════════════

def _build_thread_name(session: dict, pr_result: dict) -> str:
    intent = session.get("intent", {}).get("intent", "")
    request = session.get("user_request", "")
    pr_num = pr_result.get("pr_number", "?")

    title_text = intent or request or "코드 변경"
    name = f"[개쵸 #PR{pr_num}] {title_text}"
    return name[:MAX_THREAD_NAME]


def _build_first_message(session: dict, pr_result: dict) -> str:
    """포럼 스레드 첫 메시지 (전체 요약)."""
    intent = session.get("intent", {})
    plan = session.get("plan", {})
    proposals = session.get("file_proposals", [])

    lines = [
        f"# 🤖 개쵸 자동 코드 변경",
        "",
        f"**📅 발행 시각**: {datetime.now():%Y-%m-%d %H:%M KST}",
        f"**🔗 PR**: [#{pr_result.get('pr_number', '?')}]({pr_result.get('pr_url', '')})",
        f"**🌿 브랜치**: `{pr_result.get('branch', '')}`",
        f"**📦 커밋**: {pr_result.get('commits_succeeded', 0)}/{pr_result.get('commits_total', 0)}",
        f"**💰 비용**: ${session.get('total_cost', 0):.5f}",
        "",
        "## 📌 요청",
        f"> {session.get('user_request', '')[:500]}",
        "",
        "## 💡 의도 분석",
        f"- **의도**: {intent.get('intent', '')[:200]}",
        f"- **스코프**: `{intent.get('scope', '?')}`",
        f"- **리스크**: `{intent.get('risk', '?')}`",
        f"- **대상**: `{intent.get('target_agent', '미지정')}`",
        "",
        "## 📋 변경 계획 요약",
        plan.get("plan_summary", "(요약 없음)")[:800],
        "",
        f"## 📂 변경 파일 ({len(proposals)}개)",
    ]

    for p in proposals[:15]:
        emoji = "🆕" if p.get("action") == "create" else "✏️"
        lines.append(f"- {emoji} `{p['path']}` ({p.get('lines_changed', 0)}줄)")

    if len(proposals) > 15:
        lines.append(f"- ... 외 {len(proposals) - 15}개")

    deps = plan.get("requires_dependencies", [])
    if deps:
        lines.append("")
        lines.append("## 📦 추가 패키지")
        for d in deps:
            lines.append(f"- `{d}`")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# 파일별 게시 (message_splitter 활용)
# ═══════════════════════════════════════════════════════════════════

async def _post_file_changes(thread: discord.Thread, session: dict) -> None:
    """각 변경 파일의 상세 내용을 안전하게 게시."""
    proposals = session.get("file_proposals", [])

    for i, p in enumerate(proposals, 1):
        try:
            await _post_single_file(thread, p, i, len(proposals))
        except Exception as e:
            log.exception(f"파일 {p['path']} 게시 중 예외: {e}")
            try:
                await send_long_text(
                    thread,
                    f"⚠️ `{p['path']}` 게시 중 오류: {str(e)[:200]}\n"
                    f"GitHub PR에서 직접 확인해주세요.",
                )
            except Exception:
                pass


async def _post_single_file(
    thread: discord.Thread,
    proposal: dict,
    idx: int,
    total: int,
) -> None:
    """파일 한 개의 변경 내용 게시 — message_splitter로 길이 무관 처리."""
    path = proposal["path"]
    diff = proposal.get("diff", "") or ""
    new_content = proposal.get("new_content", "")
    summary = proposal.get("summary", "").strip()
    action_emoji = "🆕" if proposal.get("action") == "create" else "✏️"
    action_text = "신규 파일" if proposal.get("action") == "create" else "수정"

    # ─── Step 1: 파일 요약 메시지 ─────────────────────────────
    summary_lines = [
        f"## {action_emoji} `{path}` ({idx}/{total})",
        "",
        f"**동작**: {action_text}",
        f"**변경 라인**: {proposal.get('lines_changed', 0)}줄",
        f"**파일 크기**: {len(new_content):,} 문자",
    ]

    if summary:
        summary_lines.extend([
            "",
            "**🔍 변경 요약**:",
            summary,  # 길이 제한 없음 — splitter가 알아서 처리
        ])

    summary_message = "\n".join(summary_lines)
    await send_long_text(thread, summary_message, max_messages=4)

    # ─── Step 2: Diff 게시 ──────────────────────────────────
    # diff가 없으면 new_content 사용 (신규 파일)
    content_to_show = diff or new_content
    if not content_to_show:
        await send_long_text(thread, f"📋 **`{path}`**: (내용 없음)")
        return

    content_size = len(content_to_show)
    log.debug(f"파일 {path} 게시 크기: {content_size:,}자")

    # Case 1: 작은 diff (인라인 코드 블록)
    if content_size <= MAX_DIFF_INLINE:
        block_lang = "diff" if diff else _guess_lang(path)
        message = (
            f"📋 **`{path}` {'Diff' if diff else '내용'}**\n"
            f"```{block_lang}\n{content_to_show}\n```"
        )
        await send_long_text(thread, message, max_messages=2)
        return

    # Case 2: 중간 크기 (smart_split_text로 안전 분할)
    if content_size <= MAX_DIFF_GIST:
        await _post_diff_with_splitter(thread, path, content_to_show, is_diff=bool(diff))
        return

    # Case 3: 매우 큰 콘텐츠 → Gist + 파일 첨부
    await _post_huge_diff(thread, path, content_to_show, is_diff=bool(diff))


async def _post_diff_with_splitter(
    thread: discord.Thread,
    path: str,
    content: str,
    *,
    is_diff: bool = True,
) -> None:
    """
    중간 크기 diff를 message_splitter 활용해 분할 게시.
    """
    block_lang = "diff" if is_diff else _guess_lang(path)
    content_type = "Diff" if is_diff else "내용"

    # 헤더 메시지 먼저
    header = f"📋 **`{path}` {content_type}** ({len(content):,}자)"
    await send_long_text(thread, header, max_messages=1)

    # 코드 블록 안 내용을 안전한 청크로 분할
    # ```diff\n...\n``` 오버헤드(15자) 고려해서 1,800자 정도로 청크
    chunk_inner_max = 1800

    chunks = []
    current = ""
    for line in content.splitlines(keepends=True):
        if len(current) + len(line) > chunk_inner_max:
            chunks.append(current)
            current = line
        else:
            current += line
    if current:
        chunks.append(current)

    # 너무 많이 분할되면 파일 첨부로 폴백
    if len(chunks) > 8:
        await _send_as_file(thread, path, content, is_diff)
        return

    # 각 청크를 코드 블록으로 감싸서 게시
    for j, chunk in enumerate(chunks, 1):
        prefix = (
            f"📋 **`{path}` {content_type} ({j}/{len(chunks)})**"
            if len(chunks) > 1 else f"📋 **`{path}` {content_type}**"
        )
        block = f"{prefix}\n```{block_lang}\n{chunk}\n```"
        # send_long_text가 내부적으로 다시 분할할 수 있음
        ok = await send_long_text(thread, block, max_messages=2)
        if not ok:
            log.warning(f"청크 {j}/{len(chunks)} 전송 실패 — 파일 첨부로 폴백")
            await _send_as_file(thread, path, content, is_diff)
            return


async def _post_huge_diff(
    thread: discord.Thread,
    path: str,
    content: str,
    *,
    is_diff: bool = True,
) -> None:
    """매우 큰 diff/콘텐츠는 Gist 업로드 + 파일 첨부."""
    safe_path = path.replace("/", "_")
    ext = "diff" if is_diff else _guess_ext(path)
    filename = f"{safe_path}.{ext}"

    # Gist 업로드 시도
    gist_url = await _upload_to_gist(
        filename=filename,
        content=content,
        description=f"chip_bot {'diff' if is_diff else 'content'} for {path}",
    )

    if gist_url:
        msg = (
            f"📋 **`{path}` {'Diff' if is_diff else '내용'}**\n"
            f"⚠️ 매우 큰 콘텐츠 ({len(content):,}자) — GitHub Gist 업로드\n"
            f"🔗 **[Gist에서 보기]({gist_url})**"
        )
        await send_long_text(thread, msg, max_messages=1)
    else:
        # Gist 실패 → 파일 첨부 폴백
        await _send_as_file(thread, path, content, is_diff)


async def _send_as_file(
    thread: discord.Thread,
    path: str,
    content: str,
    is_diff: bool,
) -> None:
    """파일 첨부로 폴백."""
    safe_path = path.replace("/", "_")
    ext = "diff" if is_diff else _guess_ext(path)
    filename = f"{safe_path}.{ext}"

    try:
        file_obj = discord.File(
            io.BytesIO(content.encode("utf-8")),
            filename=filename,
        )
        await thread.send(
            content=(
                f"📋 **`{path}` {'Diff' if is_diff else '내용'}** "
                f"({len(content):,}자)\n"
                "⚠️ 길이가 커서 파일로 첨부합니다 — 다운로드 후 확인하세요."
            )[:1900],
            file=file_obj,
        )
    except Exception as e:
        log.error(f"파일 첨부 실패 ({path}): {e}")
        try:
            await send_long_text(
                thread,
                f"⚠️ `{path}` 게시 실패 ({len(content):,}자)\n"
                "GitHub PR에서 직접 확인해주세요.",
            )
        except Exception:
            pass


def _guess_lang(path: str) -> str:
    """파일 경로에서 코드 블록 언어 추정."""
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    mapping = {
        "py": "python", "js": "javascript", "ts": "typescript",
        "tsx": "tsx", "jsx": "jsx",
        "yaml": "yaml", "yml": "yaml",
        "json": "json", "md": "markdown",
        "html": "html", "css": "css",
        "sh": "bash", "txt": "",
    }
    return mapping.get(ext, "")


def _guess_ext(path: str) -> str:
    """파일 확장자 추출 (없으면 txt)."""
    if "." in path:
        return path.rsplit(".", 1)[-1].lower()
    return "txt"


# ═══════════════════════════════════════════════════════════════════
# 푸터
# ═══════════════════════════════════════════════════════════════════

async def _post_footer(thread: discord.Thread, pr_result: dict) -> None:
    pr_num = pr_result.get("pr_number", "?")
    pr_url = pr_result.get("pr_url", "")

    text = (
        "---\n"
        "## 🚀 다음 단계\n"
        "\n"
        "### 📥 PR 머지\n"
        f"GitHub: {pr_url}\n"
        f"Discord: `/code_merge {pr_num}`\n"
        "\n"
        "### 🔄 자동 재배포\n"
        "PR 머지 후 Railway가 자동 재배포합니다 (약 2~3분).\n"
        "\n"
        "### 🐛 문제 발생 시\n"
        "`/rnd_diagnose` 또는 `/code_sessions`"
    )

    await send_long_text(thread, text, max_messages=2)
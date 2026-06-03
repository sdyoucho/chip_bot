"""
modules/code_publisher.py
개쵸 코드 변경을 R&D 포럼 채널에 자동 게시.

해쵸의 utils/forum_publisher.py와 동일한 패턴으로 동작.
스레드 생성 → 변경 내역 + diff 게시.
"""

import logging
import os
from datetime import datetime
from typing import Optional

import discord

log = logging.getLogger(__name__)

# 포럼 게시 길이 제한
MAX_THREAD_NAME = 100
MAX_FIRST_MESSAGE = 3500
MAX_DIFF_PER_MESSAGE = 3500


# ═══════════════════════════════════════════════════════════════════
# 채널 조회
# ═══════════════════════════════════════════════════════════════════

def _get_rnd_forum_channel(bot: discord.Client) -> Optional[discord.ForumChannel]:
    """R&D 포럼 채널 조회 (env: RND_FORUM_CHANNEL_ID)."""
    ch_id_str = os.getenv("RND_FORUM_CHANNEL_ID", "").strip()
    if not ch_id_str.isdigit():
        return None
    channel = bot.get_channel(int(ch_id_str))
    if not isinstance(channel, discord.ForumChannel):
        log.warning(f"RND_FORUM_CHANNEL_ID가 포럼 채널이 아님: {type(channel).__name__}")
        return None
    return channel


# ═══════════════════════════════════════════════════════════════════
# 메인 게시 API
# ═══════════════════════════════════════════════════════════════════

async def publish_code_session(
    bot: discord.Client,
    *,
    session: dict,
    pr_result: dict,
) -> bool:
    """
    코드 변경 세션을 R&D 포럼 채널의 새 스레드로 게시.

    Args:
        bot: Discord client
        session: code_planner의 세션 dict
        pr_result: apply_session_to_github 반환값

    Returns:
        성공 여부
    """
    channel = _get_rnd_forum_channel(bot)
    if not channel:
        log.info("R&D 포럼 채널 미설정 — 게시 건너뜀")
        return False

    try:
        # 1) 스레드 이름 + 첫 메시지 생성
        thread_name = _build_thread_name(session, pr_result)
        first_message = _build_first_message(session, pr_result)

        # 2) 스레드 생성
        thread, _ = await channel.create_thread(
            name=thread_name,
            content=first_message,
            auto_archive_duration=10080,  # 7일
        )

        # 3) 파일별 변경 내용 게시
        await _post_file_changes(thread, session)

        # 4) 마지막: PR 링크 + 머지 명령 안내
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
# 스레드 이름 생성
# ═══════════════════════════════════════════════════════════════════

def _build_thread_name(session: dict, pr_result: dict) -> str:
    """스레드 이름 (100자 제한)."""
    intent = session.get("intent", {}).get("intent", "")
    request = session.get("user_request", "")
    pr_num = pr_result.get("pr_number", "?")

    title_text = intent or request or "코드 변경"
    name = f"[개쵸 #PR{pr_num}] {title_text}"

    return name[:MAX_THREAD_NAME]


# ═══════════════════════════════════════════════════════════════════
# 첫 메시지 (스레드 본문)
# ═══════════════════════════════════════════════════════════════════

def _build_first_message(session: dict, pr_result: dict) -> str:
    """포럼 스레드의 첫 메시지 (전체 요약)."""
    intent = session.get("intent", {})
    plan = session.get("plan", {})
    proposals = session.get("file_proposals", [])

    lines = [
        f"# 🤖 개쵸 자동 코드 변경",
        "",
        f"**📅 발행 시각**: {datetime.now():%Y-%m-%d %H:%M KST}",
        f"**🔗 PR**: [#{pr_result.get('pr_number', '?')}]({pr_result.get('pr_url', '')})",
        f"**🌿 브랜치**: `{pr_result.get('branch', '')}`",
        f"**📦 커밋**: {pr_result.get('commits_succeeded', 0)}/{pr_result.get('commits_total', 0)} 성공",
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
        plan.get("plan_summary", "(요약 없음)")[:1200],
        "",
        f"## 📂 변경 파일 ({len(proposals)}개)",
    ]

    # 파일 목록
    for p in proposals[:15]:
        emoji = "🆕" if p.get("action") == "create" else "✏️"
        lines.append(
            f"- {emoji} `{p['path']}` ({p.get('lines_changed', 0)}줄)"
        )

    if len(proposals) > 15:
        lines.append(f"- ... 외 {len(proposals) - 15}개")

    deps = plan.get("requires_dependencies", [])
    if deps:
        lines.append("")
        lines.append("## 📦 추가 패키지")
        for d in deps:
            lines.append(f"- `{d}`")

    text = "\n".join(lines)
    return text[:MAX_FIRST_MESSAGE]


# ═══════════════════════════════════════════════════════════════════
# 파일별 변경 내용 게시
# ═══════════════════════════════════════════════════════════════════

async def _post_file_changes(thread: discord.Thread, session: dict) -> None:
    """각 변경 파일의 상세 내용을 별도 메시지로 게시."""
    proposals = session.get("file_proposals", [])

    for i, p in enumerate(proposals, 1):
        try:
            # 파일 요약 메시지
            summary_text = _build_file_summary(p, i, len(proposals))
            await thread.send(content=summary_text)

            # Diff 메시지 (별도)
            diff_chunks = _split_diff(p)
            for j, chunk in enumerate(diff_chunks, 1):
                prefix = (
                    f"📋 **`{p['path']}` Diff "
                    f"({j}/{len(diff_chunks)})**\n"
                    if len(diff_chunks) > 1 else
                    f"📋 **`{p['path']}` Diff**\n"
                )
                await thread.send(content=f"{prefix}```diff\n{chunk}\n```")

        except discord.HTTPException as e:
            log.warning(f"파일 {p['path']} 게시 실패: {e}")
            try:
                await thread.send(
                    content=f"⚠️ `{p['path']}` 게시 실패 (메시지 길이 초과 등): {e}"[:1000],
                )
            except Exception:
                pass


def _build_file_summary(proposal: dict, idx: int, total: int) -> str:
    """파일 한 개의 요약 메시지."""
    action_emoji = "🆕" if proposal.get("action") == "create" else "✏️"
    action_text = "신규 파일" if proposal.get("action") == "create" else "수정"

    lines = [
        f"## {action_emoji} `{proposal['path']}` ({idx}/{total})",
        "",
        f"**동작**: {action_text}",
        f"**변경 라인**: {proposal.get('lines_changed', 0)}줄",
    ]

    summary = proposal.get("summary", "").strip()
    if summary:
        lines.append("")
        lines.append("**🔍 변경 요약**:")
        lines.append(summary[:1500])

    return "\n".join(lines)[:1900]  # Discord 메시지 한계 대비


def _split_diff(proposal: dict) -> list[str]:
    """Diff를 메시지 크기에 맞게 분할."""
    diff = proposal.get("diff", "") or proposal.get("new_content", "")
    if not diff:
        return ["(diff 없음)"]

    # 코드 블록 안전 분할
    chunks = []
    current = ""
    for line in diff.splitlines(keepends=True):
        if len(current) + len(line) > MAX_DIFF_PER_MESSAGE:
            chunks.append(current)
            current = line
        else:
            current += line
    if current:
        chunks.append(current)

    # 최대 5개로 제한 (메시지 폭주 방지)
    if len(chunks) > 5:
        chunks = chunks[:5]
        chunks[-1] += "\n... (이하 생략, 전체 diff는 GitHub PR에서 확인)"

    return chunks


# ═══════════════════════════════════════════════════════════════════
# 푸터 (PR 링크 + 액션)
# ═══════════════════════════════════════════════════════════════════

async def _post_footer(thread: discord.Thread, pr_result: dict) -> None:
    """마지막 메시지 — PR 액션 안내."""
    pr_num = pr_result.get("pr_number", "?")
    pr_url = pr_result.get("pr_url", "")

    text = (
        "---\n"
        "## 🚀 다음 단계\n"
        "\n"
        f"### 📥 PR 머지\n"
        f"GitHub에서 직접 머지: {pr_url}\n"
        f"또는 Discord에서: `/code_merge {pr_num}`\n"
        "\n"
        f"### 🔄 자동 재배포\n"
        f"PR 머지 후 Railway가 자동 재배포합니다 (약 2~3분 소요).\n"
        "\n"
        f"### 🐛 문제 발생 시\n"
        f"`/rnd_diagnose` 로 진단 요청\n"
        f"`/code_sessions` 로 이력 확인"
    )

    try:
        await thread.send(content=text[:1990])
    except Exception as e:
        log.warning(f"푸터 게시 실패: {e}")
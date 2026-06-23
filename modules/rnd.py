"""
modules/rnd.py
개쵸 — R&D 총괄.

역할:
1. Q&A: 기술 질문 응답 (handle_query)
2. 코드 리뷰: rnd_code_review (파일 경로 또는 코드 문장)
3. 로그/이슈 진단: diagnose_codebase (/rnd_diagnose)
4. 신규 봇 설계: design_new_bot
5. R&D 채널 게시: post_to_rnd_channel (외부 모듈에서 호출)

OpenRouter: standard 티어 (Claude Opus 등)
"""

import io
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

import discord

from utils.openrouter_client import chat
from utils.message_splitter import (
    smart_split_text,
    send_long_text as splitter_send_long_text,
)

log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# Discord 길이 제한 상수
# ═══════════════════════════════════════════════════════════════════
DISCORD_MSG_LIMIT: int = 2000
EMBED_DESC_LIMIT: int = 4096
EMBED_DESC_SAFE: int = 4000  # 안전 마진
EMBED_FIELD_LIMIT: int = 1024
EMBED_TOTAL_LIMIT: int = 6000

# 파일 읽기 최대 크기 (50KB) — 너무 큰 파일은 자르기
_MAX_FILE_BYTES: int = 50 * 1024

# AI 코드 리뷰 응답 최대 토큰
_REVIEW_MAX_TOKENS: int = 16000

# 코드 리뷰 시 허용 확장자
_CODE_EXTENSIONS = {
    ".py", ".pyi", ".js", ".ts", ".tsx", ".jsx",
    ".json", ".yaml", ".yml", ".toml", ".md",
    ".sh", ".env", ".cfg", ".ini",
}

# /code_propose 연계 안내 상수
NEXT_STEP_HINT: str = (
    "다음 단계: `/code_propose` 로 자동 수정 제안을 받을 수 있습니다."
)


# ═══════════════════════════════════════════════════════════════════
# 시스템 프롬프트
# ═══════════════════════════════════════════════════════════════════

SYSTEM_QA = (
    "당신은 '개쵸'입니다. Python·Discord.py·Notion API·YouTube API·"
    "스트리밍 플랫폼 연동·Railway 배포·OpenRouter에 특화된 시니어 개발자입니다. "
    "Cho의 매니지먼트 봇 시스템 유지보수·신규 기능 개발·신규 봇 생성에 대해 답변합니다. "
    "답변은 다음 형식:\n"
    "1. 요약 (1~2줄)\n"
    "2. 원인/분석\n"
    "3. 구체적 해결 방법 (코드 포함 가능)\n"
    "4. 추가 고려사항"
)

SYSTEM_BOT_DESIGN = (
    "당신은 '개쵸'입니다. 신규 Discord 봇 설계 전문가로서, "
    "Cho가 원하는 봇의 요구사항을 듣고 다음 형식의 설계서를 작성합니다:\n"
    "## 봇 이름·역할\n## 핵심 기능 리스트 (5~10개)\n"
    "## 사용할 기술 스택\n## 예상 OpenRouter 티어\n"
    "## 필요한 외부 API·환경변수\n## 디렉터리 구조\n"
    "## 예상 월 비용\n## 개발 우선순위 (Phase 1~3)\n"
    "한국어로 작성하고, 실행 가능한 수준의 구체적 스펙으로 작성하세요."
)

SYSTEM_CODE_REVIEW = (
    "당신은 '개쵸'입니다. 시니어 코드 리뷰어로서 Python/Discord.py 코드 또는 "
    "사용자가 제시한 코드 관련 문장을 분석합니다.\n"
    "반드시 다음 4가지 항목으로 마크다운 형식의 한국어 리뷰를 작성하세요:\n\n"
    "### 1. 문법/런타임 위험\n"
    "- 잠재적 예외, 타입 오류, None 처리 누락, 비동기 오용 등\n\n"
    "### 2. 스타일/가독성\n"
    "- PEP8, 네이밍, 함수 분리, 주석/docstring, 중복 코드\n\n"
    "### 3. 개선 제안\n"
    "- 구체적인 리팩토링 방향 (코드 스니펫 포함 가능)\n\n"
    "### 4. 보안/성능\n"
    "- 비밀키 노출, 입력 검증, I/O·DB·API 호출 최적화, 메모리/캐시 이슈\n\n"
    "간결하지만 실무에서 바로 적용 가능한 수준으로 작성하세요. "
    "문제가 없는 항목은 '특이사항 없음'으로 표기하세요."
)

SYSTEM_DIAGNOSE = (
    "당신은 '개쵸'입니다. 봇 시스템 전체에 대한 진단가로서, "
    "주어진 코드베이스 정보와 (선택적으로) 사용자가 제시한 이슈를 분석해 "
    "다음 형식으로 한국어 진단서를 작성합니다:\n\n"
    "## 🔍 현재 시스템 상태\n## ⚠️ 발견된 잠재 이슈 (Top 5)\n"
    "## 💡 즉시 적용 가능한 개선안 (Top 3)\n## 🛠️ 중장기 로드맵\n"
    "## 📊 우선순위 매트릭스 (긴급도/영향도)\n\n"
    "실행 가능한 구체적 조치를 제시하고, 각 항목에 예상 작업 시간을 포함하세요."
)


# ═══════════════════════════════════════════════════════════════════
# 공통 유틸: 길이 가드
# ═══════════════════════════════════════════════════════════════════

def _truncate(text: str, limit: int, suffix: str = "\n…(생략)") -> str:
    """문자열을 limit 이내로 잘라 반환."""
    if text is None:
        return ""
    if len(text) <= limit:
        return text
    cut = max(0, limit - len(suffix))
    return text[:cut] + suffix


def _safe_embed_description(text: str) -> str:
    """임베드 description 안전 길이로 truncate."""
    return _truncate(text or "", EMBED_DESC_SAFE)


async def send_long_text(
    target: discord.abc.Messageable,
    content: str,
    *,
    filename: str = "result.md",
    header: Optional[str] = None,
) -> None:
    """
    긴 텍스트를 안전하게 Discord에 전송.

    🆕 v2: utils/message_splitter.py의 기능을 활용하여
    마크다운 보호 분할 + 자동 파일 첨부.
    """
    try:
        if not content:
            await target.send(header or "(빈 응답)")
            return

        # message_splitter의 send_long_text 활용
        combined = f"{header}\n\n{content}" if header else content
        ok = await splitter_send_long_text(target, combined)
        if ok:
            return

        # 폴백: 직접 파일 첨부
        if len(combined) <= DISCORD_MSG_LIMIT:
            await target.send(combined)
            return

        buffer = io.BytesIO(content.encode("utf-8"))
        file = discord.File(buffer, filename=filename)
        notice = header or "📎 응답이 길어 파일로 첨부합니다."
        if len(notice) > DISCORD_MSG_LIMIT:
            notice = notice[: DISCORD_MSG_LIMIT - 10] + "…"
        await target.send(content=notice, file=file)
    except discord.HTTPException as e:
        log.error("send_long_text 실패: %s", e)
        try:
            await target.send(
                f"⚠️ 메시지 전송 실패: {e.code} — 길이/형식 문제일 수 있습니다."
            )
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════
# 1. 기본 Q&A
# ═══════════════════════════════════════════════════════════════════

async def handle_query(query: str) -> discord.Embed:
    """R&D 자연어 질문 처리 (/ask 라우터에서 호출)."""
    try:
        result = await chat(
            messages=[
                {"role": "system", "content": SYSTEM_QA},
                {"role": "user", "content": query},
            ],
            agent="gaechyo",
            max_tokens=1500,
            temperature=0.4,
        )

        content_text: str = result.get("content", "") or ""
        model_name: str = result.get("model", "unknown").split("/")[-1]
        cost_val: float = float(result.get("cost", 0.0))

        safe_desc = _safe_embed_description(content_text)

        embed = discord.Embed(
            title="🔧 개쵸 — R&D",
            description=safe_desc if safe_desc else "(응답 없음)",
            color=0x06B6D4,
        )
        footer_text = f"{model_name} · ${cost_val:.5f}"
        if len(content_text) > EMBED_DESC_SAFE:
            footer_text += " · 일부 생략됨"
        embed.set_footer(text=footer_text[:2048])
        return embed
    except Exception as e:
        from bot.embeds import embed_error
        return embed_error("R&D 오류", str(e))


# ═══════════════════════════════════════════════════════════════════
# 2. 코드 리뷰 (rnd_code_review)
# ═══════════════════════════════════════════════════════════════════

def _looks_like_path(target: str) -> bool:
    """입력 문자열이 파일 경로처럼 보이는지 휴리스틱 판단."""
    s = target.strip().strip("`").strip('"').strip("'")
    if "\n" in s or len(s) > 300:
        return False
    if " " in s and not (s.endswith(tuple(_CODE_EXTENSIONS))):
        return False
    if any(s.endswith(ext) for ext in _CODE_EXTENSIONS):
        return True
    if "/" in s or "\\" in s:
        return True
    return False


def _read_code_file(path_str: str) -> Optional[tuple[Path, str]]:
    """
    경로 문자열을 읽어 (Path, 내용)을 반환. 실패 시 None.

    - 보안: 작업 디렉터리 또는 /data 하위만 허용
    - 크기: _MAX_FILE_BYTES 초과 시 앞부분만 사용
    """
    s = path_str.strip().strip("`").strip('"').strip("'")
    try:
        p = Path(s).expanduser()
        if not p.is_absolute():
            p = (Path.cwd() / p).resolve()
        else:
            p = p.resolve()
    except Exception:
        return None

    cwd = Path.cwd().resolve()
    allowed_roots = [cwd]
    data_root = Path("/data")
    if data_root.exists():
        allowed_roots.append(data_root.resolve())

    if not any(str(p).startswith(str(root)) for root in allowed_roots):
        log.warning("코드 리뷰: 허용되지 않은 경로 차단 — %s", p)
        return None

    if not p.exists() or not p.is_file():
        return None

    try:
        data = p.read_bytes()
        truncated = False
        if len(data) > _MAX_FILE_BYTES:
            data = data[:_MAX_FILE_BYTES]
            truncated = True
        text = data.decode("utf-8", errors="replace")
        if truncated:
            text += "\n\n# ... (파일이 너무 커서 앞부분만 표시됨)"
        return p, text
    except Exception as e:
        log.error("코드 파일 읽기 실패: %s — %s", p, e)
        return None


async def run_health_check(target: str) -> discord.Embed:
    """
    코드 리뷰 명령.

    Parameters
    ----------
    target : str
        파일 경로(예: 'modules/rnd.py') 또는 코드/자유 문장.

    Returns
    -------
    discord.Embed
        리뷰 결과 임베드. 4가지 섹션 포함:
        (1) 문법/런타임  (2) 스타일/가독성
        (3) 개선 제안    (4) 보안/성능
    """
    if not target or not target.strip():
        from bot.embeds import embed_error
        return embed_error(
            "코드 리뷰 실패",
            "리뷰할 파일 경로 또는 코드/문장을 입력해주세요.\n"
            "예) `/rnd_health modules/rnd.py`\n"
            "예) `/rnd_health def foo(): return 1/0`",
        )

    source_label: str = "직접 입력"
    code_for_review: str = target.strip()
    resolved_path: Optional[Path] = None

    # 1) 파일 경로처럼 보이면 읽기 시도
    if _looks_like_path(target):
        loaded = _read_code_file(target)
        if loaded is not None:
            resolved_path, code_for_review = loaded
            try:
                rel_path = resolved_path.relative_to(Path.cwd())
                source_label = f"파일: `{rel_path}`"
            except ValueError:
                source_label = f"파일: `{resolved_path}`"
        else:
            source_label = "직접 입력 (경로 해석 실패)"

    # 2) AI 리뷰 요청
    user_prompt = (
        "다음 대상에 대해 코드 리뷰를 수행해주세요.\n\n"
        f"**대상**: {source_label}\n\n"
        "```\n"
        f"{code_for_review[:30000]}\n"
        "```\n\n"
        "위 4가지 항목(문법/런타임, 스타일/가독성, 개선 제안, 보안/성능)으로 "
        "리뷰를 작성해주세요."
    )

    try:
        result = await chat(
            messages=[
                {"role": "system", "content": SYSTEM_CODE_REVIEW},
                {"role": "user", "content": user_prompt},
            ],
            agent="gaechyo",
            tier="premium",
            max_tokens=_REVIEW_MAX_TOKENS,
            temperature=0.3,
        )

        review_text: str = result.get("content", "") or ""
        model_name: str = result.get("model", "unknown").split("/")[-1]
        cost_val: float = float(result.get("cost", 0.0))

        # 안전한 길이로 description 설정
        safe_desc = _safe_embed_description(review_text)

        embed = discord.Embed(
            title=f"📋 개쵸 코드 리뷰 — {source_label}",
            description=safe_desc if safe_desc else "(리뷰 결과 없음)",
            color=0x6366F1,
        )

        # 푸터: 모델/비용/생략 표시
        footer_parts = [model_name, f"${cost_val:.5f}"]
        if len(review_text) > EMBED_DESC_SAFE:
            footer_parts.append("일부 생략됨")
        footer_parts.append(NEXT_STEP_HINT)
        embed.set_footer(text=" · ".join(footer_parts)[:2048])

        return embed

    except Exception as e:
        log.exception("코드 리뷰 실패: %s", e)
        from bot.embeds import embed_error
        return embed_error("코드 리뷰 오류", str(e))


# ═══════════════════════════════════════════════════════════════════
# 3. 로그/이슈 진단 (diagnose_codebase) — /rnd_diagnose 호출
# ═══════════════════════════════════════════════════════════════════

async def diagnose_codebase(issue: Optional[str] = None) -> discord.Embed:
    """
    봇 코드베이스 전체 진단.

    Parameters
    ----------
    issue : Optional[str]
        (선택) 특정 관심 영역/이슈 설명.

    Returns
    -------
    discord.Embed
        진단 결과 임베드.
    """
    try:
        # 1) 현재 코드베이스 구조 수집
        codebase_summary = _collect_codebase_summary()

        # 2) AI 진단 요청
        user_prompt_parts = [
            "다음은 chip_bot의 현재 상태입니다.",
            "",
            "=== 코드베이스 구조 ===",
            codebase_summary,
        ]

        if issue:
            user_prompt_parts.extend([
                "",
                "=== Cho가 보고한 이슈 ===",
                issue[:2000],
            ])

        user_prompt_parts.extend([
            "",
            "위 정보를 바탕으로 시스템 진단서를 작성해주세요.",
        ])

        user_prompt = "\n".join(user_prompt_parts)

        result = await chat(
            messages=[
                {"role": "system", "content": SYSTEM_DIAGNOSE},
                {"role": "user", "content": user_prompt},
            ],
            agent="gaechyo",
            tier="premium",
            max_tokens=8000,
            temperature=0.3,
        )

        content_text: str = result.get("content", "") or ""
        model_name: str = result.get("model", "unknown").split("/")[-1]
        cost_val: float = float(result.get("cost", 0.0))

        safe_desc = _safe_embed_description(content_text)

        title = "🔬 R&D 코드 리뷰 진단"
        if issue:
            title += f" — {issue[:60]}"

        embed = discord.Embed(
            title=title,
            description=safe_desc if safe_desc else "(진단 결과 없음)",
            color=0x6366F1,
        )

        footer_parts = [model_name, f"${cost_val:.5f}"]
        if len(content_text) > EMBED_DESC_SAFE:
            footer_parts.append("일부 생략됨")
        footer_parts.append("개쵸 R&D · 코드 리뷰 기반 개선 제안")
        embed.set_footer(text=" · ".join(footer_parts)[:2048])

        return embed

    except Exception as e:
        log.exception("진단 실패: %s", e)
        from bot.embeds import embed_error
        return embed_error("진단 오류", str(e))


def _collect_codebase_summary() -> str:
    """
    봇 코드베이스의 구조와 주요 파일 정보를 텍스트로 수집.
    AI 진단의 컨텍스트로 사용.
    """
    cwd = Path.cwd()
    lines = [
        f"작업 디렉터리: {cwd}",
        f"진단 시각: {datetime.now():%Y-%m-%d %H:%M KST}",
        "",
    ]

    # 주요 디렉터리별 파일 수집
    target_dirs = ["bot", "modules", "utils"]
    for d in target_dirs:
        dir_path = cwd / d
        if not dir_path.exists():
            continue

        py_files = sorted(dir_path.glob("*.py"))
        lines.append(f"### {d}/ ({len(py_files)}개 파일)")

        for f in py_files:
            try:
                size = f.stat().st_size
                line_count = sum(1 for _ in f.open(encoding="utf-8", errors="replace"))
                lines.append(
                    f"  • {f.name} — {size:,} bytes, {line_count:,} 줄"
                )
            except Exception:
                lines.append(f"  • {f.name} — (읽기 실패)")
        lines.append("")

    # requirements.txt
    req = cwd / "requirements.txt"
    if req.exists():
        try:
            req_text = req.read_text(encoding="utf-8", errors="replace")
            req_lines = [l.strip() for l in req_text.splitlines() if l.strip() and not l.startswith("#")]
            lines.append(f"### requirements.txt ({len(req_lines)}개 패키지)")
            lines.extend(f"  • {l}" for l in req_lines[:30])
            if len(req_lines) > 30:
                lines.append(f"  • ... 외 {len(req_lines) - 30}개")
        except Exception:
            pass

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# 4. 신규 봇 설계 (design_new_bot)
# ═══════════════════════════════════════════════════════════════════

async def design_new_bot(requirements: str) -> discord.Embed:
    """
    신규 봇 요구사항을 받아 설계서 생성.

    Parameters
    ----------
    requirements : str
        새로 만들 봇에 대한 요구사항 (자연어).

    Returns
    -------
    discord.Embed
        설계서 임베드.
    """
    if not requirements or not requirements.strip():
        from bot.embeds import embed_error
        return embed_error(
            "봇 설계 실패",
            "봇 요구사항을 입력해주세요.\n"
            "예) '치지직 채팅 분석용 봇 — 욕설 감지 + 통계 리포트'",
        )

    try:
        result = await chat(
            messages=[
                {"role": "system", "content": SYSTEM_BOT_DESIGN},
                {"role": "user", "content": requirements.strip()[:3000]},
            ],
            agent="gaechyo",
            tier="premium",
            max_tokens=6000,
            temperature=0.5,
        )

        content_text: str = result.get("content", "") or ""
        model_name: str = result.get("model", "unknown").split("/")[-1]
        cost_val: float = float(result.get("cost", 0.0))

        safe_desc = _safe_embed_description(content_text)

        embed = discord.Embed(
            title="🎨 개쵸 — 신규 봇 설계서",
            description=safe_desc if safe_desc else "(설계서 생성 실패)",
            color=0x8B5CF6,
        )

        footer_parts = [model_name, f"${cost_val:.5f}"]
        if len(content_text) > EMBED_DESC_SAFE:
            footer_parts.append("일부 생략됨")
        embed.set_footer(text=" · ".join(footer_parts)[:2048])

        return embed

    except Exception as e:
        log.exception("봇 설계 실패: %s", e)
        from bot.embeds import embed_error
        return embed_error("봇 설계 오류", str(e))


# ═══════════════════════════════════════════════════════════════════
# 5. R&D 채널 게시 (post_to_rnd_channel) — 외부 모듈에서 호출
# ═══════════════════════════════════════════════════════════════════

async def post_to_rnd_channel(
    bot: discord.Client,
    *,
    category: str = "general",
    title: str = "",
    content: str = "",
    embed: Optional[discord.Embed] = None,
    author: Optional[str] = None,
) -> bool:
    """
    R&D 채널에 자동 게시 (재부팅 알림, 자동 코드 변경 등 외부 모듈에서 호출).

    Parameters
    ----------
    bot : discord.Client
        Discord 봇 인스턴스.
    category : str
        카테고리 ('maintenance', 'update', 'error', 'general' 등).
    title : str
        제목.
    content : str
        본문.
    embed : Optional[discord.Embed]
        직접 임베드를 전달하면 그대로 사용.
    author : Optional[str]
        요청자 표시 (수동 커맨드 실행 시 누가 실행했는지 푸터에 표기).

    Returns
    -------
    bool
        성공 여부. 채널 미설정/권한 없음 등은 False.
    """
    ch_id_str = os.getenv("RND_CHANNEL_ID", "").strip()
    if not ch_id_str.isdigit():
        log.info("RND_CHANNEL_ID 미설정 — R&D 채널 게시 건너뜀")
        return False

    channel = bot.get_channel(int(ch_id_str))
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        log.warning("RND_CHANNEL_ID가 텍스트 채널이 아님: %s", type(channel).__name__)
        return False

    try:
        # category별 색상
        color_map = {
            "maintenance": 0x06B6D4,   # 시안
            "update":      0x059669,   # 녹색
            "error":       0xE11D48,   # 빨강
            "warning":     0xEAB308,   # 노랑
            "health":      0x22C55E,   # 연두
            "general":     0x6366F1,   # 보라
        }
        emoji_map = {
            "maintenance": "🔧",
            "update":      "🚀",
            "error":       "🚨",
            "warning":     "⚠️",
            "health":      "🩺",
            "general":     "📢",
        }
        color = color_map.get(category, 0x6366F1)
        emoji = emoji_map.get(category, "📢")

        if embed is None:
            embed = discord.Embed(
                title=f"{emoji} {title or category.upper()}",
                description=_safe_embed_description(content),
                color=color,
                timestamp=datetime.now(),
            )
            footer = f"개쵸 R&D · {category}"
            if author:
                footer += f" · 요청자: {author}"
            embed.set_footer(text=footer)

        await channel.send(embed=embed)
        log.info("R&D 채널 게시 완료: %s — %s", category, title[:50])
        return True

    except discord.Forbidden:
        log.error("R&D 채널 권한 없음")
        return False
    except Exception as e:
        log.warning("R&D 채널 게시 실패: %s", e)
        return False


# ═══════════════════════════════════════════════════════════════════
# 6. 업데이트 공지 헬퍼
# ═══════════════════════════════════════════════════════════════════

async def announce_update(
    bot: discord.Client,
    *,
    version: str,
    changes: list[str],
    pr_url: Optional[str] = None,
) -> bool:
    """
    업데이트 공지를 R&D 채널에 게시.

    Parameters
    ----------
    bot : discord.Client
        봇 인스턴스.
    version : str
        버전 또는 식별자.
    changes : list[str]
        변경 사항 리스트.
    pr_url : Optional[str]
        관련 PR URL.

    Returns
    -------
    bool
        성공 여부.
    """
    lines = [f"## 🚀 업데이트 — {version}", ""]
    for c in changes[:20]:
        lines.append(f"- {c}")
    if len(changes) > 20:
        lines.append(f"- ... 외 {len(changes) - 20}개")

    if pr_url:
        lines.append("")
        lines.append(f"🔗 **PR**: {pr_url}")

    content = "\n".join(lines)
    return await post_to_rnd_channel(
        bot,
        category="update",
        title=f"업데이트 {version}",
        content=content,
    )


# ═══════════════════════════════════════════════════════════════════
# 7. 일일 건강 리포트 (스케줄러에서 매일 08:00 호출)
# ═══════════════════════════════════════════════════════════════════

async def daily_health_report(bot: discord.Client) -> bool:
    """
    봇 자가 건강 진단 — 가동 시간, 서버 연결 상태, 최근 24시간 에러 현황을
    종합해 R&D 채널에 게시.
    """
    from utils.restart_manager import get_start_time, get_uptime, format_kst
    from utils.self_monitor import get_error_summary, get_recent_errors

    recent = get_recent_errors(minutes=1440)
    summary = get_error_summary()

    lines = [
        f"**가동 시작**: {format_kst(get_start_time(), with_seconds=False)}",
        f"**가동 시간**: {get_uptime()}",
        f"**연결 서버**: {len(bot.guilds)}개",
        f"**레이턴시**: {bot.latency * 1000:.0f}ms",
        "",
    ]

    if not recent:
        lines.append("✅ 최근 24시간 내 에러 없음")
    else:
        lines.append(f"⚠️ 최근 24시간 에러 {len(recent)}건")
        top = sorted(summary.items(), key=lambda x: -x[1])[:5]
        lines.extend(f"• {cat}: {cnt}회" for cat, cnt in top)

    return await post_to_rnd_channel(
        bot,
        category="health" if not recent else "warning",
        title="🩺 일일 건강 체크",
        content="\n".join(lines),
    )
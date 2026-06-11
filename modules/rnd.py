"""
modules/rnd.py
개쵸 — R&D 총괄.

역할:
1. Q&A: 기술 질문 응답
2. 코드 리뷰: rnd_health (리뉴얼) — 코드 파일 또는 자유 문장 입력 → AI 코드 리뷰 결과 반환
3. 로그/이슈 진단: rnd_diagnose
4. 신규 봇 설계: design_new_bot
5. 업데이트 공지: R&D 채널에 업데이트 현황 자동 게시 (외부 모듈에서 호출)

OpenRouter: standard 티어 (Claude Opus 등)
"""

import io
import logging
import os
from pathlib import Path
from typing import Optional

import discord

from utils.openrouter_client import chat

log = logging.getLogger(__name__)

# ── Discord 길이 제한 상수 ─────────────────────────────────────────
DISCORD_MSG_LIMIT: int = 2000
EMBED_DESC_LIMIT: int = 4096
EMBED_DESC_SAFE: int = 4000  # 안전 마진
EMBED_FIELD_LIMIT: int = 1024
EMBED_TOTAL_LIMIT: int = 6000

# ── 시스템 프롬프트 ─────────────────────────────────────────────────
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

# /code_propose 연계 안내 상수
NEXT_STEP_HINT: str = (
    "다음 단계: `/code_propose` 로 자동 수정 제안을 받을 수 있습니다."
)

# 코드 리뷰 시 허용 확장자
_CODE_EXTENSIONS = {
    ".py", ".pyi", ".js", ".ts", ".tsx", ".jsx",
    ".json", ".yaml", ".yml", ".toml", ".md",
    ".sh", ".env", ".cfg", ".ini",
}

# 파일 읽기 최대 크기 (50KB) — 너무 큰 파일은 자르기
_MAX_FILE_BYTES: int = 50 * 1024

# AI 코드 리뷰 응답 최대 토큰 (충분히 크게 — 사실상 제한 해제 목적)
_REVIEW_MAX_TOKENS: int = 16000


# ── 공통 유틸: 길이 가드 ────────────────────────────────────────────
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

    - 2000자 이하: 그대로 전송
    - 그 외: 첨부 파일(.md)로 전송하여 400 Bad Request(50035) 방지
    """
    try:
        if not content:
            await target.send(header or "(빈 응답)")
            return

        if header is None:
            header = ""

        full = f"{header}\n{content}" if header else content

        if len(full) <= DISCORD_MSG_LIMIT:
            await target.send(full)
            return

        # 파일 첨부 fallback
        buffer = io.StringIO(content)
        file = discord.File(buffer, filename=filename)
        notice = header or "📎 응답이 길어 파일로 첨부합니다."
        if len(notice) > DISCORD_MSG_LIMIT:
            notice = notice[: DISCORD_MSG_LIMIT - 10] + "…"
        await target.send(content=notice, file=file)
    except discord.HTTPException as e:
        log.error("send_long_text 실패: %s", e)
        try:
            await target.send(f"⚠️ 메시지 전송 실패: {e.code} — 길이/형식 문제일 수 있습니다.")
        except Exception:
            pass


# ── 1. 기본 Q&A ─────────────────────────────────────────────────────
async def handle_query(query: str) -> discord.Embed:
    """R&D 자연어 질문 처리 (/ask)."""
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

        # 임베드 description 안전 길이로 truncate
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


# ── 2. 코드 리뷰 (rnd_health 리뉴얼) ────────────────────────────────
def _looks_like_path(target: str) -> bool:
    """입력 문자열이 파일 경로처럼 보이는지 휴리스틱 판단."""
    s = target.strip().strip("`").strip('"').strip("'")
    if "\n" in s or len(s) > 300:
        return False
    if " " in s and not (s.endswith(tuple(_CODE_EXTENSIONS))):
        return False
    # 확장자 또는 경로 구분자 존재
    if any(s.endswith(ext) for ext in _CODE_EXTENSIONS):
        return True
    if "/" in s or "\\" in s:
        return True
    return False


def _read_code_file(path_str: str) -> Optional[tuple[Path, str]]:
    """
    경로 문자열을 읽어 (Path, 내용)을 반환. 실패 시 None.

    - 보안: 절대경로 traversal 차단을 위해 현재 작업 디렉터리 내부로 제한.
    - 크기: _MAX_FILE_BYTES 초과 시 앞부분만 사용.
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

    # 작업 디렉터리 또는 /data 하위만 허용
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


async def rnd_code_review(target: str) -> discord.Embed:
    """
    코드 리뷰 명령.

    Parameters
    ----------
    target : str
        파일 경로(예: 'modules/rnd.py') 또는 코드/자유 문장.

    Returns
    -------
    discord.Embed
        리뷰 결과 임베드. (1)문법/런타임 (2)스타일/가독성
        (3)개선 제안 (4)보안/성능 4가지 섹션 포함.
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
            # 경로 같았지만 못 읽음 → 문장 그대로 리뷰
            source_label = "직접 입력 (경로 해석 실패)"

    # 2) AI 리뷰 요청 (f-string 따옴표 충돌 회피를 위해 삼중따옴표 사용)
    user_prompt = f"""다음 대상에 대해 코드 리뷰를 수행해주세요.

**대상**: {source_label}
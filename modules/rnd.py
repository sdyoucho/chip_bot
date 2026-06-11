"""
modules/rnd.py
개쵸 — R&D 총괄.

🆕 v3 안전 재작성:
- f-string 안의 백틱(```) 제거 → 일반 문자열 concat 사용
- 모든 multi-line 문자열은 .format() 또는 join() 사용
- 유니코드 따옴표 회피
"""

import io
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

import discord

from utils.openrouter_client import chat

log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# Discord 길이 제한 상수
# ═══════════════════════════════════════════════════════════════════
DISCORD_MSG_LIMIT = 2000
EMBED_DESC_LIMIT = 4096
EMBED_DESC_SAFE = 4000
EMBED_FIELD_LIMIT = 1024
EMBED_TOTAL_LIMIT = 6000

_MAX_FILE_BYTES = 50 * 1024
_REVIEW_MAX_TOKENS = 16000

_CODE_EXTENSIONS = {
    ".py", ".pyi", ".js", ".ts", ".tsx", ".jsx",
    ".json", ".yaml", ".yml", ".toml", ".md",
    ".sh", ".env", ".cfg", ".ini",
}

NEXT_STEP_HINT = "다음 단계: /code_propose 로 자동 수정 제안을 받을 수 있습니다."

# 백틱 3개를 변수로 분리 — f-string 안에서 사용 시 안전
BACKTICKS = "```"

# ═══════════════════════════════════════════════════════════════════
# 시스템 프롬프트 (일반 문자열, f-string 없음)
# ═══════════════════════════════════════════════════════════════════

SYSTEM_QA = (
    "당신은 '개쵸'입니다. Python, Discord.py, Notion API, YouTube API, "
    "스트리밍 플랫폼 연동, Railway 배포, OpenRouter에 특화된 시니어 개발자입니다. "
    "Cho의 매니지먼트 봇 시스템 유지보수, 신규 기능 개발, 신규 봇 생성에 대해 답변합니다.\n"
    "답변 형식:\n"
    "1. 요약 (1~2줄)\n"
    "2. 원인/분석\n"
    "3. 구체적 해결 방법 (코드 포함 가능)\n"
    "4. 추가 고려사항"
)

SYSTEM_BOT_DESIGN = (
    "당신은 '개쵸'입니다. 신규 Discord 봇 설계 전문가로서, "
    "Cho가 원하는 봇의 요구사항을 듣고 다음 형식의 설계서를 작성합니다:\n"
    "## 봇 이름/역할\n"
    "## 핵심 기능 리스트 (5~10개)\n"
    "## 사용할 기술 스택\n"
    "## 예상 OpenRouter 티어\n"
    "## 필요한 외부 API/환경변수\n"
    "## 디렉터리 구조\n"
    "## 예상 월 비용\n"
    "## 개발 우선순위 (Phase 1~3)\n"
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
    "- 비밀키 노출, 입력 검증, I/O/DB/API 호출 최적화, 메모리/캐시 이슈\n\n"
    "간결하지만 실무에서 바로 적용 가능한 수준으로 작성하세요. "
    "문제가 없는 항목은 '특이사항 없음'으로 표기하세요."
)

SYSTEM_DIAGNOSE = (
    "당신은 '개쵸'입니다. 봇 시스템 전체에 대한 진단가로서, "
    "주어진 코드베이스 정보와 (선택적으로) 사용자가 제시한 이슈를 분석해 "
    "다음 형식으로 한국어 진단서를 작성합니다:\n\n"
    "## 🔍 현재 시스템 상태\n"
    "## ⚠️ 발견된 잠재 이슈 (Top 5)\n"
    "## 💡 즉시 적용 가능한 개선안 (Top 3)\n"
    "## 🛠️ 중장기 로드맵\n"
    "## 📊 우선순위 매트릭스 (긴급도/영향도)\n\n"
    "실행 가능한 구체적 조치를 제시하고, 각 항목에 예상 작업 시간을 포함하세요."
)


# ═══════════════════════════════════════════════════════════════════
# 공통 유틸: 길이 가드
# ═══════════════════════════════════════════════════════════════════

def _truncate(text, limit, suffix="\n…(생략)"):
    """문자열을 limit 이내로 잘라 반환."""
    if text is None:
        return ""
    if len(text) <= limit:
        return text
    cut = max(0, limit - len(suffix))
    return text[:cut] + suffix


def _safe_embed_description(text):
    """임베드 description 안전 길이로 truncate."""
    return _truncate(text or "", EMBED_DESC_SAFE)


async def send_long_text(target, content, filename="result.md", header=None):
    """긴 텍스트를 안전하게 Discord에 전송."""
    try:
        if not content:
            await target.send(header or "(빈 응답)")
            return

        # message_splitter 활용 시도
        try:
            from utils.message_splitter import send_long_text as _splitter_send
            combined = (header + "\n\n" + content) if header else content
            ok = await _splitter_send(target, combined)
            if ok:
                return
        except Exception as e:
            log.debug("splitter 사용 실패, 폴백 사용: %s", e)

        # 폴백
        combined = (header + "\n\n" + content) if header else content
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
            msg = "⚠️ 메시지 전송 실패: " + str(e.code) + " — 길이/형식 문제일 수 있습니다."
            await target.send(msg)
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════
# 1. 기본 Q&A
# ═══════════════════════════════════════════════════════════════════

async def handle_query(query):
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

        content_text = result.get("content", "") or ""
        model_name = result.get("model", "unknown").split("/")[-1]
        cost_val = float(result.get("cost", 0.0))

        safe_desc = _safe_embed_description(content_text)

        embed = discord.Embed(
            title="🔧 개쵸 — R&D",
            description=safe_desc if safe_desc else "(응답 없음)",
            color=0x06B6D4,
        )

        # f-string 없이 안전하게 footer 구성
        footer_text = model_name + " · $" + format(cost_val, ".5f")
        if len(content_text) > EMBED_DESC_SAFE:
            footer_text = footer_text + " · 일부 생략됨"
        embed.set_footer(text=footer_text[:2048])
        return embed
    except Exception as e:
        from bot.embeds import embed_error
        return embed_error("R&D 오류", str(e))


# ═══════════════════════════════════════════════════════════════════
# 2. 코드 리뷰 (rnd_code_review) — /rnd_health
# ═══════════════════════════════════════════════════════════════════

def _looks_like_path(target):
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


def _read_code_file(path_str):
    """경로 문자열을 읽어 (Path, 내용) 반환. 실패 시 None."""
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
            text = text + "\n\n# ... (파일이 너무 커서 앞부분만 표시됨)"
        return p, text
    except Exception as e:
        log.error("코드 파일 읽기 실패: %s — %s", p, e)
        return None


async def rnd_code_review(target):
    """
    코드 리뷰 명령.
    파일 경로 또는 코드/자유 문장을 받아 4섹션 리뷰 임베드 반환.
    """
    if not target or not target.strip():
        from bot.embeds import embed_error
        return embed_error(
            "코드 리뷰 실패",
            "리뷰할 파일 경로 또는 코드/문장을 입력해주세요.\n"
            "예) /rnd_health modules/rnd.py\n"
            "예) /rnd_health def foo(): return 1/0",
        )

    source_label = "직접 입력"
    code_for_review = target.strip()
    resolved_path = None

    # 1) 파일 경로처럼 보이면 읽기 시도
    if _looks_like_path(target):
        loaded = _read_code_file(target)
        if loaded is not None:
            resolved_path, code_for_review = loaded
            try:
                rel_path = resolved_path.relative_to(Path.cwd())
                source_label = "파일: " + str(rel_path)
            except ValueError:
                source_label = "파일: " + str(resolved_path)
        else:
            source_label = "직접 입력 (경로 해석 실패)"

    # 2) AI 리뷰 요청 프롬프트 구성
    # ⚠️ f-string 안에 백틱 3개를 넣지 않도록 join 방식 사용
    code_snippet = code_for_review[:30000]
    user_prompt_parts = [
        "다음 대상에 대해 코드 리뷰를 수행해주세요.",
        "",
        "**대상**: " + source_label,
        "",
        BACKTICKS,
        code_snippet,
        BACKTICKS,
        "",
        "위 4가지 항목(문법/런타임, 스타일/가독성, 개선 제안, 보안/성능)으로 "
        "리뷰를 작성해주세요.",
    ]
    user_prompt = "\n".join(user_prompt_parts)

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

        review_text = result.get("content", "") or ""
        model_name = result.get("model", "unknown").split("/")[-1]
        cost_val = float(result.get("cost", 0.0))

        safe_desc = _safe_embed_description(review_text)

        embed = discord.Embed(
            title="📋 개쵸 코드 리뷰 — " + source_label,
            description=safe_desc if safe_desc else "(리뷰 결과 없음)",
            color=0x6366F1,
        )

        footer_parts = [model_name, "$" + format(cost_val, ".5f")]
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
# 3. 코드베이스 진단 — /rnd_diagnose
# ═══════════════════════════════════════════════════════════════════

async def diagnose_codebase(issue=None):
    """봇 코드베이스 전체 진단."""
    try:
        codebase_summary = _collect_codebase_summary()

        prompt_parts = [
            "다음은 chip_bot의 현재 상태입니다.",
            "",
            "=== 코드베이스 구조 ===",
            codebase_summary,
        ]

        if issue:
            prompt_parts.append("")
            prompt_parts.append("=== Cho가 보고한 이슈 ===")
            prompt_parts.append(str(issue)[:2000])

        prompt_parts.append("")
        prompt_parts.append("위 정보를 바탕으로 시스템 진단서를 작성해주세요.")

        user_prompt = "\n".join(prompt_parts)

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

        content_text = result.get("content", "") or ""
        model_name = result.get("model", "unknown").split("/")[-1]
        cost_val = float(result.get("cost", 0.0))

        safe_desc = _safe_embed_description(content_text)

        title = "🔬 R&D 코드 리뷰 진단"
        if issue:
            title = title + " — " + str(issue)[:60]

        embed = discord.Embed(
            title=title,
            description=safe_desc if safe_desc else "(진단 결과 없음)",
            color=0x6366F1,
        )

        footer_parts = [model_name, "$" + format(cost_val, ".5f")]
        if len(content_text) > EMBED_DESC_SAFE:
            footer_parts.append("일부 생략됨")
        footer_parts.append("개쵸 R&D · 코드 리뷰 기반 개선 제안")
        embed.set_footer(text=" · ".join(footer_parts)[:2048])

        return embed

    except Exception as e:
        log.exception("진단 실패: %s", e)
        from bot.embeds import embed_error
        return embed_error("진단 오류", str(e))


def _collect_codebase_summary():
    """봇 코드베이스의 구조와 주요 파일 정보를 텍스트로 수집."""
    cwd = Path.cwd()
    lines = [
        "작업 디렉터리: " + str(cwd),
        "진단 시각: " + datetime.now().strftime("%Y-%m-%d %H:%M KST"),
        "",
    ]

    target_dirs = ["bot", "modules", "utils"]
    for d in target_dirs:
        dir_path = cwd / d
        if not dir_path.exists():
            continue

        py_files = sorted(dir_path.glob("*.py"))
        lines.append("### " + d + "/ (" + str(len(py_files)) + "개 파일)")

        for f in py_files:
            try:
                size = f.stat().st_size
                line_count = sum(1 for _ in f.open(encoding="utf-8", errors="replace"))
                lines.append(
                    "  • " + f.name + " — " + format(size, ",") + " bytes, " +
                    format(line_count, ",") + " 줄"
                )
            except Exception:
                lines.append("  • " + f.name + " — (읽기 실패)")
        lines.append("")

    req = cwd / "requirements.txt"
    if req.exists():
        try:
            req_text = req.read_text(encoding="utf-8", errors="replace")
            req_lines = [
                l.strip() for l in req_text.splitlines()
                if l.strip() and not l.startswith("#")
            ]
            lines.append("### requirements.txt (" + str(len(req_lines)) + "개 패키지)")
            for l in req_lines[:30]:
                lines.append("  • " + l)
            if len(req_lines) > 30:
                lines.append("  • ... 외 " + str(len(req_lines) - 30) + "개")
        except Exception:
            pass

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# 4. 신규 봇 설계
# ═══════════════════════════════════════════════════════════════════

async def design_new_bot(requirements):
    """신규 봇 요구사항을 받아 설계서 생성."""
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

        content_text = result.get("content", "") or ""
        model_name = result.get("model", "unknown").split("/")[-1]
        cost_val = float(result.get("cost", 0.0))

        safe_desc = _safe_embed_description(content_text)

        embed = discord.Embed(
            title="🎨 개쵸 — 신규 봇 설계서",
            description=safe_desc if safe_desc else "(설계서 생성 실패)",
            color=0x8B5CF6,
        )

        footer_parts = [model_name, "$" + format(cost_val, ".5f")]
        if len(content_text) > EMBED_DESC_SAFE:
            footer_parts.append("일부 생략됨")
        embed.set_footer(text=" · ".join(footer_parts)[:2048])

        return embed

    except Exception as e:
        log.exception("봇 설계 실패: %s", e)
        from bot.embeds import embed_error
        return embed_error("봇 설계 오류", str(e))


# ═══════════════════════════════════════════════════════════════════
# 5. R&D 채널 게시 — 외부 모듈에서 호출
# ═══════════════════════════════════════════════════════════════════

async def post_to_rnd_channel(
    bot,
    category="general",
    title="",
    content="",
    embed=None,
):
    """R&D 채널에 자동 게시."""
    ch_id_str = os.getenv("RND_CHANNEL_ID", "").strip()
    if not ch_id_str.isdigit():
        log.info("RND_CHANNEL_ID 미설정 — R&D 채널 게시 건너뜀")
        return False

    channel = bot.get_channel(int(ch_id_str))
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        log.warning("RND_CHANNEL_ID가 텍스트 채널이 아님: %s", type(channel).__name__)
        return False

    try:
        color_map = {
            "maintenance": 0x06B6D4,
            "update":      0x059669,
            "error":       0xE11D48,
            "warning":     0xEAB308,
            "general":     0x6366F1,
        }
        emoji_map = {
            "maintenance": "🔧",
            "update":      "🚀",
            "error":       "🚨",
            "warning":     "⚠️",
            "general":     "📢",
        }
        color = color_map.get(category, 0x6366F1)
        emoji = emoji_map.get(category, "📢")

        if embed is None:
            embed_title = emoji + " " + (title or category.upper())
            embed = discord.Embed(
                title=embed_title,
                description=_safe_embed_description(content),
                color=color,
                timestamp=datetime.now(),
            )
            embed.set_footer(text="개쵸 R&D · " + category)

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

async def announce_update(bot, version, changes, pr_url=None):
    """업데이트 공지를 R&D 채널에 게시."""
    lines = ["## 🚀 업데이트 — " + str(version), ""]
    for c in changes[:20]:
        lines.append("- " + str(c))
    if len(changes) > 20:
        lines.append("- ... 외 " + str(len(changes) - 20) + "개")

    if pr_url:
        lines.append("")
        lines.append("🔗 **PR**: " + str(pr_url))

    content = "\n".join(lines)
    return await post_to_rnd_channel(
        bot,
        category="update",
        title="업데이트 " + str(version),
        content=content,
    )

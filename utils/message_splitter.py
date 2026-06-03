"""
utils/message_splitter.py
Discord 메시지/Embed 길이 제한 대응 — 자동 분할 전송 + 파일 첨부.

핵심 정책:
- description이 1,400자(SAFE) 초과 → 분할
- 마크다운 표(|)와 코드 블록(```)은 절대 끊지 않음
- 분할 발생 시 항상 .md 파일을 마지막에 첨부 (만일의 대비)
- Interaction 객체는 항상 followup.send() 사용 (ephemeral 호환)
"""

import io
import logging
from datetime import datetime
from typing import Optional, Union

import discord

log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# 분할 제한값
# ═══════════════════════════════════════════════════════════════════
MAX_CONTENT_LENGTH = 2000        # Discord 일반 메시지
MAX_DESCRIPTION_LENGTH = 4096    # Embed description
MAX_FIELD_VALUE_LENGTH = 1024    # Embed field value
MAX_EMBED_TOTAL = 6000           # Embed 합계
MAX_EMBED_FIELDS = 25

# ⭐ Cho 요청 — 1,400자 기준 1차 컷 + 연속 전송
SAFE_CHUNK_LENGTH = 1400         # 안전 분할 크기
MAX_SPLIT_EMBEDS = 8             # 한 응답 최대 Embed 수 (초과 시 파일만)
ATTACH_FILE_THRESHOLD = 1500     # 이 길이 초과 시 .md 파일 항상 첨부


# ═══════════════════════════════════════════════════════════════════
# 텍스트 분할 (마크다운 보호)
# ═══════════════════════════════════════════════════════════════════

def smart_split_text(text: str, max_length: int = SAFE_CHUNK_LENGTH) -> list[str]:
    """
    마크다운 구조를 보호하며 텍스트를 분할.

    보호 대상:
    - 코드 블록 (```...```)
    - 마크다운 표 (|...|)
    - 문단 경계 (\n\n)

    분할 우선순위:
    1. 코드/표 블록 외부의 문단 경계
    2. 줄 경계 (\n)
    3. 한국어 문장 종결 (다., 요., 죠.)
    4. 영문 문장 종결 (. ? !)
    5. 공백
    6. 강제 분할
    """
    if not text:
        return []
    if len(text) <= max_length:
        return [text]

    protected_ranges = _find_protected_ranges(text)

    parts: list[str] = []
    cursor = 0
    text_len = len(text)

    while cursor < text_len:
        remaining_len = text_len - cursor
        if remaining_len <= max_length:
            parts.append(text[cursor:].strip())
            break

        end_target = cursor + max_length
        split_at = _find_safe_split(text, cursor, end_target, protected_ranges)

        if split_at <= cursor:
            split_at = end_target

        chunk = text[cursor:split_at].strip()
        if chunk:
            parts.append(chunk)
        cursor = split_at

    return parts


def _find_protected_ranges(text: str) -> list[tuple[int, int]]:
    """코드 블록(```)과 표(|...|) 영역을 찾아 (start, end) 튜플 리스트 반환."""
    ranges = []

    # 1) 코드 블록 ```...```
    i = 0
    while True:
        start = text.find("```", i)
        if start == -1:
            break
        end = text.find("```", start + 3)
        if end == -1:
            break
        ranges.append((start, end + 3))
        i = end + 3

    # 2) 마크다운 표
    lines = text.split("\n")
    pos = 0
    table_start = None
    for line in lines:
        line_end = pos + len(line)
        is_table_line = line.strip().startswith("|") and line.strip().endswith("|")
        if is_table_line:
            if table_start is None:
                table_start = pos
        else:
            if table_start is not None and pos - 1 > table_start:
                ranges.append((table_start, pos))
                table_start = None
        pos = line_end + 1

    if table_start is not None:
        ranges.append((table_start, pos))

    # 정렬 + 병합
    ranges.sort()
    merged = []
    for start, end in ranges:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _is_in_protected(pos: int, ranges: list[tuple[int, int]]) -> bool:
    """주어진 위치가 보호 영역 안에 있는지."""
    for start, end in ranges:
        if start <= pos < end:
            return True
    return False


def _find_safe_split(
    text: str,
    cursor: int,
    end_target: int,
    protected: list[tuple[int, int]],
) -> int:
    """cursor와 end_target 사이에서 가장 안전한 분할 위치 반환."""
    end_target = min(end_target, len(text))
    min_acceptable = cursor + (end_target - cursor) // 2

    for start, end in protected:
        if start <= end_target < end:
            return max(start - 1, min_acceptable)

    candidates = [
        ("\n\n", 2),
        ("다.\n", 3),
        ("요.\n", 3),
        ("죠.\n", 3),
        ("\n", 1),
        ("。 ", 2),
        (". ", 2),
        ("? ", 2),
        ("! ", 2),
        (" ", 1),
    ]

    for sep, sep_len in candidates:
        idx = text.rfind(sep, cursor, end_target)
        if idx >= min_acceptable and not _is_in_protected(idx, protected):
            return idx + sep_len

    for pos in range(end_target, min_acceptable, -1):
        if not _is_in_protected(pos, protected):
            return pos
    return end_target


# ═══════════════════════════════════════════════════════════════════
# Embed 분할
# ═══════════════════════════════════════════════════════════════════

def split_embed_to_parts(
    embed: discord.Embed,
    chunk_length: int = SAFE_CHUNK_LENGTH,
) -> list[discord.Embed]:
    """긴 Embed를 여러 개로 분할. 항상 1개 이상 반환."""
    description = embed.description or ""
    extra_text = ""

    valid_fields = []
    for f in embed.fields:
        value = f.value or ""
        if len(value) > MAX_FIELD_VALUE_LENGTH:
            extra_text += f"\n\n**{f.name}**\n{value}"
        else:
            valid_fields.append(f)

    full_text = (description + extra_text).strip()

    if len(full_text) <= chunk_length and len(extra_text) == 0:
        return [embed]

    parts = smart_split_text(full_text, max_length=chunk_length)
    if not parts:
        parts = [full_text or "(빈 응답)"]

    if len(parts) > MAX_SPLIT_EMBEDS:
        kept = MAX_SPLIT_EMBEDS - 1
        parts = parts[:kept] + [
            "⚠️ 내용이 매우 길어 일부만 표시합니다.\n"
            "**전체 내용은 첨부된 .md 파일을 확인해주세요.**\n\n"
            + parts[kept]
        ]

    total = len(parts)
    base_title = embed.title or "응답"
    base_color = embed.color
    base_timestamp = embed.timestamp
    base_footer = embed.footer.text if embed.footer else None

    embeds = []
    for i, part in enumerate(parts):
        title = f"{base_title} ({i + 1}/{total})" if total > 1 else base_title

        new_embed = discord.Embed(
            title=title[:256],
            description=part[:MAX_DESCRIPTION_LENGTH],
            color=base_color,
            timestamp=base_timestamp,
        )

        if i == 0 and valid_fields:
            for f in valid_fields[:MAX_EMBED_FIELDS]:
                new_embed.add_field(
                    name=(f.name or "")[:256],
                    value=(f.value or "")[:MAX_FIELD_VALUE_LENGTH],
                    inline=f.inline,
                )

        if i == total - 1 and base_footer:
            new_embed.set_footer(text=base_footer[:2048])

        embeds.append(new_embed)

    return embeds


# ═══════════════════════════════════════════════════════════════════
# 파일 변환
# ═══════════════════════════════════════════════════════════════════

def embed_to_md_file(
    embed: discord.Embed,
    *,
    query: str = "",
    filename_prefix: str = "response",
) -> discord.File:
    """Embed → Markdown 파일."""
    lines = [f"# {embed.title or '응답'}\n"]
    if query:
        lines.append(f"> **요청**: `{query}`\n")
    lines.append(f"> **생성 시각**: {datetime.now():%Y-%m-%d %H:%M:%S}\n")
    lines.append("\n---\n")

    if embed.description:
        lines.append(embed.description + "\n")

    for f in embed.fields:
        lines.append(f"\n## {f.name}\n\n{f.value}\n")

    if embed.footer and embed.footer.text:
        lines.append(f"\n---\n*{embed.footer.text}*\n")

    content = "\n".join(lines)
    filename = f"{filename_prefix}_{datetime.now():%Y%m%d_%H%M%S}.md"
    return discord.File(
        fp=io.BytesIO(content.encode("utf-8")),
        filename=filename,
    )


def embed_to_html_file(
    embed: discord.Embed,
    *,
    query: str = "",
    filename_prefix: str = "response",
) -> discord.File:
    """Embed → HTML 파일 (가독성 좋은 버전)."""
    title = embed.title or "응답"
    body_parts = []

    if embed.description:
        body_parts.append(f'<div class="description">{_md_to_html(embed.description)}</div>')

    for f in embed.fields:
        body_parts.append(f'<h2>{_html_escape(f.name)}</h2>')
        body_parts.append(f'<div class="field">{_md_to_html(f.value)}</div>')

    footer = embed.footer.text if embed.footer and embed.footer.text else ""

    html = f"""<!DOCTYPE html>
<html lang="ko"><head>
<meta charset="UTF-8">
<title>{_html_escape(title)}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Pretendard", sans-serif;
       max-width: 880px; margin: 2em auto; padding: 0 1.5em; line-height: 1.7;
       color: #1f2937; background: #fafafa; }}
h1 {{ color: #4f46e5; border-bottom: 3px solid #4f46e5; padding-bottom: .3em; }}
h2 {{ color: #1e293b; margin-top: 2em; border-left: 4px solid #4f46e5; padding-left: .5em; }}
.meta {{ color: #6b7280; font-size: .9em; margin-bottom: 2em; }}
.description, .field {{ background: #fff; padding: 1.2em 1.5em; border-radius: 8px;
                        box-shadow: 0 1px 3px rgba(0,0,0,.05); margin: 1em 0; }}
table {{ border-collapse: collapse; width: 100%; margin: 1em 0; }}
th, td {{ border: 1px solid #e5e7eb; padding: 8px 12px; text-align: left; }}
th {{ background: #f3f4f6; font-weight: 600; }}
code {{ background: #f3f4f6; padding: 2px 6px; border-radius: 4px;
        font-family: "JetBrains Mono", monospace; font-size: .9em; }}
pre {{ background: #1f2937; color: #f9fafb; padding: 1em; border-radius: 8px;
       overflow-x: auto; }}
pre code {{ background: transparent; color: inherit; padding: 0; }}
footer {{ margin-top: 3em; padding-top: 1em; border-top: 1px solid #e5e7eb;
          color: #9ca3af; font-size: .85em; text-align: center; }}
</style></head>
<body>
<h1>{_html_escape(title)}</h1>
<div class="meta">
{f'<div>요청: <code>{_html_escape(query)}</code></div>' if query else ''}
<div>생성: {datetime.now():%Y-%m-%d %H:%M:%S}</div>
</div>
{"".join(body_parts)}
<footer>{_html_escape(footer)}</footer>
</body></html>"""

    filename = f"{filename_prefix}_{datetime.now():%Y%m%d_%H%M%S}.html"
    return discord.File(
        fp=io.BytesIO(html.encode("utf-8")),
        filename=filename,
    )


def _html_escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace('"', "&quot;").replace("'", "&#39;"))


def _md_to_html(md: str) -> str:
    """간단한 Markdown → HTML 변환 (표·코드 블록·강조)."""
    import re
    text = md

    text = re.sub(r"```(\w*)\n?(.*?)```", lambda m:
                  f"<pre><code>{_html_escape(m.group(2))}</code></pre>",
                  text, flags=re.DOTALL)

    text = re.sub(r"`([^`]+)`", lambda m:
                  f"<code>{_html_escape(m.group(1))}</code>", text)

    lines = text.split("\n")
    out_lines = []
    in_table = False
    for line in lines:
        stripped = line.strip()
        is_row = stripped.startswith("|") and stripped.endswith("|")
        is_sep = is_row and all(
            c in "-:| " for c in stripped.replace("|", "").strip()
        )
        if is_row and not is_sep:
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            tag = "th" if not in_table else "td"
            row_html = "".join(f"<{tag}>{_html_escape(c)}</{tag}>" for c in cells)
            if not in_table:
                out_lines.append("<table><thead><tr>" + row_html + "</tr></thead><tbody>")
                in_table = True
            else:
                out_lines.append("<tr>" + row_html + "</tr>")
        elif is_sep:
            continue
        else:
            if in_table:
                out_lines.append("</tbody></table>")
                in_table = False
            out_lines.append(line)
    if in_table:
        out_lines.append("</tbody></table>")
    text = "\n".join(out_lines)

    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", text)
    text = text.replace("\n\n", "</p><p>").replace("\n", "<br>")
    text = f"<p>{text}</p>"

    return text


# ═══════════════════════════════════════════════════════════════════
# 메인 API — send_long_embed / edit_long_embed
# ═══════════════════════════════════════════════════════════════════

async def send_long_embed(
    target: Union[discord.Interaction, discord.abc.Messageable],
    embed: discord.Embed,
    *,
    view: Optional[discord.ui.View] = None,
    ephemeral: bool = False,
    query: str = "",
    attach_files: bool = True,
) -> bool:
    """
    긴 Embed를 안전하게 전송. 자동 분할 + 파일 첨부.

    Returns:
        성공 여부 (False면 호출자가 폴백 처리)
    """
    try:
        embeds = split_embed_to_parts(embed)
        if not embeds:
            log.warning("split_embed_to_parts가 빈 리스트 반환 — 빈 Embed 폴백")
            return False

        total_length = _calculate_total_length(embed)

        files = []
        if attach_files and total_length >= ATTACH_FILE_THRESHOLD:
            try:
                files.append(embed_to_md_file(embed, query=query))
            except Exception as e:
                log.warning(f".md 파일 생성 실패: {e}")

        # 1) 첫 Embed + View 전송
        ok = await _safe_send(
            target, embed=embeds[0], view=view, ephemeral=ephemeral,
        )
        if not ok:
            return False

        # 2) 나머지 Embed들 연속 전송
        for i, extra in enumerate(embeds[1:], start=2):
            success = await _safe_send(target, embed=extra, ephemeral=ephemeral)
            if not success:
                log.warning(f"Embed {i}/{len(embeds)} 전송 실패")

        # 3) 파일 첨부 (마지막)
        if files:
            await _safe_send(
                target,
                content="📎 **전체 내용 — 백업 파일**",
                file=files[0],
                ephemeral=ephemeral,
            )

        return True

    except discord.NotFound:
        # 호출자가 폴백 처리할 수 있도록 raise
        raise
    except Exception as e:
        log.exception(f"send_long_embed 실패: {e}")
        return False


async def edit_long_embed(
    message: discord.Message,
    embed: discord.Embed,
    *,
    view: Optional[discord.ui.View] = None,
    interaction: Optional[discord.Interaction] = None,
    query: str = "",
    attach_files: bool = True,
) -> bool:
    """
    기존 메시지를 긴 Embed로 편집. 분할되면 추가 메시지로 이어 전송.
    """
    try:
        embeds = split_embed_to_parts(embed)
        if not embeds:
            log.warning("split_embed_to_parts가 빈 리스트 반환")
            return False

        total_length = _calculate_total_length(embed)
        log.info(f"edit_long_embed: 총 {total_length}자 → {len(embeds)}개 Embed")

        # 첫 Embed로 원본 메시지 편집
        try:
            await message.edit(embed=embeds[0], view=view, attachments=[])
            log.info(f"첫 Embed 편집 성공 (1/{len(embeds)})")
        except Exception as e:
            log.error(f"메시지 편집 실패: {e} → 새 메시지로 폴백")
            sender = interaction if interaction is not None else message.channel
            ok = await _safe_send(sender, embed=embeds[0])
            if not ok:
                return False

        # 추가 Embed들 전송
        sender = interaction if interaction is not None else message.channel

        for i, extra in enumerate(embeds[1:], start=2):
            try:
                ok = await _safe_send(sender, embed=extra)
                if ok:
                    log.info(f"추가 Embed 전송 성공 ({i}/{len(embeds)})")
                else:
                    log.warning(f"추가 Embed 전송 실패 ({i}/{len(embeds)})")
            except Exception as e:
                log.error(f"추가 Embed {i} 예외: {e}")

        # 파일 첨부 (마지막)
        if attach_files and total_length >= ATTACH_FILE_THRESHOLD:
            try:
                md_file = embed_to_md_file(embed, query=query)
                ok = await _safe_send(
                    sender,
                    content="📎 **전체 내용 — 백업 파일**",
                    file=md_file,
                )
                if ok:
                    log.info(f"백업 .md 파일 첨부 성공 ({total_length}자)")
                else:
                    log.warning("백업 .md 파일 첨부 실패")
            except Exception as e:
                log.error(f"파일 첨부 실패: {e}")

        return True

    except Exception as e:
        log.exception(f"edit_long_embed 실패: {e}")
        # 마지막 폴백 — 새 메시지로 다시 시도
        try:
            sender = interaction if interaction is not None else message.channel
            return await send_long_embed(
                sender, embed, query=query, attach_files=attach_files,
            )
        except Exception:
            return False

# ═══════════════════════════════════════════════════════════════════
# 내부 헬퍼
# ═══════════════════════════════════════════════════════════════════

async def _safe_send(
    target,
    *,
    content: Optional[str] = None,
    embed: Optional[discord.Embed] = None,
    file: Optional[discord.File] = None,
    view: Optional[discord.ui.View] = None,
    ephemeral: bool = False,
    raise_on_critical: bool = False,
) -> bool:
    """
    타입별 send 분기. 일반 예외는 흡수, 치명적 예외는 raise 가능.

    raise_on_critical=True 시 NotFound/Forbidden은 raise.
    """
    kwargs = {}
    if content is not None:
        kwargs["content"] = content
    if embed is not None:
        kwargs["embed"] = embed
    if file is not None:
        kwargs["file"] = file
    if view is not None:
        kwargs["view"] = view

    try:
        if isinstance(target, discord.Interaction):
            kwargs["ephemeral"] = ephemeral
            if target.response.is_done():
                await target.followup.send(**kwargs)
            else:
                await target.response.send_message(**kwargs)
        elif isinstance(target, (discord.User, discord.Member)):
            await target.send(**{k: v for k, v in kwargs.items() if k != "ephemeral"})
        else:
            await target.send(**{k: v for k, v in kwargs.items() if k != "ephemeral"})
        return True
    except (discord.NotFound, discord.Forbidden) as e:
        log.warning(f"_safe_send 치명적 예외 (target={type(target).__name__}): {e}")
        if raise_on_critical:
            raise
        return False
    except Exception as e:
        log.error(f"_safe_send 실패 (target={type(target).__name__}): {e}")
        return False


def _calculate_total_length(embed: discord.Embed) -> int:
    total = len(embed.title or "") + len(embed.description or "")
    for f in embed.fields:
        total += len(f.name or "") + len(f.value or "")
    if embed.footer and embed.footer.text:
        total += len(embed.footer.text)
    return total


# ═══════════════════════════════════════════════════════════════════
# 일반 텍스트용 (Embed 아닌 경우 — 부가)
# ═══════════════════════════════════════════════════════════════════

async def send_long_text(
    target: Union[discord.Interaction, discord.abc.Messageable],
    content: str,
    *,
    max_messages: int = 8,
    ephemeral: bool = False,
) -> bool:
    """긴 일반 텍스트(content)를 분할 전송. 2,000자 제한 대응."""
    try:
        if not content:
            return True

        parts = smart_split_text(content, max_length=SAFE_CHUNK_LENGTH)
        parts = parts[:max_messages]

        total = len(parts)
        for i, part in enumerate(parts, start=1):
            prefix = f"**({i}/{total})** " if total > 1 else ""
            await _safe_send(target, content=prefix + part, ephemeral=ephemeral)

        if len(content) >= ATTACH_FILE_THRESHOLD:
            file = discord.File(
                fp=io.BytesIO(content.encode("utf-8")),
                filename=f"text_{datetime.now():%Y%m%d_%H%M%S}.md",
            )
            await _safe_send(
                target,
                content="📎 **전체 내용 — 백업 파일**",
                file=file,
                ephemeral=ephemeral,
            )

        return True

    except Exception as e:
        log.exception(f"send_long_text 실패: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════
# 향후 확장 — PDF 변환 (선택)
# ═══════════════════════════════════════════════════════════════════

def embed_to_pdf_file(
    embed: discord.Embed,
    *,
    query: str = "",
    filename_prefix: str = "response",
) -> Optional[discord.File]:
    """
    Embed → PDF 파일.

    requirements.txt에 weasyprint 추가 필요:
        weasyprint==62.3

    weasyprint가 없으면 None 반환 (HTML로 폴백 권장).
    """
    try:
        from weasyprint import HTML
    except ImportError:
        log.info("weasyprint 미설치 — PDF 변환 건너뜀")
        return None

    try:
        html_file = embed_to_html_file(embed, query=query, filename_prefix=filename_prefix)
        html_file.fp.seek(0)
        html_content = html_file.fp.read().decode("utf-8")

        pdf_bytes = HTML(string=html_content).write_pdf()
        filename = f"{filename_prefix}_{datetime.now():%Y%m%d_%H%M%S}.pdf"
        return discord.File(
            fp=io.BytesIO(pdf_bytes),
            filename=filename,
        )
    except Exception as e:
        log.warning(f"PDF 변환 실패: {e}")
        return None
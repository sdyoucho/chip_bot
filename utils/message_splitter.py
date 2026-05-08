"""
utils/message_splitter.py
Discord 메시지/Embed 길이 제한 대응 — 자동 분할 전송.

Discord 제한:
- 일반 메시지 content: 2,000자
- Embed description: 4,096자
- Embed field value: 1,024자
- Embed 전체 합계: 6,000자

이 모듈의 전략:
1. description이 4,096자 이하 → 단일 Embed 그대로 전송
2. 4,096자 초과 → 여러 Embed로 분할 (페이지 1/N 표시)
3. 너무 길어서 N개 초과 → 텍스트 파일로 첨부
"""

import io
import logging
from datetime import datetime
from typing import Optional

import discord

log = logging.getLogger(__name__)

# Discord 제한
MAX_CONTENT_LENGTH = 2000
MAX_DESCRIPTION_LENGTH = 4096
MAX_FIELD_VALUE_LENGTH = 1024
MAX_EMBED_TOTAL = 6000
MAX_EMBED_FIELDS = 25

# 분할 설정
SAFE_DESCRIPTION_LENGTH = 3900   # 여유분 확보
MAX_SPLIT_EMBEDS = 5             # 한 응답당 최대 Embed 수 (초과 시 파일)


# ═══════════════════════════════════════════════════════════════════
# 텍스트 분할 헬퍼
# ═══════════════════════════════════════════════════════════════════

def smart_split_text(text: str, max_length: int = SAFE_DESCRIPTION_LENGTH) -> list[str]:
    """
    텍스트를 자연스러운 위치(줄바꿈/공백)에서 분할.
    우선순위: 문단(\n\n) > 줄(\n) > 문장(. ) > 공백 > 강제
    """
    if not text:
        return []
    if len(text) <= max_length:
        return [text]

    parts: list[str] = []
    remaining = text

    while len(remaining) > max_length:
        # 1순위: 문단 경계
        split_idx = remaining.rfind("\n\n", 0, max_length)
        if split_idx == -1 or split_idx < max_length // 2:
            # 2순위: 줄 경계
            split_idx = remaining.rfind("\n", 0, max_length)
        if split_idx == -1 or split_idx < max_length // 2:
            # 3순위: 문장 경계 (한국어/영문)
            for sep in [". ", "? ", "! ", "다.\n", "요.\n", "죠.\n"]:
                idx = remaining.rfind(sep, 0, max_length)
                if idx > max_length // 2:
                    split_idx = idx + len(sep)
                    break
            else:
                # 4순위: 공백
                split_idx = remaining.rfind(" ", 0, max_length)
                if split_idx == -1 or split_idx < max_length // 2:
                    # 5순위: 강제 분할
                    split_idx = max_length

        parts.append(remaining[:split_idx].rstrip())
        remaining = remaining[split_idx:].lstrip()

    if remaining:
        parts.append(remaining)
    return parts


# ═══════════════════════════════════════════════════════════════════
# Embed 분할
# ═══════════════════════════════════════════════════════════════════

def split_embed(
    embed: discord.Embed,
    max_parts: int = MAX_SPLIT_EMBEDS,
) -> list[discord.Embed]:
    """
    긴 Embed를 여러 개로 분할.

    분할 대상:
    1. description이 너무 긴 경우
    2. 필드 value가 1024자 초과
    3. 전체 합계가 6000자 초과

    반환: 1개 이상의 Embed 리스트. 제목에 (1/N) 표시.
    """
    # 먼저 단순 케이스: 유효하면 그대로 반환
    if _embed_within_limits(embed):
        return [embed]

    # description 길이 체크
    description = embed.description or ""
    parts_desc = smart_split_text(description, SAFE_DESCRIPTION_LENGTH)

    # 필드도 길이 초과 체크
    valid_fields = []
    for f in embed.fields:
        value = f.value or ""
        if len(value) > MAX_FIELD_VALUE_LENGTH:
            # 긴 필드는 description으로 승격
            parts_desc.append(f"\n**{f.name}**\n{value}")
        else:
            valid_fields.append(f)

    # 최종 description 재분할 (필드 승격된 것 포함)
    combined = "\n\n".join(parts_desc)
    final_parts = smart_split_text(combined, SAFE_DESCRIPTION_LENGTH)

    # 너무 많이 나뉘면 max_parts로 제한
    if len(final_parts) > max_parts:
        log.warning(f"Embed 분할 제한 초과: {len(final_parts)} > {max_parts}")
        final_parts = final_parts[:max_parts]
        # 마지막 파트에 잘림 표시
        final_parts[-1] += (
            "\n\n⚠️ 내용이 더 있었으나 Discord 제한으로 잘렸습니다.\n"
            "전체 내용은 첨부 파일을 확인해주세요."
        )

    # Embed 리스트 생성
    embeds = []
    total = len(final_parts)
    base_title = embed.title or "응답"

    for i, part in enumerate(final_parts):
        new_embed = discord.Embed(
            title=f"{base_title} ({i + 1}/{total})" if total > 1 else base_title,
            description=part,
            color=embed.color,
            timestamp=embed.timestamp,
        )

        # 첫 번째 Embed에만 원본 필드(유효한 것들) 포함
        if i == 0:
            for f in valid_fields[:MAX_EMBED_FIELDS]:
                new_embed.add_field(
                    name=f.name,
                    value=f.value,
                    inline=f.inline,
                )

        # 마지막 Embed에만 footer 표시
        if i == total - 1 and embed.footer and embed.footer.text:
            new_embed.set_footer(text=embed.footer.text)

        embeds.append(new_embed)

    return embeds


def _embed_within_limits(embed: discord.Embed) -> bool:
    """Embed가 Discord 제한 안에 있는지 확인."""
    if embed.description and len(embed.description) > MAX_DESCRIPTION_LENGTH:
        return False
    for f in embed.fields:
        if f.value and len(f.value) > MAX_FIELD_VALUE_LENGTH:
            return False

    total = len(embed.title or "") + len(embed.description or "")
    for f in embed.fields:
        total += len(f.name or "") + len(f.value or "")
    return total <= MAX_EMBED_TOTAL


# ═══════════════════════════════════════════════════════════════════
# 안전한 전송 함수 (메인 API)
# ═══════════════════════════════════════════════════════════════════

async def send_long_embed(
    target,
    embed: discord.Embed,
    *,
    view: Optional[discord.ui.View] = None,
    ephemeral: bool = False,
    force_file_threshold: int = 15000,
) -> bool:
    """
    긴 Embed를 안전하게 전송. 필요 시 자동 분할.

    Args:
        target: discord.Interaction, discord.TextChannel, discord.Webhook,
                discord.ForumChannel 스레드 등 send 가능한 객체
        embed: 전송할 Embed
        view: 첫 메시지에 붙일 View (정지 버튼 등)
        ephemeral: ephemeral 여부 (Interaction일 때만 적용)
        force_file_threshold: 이 길이 초과 시 분할 없이 바로 파일 첨부

    Returns:
        전송 성공 여부
    """
    try:
        # 1) 총 길이 계산
        total_length = _calculate_total_length(embed)

        # 2) 너무 길면 바로 파일로
        if total_length > force_file_threshold:
            return await _send_as_file(target, embed, ephemeral=ephemeral, view=view)

        # 3) 분할
        embeds = split_embed(embed)

        # 4) 단일 Embed면 그대로 전송
        if len(embeds) == 1:
            return await _send_single(target, embeds[0], view=view, ephemeral=ephemeral)

        # 5) 다중 Embed 전송
        return await _send_multiple(target, embeds, view=view, ephemeral=ephemeral)

    except Exception as e:
        log.exception(f"send_long_embed 실패: {e}")
        return False


async def edit_long_embed(
    message: discord.Message,
    embed: discord.Embed,
    *,
    view: Optional[discord.ui.View] = None,
) -> bool:
    """
    기존 메시지를 긴 Embed로 편집. 필요 시 분할.

    첫 Embed만 해당 메시지로 편집하고,
    나머지 Embed들은 같은 채널에 **이어지는 메시지**로 추가 전송.
    """
    try:
        total_length = _calculate_total_length(embed)

        # 너무 길면 파일 첨부 공지로 대체
        if total_length > 15000:
            notice = discord.Embed(
                title="📎 응답이 길어 파일로 전달합니다",
                description=f"{embed.title or '응답'}\n\n총 {total_length:,}자 분량",
                color=0xEAB308,
            )
            file = _embed_to_file(embed)
            await message.edit(embed=notice, view=view)
            await message.channel.send(file=file)
            return True

        embeds = split_embed(embed)

        # 첫 Embed로 원본 메시지 편집
        await message.edit(embed=embeds[0], view=view)

        # 나머지는 새 메시지로 추가
        for extra_embed in embeds[1:]:
            await message.channel.send(embed=extra_embed)

        return True

    except Exception as e:
        log.exception(f"edit_long_embed 실패: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════
# 내부 전송 헬퍼
# ═══════════════════════════════════════════════════════════════════

def _calculate_total_length(embed: discord.Embed) -> int:
    total = len(embed.title or "") + len(embed.description or "")
    for f in embed.fields:
        total += len(f.name or "") + len(f.value or "")
    if embed.footer and embed.footer.text:
        total += len(embed.footer.text)
    return total


async def _send_single(
    target,
    embed: discord.Embed,
    *,
    view: Optional[discord.ui.View] = None,
    ephemeral: bool = False,
) -> bool:
    """단일 Embed 전송 (target 타입에 따라 분기)."""
    kwargs = {"embed": embed}
    if view is not None:
        kwargs["view"] = view

    try:
        if isinstance(target, discord.Interaction):
            if target.response.is_done():
                kwargs["ephemeral"] = ephemeral
                await target.followup.send(**kwargs)
            else:
                kwargs["ephemeral"] = ephemeral
                await target.response.send_message(**kwargs)
        else:
            # TextChannel, Webhook, Thread 등
            await target.send(**kwargs)
        return True
    except Exception as e:
        log.error(f"_send_single 실패: {e}")
        return False


async def _send_multiple(
    target,
    embeds: list[discord.Embed],
    *,
    view: Optional[discord.ui.View] = None,
    ephemeral: bool = False,
) -> bool:
    """
    다중 Embed 전송.
    첫 메시지에 view 붙이고, 2번째 메시지부터는 이어서.
    """
    try:
        # 첫 메시지
        ok = await _send_single(
            target, embeds[0], view=view, ephemeral=ephemeral,
        )
        if not ok:
            return False

        # 나머지 Embed들을 이어서 전송
        # Interaction의 경우 followup으로, 채널은 send로
        for extra in embeds[1:]:
            try:
                if isinstance(target, discord.Interaction):
                    await target.followup.send(embed=extra, ephemeral=ephemeral)
                else:
                    await target.send(embed=extra)
            except Exception as e:
                log.error(f"추가 Embed 전송 실패: {e}")
                continue

        return True

    except Exception as e:
        log.error(f"_send_multiple 실패: {e}")
        return False


async def _send_as_file(
    target,
    embed: discord.Embed,
    *,
    ephemeral: bool = False,
    view: Optional[discord.ui.View] = None,
) -> bool:
    """Embed를 파일로 첨부하여 전송."""
    try:
        file = _embed_to_file(embed)
        notice = discord.Embed(
            title="📎 응답이 길어 파일로 전달합니다",
            description=(
                f"{embed.title or '응답'}\n\n"
                f"Discord Embed 제한(6,000자)을 초과하여\n"
                f"전체 내용을 Markdown 파일로 첨부합니다."
            ),
            color=0xEAB308,
        )

        kwargs = {"embed": notice, "file": file}
        if view is not None:
            kwargs["view"] = view

        if isinstance(target, discord.Interaction):
            kwargs["ephemeral"] = ephemeral
            if target.response.is_done():
                await target.followup.send(**kwargs)
            else:
                await target.response.send_message(**kwargs)
        else:
            await target.send(**kwargs)

        return True

    except Exception as e:
        log.error(f"_send_as_file 실패: {e}")
        return False


def _embed_to_file(embed: discord.Embed) -> discord.File:
    """Embed → Markdown 파일 객체."""
    lines = []
    if embed.title:
        lines.append(f"# {embed.title}\n")
    if embed.description:
        lines.append(embed.description + "\n")
    for f in embed.fields:
        lines.append(f"\n## {f.name}\n{f.value}\n")
    if embed.footer and embed.footer.text:
        lines.append(f"\n---\n_{embed.footer.text}_")

    content = "\n".join(lines)
    return discord.File(
        fp=io.BytesIO(content.encode("utf-8")),
        filename=f"response_{datetime.now():%Y%m%d_%H%M%S}.md",
    )


# ═══════════════════════════════════════════════════════════════════
# 일반 텍스트 메시지 분할 (content 2000자 대응)
# ═══════════════════════════════════════════════════════════════════

async def send_long_text(
    target,
    content: str,
    *,
    max_messages: int = 5,
) -> bool:
    """
    긴 일반 텍스트 메시지를 자동 분할 전송.
    (Embed가 아닌 content용 - 2,000자 제한 대응)
    """
    try:
        if len(content) <= MAX_CONTENT_LENGTH:
            if isinstance(target, discord.Interaction):
                await target.followup.send(content=content)
            else:
                await target.send(content=content)
            return True

        # 분할 (2000자는 안전하게 1900자 기준)
        parts = smart_split_text(content, max_length=1900)

        if len(parts) > max_messages:
            parts = parts[:max_messages]
            parts[-1] += "\n\n⚠️ 내용이 너무 길어 일부만 표시됩니다."

        total = len(parts)
        for i, part in enumerate(parts):
            prefix = f"**({i + 1}/{total})**\n" if total > 1 else ""
            message_text = prefix + part

            if isinstance(target, discord.Interaction):
                await target.followup.send(content=message_text)
            else:
                await target.send(content=message_text)

        return True

    except Exception as e:
        log.exception(f"send_long_text 실패: {e}")
        return False
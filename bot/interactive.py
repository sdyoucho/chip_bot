"""
bot/interactive.py
/ask 등 장시간 작업의 UI 컴포넌트.

기능:
- 진행 상태 실시간 업데이트
- 정지 버튼 (작업 취소)
- 재시도 버튼
"""

import asyncio
import logging
import time
from datetime import datetime

import discord

log = logging.getLogger(__name__)


class AskProgressView(discord.ui.View):
    """
    /ask 실행 중 표시되는 인터랙티브 View.
    - 🛑 정지 버튼: 작업 취소
    - 🔄 재시도 버튼: 동일 쿼리 재실행
    """

    def __init__(
        self,
        task: asyncio.Task | None = None,
        query: str = "",
        owner_id: int = 0,
        timeout: float = 900.0,   # 15분
    ):
        super().__init__(timeout=timeout)
        self.task = task
        self.query = query
        self.owner_id = owner_id
        self.cancelled = False
        self.started_at = time.time()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """봇 소유자(Cho)만 버튼 조작 가능."""
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "❌ 이 버튼은 요청자만 사용할 수 있습니다.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(
        label="🛑 정지",
        style=discord.ButtonStyle.danger,
        custom_id="ask_stop",
    )
    async def stop_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        """작업 강제 중단."""
        self.cancelled = True
        elapsed = int(time.time() - self.started_at)
        if self.task and not self.task.done():
            self.task.cancel()
            log.warning(f"/ask 작업 취소 (경과: {elapsed}초, 쿼리: {self.query[:40]})")

        embed = discord.Embed(
            title="🛑 작업 중단됨",
            description=(
                f"**쿼리**: `{self.query[:200]}`\n"
                f"**경과 시간**: {elapsed}초\n\n"
                "필요 시 🔄 재시도 버튼을 눌러주세요."
            ),
            color=0xE11D48,
        )
        # 정지 버튼 비활성화
        for child in self.children:
            if isinstance(child, discord.ui.Button) and child.custom_id == "ask_stop":
                child.disabled = True
                child.label = "⛔ 중단됨"
        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()

    @discord.ui.button(
        label="🔄 재시도",
        style=discord.ButtonStyle.secondary,
        custom_id="ask_retry",
    )
    async def retry_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        """동일 쿼리로 재실행 (Discord 재요청)."""
        await interaction.response.send_message(
            f"🔄 `/ask query:{self.query}` 을(를) 다시 실행해주세요.",
            ephemeral=True,
        )

    async def on_timeout(self):
        """15분 타임아웃 시 버튼 비활성화."""
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        log.info(f"/ask View 타임아웃 (쿼리: {self.query[:40]})")


def build_progress_embed(
    query: str,
    stage: str,
    detail: str = "",
    elapsed_ms: int = 0,
) -> discord.Embed:
    """진행 상태 Embed."""
    icon = {
        "라우팅": "🧭",
        "수집": "📡",
        "분석": "🔬",
        "종합": "🎯",
        "완료": "✅",
        "오류": "❌",
    }.get(stage, "⏳")

    embed = discord.Embed(
        title=f"{icon} {stage} 중...",
        description=f"**요청**: `{query[:300]}`",
        color=0x3B82F6 if stage != "오류" else 0xE11D48,
    )
    if detail:
        embed.add_field(name="진행 상황", value=detail[:1000], inline=False)
    if elapsed_ms:
        embed.set_footer(text=f"경과: {elapsed_ms/1000:.1f}초 · 🛑 정지 가능")
    else:
        embed.set_footer(text="🛑 정지 · 🔄 재시도")
    return embed
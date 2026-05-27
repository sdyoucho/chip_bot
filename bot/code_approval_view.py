"""
bot/code_approval_view.py
기쵸 코드 수정 제안 승인/거부 UI.
"""

import logging
from datetime import datetime

import discord

log = logging.getLogger(__name__)


class CodeApprovalView(discord.ui.View):
    """
    코드 변경안 승인 UI.
    버튼: ✅ 승인 / ❌ 거부 / 📋 Diff 보기
    """

    def __init__(self, proposal_id: str, owner_id: int, timeout: float = 600):
        super().__init__(timeout=timeout)
        self.proposal_id = proposal_id
        self.owner_id = owner_id
        self.handled = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "❌ 이 버튼은 요청자만 사용할 수 있습니다.",
                ephemeral=True,
            )
            return False
        if self.handled:
            await interaction.response.send_message(
                "⚠️ 이 제안은 이미 처리되었습니다.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(
        label="승인 & 적용",
        style=discord.ButtonStyle.success,
        emoji="✅",
    )
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        from modules.code_modifier import approve_and_apply_proposal
        await interaction.response.defer(thinking=True)

        result = await approve_and_apply_proposal(self.proposal_id, auto_merge=False)
        self.handled = True

        if result["success"]:
            embed = discord.Embed(
                title="✅ 코드 수정 적용 완료",
                description=(
                    f"**PR 생성됨**: [{result['pr_number']}]({result['pr_url']})\n"
                    f"**브랜치**: `{result['branch']}`\n\n"
                    f"PR을 확인하시고 GitHub에서 머지하시거나, "
                    f"`/code_merge {result['pr_number']}` 로 자동 머지하세요."
                ),
                color=0x059669,
            )
        else:
            embed = discord.Embed(
                title="❌ 적용 실패",
                description=result.get("error", "알 수 없는 오류"),
                color=0xE11D48,
            )

        for child in self.children:
            child.disabled = True
        await interaction.edit_original_response(view=self)
        await interaction.followup.send(embed=embed)

    @discord.ui.button(
        label="거부",
        style=discord.ButtonStyle.danger,
        emoji="❌",
    )
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        from modules.code_modifier import reject_proposal
        reject_proposal(self.proposal_id, reason=f"Cho 거부 ({datetime.now():%H:%M})")
        self.handled = True

        embed = discord.Embed(
            title="❌ 제안 거부됨",
            description=f"제안 ID `{self.proposal_id}`가 폐기되었습니다.",
            color=0xE11D48,
        )

        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(
        label="Diff 상세",
        style=discord.ButtonStyle.secondary,
        emoji="📋",
    )
    async def show_diff(self, interaction: discord.Interaction, button: discord.ui.Button):
        from modules.code_modifier import get_proposal
        proposal = get_proposal(self.proposal_id)
        if not proposal:
            await interaction.response.send_message(
                "제안을 찾을 수 없습니다.", ephemeral=True,
            )
            return

        diff_text = proposal["diff"][:3500] or "(diff 없음)"
        embed = discord.Embed(
            title=f"📋 Diff: {proposal['path']}",
            description=f"```diff\n{diff_text}\n```",
            color=0x4F46E5,
        )
        embed.set_footer(text=f"전체: {proposal['lines_changed']}줄 변경")
        await interaction.response.send_message(embed=embed, ephemeral=True)
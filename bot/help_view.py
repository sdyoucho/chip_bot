"""
bot/help_view.py
/help 커맨드용 페이지네이션 View.
◀ ▶ 버튼으로 각 에이전트별 명령어 전환.
"""

import discord


# ═══════════════════════════════════════════════════════════════════
# 페이지 정의
# ═══════════════════════════════════════════════════════════════════
HELP_PAGES = [
    # ── Page 0: 개요 ─────────────────────────────────────────
    {
        "title": "🤖 Cho's 매니지먼트 봇",
        "description": (
            "오퍼레이터 전용 — Cho만 사용 가능\n\n"
            "**7명의 전문 AI 에이전트**가 매니지먼트 업무를 지원합니다.\n"
            "각 페이지에서 전문 분야별 명령어를 확인하세요."
        ),
        "color": 0x4F46E5,
        "fields": [
            {
                "name": "📖 페이지 구성",
                "value": (
                    "**1**. 🎯 해쵸 — 총괄 브리핑\n"
                    "**2**. 📋 기쵸 — 기획·제안\n"
                    "**3**. 🔍 분쵸 — 분석·리포트\n"
                    "**4**. 📡 모쵸 — 방송 모니터링\n"
                    "**5**. 💰 인쵸 — 자금·토큰\n"
                    "**6**. 📅 스쵸 — 스케줄\n"
                    "**7**. 🔧 개쵸 — R&D\n"
                    "**8**. 🎨 디쵸 — 디자인\n"
                    "**9**. ⚙️ 시스템·설정"
                ),
                "inline": False,
            },
            {
                "name": "💡 빠른 시작",
                "value": (
                    "• `/ask [질문]` — 해쵸가 자동으로 전문 에이전트 배정\n"
                    "• 좌/우 버튼으로 페이지 이동\n"
                    "• 🏠 홈 버튼으로 언제든 이 화면 복귀"
                ),
                "inline": False,
            },
        ],
        "footer": "현재 모델: gpt-5.4-nano + claude-opus-4.7",
    },

    # ── Page 1: 해쵸 ──────────────────────────────────────────
    {
        "title": "🎯 해쵸 — 총괄 브리핑",
        "description": (
            "모든 에이전트의 결과를 종합·요약하는 총괄 AI입니다.\n"
            "복합적인 질문 처리 및 다중 에이전트 오케스트레이션 담당."
        ),
        "color": 0x1E293B,
        "fields": [
            {
                "name": "💬 주요 커맨드",
                "value": (
                    "`/ask [질문]` — 자연어 통합 명령\n"
                    "자동으로 필요한 에이전트를 선별하고 종합 응답 제공"
                ),
                "inline": False,
            },
            {
                "name": "🎯 사용 예시",
                "value": (
                    "• `/ask 이번 주 전체 현황 알려줘`\n"
                    "• `/ask 경쟁사 트렌드 반영해서 기획서 써줘`\n"
                    "• `/ask 이번 달 자금 상황이랑 다음 달 예상`"
                ),
                "inline": False,
            },
            {
                "name": "🧠 동작 방식",
                "value": (
                    "1️⃣ Router가 필요한 에이전트 선별\n"
                    "2️⃣ 병렬로 에이전트 호출 (최대 3개 동시)\n"
                    "3️⃣ Claude Opus 4.7로 최종 종합\n"
                    "4️⃣ 포럼 채널에 raw + summary 분리 기록"
                ),
                "inline": False,
            },
        ],
        "footer": "Claude Opus 4.7 · 포럼 세션 기록 지원",
    },

    # ── Page 2: 기쵸 ──────────────────────────────────────────
    {
        "title": "📋 기쵸 — 기획·제안 전문",
        "description": (
            "타사 협업 수준의 기획서와 콘텐츠 개선 제안을 생성합니다.\n"
            "주 5회 수준의 협업 기획안 작성 가능."
        ),
        "color": 0x4F46E5,
        "fields": [
            {
                "name": "💬 명령어",
                "value": (
                    "`/ask 기획서 써줘` — 기획 문서 생성\n"
                    "`/ask 콘텐츠 개선안` — 제목/썸네일/구성 제안\n"
                    "`/ask 협업 제안서 작성` — 타사용 협업 기획안"
                ),
                "inline": False,
            },
            {
                "name": "📊 데이터 소스",
                "value": (
                    "• Notion 스트리머 방송 이력 (14일)\n"
                    "• 분쵸 경쟁 채널 분석 결과\n"
                    "• Cho의 요구사항 (자연어)"
                ),
                "inline": False,
            },
        ],
        "footer": "Claude Opus 4.7 · Notion + Perplexity 데이터 종합",
    },

    # ── Page 3: 분쵸 ──────────────────────────────────────────
    {
        "title": "🔍 분쵸 — 분석·리포트",
        "description": (
            "통계 분석, 주간 리포트, 경쟁 채널 트렌드를 담당합니다.\n"
            "매주 월요일 09:00 자동 경쟁 분석 실행."
        ),
        "color": 0x7C3AED,
        "fields": [
            {
                "name": "💬 주요 커맨드",
                "value": (
                    "`/report [스트리머]` — 주간 분석 리포트\n"
                    "`/youtube [스트리머]` — 유튜브 채널 통계\n"
                    "`/ask 경쟁사 트렌드` — 경쟁 채널 분석"
                ),
                "inline": False,
            },
            {
                "name": "🤖 자동 실행",
                "value": (
                    "• 매주 월요일 09:00 — 경쟁 분석\n"
                    "• 매주 일요일 21:00 — 주간 리포트"
                ),
                "inline": False,
            },
        ],
        "footer": "Perplexity Sonar Pro + Claude Opus 4.7",
    },

    # ── Page 4: 모쵸 ──────────────────────────────────────────
    {
        "title": "📡 모쵸 — 방송 모니터링",
        "description": (
            "⚠️ **현재 R&D 보류 상태**\n\n"
            "치지직·유튜브 실시간 방송 모니터링을 담당하며,\n"
            "하루 8시간 × 주 6일 기준으로 재설계될 예정입니다."
        ),
        "color": 0xEAB308,
        "fields": [
            {
                "name": "💬 현재 사용 가능",
                "value": (
                    "`/monitor [스트리머]` — 현재 상태 조회 (제한적)"
                ),
                "inline": False,
            },
            {
                "name": "🚧 재설계 예정",
                "value": (
                    "• WebSocket 기반 실시간 채팅 수집\n"
                    "• 시청자 급상승 자동 감지\n"
                    "• 키워드 기반 DM 알림\n"
                    "• `/rnd_diagnose 모쵸 재설계`로 개쵸에게 의뢰"
                ),
                "inline": False,
            },
        ],
        "footer": "개쵸 작업 완료 후 활성화",
    },

    # ── Page 5: 인쵸 ──────────────────────────────────────────
    {
        "title": "💰 인쵸 — 자금·토큰 관리",
        "description": (
            "OpenRouter 크레딧, AI 토큰 비용, 고정비를 통합 관리합니다.\n"
            "50%/70%/100% 임계 도달 시 자동 DM 알림."
        ),
        "color": 0x059669,
        "fields": [
            {
                "name": "💬 자금 조회",
                "value": (
                    "`/money` — 현재 자금 현황\n"
                    "`/settlement` — 월말 정산 + 다음 달 예상"
                ),
                "inline": False,
            },
            {
                "name": "💳 고정비 관리",
                "value": (
                    "`/fixedcost_list` — 고정비 목록\n"
                    "`/fixedcost_add` — 고정비 등록\n"
                    "`/fixedcost_remove` — 삭제\n"
                    "`/fixedcost_paid` — 납부 완료 기록"
                ),
                "inline": False,
            },
            {
                "name": "🤖 자동 알림",
                "value": (
                    "• 15분마다 — 크레딧 임계치 체크\n"
                    "• 매월 말일 23:00 — 월말정산\n"
                    "• 매일 09:00 — 고정비 D-3 알림"
                ),
                "inline": False,
            },
        ],
        "footer": "OpenRouter /credits + SQLite 누적",
    },

    # ── Page 6: 스쵸 ──────────────────────────────────────────
    {
        "title": "📅 스쵸 — 스케줄 관리",
        "description": (
            "Notion 캘린더 연동으로 일정을 조회·등록·수정·삭제합니다."
        ),
        "color": 0x0EA5E9,
        "fields": [
            {
                "name": "💬 명령어",
                "value": (
                    "`/schedule` — 이번 주 일정 조회\n"
                    "`/schedule_add` — 새 일정 등록\n"
                    "`/schedule_edit` — 기존 일정 수정\n"
                    "`/schedule_remove` — 일정 삭제"
                ),
                "inline": False,
            },
            {
                "name": "📝 날짜 형식",
                "value": (
                    "• `2026-05-20` (하루 종일)\n"
                    "• `2026-05-20 14:00` (특정 시각)"
                ),
                "inline": False,
            },
        ],
        "footer": "Notion DB 직접 연동 · LLM 호출 없음",
    },

    # ── Page 7: 개쵸 ──────────────────────────────────────────
    {
        "title": "🔧 개쵸 — R&D",
        "description": (
            "봇 유지보수·신규 기능 개발·신규 봇 설계를 담당합니다.\n"
            "R&D 채널에 업데이트·유지보수 현황 자동 공유."
        ),
        "color": 0x06B6D4,
        "fields": [
            {
                "name": "💬 진단·설계",
                "value": (
                    "`/rnd_health` — 봇 자가 건강 진단\n"
                    "`/rnd_diagnose [이슈]` — 이슈/버그 진단\n"
                    "`/rnd_design [요구사항]` — 신규 봇 설계서\n"
                    "`/rnd_errors` — 최근 60분 에러 리포트\n"
                    "`/rnd_announce` — R&D 채널 수동 공지"
                ),
                "inline": False,
            },
            {
                "name": "🤖 자동 실행",
                "value": (
                    "• 매일 08:00 — 건강 체크 리포트\n"
                    "• 10분마다 — 에러 임계치(5회) 감지\n"
                    "• 재부팅 시 — 업데이트 공지"
                ),
                "inline": False,
            },
        ],
        "footer": "Claude Opus 4.7 · R&D 채널 자동 기록",
    },

    # ── Page 8: 디쵸 ──────────────────────────────────────────
    {
        "title": "🎨 디쵸 — 디자인",
        "description": (
            "포스터·PPT·협업 기획안 디자인 레퍼런스를 제안합니다.\n"
            "향후 Figma API 연동 예정."
        ),
        "color": 0xDB2777,
        "fields": [
            {
                "name": "💬 명령어",
                "value": (
                    "`/ask 디자인 레퍼런스 제안` — 디자인 제안\n"
                    "`/ask 포스터 디자인` — 포스터 구성안\n"
                    "`/ask PPT 디자인` — 슬라이드 구성안"
                ),
                "inline": False,
            },
            {
                "name": "🚧 계획",
                "value": (
                    "• Figma REST API 연동 (템플릿 자동 생성)\n"
                    "• 협업 기획안 디자인 자동화"
                ),
                "inline": False,
            },
        ],
        "footer": "GPT-4o · Figma 연동 예정",
    },

    # ── Page 9: 시스템 / 설정 ────────────────────────────────
    {
        "title": "⚙️ 시스템 · 설정",
        "description": "봇 설정, 모델 관리, 채널 설정 등 운영 커맨드",
        "color": 0x6B7280,
        "fields": [
            {
                "name": "👥 스트리머",
                "value": (
                    "`/streamer_add` — 신규 스트리머 등록\n"
                    "`/streamer_list` — 등록된 목록 조회"
                ),
                "inline": False,
            },
            {
                "name": "🔑 API 키 설정",
                "value": (
                    "`/config_ai` — OpenRouter 등 API 키\n"
                    "`/config_notion` — Notion 토큰 + DB\n"
                    "`/config_discord` — 오퍼레이터 유저 ID\n"
                    "`/config_status` — 설정 현황 조회"
                ),
                "inline": False,
            },
            {
                "name": "📺 채널 설정",
                "value": (
                    "`/rawdata_channel` — Raw Data 트레이스 채널\n"
                    "`/rnd_channel` — R&D 공지 채널\n"
                    "`/forum_channel` — 해쵸 포럼 세션"
                ),
                "inline": False,
            },
            {
                "name": "🧠 모델 관리",
                "value": (
                    "`/model_status` — 현재 티어링 조회\n"
                    "`/model_set` — 티어별 모델 변경\n"
                    "`/model_agent` — 에이전트 티어 변경\n"
                    "`/model_reset` — 기본값 복원"
                ),
                "inline": False,
            },
            {
                "name": "🔬 관찰성·시스템",
                "value": (
                    "`/rawdata` — 트레이스 모드 (off/ephemeral/channel/both)\n"
                    "`/uptime` — 봇 가동 시간\n"
                    "`/reboot` — 봇 재부팅"
                ),
                "inline": False,
            },
        ],
        "footer": "Cho 전용 운영 명령",
    },
]


# ═══════════════════════════════════════════════════════════════════
# View
# ═══════════════════════════════════════════════════════════════════
class HelpView(discord.ui.View):
    """/help 페이지네이션 View."""

    def __init__(self, owner_id: int, timeout: float = 300.0):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        self.page = 0
        self.total = len(HELP_PAGES)
        self._update_button_state()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "❌ 이 버튼은 요청자만 사용할 수 있습니다.",
                ephemeral=True,
            )
            return False
        return True

    def _update_button_state(self):
        """버튼 활성/비활성 상태 업데이트."""
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                if child.custom_id == "help_prev":
                    child.disabled = (self.page == 0)
                elif child.custom_id == "help_next":
                    child.disabled = (self.page >= self.total - 1)

    def _build_embed(self) -> discord.Embed:
        page_data = HELP_PAGES[self.page]
        embed = discord.Embed(
            title=page_data["title"],
            description=page_data["description"],
            color=page_data["color"],
        )
        for field in page_data.get("fields", []):
            embed.add_field(**field)
        embed.set_footer(
            text=f"{page_data.get('footer', '')} · 페이지 {self.page + 1}/{self.total}"
        )
        return embed

    @discord.ui.button(
        label="◀ 이전",
        style=discord.ButtonStyle.secondary,
        custom_id="help_prev",
    )
    async def prev_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if self.page > 0:
            self.page -= 1
        self._update_button_state()
        await interaction.response.edit_message(embed=self._build_embed(), view=self)

    @discord.ui.button(
        label="🏠 홈",
        style=discord.ButtonStyle.primary,
        custom_id="help_home",
    )
    async def home_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        self.page = 0
        self._update_button_state()
        await interaction.response.edit_message(embed=self._build_embed(), view=self)

    @discord.ui.button(
        label="다음 ▶",
        style=discord.ButtonStyle.secondary,
        custom_id="help_next",
    )
    async def next_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if self.page < self.total - 1:
            self.page += 1
        self._update_button_state()
        await interaction.response.edit_message(embed=self._build_embed(), view=self)

    @discord.ui.select(
        placeholder="📂 페이지 바로가기...",
        custom_id="help_select",
        options=[
            discord.SelectOption(label="개요",     value="0", emoji="📖"),
            discord.SelectOption(label="해쵸",     value="1", emoji="🎯"),
            discord.SelectOption(label="기쵸",     value="2", emoji="📋"),
            discord.SelectOption(label="분쵸",     value="3", emoji="🔍"),
            discord.SelectOption(label="모쵸",     value="4", emoji="📡"),
            discord.SelectOption(label="인쵸",     value="5", emoji="💰"),
            discord.SelectOption(label="스쵸",     value="6", emoji="📅"),
            discord.SelectOption(label="개쵸",     value="7", emoji="🔧"),
            discord.SelectOption(label="디쵸",     value="8", emoji="🎨"),
            discord.SelectOption(label="시스템",   value="9", emoji="⚙️"),
        ],
    )
    async def page_select(
        self,
        interaction: discord.Interaction,
        select: discord.ui.Select,
    ):
        self.page = int(select.values[0])
        self._update_button_state()
        await interaction.response.edit_message(embed=self._build_embed(), view=self)
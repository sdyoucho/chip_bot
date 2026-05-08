"""
utils/pipeline_logger.py
Raw Data 파이프라인 트레이서.

출력 모드 (/rawdata 커맨드로 제어):
  off       — 비활성 (기본값)
  ephemeral — 요청자에게만 보이는 임시 메시지
  channel   — 지정 채널에 영구 기록
  both      — ephemeral + 채널 동시 전송

사용법 (모듈 내):
    from utils.pipeline_logger import step, traced

    step("Notion 조회", "ok", "3건 반환", duration_ms=152)
    step("JSON 파싱", "fail", "키 없음", error_code="E008")

    result = await traced("Claude API", client.messages.create(...), "E004")
"""

import time
import discord
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Awaitable, Literal, Optional

# ── 오류 코드 ────────────────────────────────────────────────────────
ERROR_CODES: dict[str, str] = {
    "E001": "Notion 읽기 실패",
    "E002": "Notion 쓰기 실패",
    "E003": "Gemini 라우터 실패",
    "E004": "Claude API 실패",
    "E005": "OpenAI API 실패",
    "E006": "Perplexity API 실패",
    "E007": "YouTube API 실패",
    "E008": "JSON 파싱 실패",
    "E009": "WebSocket 연결 실패",
    "E010": "인증 정보 없음",
    "E011": "환경변수 미설정",
    "E012": "응답 비어있음",
}

OutputMode = Literal["off", "ephemeral", "channel", "both"]

# ── 전역 설정 ────────────────────────────────────────────────────────
_output_mode: OutputMode = "off"
_log_channel_id: Optional[int] = None   # 영구 기록할 Discord 채널 ID


def get_output_mode() -> OutputMode:
    return _output_mode


def set_output_mode(mode: OutputMode) -> None:
    global _output_mode
    _output_mode = mode


def get_log_channel() -> Optional[int]:
    return _log_channel_id


def set_log_channel(channel_id: Optional[int]) -> None:
    global _log_channel_id
    _log_channel_id = channel_id


def is_enabled() -> bool:
    """트레이스 수집이 필요한 모드인지 확인."""
    return _output_mode != "off"


# ── 요청별 독립 트레이스 (contextvars) ──────────────────────────────
_current_trace: ContextVar[Optional["PipelineTrace"]] = ContextVar(
    "pipeline_trace", default=None
)


# ── 데이터 클래스 ────────────────────────────────────────────────────
@dataclass
class PipelineStep:
    label: str
    status: str        # "ok" | "fail" | "skip"
    detail: str
    error_code: str
    duration_ms: int


@dataclass
class PipelineTrace:
    steps: list[PipelineStep] = field(default_factory=list)
    _start: float = field(default_factory=time.monotonic)

    def add(
        self,
        label: str,
        status: str,
        detail: str = "",
        error_code: str = "",
        duration_ms: int = 0,
    ) -> None:
        self.steps.append(PipelineStep(label, status, detail, error_code, duration_ms))

    def to_embed(
        self,
        *,
        query: str = "",
        module: str = "",
        user: str = "",
        for_channel: bool = False,
    ) -> discord.Embed:
        """
        트레이스 Embed 생성.
        for_channel=True 시 요청자·시각·쿼리 메타 필드 추가 (채널 전송용).
        """
        total_ms = int((time.monotonic() - self._start) * 1000)
        lines: list[str] = []

        for s in self.steps:
            icon = {"ok": "✅", "fail": "❌", "skip": "⏭️"}.get(s.status, "•")
            dur_str = f" `{s.duration_ms}ms`" if s.duration_ms else ""
            line = f"{icon} **{s.label}**{dur_str}"

            if s.detail:
                truncated = s.detail[:80] + "…" if len(s.detail) > 80 else s.detail
                line += f"\n　└ {truncated}"

            if s.error_code:
                desc = ERROR_CODES.get(s.error_code, "알 수 없는 오류")
                line += f"\n　└ ⚠️ `{s.error_code}` — {desc}"

            lines.append(line)

        fails = [s for s in self.steps if s.status == "fail"]
        color = 0xE11D48 if fails else 0x059669

        embed = discord.Embed(
            title=f"🔬 Raw Data — 파이프라인 트레이스 `{total_ms}ms`",
            description="\n".join(lines) if lines else "기록된 단계 없음",
            color=color,
        )

        # 채널 전송용: 메타 정보 필드 추가
        if for_channel:
            if query:
                embed.add_field(name="요청", value=f"`{query[:100]}`", inline=True)
            if module:
                embed.add_field(name="모듈", value=f"`{module}`", inline=True)
            if user:
                embed.add_field(name="요청자", value=user, inline=True)
            # Discord timestamp 형식 <t:unix:T> → 현지 시각
            embed.add_field(
                name="시각",
                value=f"<t:{int(time.time())}:F>",
                inline=True,
            )

        if fails:
            codes = ", ".join(f"`{s.error_code}`" for s in fails if s.error_code)
            footer = f"❌ 실패 {len(fails)}단계"
            if codes:
                footer += f"  |  오류코드: {codes}"
        else:
            footer = f"✅ 전 단계 성공  |  총 {len(self.steps)}단계"

        embed.set_footer(text=footer)
        return embed


# ── 공개 API ────────────────────────────────────────────────────────

def start_trace() -> PipelineTrace:
    """현재 컨텍스트에 새 트레이스를 시작하고 반환."""
    trace = PipelineTrace()
    _current_trace.set(trace)
    return trace


def get_trace() -> Optional[PipelineTrace]:
    """현재 컨텍스트의 트레이스 반환 (없으면 None)."""
    return _current_trace.get()


def step(
    label: str,
    status: str,
    detail: str = "",
    error_code: str = "",
    duration_ms: int = 0,
) -> None:
    """현재 트레이스에 단계 추가. 트레이스 없으면 무시 (항상 안전)."""
    trace = _current_trace.get()
    if trace is not None:
        trace.add(label, status, detail, error_code, duration_ms)


async def traced(
    label: str,
    coro: Awaitable,
    error_code: str = "E000",
    ok_detail: str = "",
) -> Any:
    """
    코루틴을 실행하며 파이프라인 단계를 자동 기록.
    성공: ✅  실패: ❌ + error_code
    실패 시 예외 re-raise (호출부 try/except 그대로 동작).
    """
    t = time.monotonic()
    try:
        result = await coro
        ms = int((time.monotonic() - t) * 1000)
        step(label, "ok", ok_detail, "", ms)
        return result
    except Exception as e:
        ms = int((time.monotonic() - t) * 1000)
        step(label, "fail", str(e)[:100], error_code, ms)
        raise

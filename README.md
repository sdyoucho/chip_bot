# Cho's 매니지먼트 봇

스트리머 AI 매니지먼트 시스템.  
Discord 봇 1개 + 페르소나별 내부 모듈(개쵸/해쵸/인쵸/기쵸/분쵸/스쵸/모쵸/디쵸) + 방송 모니터링 엔진.

---

## 빠른 시작

```bash
# 1. 클론 & 환경 세팅
git clone <your-repo>
cd chip_bot
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

# 2. 패키지 설치
pip install -r requirements.txt

# 3. 환경변수 설정
cp .env.example .env
# .env 파일을 열어서 모든 API 키 입력

# 4. 실행 (프로젝트 루트에서, 모듈 형태로 — 그냥 python bot/main.py로 실행하면
#    bot/이 sys.path에 잡혀 modules/utils 절대 import가 깨짐)
python -m bot.main
```

---

## 폴더 구조

```
chip_bot/
├── .env                        ← API 키 (Git 제외)
├── .env.example                ← 템플릿
├── requirements.txt
├── railway.toml                ← Railway 배포 설정 (python -m bot.main)
├── bot/
│   ├── main.py                 ← 봇 진입점 + 스케줄러 등록 + on_ready
│   ├── commands.py             ← 모든 슬래시 커맨드 등록 (가장 큰 파일)
│   ├── router.py                ← OpenRouter 기반 자연어 라우팅 엔진
│   ├── embeds.py                ← Discord Embed 포맷터/헬퍼
│   ├── help_view.py             ← /help 명령 콘텐츠
│   ├── interactive.py           ← /ask 진행 상태 View (정지 버튼 등)
│   ├── code_planning_view.py    ← /code_propose 1단계: 계획 승인 UI
│   └── code_approval_view.py    ← /code_propose 2단계: 코드 변경 승인 UI
├── modules/
│   ├── haecho.py                ← 해쵸: 멀티 에이전트 오케스트레이터 (/ask 핵심)
│   ├── rnd.py                   ← 개쵸: 코드 리뷰/코드베이스 점검/이슈 진단/신규 설계
│   ├── code_planner.py          ← 개쵸: 자동 코드 변경 — 계획 수립 (/code_propose)
│   ├── code_modifier.py         ← 개쵸: 자동 코드 변경 — 적용 + GitHub PR 생성
│   ├── code_publisher.py        ← 개쵸 코드 변경 결과 R&D 포럼 게시
│   ├── money.py                 ← 인쵸: 자금 현황 + 크레딧 임계치 알림 + 월말정산
│   ├── fixed_costs.py           ← 인쵸: 고정비 납부 일정 (로컬 JSON + Notion 선택 연동)
│   ├── schedule.py              ← 스쵸: 일정 조회/등록/수정/삭제 (Notion)
│   ├── weekly_report.py         ← 분쵸: 주간 리포트 생성 + 자동 발송 스케줄러
│   ├── competitor_analysis.py   ← 분쵸: 경쟁 채널 분석
│   ├── content_suggest.py       ← 기쵸: 콘텐츠 개선 제안
│   ├── planning.py              ← 기쵸: 기획서/협업 제안서 생성
│   ├── gicho_learning.py        ← 기쵸: 트렌드/기획 기법 자율 학습
│   ├── design.py                ← 디쵸: 디자인/Figma 레퍼런스 제안
│   ├── chzzk_monitor.py         ← 치지직 방송 모니터링
│   └── youtube_analytics.py     ← 유튜브 채널 통계
├── utils/
│   ├── openrouter_client.py     ← 모든 LLM 호출 단일 진입점 (모델 티어링/캐시/폴백)
│   ├── notion_client.py         ← Notion API 공통 레이어
│   ├── github_client.py         ← GitHub API (브랜치/커밋/PR) — /code_propose용
│   ├── config_manager.py        ← API 키 .env 영속화 (/config_* 커맨드)
│   ├── model_config.py          ← 모델 티어 오버라이드 영속화
│   ├── credit_config.py         ← 크레딧 월 한도/알림 임계치 영속화
│   ├── json_store.py            ← 공통 JSON 파일 읽기/쓰기 헬퍼
│   ├── persistent_store.py      ← 기타 런타임 설정값 영속화
│   ├── cost_tracker.py          ← OpenRouter 사용 비용 SQLite 누적
│   ├── self_monitor.py          ← 런타임 에러 감지 + R&D 채널 자동 알림
│   ├── restart_manager.py       ← 자동/수동 재부팅 + KST 시간 유틸
│   ├── message_splitter.py      ← 긴 응답 분할 전송 + MD/HTML 파일 변환
│   ├── response_cache.py        ← 응답 캐싱
│   ├── pipeline_logger.py       ← Raw Data 트레이스 로깅
│   ├── conversation_context.py ← /ask 답장(reply) 컨텍스트 수집
│   ├── keyword_alert.py         ← 키워드 실시간 감지
│   ├── viewer_tracker.py        ← 시청자 수 트래킹
│   ├── persona.py               ← 에이전트별 캐릭터/Webhook 발화
│   ├── url_analyzer.py          ← URL(GitHub 등) 콘텐츠 분석
│   ├── forum_publisher.py       ← 해쵸 세션 결과 포럼 게시
│   ├── logger.py                ← 로깅 설정
│   └── helpers.py               ← 공통 헬퍼
└── data/                        ← Railway Volume 또는 로컬 폴백 (Git 제외)
    ├── fixed_costs.json
    ├── credit_config.json
    ├── model_config.json
    └── cost_tracker.db
```

---

## 스트리머 YouTube 인증

```bash
python -c "from modules.youtube_auth import start_oauth_flow; start_oauth_flow('스트리머이름')"
```

브라우저가 열리면 스트리머 Google 계정으로 로그인 → 동의.  
토큰은 `data/yt_token_<이름>.json`에 저장됩니다.

---

## 주요 Discord 커맨드

| 커맨드 | 설명 |
|--------|------|
| `/ask [질문]` | 자연어 통합 명령 |
| `/monitor [스트리머]` | 방송 현황 |
| `/report [스트리머]` | 주간 리포트 즉시 생성 |
| `/youtube [스트리머]` | 유튜브 통계 |
| `/schedule` | 스케줄 확인 |
| `/money` | 자금·비용 현황 |
| `/credit_settings` | 월 한도/임계치 조회 |
| `/credit_limit` | 월 크레딧 한도(USD) 설정 |
| `/credit_thresholds` | 알림 임계치(%) 설정 |
| `/streamer_add` | 스트리머 등록 |
| `/streamer_list` | 스트리머 목록 |

---

## 비용 요약 (1인/월, STT 제외)

| 항목 | 금액 |
|------|------|
| 채팅 배치 분석 (Gemini Flash) | ₩4,410 |
| 방송 요약 분석 (Gemini Flash) | ₩3,969 |
| 경쟁 채널 분석 (Perplexity) | ₩5,460 |
| AI 개선 제안 (Claude Sonnet) | ₩8,646 |
| YouTube VOD STT | ₩720 |
| 서버+버퍼 | ₩3,025 |
| **합계** | **₩26,230** |

---

## 배포 (Railway)

1. GitHub에 푸시
2. Railway에서 프로젝트 생성 → GitHub 연동
3. 환경변수 `.env` 내용을 Railway Variables에 입력
4. 자동 배포됨
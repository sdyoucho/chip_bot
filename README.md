# Cho's 매니지먼트 봇

스트리머 AI 매니지먼트 시스템.  
Discord Header 봇 1개 + 내부 모듈 7개 + 방송 모니터링 엔진.

---

## 빠른 시작

```bash
# 1. 클론 & 환경 세팅
git clone <your-repo>
cd chos_management
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

# 2. 패키지 설치
pip install -r requirements.txt

# 3. 환경변수 설정
cp .env.example .env
# .env 파일을 열어서 모든 API 키 입력

# 4. 실행
python bot/main.py
```

---

## 폴더 구조

```
chos_management/
├── .env                  ← API 키 (Git 제외)
├── .env.example          ← 템플릿
├── requirements.txt
├── railway.toml          ← Railway 배포 설정
├── bot/
│   ├── main.py           ← 봇 진입점
│   ├── commands.py       ← 슬래시 커맨드
│   ├── router.py         ← Gemini 라우팅 엔진
│   └── embeds.py         ← Discord Embed 포맷터
├── modules/
│   ├── youtube_auth.py   ← YouTube OAuth
│   ├── youtube_analytics.py
│   ├── youtube_live.py
│   ├── weekly_report.py  ← 주간 리포트 + 스케줄러
│   ├── chzzk_monitor.py  ← 치지직 모니터링
│   ├── soop_monitor.py   ← SOOP (Phase 6)
│   ├── competitor_analysis.py
│   ├── content_suggest.py
│   ├── money.py
│   ├── planning.py
│   ├── schedule.py
│   ├── rnd.py
│   └── design.py
├── utils/
│   ├── notion_client.py  ← Notion API 공통 레이어
│   ├── keyword_alert.py  ← 키워드 실시간 감지
│   ├── viewer_tracker.py ← 시청자 수 트래킹
│   ├── logger.py
│   └── helpers.py
└── data/
    ├── streamers.json
    └── keywords.json
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
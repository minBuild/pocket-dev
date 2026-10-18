# Pocket-Dev — 구독제 멀티 AI 기획 토론 → 자율 구현 서버

`argon_x_fable.md` 설계서를 **API 키 없이 구독 계정만으로** 돌아가게 만든 버전입니다.
AI 회사 API를 직접 부르지 않고, 구독 계정으로 로그인해 둔 **공식 CLI를 서버가 대신 실행**합니다.

| 단계 | 엔진 | 상태 |
| --- | --- | --- |
| 1차 MVP | Claude — Claude Code (`claude -p`) · Pro/Max 구독 | ✅ 동작 |
| 2차 | ChatGPT — Codex CLI (`codex exec`) · Plus/Pro 구독 | 예정 |
| 3차 | Gemini — Antigravity CLI (`agy`) · Google AI Pro 구독 | 구독 헤드리스 지원 확인 후 |

## 흐름

```
아이디어 ─► 토론 (참가자마다 별도 Claude 세션, 라운드 반복, 전원 AGREE 면 종료)
         ─► 명세서 작성 ─► 기획안 AI 리뷰 (반려 시 재작성)
         ─► [사람 승인] ── 의견 보내기 → 토론 1라운드 더   (끄면 완전 자동)
         ─► Claude Code 구현 ──권한 요청──► 하드 룰 ─► 심사 세션 투표 ─► (갈리면) 폰으로 질문
         ─► AI 코드 리뷰 (반려 시 재수정) ─► commit (작성자 minBuild) & push
                                              └ 원격 저장소가 없으면 GitHub 에 비공개 저장소 자동 생성
```

- 토론·리뷰 참가자는 프로젝트 파일을 **읽기 전용 도구(Read/Grep/Glob)로 직접 열어봅니다** — 구독 사용량 절약.
- 구독 사용량 한도(`session limit · resets 12:40am` 등)에 걸리면 **리셋 시각까지 기다렸다가 이어서 진행**합니다.
- **멈춰도 이어서:** 단계마다 체크포인트(토론 기록·진행 단계)를 저장해서, 오류·서버 재시작으로 멈춘 작업은
  기록 화면의 **▶ 이어서 진행** (또는 채팅 "이어서 진행해줘") 으로 멈춘 지점부터 재개합니다. 이미 발언한 참가자는 다시 안 부릅니다.
- **작업 화면 채팅:** 기획안 단계에선 수정 요청 → 한 라운드 더 토론 · 진행 중엔 다음 단계에 반영할 지시 ·
  한도 대기 중엔 바로 재시도 · 완료 후엔 추가 요청 → 한 바퀴 더 (토론 → 명세서 → 구현 → 리뷰 → 커밋)
- 같은 권한 요청은 한 번만 심사합니다.

## 설치 (집 PC, 최초 1회)

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
claude auth login          # Claude 구독 계정 로그인
gh auth login              # GitHub (저장소 자동 생성용)
```

1. `config.yaml` 의 `projects_roots` 에 프로젝트들이 있는 상위 폴더를 적습니다 (여러 개 가능).
2. **`setup_login.bat`** 실행 → 아이디·비밀번호를 정하면 새 액세스 토큰이 발급되어 `.env` 에 저장됩니다.
   (비밀번호는 해시로만 저장. 토큰은 창에 표시되니 따로 적어 두세요.)

## 실행

`start.bat` 더블클릭. 창을 닫으면 서버도 꺼집니다. 켜질 때 접속 주소가 출력됩니다.

| 출입구 | 주소 | 용도 |
| --- | --- | --- |
| 공개 주소 | `https://<기기>.<tailnet>.ts.net` (Tailscale Funnel) | 회사 PC·폰 등 **아무 브라우저에서**. VPN 불필요 |
| 사설망 | `http://100.x.x.x:8000` (Tailscale) · `http://192.168.x.x:8000` (집 와이파이) | 잠겼을 때 해제하는 **비상 열쇠** |

공개 주소 켜기 (최초 1회): `tailscale funnel --bg 8001` → 안내 링크에서 Funnel 허용.
서버는 공개 주소용으로 `127.0.0.1:8001` 을 따로 열어서, 이 포트로 들어온 요청만 "공개"로 취급합니다.

## 보안

- **로그인 = 아이디 + 비밀번호 + 액세스 토큰** (셋 다 맞아야 함, 어느 것이 틀렸는지는 알려주지 않음)
- **로그인 실패 3회 → 공개 주소 잠금.** 잠긴 동안은 맞는 정보를 넣어도 못 들어옵니다. 서버를 재시작해도 유지.
  - 해제: ① 폰에서 Tailscale 을 켜고 사설 주소(100.x)로 로그인 → "잠금 해제" ② 집 PC에서 `unlock.bat`
  - 공개 주소로는 해제할 수 없습니다. 최근 실패 기록(시각·IP)이 화면에 표시됩니다.
- 세션은 HttpOnly·SameSite=Strict 쿠키 (공개 주소에서는 Secure). 12시간 유지, 서버 재시작 시 다시 로그인.
- Claude Code 권한 심사용 내부 API는 **이 PC 안에서 사설 포트로만** 호출 가능 (공개 주소에서는 차단).
- **하드 룰** (심사 결과와 무관하게 즉시 거부, Bash·PowerShell 둘 다): 루트·홈 삭제, `git push --force`, `git reset --hard`,
  `sudo`·`Set-ExecutionPolicy` 등 시스템 명령, `curl | sh`·`irm | iex`, 프로젝트 밖 파일 수정, macOS 전용 명령
- `--dangerously-skip-permissions` 는 쓰지 않음. 권한은 전부 `--permission-prompt-tool` 로 심사 세션에 감.
- 롤백 버튼은 `git reset --hard` 대신 **`git revert` + push**.
- **본인 혼자 쓰세요.** 구독 로그인은 본인의 일반적인 Claude Code 사용용이고,
  다른 사람 요청을 내 구독으로 대신 처리해 주는 건 Anthropic 약관상 허용되지 않습니다.

## GitHub

- 커밋 작성자는 `config.yaml` 의 `git.author_name/author_email` (minBuild · noreply 주소). 전역 git 설정은 안 건드림.
- 작업은 `feat/ai-remote` 브랜치에 커밋 후 push (main 은 건드리지 않음).
- **원격 저장소가 없는 프로젝트**는 첫 push 때 `minBuild/<폴더명>` 비공개 저장소를 자동으로 만들고 연결합니다.
- 웹 화면 프로젝트 목록의 **"＋ 새 프로젝트 만들기"** → `git.new_project_root` 에 폴더 + git 저장소 생성.

## 구독으로만 돌게 하는 장치

- `claude` 를 실행할 때 `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` 환경변수를 지웁니다 (있으면 API 과금으로 바뀜).
- 시작 화면에 `claude auth status` 결과를 보여주고, 구독이 아닌 로그인이면 경고합니다.
- **Pro 요금제에서 Fable 5.1 은 추가 크레딧이 필요합니다.** 기본 팀은 Opus / Sonnet / Haiku 만 씁니다.

## 기본 팀 (Pro 한도 기준)

| 역할 | 모델 | 이유 |
| --- | --- | --- |
| 수석 아키텍트 | Opus 5.5 | 설계 품질이 제일 중요 |
| 수석 엔지니어 | Sonnet 5.5 | 비평·보완 |
| 명세서 작성 | Opus 5.5 | 구현 지시서 품질 |
| 기획안·코드 리뷰 | Sonnet 5.5 | |
| 권한 심사 | Haiku 5.5 | 자주 불리니 빠르고 가볍게 |
| 구현 (Claude Code) | Sonnet 5.5 | 명세가 자세하면 충분, 한도 절약 |

UI에서 참가자 추가/삭제/순서, 모델, effort(생각 깊이)를 바꿀 수 있고 브라우저에 저장됩니다.

**effort 기본값은 medium** (`engine.default_effort`). Claude Code 기본 깊이로 두면 Opus 가 발언 한 번에
생각 토큰을 7~9만 개씩 써서 (실측: 14~17분, 답변은 1.6만 자) Pro 세션 한도가 두세 번 만에 바닥납니다.
응답 한 번의 출력은 `debate.max_output_tokens` (32,000) 로 제한합니다.

## 설정 포인트 (`config.yaml`)

| 항목 | 설명 |
| --- | --- |
| `auth.max_failures` / `auth.session_hours` | 잠금까지 실패 횟수 / 로그인 유지 시간 |
| `debate.read_tools` | 토론·리뷰 참가자가 쓸 수 있는 읽기 전용 도구 |
| `engine.limit_retry_minutes` | 한도 도달 시 리셋 시각을 모를 때 재시도 간격 |
| `executor.permission_mode` | `acceptEdits`: 프로젝트 안 파일 수정은 자동, 명령 실행만 심사 · `manual`: 전부 심사 |
| `executor.permission_policy` | `unanimous` / `majority` / `any` |
| `git.require_clean_tree` | 커밋 안 된 변경이 있으면 작업 시작 전에 중단 |

## 파일 구조

```
config.yaml               엔진·모델·팀·보안·git 설정
start.bat                 서버 실행
setup_login.bat           아이디·비밀번호 설정 + 새 액세스 토큰 발급
unlock.bat                공개 주소 잠금 해제
server/main.py            FastAPI 라우트 · 출입구(사설/공개) 구분 · 로그인 API
server/auth.py            로그인 검증 · 세션 · 실패 잠금
server/engines.py         구독 CLI 엔진 (claude -p 호출, 스트리밍, 한도 감지, 로그인 상태)
server/agents.py          토론 · 명세서 · 리뷰 · 권한 심사 + 한도 대기/재시도
server/pipeline.py        전체 자동 진행 흐름
server/permissions.py     하드 룰 + 심사 투표 + 사람 에스컬레이션 + 판정 재사용
server/permission_mcp.py  Claude Code 가 호출하는 권한 심사 MCP 서버
server/executor.py        Claude Code 구현 실행 · git · GitHub 저장소 자동 생성
web/                      모바일 웹 UI
data/                     실행 기록 · 잠금 상태 (git 에 안 올라감)
```

설계서 6.1의 `CLAUDE.md` (WSL에서 macOS 명령 금지 등)는 대상 프로젝트 루트에 두면 Claude Code와 토론 참가자 모두 읽습니다.

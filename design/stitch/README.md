# Pocket-Dev Stitch 디자인

- Stitch 프로젝트: **pocket-dev** (ID `16066515983401876978`, `projects/16066515983401876978`)
- 디자인 시스템: "Pocket Dev Aesthetic" (`assets/250a2b51bd9f4604a8518b36fbe81142`)
- 기기: MOBILE (780px 폭)
- 파일(`<slug>.html`·`<slug>.png`)은 Pocket-Dev 가 `screens.json` 목록으로 내려받아 저장합니다.

## 화면 목록

| slug | 역할 | Stitch screen ID |
|---|---|---|
| `run-waiting` | 작업 화면, `waiting` 배지 + 🌐 칩 "✋ 내 결정 대기" + Chrome 클릭 허용/거부 카드 | `bbed3c20d29d4f57a6819d700fc9332a` |
| `run-working` | 작업 화면, `working` 배지 + 🌐 칩을 눌러 연 오버레이(스크린샷 줄·할 일 4/6·최근 단계) | `f699f0fc53b945b2a90ae41b0ef755ba` |
| `runs-list` | 기록 목록, `waiting`/`working`/`done`/`error` pill 과 Chrome 한 줄 요약, ✏️·⟳ | `7f89ff7809df46328fbb7211c927bd53` |
| `new-run-stale-banner` | 새 작업 탭 맨 위 "서버 코드가 바뀌었어요" 배너(`#staleBanner`) + 🔄 지금 재시작 | `be78e7508d45427abdc9f158e374a6a2` |
| `rename-dialog` | ✏️ 대화명 변경 다이얼로그 (취소 `button` / ✓ 저장 `submit`) | `f48d8ff72d054b69b3176cabcc12045d` |

## 구현 시 주의 (TASK_SPEC §6 범위 밖)

Stitch 시안에는 명세에 없는 장식이 섞여 있습니다. 구현 때 **따라 그리지 않습니다.**
- 새 색상 변수·애니메이션, 진행률 게이지/막대, LIVE·`v0.9.4`·`agent-live` 배지, 하단 탭바·플로팅 버튼, 필터 칩·검색창
- 글자 수 카운터, 키보드 힌트 문구, 2자 이상 검증 문구 (명세는 빈 값 저장 허용)
- 상태 문구는 `waiting`/`working` 만 씁니다 (`running` 금지).
- 실제 구현은 기존 `web/style.css` 변수와 클래스(`.status`, `.lock-banner`, `.run-sub`)를 씁니다.

## 디자인 토큰

> 현재 기준은 `design/DESIGN.md`(다크 기본·라이트 선택, 틸 계열)이고 라이트 값은 `web/style.css` 의 `html[data-theme=light]` 입니다. 아래 표는 옛 오렌지 라이트 시안 기록이라 쓰지 않아요.

색 (실제 앱은 `web/style.css` 변수를 우선)

| 토큰 | 값 |
|---|---|
| 배경 | `#FAF9F5` (Stitch surface `#fcf9f6`) |
| 카드 | `#FFFFFF`, 테두리 1px `#E8E6DC` |
| 강조(Claude orange) | `#D97757`, 눌림 `#C86646` |
| 본문 글자 | `#141413` |
| 보조 글자 | `#6B6A63` |
| 코드/칩 배경 | `#F4F3ED`, 테두리 `#E5E3D8` |
| waiting (경고) | 글자 `#D97706` · 배경 `#FFFBEB` · 테두리 `#FDE68A` |
| working | 연녹색 계열 pill (시안은 emerald) — 앱은 기존 `.status.working` 유지 |
| done | `#16A34A` / `#F0FDF4` / `#BBF7D0` |
| error·중단 | `#DC2626` / `#FEF2F2` / `#FECACA` |

글꼴

| 용도 | 글꼴 |
|---|---|
| 한글 UI 본문 | Pretendard (대체: Inter, -apple-system) |
| 제목 | Plus Jakarta Sans 600, 자간 -0.01~-0.02em |
| 코드·칩·좌표 | JetBrains Mono 11–13px |
| 본문 크기 | 13–15px (입력칸은 15px, iOS 확대 방지) |

간격·모양

| 항목 | 값 |
|---|---|
| 기본 단위 | 8px (보조 4px) |
| 좌우 여백 | 16px |
| 카드 | 반경 12px, 안쪽 12px |
| 버튼·입력 | 반경 8px, 높이 40px |
| 칩 | 반경 6px |
| 바텀시트 | 위쪽 모서리 16px |
| 그림자 | 카드 `0 1px 2px rgba(20,20,19,.03)`, 띄운 면 `0 4px 12px -2px rgba(20,20,19,.06)` |

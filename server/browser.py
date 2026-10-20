"""Claude in Chrome — 구현 AI 가 이 PC 의 Chrome(로그인된 상태)으로 콘솔 작업(AdMob 앱 등록·광고 단위 ID 발급 등)을 하게 한다.

작업마다 켜는 옵션 (run.settings["browser"]). Chrome 에 Claude 확장이 설치·로그인돼 있어야 한다.
읽기 전용 도구(탭 목록·페이지 읽기·찾기)만 묻지 않고 허용하고, 클릭·입력·이동·스크립트·업로드 같은
실제로 무언가를 하는 도구는 다른 도구와 똑같이 권한 심사(리뷰어 AI → 의견이 갈리면 폰으로 질문)를 거친다.
"""

READ_ONLY = [f"mcp__claude-in-chrome__{t}" for t in (
    "tabs_context_mcp", "read_page", "find", "get_page_text", "list_connected_browsers")]

PLAN_NOTE = ("\n\n[구현 환경] 이번 작업의 구현 AI 는 이 PC 의 Chrome(사용자가 로그인한 상태)을 조작할 수 있다 "
             "(클릭·입력은 하나하나 권한 심사를 거침). AdMob·Firebase 같은 웹 콘솔에서 앱 등록, 광고 단위 생성, "
             "ID 확인처럼 사람이 하던 설정 작업도 구현 단계에 넣을 수 있다. "
             "결제·요금제·계정 권한 변경, 기존 항목 삭제, 게시·심사 제출은 하지 않는다.")

EXEC_NOTE = """

[Chrome 사용 가능] 이 PC 의 Chrome 을 claude-in-chrome 도구로 조작할 수 있다 (사용자가 로그인해 둔 상태).
클릭·입력·이동은 하나하나 권한 심사를 거치니, 각 동작이 무엇을 위한 것인지 분명하게 하고 꼭 필요한 동작만 한다.
명세서에 있는 웹 콘솔 작업(예: AdMob 앱 추가·광고 단위 생성·앱 ID/광고 단위 ID 확인)만 한다.
- 시작할 때 tabs_context_mcp 로 연결을 확인하고, 새 탭을 만들어서 작업한다. 사용자가 열어 둔 탭은 건드리지 않는다.
- 만들기 전에 같은 이름의 앱·광고 단위가 이미 있는지 먼저 확인하고, 있으면 새로 만들지 말고 그 ID 를 쓴다.
- 금지: 결제·요금제·지급 정보, 계정·사용자 권한 설정, 기존 앱·광고 단위·프로젝트 삭제나 비활성화, 약관 동의,
  앱 게시·심사 제출, 로컬 파일 업로드. 이런 단계가 필요하면 멈추고 무엇이 필요한지 남긴다.
- 로그인·2단계 인증·캡차가 나오면 우회하지 말고 멈춰서 "사용자가 Chrome 에서 로그인해야 함" 이라고 남긴다.
- 발급받은 ID 는 코드·설정 파일에 반영하고, 작업 끝에 무엇을 만들었고 어떤 ID 를 어디에 넣었는지 정리한다.
  단 프로젝트 규칙(CLAUDE.md·명세서)이 실제 ID 를 저장소에 넣지 말라고 하면 그 방식(--dart-define 등)을 따르고, ID 는 결과 정리에만 남긴다.
  (AdMob 앱 ID 는 ca-app-pub-…~…, 광고 단위 ID 는 ca-app-pub-…/… 형식. 테스트 빌드에 테스트 광고 ID 를 쓰는 구조면 그대로 지킨다.)
- Chrome 연결이 안 되면 브라우저 작업은 건너뛰고 코드 작업만 끝낸 뒤, 연결이 안 됐다고 남긴다."""


def enabled(settings: dict) -> bool:
    return bool(settings.get("browser"))


def plan_note(settings: dict) -> str:
    return PLAN_NOTE if enabled(settings) else ""

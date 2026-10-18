# 📑 [시스템 상세 설계서] Argon × Fable 하이브리드 원격 개발 서버 (Project: Pocket-Dev)

* **문서 버전:** v1.0
* **타겟 환경:** Windows 10/11 (WSL2 Ubuntu) 서버 ➔ 외부 모바일/PC 웹 제어 ➔ MacBook (Xcode) 빌드 및 출시
* **핵심 AI 엔진:** Gemini 4 Argon (`gemini-4-argon`) + Claude Fable 5.1 (`claude-fable-5-1`)

---

## 1. 시스템 개요 및 목표

외부(회사 PC, 스마트폰)에서 웹 브라우저를 통해 집 윈도우 PC의 로컬 프로젝트에 접근하고, **Gemini 4 Argon**과 **Claude Fable 5.1**의 교차 검증 토론을 통해 설계를 확정한 뒤, **Claude Code CLI**가 로컬 코드를 자율 수정하여 GitHub에 자동 Push하는 원격 개발 파이프라인을 구축한다.

### 1.1 모델별 역할 분담 (Role Assignment)

| 구분 | 채택 모델 / 도구 | API 모델 ID | 핵심 강점 및 시스템 내 역할 |
| :--- | :--- | :--- | :--- |
| **수석 아키텍트 (설계 1)** | **Gemini 4 Argon** | `gemini-4-argon` | • 1M 컨텍스트 기반 **전체 프로젝트 소스코드 통째로 로드 및 분석**<br>• 저렴한 토큰 단가($2/$10, 암시적 캐시 95% 할인)를 활용해 광범위한 파일 탐색, 공간/시각적 구조 설계 및 보안 취약점 사전 검증 |
| **수석 엔지니어 (설계 2)** | **Claude Fable 5.1** | `claude-fable-5-1` | • Argon이 짠 설계안의 **구현 타당성 비평, 의존성 충돌 및 엣지 케이스 검출**<br>• `effort` 제어(`high`~`xhigh`)를 통해 정밀한 코드 레벨 명세서 작성 및 아키텍처 결함 보완 |
| **자율 코딩 실행기 (구현)** | **Claude Code CLI** | `claude-fable-5-1`<br>*(또는 `claude-opus-5`)* | • 확정된 설계서(`TASK_SPEC.md`)를 넘겨받아 로컬 파일 직접 생성·수정·테스트 반복<br>• 장시간 무인 자율 코딩 완수 후 `git commit & push` 자동 수행 |

---

## 2. 전체 시스템 아키텍처

```text
[외부 스마트폰 / 회사 PC 브라우저 (모바일 최적화 채팅 UI)]
        │  ▲ (HTTPS / WebSocket 실시간 스트리밍)
        ▼  │
[보안 네트워크: Tailscale VPN 또는 Cloudflare Tunnel (포트포워딩 불필요)]
        │
        ▼
[집 Windows PC - WSL2 (Ubuntu 24.04 LTS)]
┌──────────────────────────────────────────────────────────────────────┐
│  [FastAPI 백엔드 오케스트레이터]                                     │
│                                                                      │
│  ■ Phase 1: 코드베이스 컨텍스트 로더 (Context Builder)               │
│    └─ 로컬 프로젝트 구조(Tree) 및 핵심 파일 추출 (암시적 캐싱 활용)  │
│                                                                      │
│  ■ Phase 2: 듀얼 아키텍트 설계 토론 엔진 (Debate Engine)             │
│    ├─► [Turn 1] Gemini 4 Argon (thinking_level="high")               │
│    │     : 전체 코드베이스 스캔 후 아키텍처 초안 및 수정 파일 제안   │
│    ├─► [Turn 2] Claude Fable 5.1 (effort="high")                     │
│    │     : Argon 설계안 비평, 모듈 간 의존성/타입 안정성 보완        │
│    └─► [Human-in-the-Loop] 사용자(나)의 개입, 조율 및 최종 승인      │
│                                                                      │
│  ■ Phase 3: 자율 구현 및 배포 엔진 (Execution & Git Engine)          │
│    ├─► Claude Code CLI (v2.1.250 이상, --model claude-fable-5-1)     │
│    └─► Git Subprocess (ai-work 브랜치 자동 Commit & GitHub Push)     │
└──────────────────────────────────────────────────────────────────────┘
        │
        ▼ (GitHub 원격 저장소 Push)
[집 MacBook] ──(퇴근 후 git pull)──► [Xcode 빌드 & 시뮬레이터 테스트 후 출시]
```

---

## 3. 페이즈별 상세 동작 플로우 (Workflow)

### Phase 1: 설계 및 상호 검증 (Design Debate Mode)
사용자가 스마트폰 웹 UI에서 프로젝트를 선택하고 요구사항(예: *"보상형 광고 시청 후 포인트를 지급하는 로직과 UI를 추가해줘"*)을 입력한다.

1. **Step 1 — Gemini 4 Argon의 전체 스캔 및 초안 설계:**
   * 백엔드 서버가 로컬 프로젝트의 파일 트리 및 주요 소스코드 텍스트를 추출하여 `gemini-4-argon`에 전달한다.
   * Argon은 `thinking_level="high"` 모드로 전체 앱 구조와 기존 코드에 미치는 영향을 분석해 **1차 아키텍처, 데이터 흐름, 수정/생성할 파일 목록**을 제안한다.
2. **Step 2 — Claude Fable 5.1의 교차 검증 (Critique):**
   * 백엔드 서버가 Argon의 설계안과 핵심 연관 파일을 `claude-fable-5-1`에 전달한다.
   * Fable 5.1은 Argon이 놓친 비동기 예외 처리, 상태 관리 충돌, iOS/Android 플랫폼별 네이티브 설정(`Info.plist` 등) 이슈를 지적하고 수정된 구현 방향을 제시한다.
3. **Step 3 — 사용자 의사결정 (Human-in-the-Loop):**
   * 웹 화면에 🟦 **Argon의 설계안**과 🟧 **Fable의 보완 의견**이 나란히 표시된다.
   * 사용자는 추가 질문을 던져 1턴 더 토론을 시키거나, *"Fable이 지적한 예외 처리 반영하고 UI 구조는 Argon 방식대로 진행해"*라고 최종 코멘트를 남긴 뒤 **[🚀 확정 및 개발 시작]** 버튼을 누른다.

### Phase 2: 무인 코드 구현 (Autonomous Coding Mode)
승인 버튼 클릭 시 채팅 모드가 종료되고 **자율 실행 모드**로 전환된다.

1. **최종 작업 명세서(`TASK_SPEC.md`) 자동 생성:**
   * Argon이 지금까지의 설계 대화와 사용자의 최종 결정을 종합하여, 파일별 수정 지침이 담긴 마크다운 명세서(`TASK_SPEC.md`)를 로컬 프로젝트 루트에 생성한다.
2. **Claude Code CLI 구동:**
   * 백엔드 서버가 로컬 프로젝트 경로에서 `Claude Code CLI`를 서브프로세스로 호출한다.
   * 웹 UI의 기어 스위치 설정에 따라 대규모 리팩터링/고난도 기능은 `claude-fable-5-1`로, 단순 기능/UI 수정은 `claude-opus-5`로 모델을 선택해 실행한다.

### Phase 3: GitHub 자동 Push 및 맥북 연동
1. `Claude Code` 프로세스가 종료되면 백엔드가 자동으로 전용 작업 브랜치(`feat/ai-remote`)에 변경 사항을 커밋하고 원격 GitHub 저장소에 Push한다.
2. 스마트폰 화면에 `git diff --stat` 요약 결과와 완료 알림이 표시된다.
3. 퇴근 후 집 맥북에서 `run_ios.sh` 스크립트를 실행해 최신 코드를 받아오고 즉시 Xcode 시뮬레이터로 테스트한다.

---

## 4. 백엔드 서버 구현 명세 (FastAPI)

WSL2 Ubuntu 환경에서 구동될 핵심 오케스트레이터 서버(`main.py`)의 설계 코드이다.

```python
import os
import subprocess
from fastapi import FastAPI
from pydantic import BaseModel
from google import genai
from google.genai import types
import anthropic

app = FastAPI(title="Pocket-Dev: Argon x Fable Orchestrator")

# API 클라이언트 초기화 (환경변수 설정 필수)
gemini_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
claude_client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

class DebateRequest(BaseModel):
    project_path: str
    user_prompt: str
    history: str = ""

class ExecuteRequest(BaseModel):
    project_path: str
    approved_plan: str
    use_fable_engine: bool = True
    branch_name: str = "feat/ai-remote"

def get_project_tree(project_path: str) -> str:
    """프로젝트 폴더 구조(.git, node_modules, build 제외) 추출"""
    cmd = "tree -I 'node_modules|.git|build|ios/Pods|.dart_tool' -L 4"
    res = subprocess.run(cmd, cwd=project_path, shell=True, capture_output=True, text=True)
    return res.stdout

@app.post("/api/debate")
async def run_design_debate(req: DebateRequest):
    repo_tree = get_project_tree(req.project_path)
    
    # [Turn 1] Gemini 4 Argon: 전체 아키텍처 및 수정 계획 수립
    argon_prompt = f"""
    [프로젝트 디렉토리 구조]
    {repo_tree}
    
    [이전 논의 내역]
    {req.history}
    
    [사용자 요구사항]
    {req.user_prompt}
    """
    
    argon_res = gemini_client.models.generate_content(
        model="gemini-4-argon",
        contents=argon_prompt,
        config=types.GenerateContentConfig(
            system_instruction=(
                "당신은 수석 소프트웨어 아키텍트(Gemini 4 Argon)입니다. "
                "전체 프로젝트 구조를 분석하여 변경/생성이 필요한 파일 경로, 데이터 흐름, "
                "구체적인 아키텍처 설계안을 마크다운으로 명확히 제시하세요."
            ),
            thinking_config=types.ThinkingConfig(thinking_level="high")
        )
    )
    argon_plan = argon_res.text

    # [Turn 2] Claude Fable 5.1: 설계 검증, 엣지 케이스 및 iOS/Android 호환성 비평
    fable_res = claude_client.messages.create(
        model="claude-fable-5-1",
        max_tokens=16000,
        system=(
            "당신은 수석 구현 엔지니어(Claude Fable 5.1)입니다. "
            "Gemini Argon이 작성한 설계안을 비판적으로 검토하여 의존성 충돌, 비동기 예외 처리, "
            "상태 관리 문제, 추후 MacBook Xcode 빌드 시 필요한 설정(Info.plist, Podfile 등) 누락 여부를 지적하고 보완안을 제시하세요."
        ),
        messages=[
            {
                "role": "user",
                "content": f"[사용자 요구사항]\n{req.user_prompt}\n\n[Argon의 1차 설계안]\n{argon_plan}\n\n위 설계안을 기술적으로 검토하고 개선된 구현 방향을 제안해줘."
            }
        ]
    )
    fable_critique = fable_res.content[0].text

    return {
        "argon_plan": argon_plan,
        "fable_critique": fable_critique
    }

@app.post("/api/execute")
async def execute_autonomous_coding(req: ExecuteRequest):
    # 1. Argon을 이용해 최종 승인된 내용을 TASK_SPEC.md로 정리
    spec_res = gemini_client.models.generate_content(
        model="gemini-4-argon",
        contents=f"다음 합의된 설계 내용을 바탕으로 Claude Code가 즉시 구현할 수 있는 명확한 작업 지시서(TASK_SPEC.md)를 작성해줘:\n\n{req.approved_plan}"
    )
    
    spec_path = os.path.join(req.project_path, "TASK_SPEC.md")
    with open(spec_path, "w", encoding="utf-8") as f:
        f.write(spec_res.text)

    # 2. 작업용 Git 브랜치 체크아웃
    subprocess.run(f"git checkout -B {req.branch_name}", cwd=req.project_path, shell=True)

    # 3. Claude Code CLI 실행 (Fable 5.1 또는 Opus 5)
    target_model = "claude-fable-5-1" if req.use_fable_engine else "claude-opus-5"
    claude_cmd = [
        "claude",
        "--model", target_model,
        "--dangerously-skip-permissions",
        "-p", (
            "루트 경로의 TASK_SPEC.md 파일을 읽고 지시된 모든 코드 작성과 수정을 완수해. "
            "작업이 끝나면 문법 오류나 정적 분석 에러가 없는지 확인하고 TASK_SPEC.md 파일은 삭제해."
        )
    ]
    code_proc = subprocess.run(claude_cmd, cwd=req.project_path, capture_output=True, text=True)

    # 4. Git Commit & Push 자동화
    git_cmds = (
        "git add . && "
        "git commit -m 'feat(ai): Argon 설계 및 Fable 5.1 원격 자율 구현 완료' && "
        f"git push -u origin {req.branch_name}"
    )
    git_proc = subprocess.run(git_cmds, cwd=req.project_path, shell=True, capture_output=True, text=True)

    # 5. 변경 사항 요약 반환
    diff_stat = subprocess.run("git show --stat", cwd=req.project_path, shell=True, capture_output=True, text=True)

    return {
        "status": "completed",
        "model_used": target_model,
        "claude_output": code_proc.stdout,
        "git_summary": diff_stat.stdout,
        "errors": code_proc.stderr + "\n" + git_proc.stderr
    }
```

---

## 5. 모바일 웹 UI/UX 화면 구성 명세

스마트폰 브라우저에서 한 손으로 조작할 수 있도록 구성할 화면 레이아웃이다.

1. **상단 헤더 (Top Bar):**
   * **프로젝트 드롭다운:** `/home/user/projects/` 하위의 로컬 저장소 목록 선택
   * **브랜치 표시 배지:** 현재 작업 중인 브랜치명 표시 (기본값: `feat/ai-remote`)
   * **구현 엔진 스위치:** `[🔥 Fable 5.1 (정밀 구현)]` ↔ `[⚡ Opus 5 (고속 구현)]`
2. **중앙 채팅 스트림 (Debate View):**
   * 🟦 **Gemini 4 Argon 카드:** 아키텍처 다이어그램, 수정 대상 파일 리스트 표시
   * 🟧 **Claude Fable 5.1 카드:** 코드 레벨 피드백, 엣지 케이스, iOS 연동 주의사항 표시
   * ⬜ **사용자 메시지:** 방향성 지시 및 피드백 입력 내역
3. **하단 액션 바 (Control Bar):**
   * `[💬 의견 보내기 (1턴 추가 토론)]` : 내 피드백을 반영해 Argon과 Fable이 다시 한 번 설계를 다듬게 함
   * `[🚀 설계 확정 및 코딩 시작]` : 현재 합의된 내용으로 즉시 `Claude Code CLI` 구동 및 Git Push 실행
   * `[⏪ 마지막 커밋 롤백]` : 결과가 마음에 들지 않을 때 원격에서 즉시 `git reset --hard HEAD~1` 수행

---

## 6. 윈도우 서버 & 맥북 연동 필수 설정 가이드

### 6.1 프로젝트 루트 `CLAUDE.md` 파일 배치 (필수)
윈도우 PC에는 macOS 전용 빌드 도구가 없으므로, 프로젝트 최상단에 아래 내용의 `CLAUDE.md` 파일을 생성해 두어야 Fable 5.1이 불필요한 빌드 에러에 빠지지 않는다.

```markdown
# Project Environment & AI Guidelines (CLAUDE.md)

1. **실행 OS 환경 주의:**
   - 현재 코드 작업이 이루어지는 환경은 **Windows WSL2 (Linux)** 서버이다.
   - 따라서 `xcodebuild`, `pod install`, `open -a Simulator` 등 **macOS 전용 명령어는 절대 실행하지 마라.**
2. **iOS 네이티브 설정 변경 규칙:**
   - 새로운 패키지나 권한이 추가되어 iOS 설정이 필요한 경우, `ios/Runner/Info.plist` 또는 `ios/Podfile`을 텍스트로 직접 수정하여 반영해 두어라.
   - 실제 `pod install` 및 시뮬레이터 빌드는 사용자가 퇴근 후 MacBook에서 수행한다.
3. **검증 및 마무리:**
   - 코드 수정 후에는 OS에 종속되지 않는 정적 분석(예: `flutter analyze`, `npm run lint`, `pytest` 등)만 실행하여 문법적 결함이 없음을 확인하라.
```

### 6.2 집 맥북(MacBook)용 원클릭 테스트 스크립트 (`run_mac_sim.sh`)
퇴근 후 맥북을 켜고 터미널에서 명령어 한 줄로 윈도우 PC에서 작업한 코드를 받아 시뮬레이터를 띄우는 스크립트이다.

```bash
#!/bin/bash
# 파일명: run_mac_sim.sh (맥북 프로젝트 루트에 저장 후 chmod +x run_mac_sim.sh)

BRANCH="feat/ai-remote"

echo "📥 1. 윈도우 AI 서버에서 작업한 최신 코드를 가져옵니다 ($BRANCH)..."
git fetch origin
git checkout $BRANCH
git pull origin $BRANCH

echo "📦 2. 패키지 및 iOS CocoaPods 의존성을 동기화합니다..."
# Flutter 프로젝트인 경우 (React Native나 순수 iOS일 경우 맞게 변경)
flutter pub get
cd ios && pod install && cd ..

echo "📱 3. Xcode iOS 시뮬레이터를 실행하고 앱을 빌드합니다..."
open -a Simulator
flutter run
```

### 6.3 사전 구축 체크리스트
- [ ] **윈도우 절전 모드 해제:** `제어판 > 전원 옵션`에서 절전 모드 진입 시간을 '안 함'으로 변경
- [ ] **Claude Code 버전 확인:** `claude-fable-5-1` 지원을 위해 `claude --version`이 **2.1.250 이상**인지 확인 (`npm update -g @anthropic-ai/claude-code`)
- [ ] **GitHub SSH 키 등록:** WSL2 내부에 비밀번호 입력 없이 `git push`가 가능하도록 SSH Key 또는 `gh auth login` 세팅 완료
- [ ] **Tailscale 설치:** 집 윈도우 PC와 스마트폰, 회사 PC에 Tailscale을 설치하여 외부에서도 고정 사설 IP(`100.x.x.x:8000`)로 안전하게 접속되도록 연결
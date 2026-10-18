"""토론 · 명세서 · 리뷰 · 권한 심사 — 전부 구독 CLI 엔진(별도 세션)으로 실행."""
import asyncio
import json
import re
import time
import uuid

from .config import CONFIG
from .engines import SANDBOX, UsageLimitError, generate
from .runs import Run

ICON = {"claude": "🟧", "codex": "🟩", "gemini": "🟦"}


def extract_json(text: str) -> dict:
    """모델 응답에서 첫 JSON 객체를 뽑는다 (```json 블록 포함)."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidates = [fenced.group(1)] if fenced else []
    start = text.find("{")
    if start >= 0:
        depth = 0
        for i, ch in enumerate(text[start:], start):
            depth += ch == "{"
            depth -= ch == "}"
            if depth == 0:
                candidates.append(text[start:i + 1])
                break
    for c in candidates:
        try:
            return json.loads(c)
        except json.JSONDecodeError:
            continue
    raise ValueError(f"JSON 을 찾지 못함: {text[:300]}")


async def with_limit_retry(run: Run, label: str, fn):
    """구독 한도에 걸리면 리셋까지 기다렸다가(또는 사용자가 '지금 재시도') fn(attempt) 를 다시 실행."""
    attempt = 0
    while True:
        try:
            return await fn(attempt)
        except UsageLimitError as e:
            attempt += 1
            fallback = CONFIG["engine"]["limit_retry_minutes"] * 60
            wait = max(60.0, e.reset_at - time.time() + 60) if e.reset_at else fallback
            key = f"limit:{uuid.uuid4().hex[:8]}"
            await run.emit("status", status="awaiting_user")
            await run.emit("awaiting_user", key=key, kind="limit", label=label, text=str(e),
                           retry_at=time.time() + wait)
            try:
                await asyncio.wait_for(run.wait_for(key), wait)
            except asyncio.TimeoutError:
                run.waiters.pop(key, None)
                await run.emit("user_reply", key=key, action="auto_retry", text="")
            await run.emit("status", status="running")
            await run.emit("log", text=f"🔁 {label} 재시도")


async def speak(run: Run, who: dict, system: str, prompt: str, *, kind: str = "debate",
                tools: list | tuple = (), timeout: float | None = None) -> str:
    """한 모델이 별도 세션에서 답하고, 스트리밍을 UI 로 흘려보낸다."""
    name = who.get("name", who["model"])

    async def attempt(_: int) -> str:
        msg_id = uuid.uuid4().hex[:10]
        await run.emit("message_start", msg_id=msg_id, kind=kind, name=name, engine=who["engine"],
                       model=who["model"], icon=ICON.get(who["engine"], "⬜"))

        async def on_text(text: str):
            await run.emit("delta", msg_id=msg_id, text=text)

        async def on_tool(text: str):
            await run.emit("tool", msg_id=msg_id, text=text)

        try:
            res = await generate(who["engine"], who["model"], system, prompt,
                                 cwd=run.project if tools else None, tools=tools,
                                 effort=who.get("effort") or None, on_text=on_text, on_tool=on_tool,
                                 timeout=timeout or CONFIG["debate"]["turn_timeout_sec"])
        except Exception as e:
            await run.emit("message_end", msg_id=msg_id, text=f"⚠️ {e}", error=True)
            raise
        await run.emit("message_end", msg_id=msg_id, text=res["text"], usage=res["usage"])
        return res["text"]

    return await with_limit_retry(run, f"{name} 응답", attempt)


def _transcript(messages: list[dict]) -> str:
    return "\n\n".join(f"### [{m['name']} · {m['model']}]\n{m['text']}" for m in messages)


def verdict(text: str) -> str:
    m = re.findall(r"VERDICT:\s*(AGREE|REVISE)", text.upper())
    return m[-1] if m else "REVISE"


DEBATE_RULES = """
[토론 규칙]
- 여러 AI 가 한 팀으로 기획안을 만듭니다. 앞선 참가자의 의견을 읽고, 동의하는 부분은 짧게 인정하고
  문제·누락·더 나은 대안을 구체적으로 제시하세요. 같은 말 반복 금지.
- 사용자는 아이디어만 줬습니다. 사용자에게 질문하지 말고 합리적인 가정을 명시한 뒤 진행하세요.
- 작업 폴더는 프로젝트 루트입니다. 아래에 없는 파일 내용이 필요하면 Read/Grep/Glob 도구로 직접 확인하세요.
  (파일 수정은 불가. 사용량을 아끼기 위해 꼭 필요한 파일만 읽으세요.)
- 답변 마지막 줄은 반드시 다음 중 하나:
  VERDICT: AGREE   (현재 논의된 안을 이대로 구현해도 된다)
  VERDICT: REVISE  (아직 고쳐야 할 점이 있다)
"""


async def debate(run: Run, participants: list[dict], idea: str, context: str,
                 history: list[dict], max_rounds: int) -> list[dict]:
    """참가자들이 라운드를 돌며 토론. 한 라운드에서 전원 AGREE 면 조기 종료."""
    tools = CONFIG["debate"]["read_tools"]
    for rnd in range(1, max_rounds + 1):
        await run.emit("stage", stage="debate", label=f"설계 토론 {rnd}/{max_rounds} 라운드")
        verdicts = []
        for p in participants:
            system = p["persona"].strip() + "\n" + DEBATE_RULES
            prompt = (f"[프로젝트 컨텍스트]\n{context}\n\n[사용자 아이디어]\n{idea}\n\n"
                      f"[지금까지의 토론]\n{_transcript(history) or '(아직 없음 — 당신이 첫 발언자입니다. 초안을 제시하세요.)'}\n\n"
                      f"당신은 '{p['name']}' 입니다. 라운드 {rnd}. 의견을 제시하세요.")
            text = await speak(run, p, system, prompt, tools=tools)
            history.append({"name": p["name"], "model": p["model"], "text": text})
            verdicts.append(verdict(text))
        await run.emit("log", text=f"라운드 {rnd} 판정: " + ", ".join(
            f"{p['name']}={v}" for p, v in zip(participants, verdicts)))
        if all(v == "AGREE" for v in verdicts):
            await run.emit("log", text="✅ 전원 합의 — 토론 종료")
            break
    return history


SPEC_SYSTEM = """당신은 테크 리드입니다. 팀 토론 내용과 사용자 지시를 종합해 Claude Code 가 바로 구현할 수 있는
최종 작업 명세서(TASK_SPEC.md)를 마크다운으로 작성하세요. 포함할 것:
1. 목표 요약 2. 합의된 설계 결정(+채택 이유) 3. 파일별 생성/수정 지침(경로 명시)
4. 엣지 케이스/예외 처리 5. 검증 방법(정적 분석/테스트 명령) 6. 범위 밖(하지 말 것)
경로가 확실하지 않으면 Read/Grep/Glob 으로 확인하세요. 최종 답변에는 명세서 본문만 출력하세요."""


async def write_spec(run: Run, synth: dict, idea: str, context: str, history: list[dict],
                     extra: str = "") -> str:
    await run.emit("stage", stage="spec", label="작업 명세서 작성")
    prompt = (f"[프로젝트 컨텍스트]\n{context}\n\n[사용자 아이디어]\n{idea}\n\n"
              f"[팀 토론]\n{_transcript(history)}\n\n{extra}")
    return await speak(run, {**synth, "name": "명세서 작성"}, SPEC_SYSTEM, prompt, kind="spec",
                       tools=CONFIG["debate"]["read_tools"])


REVIEW_FORMAT = """최종 답변으로 아래 JSON 만 출력하세요.
{"approve": true|false, "issues": ["반드시 고쳐야 할 문제 (없으면 빈 배열)"], "summary": "한두 문장 평가"}
사소한 취향 문제로는 반려하지 마세요."""

REVIEW_SYSTEMS = {
    "plan": ("기획안", """당신은 깐깐한 시니어 리뷰어입니다. 아직 구현 전인 작업 명세서(기획안)를 검토합니다.
명세서에 나오는 파일·함수가 아직 프로젝트에 없는 것은 정상입니다 — 이제부터 구현할 내용입니다.
검토 기준: 사용자 아이디어를 빠짐없이 반영했는가, 기존 코드 구조와 맞는가(필요하면 Read/Grep/Glob 으로 확인),
경로·인터페이스가 구체적인가, 엣지 케이스와 검증 방법이 있는가, 범위가 과하지 않은가.
""" + REVIEW_FORMAT),
    "code": ("코드", """당신은 깐깐한 시니어 리뷰어입니다. 작업 명세서와 실제 변경 사항(git diff)을 비교해 검토합니다.
검토 기준: 명세 불일치, 버그, 빠진 예외 처리, 보안 문제. 필요하면 Read/Grep/Glob 으로 주변 코드를 확인하세요.
""" + REVIEW_FORMAT),
}


async def review(run: Run, reviewers: list[dict], kind: str, material: str) -> list[dict]:
    """리뷰어 전원이 병렬로 검토 (kind: plan | code). [{reviewer, approve, issues, summary}]"""
    what, system = REVIEW_SYSTEMS[kind]

    async def one(r: dict) -> dict:
        try:
            text = await speak(run, {**r, "name": f"{what} 리뷰"}, system, material,
                               kind="review", tools=CONFIG["debate"]["read_tools"])
            d = extract_json(text)
            return {"reviewer": r["model"], "approve": bool(d.get("approve")),
                    "issues": d.get("issues") or [], "summary": d.get("summary", "")}
        except Exception as e:
            return {"reviewer": r["model"], "approve": False, "issues": [f"리뷰 실패: {e}"], "summary": ""}

    return await asyncio.gather(*(one(r) for r in reviewers))


PERMISSION_SYSTEM = """당신은 자율 코딩 에이전트(Claude Code)의 보안 감독관입니다.
에이전트가 아래 작업 명세를 수행하던 중 도구 사용 권한을 요청했습니다.
판정 기준:
- allow: 명세 수행에 필요하고, 프로젝트 폴더 안에서만 영향이 있으며, 되돌릴 수 있는 작업
  (빌드/테스트/린트 실행, 패키지 설치, 프로젝트 내 파일 수정, git status/diff 등)
- deny: 명세와 무관, 프로젝트 밖 파일 변경/삭제, 비밀키·자격증명 유출 위험, 강제 push/히스토리 삭제,
  시스템 설정 변경, 알 수 없는 원격 스크립트 실행, macOS 전용 명령(xcodebuild, pod install 등)
- ask_human: 판단이 애매하거나 비용/외부 영향이 큰 작업 (배포, 결제, 외부 API 에 데이터 전송 등)
반드시 JSON 만 출력: {"decision": "allow"|"deny"|"ask_human", "reason": "한 문장"}"""


async def judge_permission(r: dict, spec: str, project: str, tool_name: str, tool_input: dict) -> dict:
    """권한 심사 — 도구 없이 별도 세션에서 빠르게 판정. 실패하면 사람에게 넘긴다."""
    prompt = (f"[프로젝트 경로]\n{project}\n\n[작업 명세 요약]\n{spec[:12000]}\n\n"
              f"[요청된 도구]\n{tool_name}\n\n[입력]\n{json.dumps(tool_input, ensure_ascii=False, indent=2)[:8000]}")
    try:
        res = await generate(r["engine"], r["model"], PERMISSION_SYSTEM, prompt,
                             cwd=str(SANDBOX), effort=r.get("effort") or None, timeout=180)
        d = extract_json(res["text"])
        decision = d.get("decision", "deny")
        if decision not in ("allow", "deny", "ask_human"):
            decision = "deny"
        return {"reviewer": r["model"], "engine": r["engine"], "decision": decision,
                "reason": d.get("reason", "")}
    except Exception as e:
        return {"reviewer": r["model"], "engine": r["engine"], "decision": "ask_human",
                "reason": f"심사 실패: {e}"}

"""아이디어 → 토론 → 명세서 → AI 검토 → (사람 승인) → Claude Code 구현 → AI 코드리뷰 → commit/push

단계(run.checkpoint["phase"])가 끝날 때마다 저장해서, 한도·오류·서버 재시작으로 멈춰도 그 단계부터 재개한다.
진행 중에 받은 사용자 메시지(run.notes)는 다음 AI 호출에 반영한다.
"""
import asyncio
from pathlib import Path

from . import agents, executor, github, stitch
from .config import CONFIG
from .attachments import prompt_block
from .context import build_context
from .runs import Run

PHASE_LABEL = {"debate": "설계 토론", "spec": "명세서 작성", "plan_review": "기획안 검토", "plan_gate": "기획안 승인 대기",
               "design": "Stitch 화면 디자인",
               "execute": "구현", "code_review": "코드 리뷰", "git": "커밋 & push", "done": "완료"}
BASE_PROMPT = ("루트의 TASK_SPEC.md 를 읽고 지시된 모든 코드 작성과 수정을 완수해. "
               "끝나면 정적 분석/테스트로 오류가 없는지 확인해. git commit/push 는 하지 마 (오케스트레이터가 함). "
               "권한이 거부되면 다른 안전한 방법을 찾아.")
RESUME_NOTE = ("\n\n(이전 실행이 중간에 끊겼다. git status/diff 로 지금까지의 변경을 확인하고 "
               "남은 작업을 이어서 완료해.)")


def _fmt_reviews(reviews: list[dict]) -> str:
    return "\n".join(f"- {r['reviewer']}: {'승인' if r['approve'] else '반려'} — {r['summary']}"
                     + "".join(f"\n    • {i}" for i in r["issues"]) for r in reviews)


def take_notes(run: Run) -> list[str]:
    notes, run.notes = run.notes, []
    if notes:
        run.save()
    return notes


def _note_block(notes: list[str]) -> str:
    return ("\n\n[사용자 추가 지시 — 반드시 반영]\n" + "\n".join(f"- {n}" for n in notes)) if notes else ""


def _set_phase(run: Run, phase: str, **extra) -> None:
    run.checkpoint.update(phase=phase, **extra)
    run.save()


def _user_entry(run: Run, text: str, name: str = "사용자") -> None:
    run.history.append({"round": agents.last_round(run.history), "pid": "user", "name": name,
                        "model": "human", "text": text})


def start(run: Run, port: int) -> None:
    run.task = asyncio.create_task(run_pipeline(run, port))


async def handle_chat(run: Run, text: str, port: int) -> None:
    """작업 화면 채팅 — 진행 비서가 먼저 읽고, 질문이면 답만 하고 작업 요청일 때만 작업을 진행시킨다."""
    decision = await agents.assistant_decide(run, text)
    action, task = decision["action"], decision["task"]
    if action == "work":
        if "plan" in run.waiters:  # 기획안 승인 대기 → 수정 요청으로 한 라운드 더
            run.resolve("plan", {"action": "feedback", "text": task})
            await run.emit("user_reply", key="plan", action="feedback", text=task)
        elif run.alive:  # 진행 중 → 다음 단계에 반영
            run.notes.append(task)
            run.save()
        elif run.status == "done":  # 완료 → 추가 작업 한 바퀴
            start_followup(run, task)
            start(run, port)
        else:  # 멈춤 → 지시를 담아 재개
            run.notes.append(task)
            start(run, port)
    elif action == "resume":
        for key in [k for k in list(run.waiters) if k.startswith("limit:")]:
            run.resolve(key, {"action": "retry"})
            await run.emit("user_reply", key=key, action="retry", text="")
        if not run.alive and run.status not in ("done",):
            start(run, port)


def start_followup(run: Run, text: str) -> None:
    """완료된 작업에 추가 요청 → 토론 1라운드부터 한 바퀴 더."""
    _user_entry(run, text, "사용자(추가 요청)")
    last = agents.last_round(run.history)
    run.checkpoint = {"phase": "debate", "round_from": last + 1, "round_until": last + 1, "followup": True}
    run.save()


async def _review_and_revise_spec(run: Run, s: dict, ctx: str) -> str:
    spec = run.spec
    if not (CONFIG["plan_review"]["enabled"] and s["reviewers"]):
        return spec
    for attempt in range(CONFIG["plan_review"]["max_revisions"] + 1):
        await run.emit("stage", stage="plan_review", label=f"기획안 AI 검토 ({attempt + 1}차)")
        reviews = await agents.review(run, s["reviewers"], "plan",
                                      f"[사용자 아이디어]\n{run.idea}\n\n{prompt_block(run.attachments)}\n\n"
                                      f"[작업 명세서]\n{spec}")
        if all(r["approve"] for r in reviews):
            await run.emit("log", text="✅ 기획안 리뷰어 전원 승인")
            return spec
        if attempt == CONFIG["plan_review"]["max_revisions"]:
            await run.emit("log", text="⚠️ 수정 한도 도달 — 남은 지적사항과 함께 진행합니다")
            return spec
        spec = await agents.write_spec(
            run, s["synthesizer"], run.idea, ctx, run.history,
            f"[이전 명세서]\n{spec}\n\n[리뷰 지적사항 — 모두 반영해서 다시 작성]\n{_fmt_reviews(reviews)}"
            + _note_block(take_notes(run)))
        run.spec = spec
        run.save()
    return spec


async def _implement(run: Run, prompt: str, model: str, port: int) -> None:
    """Claude Code 실행. 구독 한도에 걸리면 기다렸다가 이어서 진행."""
    (Path(run.project) / "TASK_SPEC.md").write_text(run.spec, encoding="utf-8")
    await agents.with_limit_retry(
        run, "Claude Code 구현",
        lambda attempt: executor.run_claude_code(run, prompt + (RESUME_NOTE if attempt else ""), model, port))


async def _ensure_branch(run: Run, s: dict) -> None:
    """작업 브랜치 준비 (디자인·구현 공통). 재개면 이미 있던 브랜치에서 커밋 안 된 변경 그대로 이어감."""
    cp = run.checkpoint
    branch = s.get("branch") or CONFIG["git"]["branch"]
    if cp.get("branch_ready"):
        run.base_branch = cp.get("base_branch", "")
        await executor.git(run.project, "checkout", branch)
    else:
        await executor.prepare_branch(run, branch)
        cp.update(branch_ready=True, base_branch=run.base_branch)
        run.save()


def _wants_design(run: Run) -> bool:
    return bool(run.settings.get("stitch")) and stitch.configured() and not run.checkpoint.get("design_done")


async def _design(run: Run, s: dict, port: int) -> None:
    """Stitch 로 화면 디자인을 먼저 뽑아 design/stitch/ 에 저장."""
    await run.emit("stage", stage="design", label="🎨 Stitch 화면 디자인")
    await _ensure_branch(run, s)
    (Path(run.project) / "TASK_SPEC.md").write_text(run.spec, encoding="utf-8")
    prompt = stitch.design_prompt(Path(run.project).name)
    await agents.with_limit_retry(
        run, "Stitch 화면 디자인",
        lambda attempt: executor.run_claude_code(run, prompt + (RESUME_NOTE if attempt else ""), stitch.model(), port,
                                                 extra_mcp=stitch.mcp_servers(), extra_allowed=["mcp__stitch"]))


async def _execute(run: Run, s: dict, port: int) -> None:
    cp = run.checkpoint
    await run.emit("stage", stage="execute", label="Claude Code 자율 구현")
    await _ensure_branch(run, s)
    prompt = BASE_PROMPT + (RESUME_NOTE if cp.get("impl_started") else "") + _note_block(take_notes(run))
    if run.attachments:
        prompt += (f"\n\n사용자 첨부 자료(기획 문서·시안 등)가 {run.attach_dir} 에 있다. "
                   "명세서에 반영돼 있지만 화면 시안 같은 건 필요하면 열어서 참고해.")
    if cp.get("design_done"):
        prompt += ("\n\ndesign/stitch/ 에 Stitch 로 만든 화면 디자인(HTML·스크린샷·README)이 있다. "
                   "UI 는 이 디자인을 최대한 따라 구현해 (Flutter 등 다른 프레임워크면 위젯으로 옮겨서).")
    _set_phase(run, "execute", impl_started=True)
    await _implement(run, prompt, s["executor_model"], port)


async def _code_review_step(run: Run, s: dict, port: int) -> bool:
    """코드 리뷰 한 번 (+ 반려 시 수정). 끝났으면 True."""
    notes = take_notes(run)
    if notes:  # 구현 중에 받은 사용자 지시 먼저 반영
        await run.emit("stage", stage="execute", label="사용자 추가 지시 반영")
        await _implement(run, BASE_PROMPT + "\n\n이미 구현이 되어 있다." + _note_block(notes),
                         s["executor_model"], port)
    if not s["reviewers"]:
        return True
    loop = run.checkpoint.get("fix_loop", 0)
    await run.emit("stage", stage="code_review", label=f"AI 코드 리뷰 ({loop + 1}차)")
    diff = await executor.staged_diff(run.project)
    reviews = await agents.review(run, s["reviewers"], "code",
                                  f"[작업 명세서]\n{run.spec}\n\n[변경 사항 git diff]\n{diff}")
    if all(r["approve"] for r in reviews):
        await run.emit("log", text="✅ 코드 리뷰 전원 승인")
        return True
    if loop >= CONFIG["executor"]["max_fix_loops"]:
        await run.emit("log", text="⚠️ 수정 한도 도달 — 남은 지적사항과 함께 마무리합니다")
        return True
    await run.emit("stage", stage="execute", label=f"리뷰 반영 수정 ({loop + 1}차)")
    await _implement(run, BASE_PROMPT + "\n\n이미 1차 구현이 되어 있다. 코드 리뷰어 지적사항을 모두 고쳐:\n"
                     + _fmt_reviews(reviews), s["executor_model"], port)
    _set_phase(run, "code_review", fix_loop=loop + 1)
    return False


async def run_pipeline(run: Run, port: int) -> None:
    s = run.settings
    project = Path(run.project)
    try:
        await run.emit("status", status="running")
        resumed = bool(run.history) or run.checkpoint.get("phase") != "debate"
        if resumed:
            await run.emit("log", text=f"▶ 이어서 진행합니다 — {PHASE_LABEL.get(run.checkpoint['phase'], '')} 단계부터")
        else:
            await run.emit("stage", stage="context", label="코드베이스 분석")
        ctx = build_context(project, CONFIG["debate"]["context_char_budget"])
        if not resumed:
            await run.emit("log", text=f"📂 컨텍스트 {len(ctx):,}자 (나머지 파일은 AI 가 직접 열어봄)")

        participants = [p for p in s["participants"] if p.get("enabled", True)]
        if not participants:
            raise RuntimeError("토론 참가 모델을 1개 이상 선택하세요")

        while (phase := run.checkpoint["phase"]) != "done":
            if phase == "debate":
                await agents.debate(run, participants, run.idea, ctx, lambda: take_notes(run))
                _set_phase(run, "spec")

            elif phase == "spec":
                for note in take_notes(run):
                    _user_entry(run, note)
                extra = ""
                if run.checkpoint.get("followup") and run.spec:
                    extra = (f"[이전 명세서 — 이미 구현·커밋됨]\n{run.spec}\n\n"
                             "이번 명세서는 사용자의 추가 요청과 이번 토론 내용만 다루세요.")
                run.spec = await agents.write_spec(run, s["synthesizer"], run.idea, ctx, run.history, extra)
                _set_phase(run, "plan_review")

            elif phase == "plan_review":
                run.spec = await _review_and_revise_spec(run, s, ctx)
                await run.emit("spec", text=run.spec)
                _set_phase(run, "plan_gate" if s.get("plan_gate", True) else ("design" if _wants_design(run) else "execute"))

            elif phase == "plan_gate":
                await run.emit("status", status="awaiting_user")
                await run.emit("awaiting_user", key="plan", kind="plan")
                answer = await run.wait_for("plan")
                await run.emit("status", status="running")
                action, text = answer.get("action"), (answer.get("text") or "").strip()
                if action == "cancel":
                    await run.emit("status", status="cancelled")
                    return
                if text:
                    _user_entry(run, text, "사용자(최종 결정권자)")
                if action == "approve":
                    if text:  # 승인 + 코멘트 → 명세서에만 반영하고 진행
                        run.spec = await agents.write_spec(
                            run, s["synthesizer"], run.idea, ctx, run.history,
                            f"[이전 명세서]\n{run.spec}\n\n사용자 마지막 코멘트를 반영해 최종본을 작성하세요.")
                        await run.emit("spec", text=run.spec)
                    _set_phase(run, "design" if _wants_design(run) else "execute")
                else:  # 수정 요청 → 한 라운드 더 토론
                    last = agents.last_round(run.history)
                    _set_phase(run, "debate", round_from=last + 1, round_until=last + 1)

            elif phase == "design":
                await _design(run, s, port)
                _set_phase(run, "execute", design_done=True)

            elif phase == "execute":
                await _execute(run, s, port)
                _set_phase(run, "code_review")

            elif phase == "code_review":
                if await _code_review_step(run, s, port):
                    _set_phase(run, "git")

            elif phase == "git":
                await run.emit("stage", stage="git", label="커밋 & push")
                (project / "TASK_SPEC.md").unlink(missing_ok=True)
                run.base_branch = run.checkpoint.get("base_branch", run.base_branch)
                branch = s.get("branch") or CONFIG["git"]["branch"]
                diff = await executor.staged_diff(run.project)
                message = ""
                if diff.strip():  # 바뀐 게 없으면 메시지도 만들지 않는다 (commit_and_push → "변경 사항 없음")
                    message = await agents.commit_message(diff, run.spec)  # 요청 문장이 아니라 실제 변경 내용으로
                    await run.emit("log", text=f"📝 커밋 메시지: {message.splitlines()[0]}")
                committed = await executor.commit_and_push(run, branch, message)
                links = {}
                web = await github.web_url(run.project) if committed["pushed"] else None
                if web:  # GitHub 에서 바로 볼 수 있는 링크 (비공개 저장소는 GitHub 로그인 필요)
                    links["branch"] = f"{web}/tree/{branch}"
                    base = await executor.default_branch(run.project, run.checkpoint.get("base_branch") or "main")
                    if base and base != branch:
                        links["compare"] = f"{web}/compare/{base}...{branch}"
                await run.emit("result", text=committed["summary"], links=links)
                _set_phase(run, "done")

        await run.emit("stage", stage="done", label="완료")
        await run.emit("status", status="done")
    except asyncio.CancelledError:
        await run.emit("status", status="cancelled")
        raise
    except Exception as e:
        await run.emit("error", text=str(e))
        await run.emit("status", status="error")
    finally:
        (project / "TASK_SPEC.md").unlink(missing_ok=True)

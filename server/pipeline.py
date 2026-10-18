"""아이디어 → 토론 → 명세서 → AI 검토 → (사람 승인) → Claude Code 구현 → AI 코드리뷰 → commit/push"""
import asyncio
from pathlib import Path

from . import agents, executor
from .config import CONFIG
from .context import build_context
from .runs import Run

RESUME_NOTE = ("\n\n(이전 실행이 구독 사용량 한도로 중간에 끊겼다. git status/diff 로 지금까지의 변경을 확인하고 "
               "남은 작업을 이어서 완료해.)")


def _fmt_reviews(reviews: list[dict]) -> str:
    return "\n".join(f"- {r['reviewer']}: {'승인' if r['approve'] else '반려'} — {r['summary']}"
                     + "".join(f"\n    • {i}" for i in r["issues"]) for r in reviews)


async def _review_and_revise_spec(run: Run, s: dict, idea: str, ctx: str, history: list, spec: str) -> str:
    if not (CONFIG["plan_review"]["enabled"] and s["reviewers"]):
        return spec
    for attempt in range(CONFIG["plan_review"]["max_revisions"] + 1):
        await run.emit("stage", stage="plan_review", label=f"기획안 AI 검토 ({attempt + 1}차)")
        reviews = await agents.review(run, s["reviewers"], "plan",
                                      f"[사용자 아이디어]\n{idea}\n\n[작업 명세서]\n{spec}")
        if all(r["approve"] for r in reviews):
            await run.emit("log", text="✅ 기획안 리뷰어 전원 승인")
            return spec
        if attempt == CONFIG["plan_review"]["max_revisions"]:
            await run.emit("log", text="⚠️ 수정 한도 도달 — 남은 지적사항과 함께 진행합니다")
            return spec
        spec = await agents.write_spec(run, s["synthesizer"], idea, ctx, history,
                                       f"[이전 명세서]\n{spec}\n\n[리뷰 지적사항 — 모두 반영해서 다시 작성]\n{_fmt_reviews(reviews)}")
    return spec


async def _implement(run: Run, prompt: str, model: str, port: int) -> None:
    """Claude Code 실행. 구독 한도에 걸리면 기다렸다가 이어서 진행."""
    await agents.with_limit_retry(
        run, "Claude Code 구현",
        lambda attempt: executor.run_claude_code(run, prompt + (RESUME_NOTE if attempt else ""), model, port))


async def run_pipeline(run: Run, port: int) -> None:
    s = run.settings
    try:
        project = Path(run.project)
        await run.emit("stage", stage="context", label="코드베이스 분석")
        ctx = build_context(project, CONFIG["debate"]["context_char_budget"])
        await run.emit("log", text=f"📂 컨텍스트 {len(ctx):,}자 (나머지 파일은 AI 가 직접 열어봄)")

        participants = [p for p in s["participants"] if p.get("enabled", True)]
        if not participants:
            raise RuntimeError("토론 참가 모델을 1개 이상 선택하세요")
        history: list[dict] = []
        idea = run.idea

        # ── 1. 토론 → 명세서 → AI 검토 → 사람 승인 (피드백 주면 반복) ──
        while True:
            await agents.debate(run, participants, idea, ctx, history, s["max_rounds"])
            spec = await agents.write_spec(run, s["synthesizer"], idea, ctx, history)
            spec = await _review_and_revise_spec(run, s, idea, ctx, history, spec)
            run.spec = spec
            await run.emit("spec", text=spec)

            if not s.get("plan_gate", True):
                break
            await run.emit("status", status="awaiting_user")
            await run.emit("awaiting_user", key="plan", kind="plan")
            answer = await run.wait_for("plan")
            await run.emit("status", status="running")
            if answer.get("action") == "cancel":
                await run.emit("status", status="cancelled")
                return
            if answer.get("text"):
                await run.emit("user", text=answer["text"])
                history.append({"name": "사용자(최종 결정권자)", "model": "human", "text": answer["text"]})
            if answer.get("action") == "approve":
                if answer.get("text"):  # 승인 + 코멘트 → 명세서에만 반영하고 진행
                    spec = await agents.write_spec(run, s["synthesizer"], idea, ctx, history,
                                                   f"[이전 명세서]\n{spec}\n\n사용자 마지막 코멘트를 반영해 최종본을 작성하세요.")
                    run.spec = spec
                    await run.emit("spec", text=spec)
                break
            # feedback → 한 라운드 더 토론
            s = {**s, "max_rounds": 1}

        # ── 2. 구현 ──
        branch = s.get("branch") or CONFIG["git"]["branch"]
        await run.emit("stage", stage="execute", label="Claude Code 자율 구현")
        await executor.prepare_branch(run, branch)
        spec_file = project / "TASK_SPEC.md"
        spec_file.write_text(spec, encoding="utf-8")
        base_prompt = ("루트의 TASK_SPEC.md 를 읽고 지시된 모든 코드 작성과 수정을 완수해. "
                       "끝나면 정적 분석/테스트로 오류가 없는지 확인해. git commit/push 는 하지 마 (오케스트레이터가 함). "
                       "권한이 거부되면 다른 안전한 방법을 찾아.")
        await _implement(run, base_prompt, s["executor_model"], port)

        # ── 3. AI 코드 리뷰 → 반려 시 재수정 ──
        for loop in range(CONFIG["executor"]["max_fix_loops"] + 1):
            if not s["reviewers"]:
                break
            await run.emit("stage", stage="code_review", label=f"AI 코드 리뷰 ({loop + 1}차)")
            diff = await executor.staged_diff(run.project)
            reviews = await agents.review(run, s["reviewers"], "code",
                                          f"[작업 명세서]\n{spec}\n\n[변경 사항 git diff]\n{diff}")
            if all(r["approve"] for r in reviews):
                await run.emit("log", text="✅ 코드 리뷰 전원 승인")
                break
            if loop == CONFIG["executor"]["max_fix_loops"]:
                await run.emit("log", text="⚠️ 수정 한도 도달 — 남은 지적사항은 커밋 메시지에 기록합니다")
                break
            await run.emit("stage", stage="execute", label=f"리뷰 반영 수정 ({loop + 1}차)")
            await _implement(run, base_prompt + "\n\n이미 1차 구현이 되어 있다. 코드 리뷰어 지적사항을 모두 고쳐:\n"
                             + _fmt_reviews(reviews), s["executor_model"], port)

        # ── 4. 커밋 & push ──
        await run.emit("stage", stage="git", label="커밋 & push")
        spec_file.unlink(missing_ok=True)
        title = run.idea.strip().splitlines()[0][:60]
        names = ", ".join(sorted({p["model"] for p in participants}))
        summary = await executor.commit_and_push(
            run, branch, f"feat(ai): {title}\n\n설계 토론: {names}\n구현: Claude Code ({s['executor_model']})")
        await run.emit("result", text=summary)
        await run.emit("stage", stage="done", label="완료")
        await run.emit("status", status="done")
    except asyncio.CancelledError:
        await run.emit("status", status="cancelled")
        raise
    except Exception as e:
        await run.emit("error", text=str(e))
        await run.emit("status", status="error")
    finally:
        (Path(run.project) / "TASK_SPEC.md").unlink(missing_ok=True)

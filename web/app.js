// Pocket-Dev 웹 UI — 구독 CLI 엔진
const $ = (s, el = document) => el.querySelector(s);
const LS_KEY = "pocketdev.team.v3";  // v3: effort 기본값 medium
const EFFORTS = ["", "low", "medium", "high", "xhigh", "max"];

let CFG = null, SESSION = null;
let state = null;   // { participants, synthesizer, reviewers, permission_reviewers, executor_model, max_rounds, branch, plan_gate }
let currentRun = null, es = null, pending = new Map(), started = false;

// ── API (로그인 세션 쿠키로 인증) ──
async function api(path, opts = {}) {
  const res = await fetch(path, { ...opts, credentials: "same-origin", headers: { "Content-Type": "application/json", ...(opts.headers || {}) } });
  if (res.status === 401 && path !== "/api/login") { showGate("login"); throw new Error("로그인이 필요해요"); }
  if (res.status === 423) { showGate("locked"); throw new Error("잠겨 있어요"); }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error(body.detail || res.statusText), { body, status: res.status });
  return body;
}
const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body) });
const esc = s => { const d = document.createElement("div"); d.textContent = s ?? ""; return d.innerHTML; };
const models = engine => CFG.engines[engine]?.models || [];

// ── 엔진 / 모델 / effort 선택 위젯 ──
function modelPicker(target, value, onChange) {
  value.engine ||= "claude";
  const eng = document.createElement("select");
  Object.entries(CFG.engines).forEach(([id, e]) => eng.add(new Option(e.label, id)));
  Object.entries(CFG.upcoming).forEach(([id, label]) => { const o = new Option(label, id); o.disabled = true; eng.add(o); });
  eng.value = value.engine;

  const model = document.createElement("select");
  const fill = () => {
    model.innerHTML = "";
    const list = [...models(eng.value)];
    if (value.model && !list.some(m => m.id === value.model)) list.unshift({ id: value.model, label: value.model });
    list.forEach(m => model.add(new Option(m.label, m.id)));
    model.add(new Option("✎ 직접 입력…", "__custom"));
    model.value = list.some(m => m.id === value.model) ? value.model : (list[0]?.id || "");
    value.model = model.value;
  };
  fill();
  eng.onchange = () => { value.engine = eng.value; value.model = ""; fill(); onChange(); };
  model.onchange = () => {
    if (model.value === "__custom") {
      const id = prompt("모델 ID 입력 (예: claude-opus-5-5)");
      if (id) value.model = id.trim();
      fill();
    } else value.model = model.value;
    onChange();
  };

  const effort = document.createElement("select");
  effort.className = "effort"; effort.title = "effort — 생각 깊이 (기본=medium, 높을수록 꼼꼼하지만 구독 한도를 훨씬 많이 씀)";
  EFFORTS.forEach(e => effort.add(new Option("⚡" + (e === "medium" ? "med" : e || "기본"), e)));
  effort.value = value.effort || "";
  effort.onchange = () => { value.effort = effort.value; onChange(); };

  target.append(eng, model, effort);
}

// ── 팀 편집 ──
const save = () => localStorage.setItem(LS_KEY, JSON.stringify(state));

function memberList(boxId, list, emptyText) {
  const box = $(boxId); box.innerHTML = list.length ? "" : `<p class="muted">${emptyText}</p>`;
  list.forEach((r, i) => {
    const row = document.createElement("div"); row.className = "member-row model-pick";
    modelPicker(row, r, save);
    const del = document.createElement("button"); del.textContent = "✕";
    del.onclick = () => { list.splice(i, 1); save(); renderTeam(); };
    row.append(del); box.append(row);
  });
}

function renderTeam() {
  const box = $("#participants"); box.innerHTML = "";
  state.participants.forEach((p, i) => {
    const el = $("#participantTpl").content.firstElementChild.cloneNode(true);
    const paint = () => el.className = `member e-${p.engine}${p.enabled ? "" : " off"}`;
    paint();
    $(".enabled", el).checked = p.enabled;
    $(".enabled", el).onchange = e => { p.enabled = e.target.checked; paint(); save(); };
    $(".name", el).value = p.name;
    $(".name", el).oninput = e => { p.name = e.target.value; save(); };
    $(".persona", el).value = p.persona || "";
    $(".persona", el).oninput = e => { p.persona = e.target.value; save(); };
    $(".del", el).onclick = () => { state.participants.splice(i, 1); save(); renderTeam(); };
    $(".up", el).onclick = () => { if (i) { [state.participants[i - 1], state.participants[i]] = [p, state.participants[i - 1]]; save(); renderTeam(); } };
    modelPicker($(".model-pick", el), p, () => { paint(); save(); });
    box.append(el);
  });

  $("#synthesizer").innerHTML = "";
  const sw = document.createElement("div"); sw.className = "member-row model-pick";
  modelPicker(sw, state.synthesizer, save); $("#synthesizer").append(sw);

  memberList("#reviewers", state.reviewers, "리뷰어 없음 — 기획안/코드 리뷰를 건너뜁니다");
  memberList("#permReviewers", state.permission_reviewers, "심사자 없음 — 권한 요청이 전부 거부됩니다");

  const ex = $("#executorModel"); ex.innerHTML = "";
  models("claude").forEach(m => ex.add(new Option(m.label, m.id)));
  if (![...ex.options].some(o => o.value === state.executor_model)) ex.add(new Option(state.executor_model, state.executor_model));
  ex.value = state.executor_model;
  ex.onchange = () => { state.executor_model = ex.value; save(); };
  $("#maxRounds").value = state.max_rounds;
  $("#maxRounds").onchange = e => { state.max_rounds = +e.target.value; save(); };
  $("#branch").value = state.branch;
  $("#branch").onchange = e => { state.branch = e.target.value; save(); };
  document.querySelectorAll("input[name=planGate]").forEach(r => {
    r.checked = (r.value === "review") === (state.plan_gate !== false);
    r.onchange = () => { state.plan_gate = $("input[name=planGate]:checked").value === "review"; save(); };
  });
}

const persona = id => (CFG.team.participants.find(p => p.id === id) || {}).persona || "";
const member = (id, name, model) => ({ id, name, engine: "claude", model, effort: model.includes("haiku") ? "" : "medium", enabled: true, persona: persona(id) });
const PRESETS = {
  duo: () => [member("architect", "수석 아키텍트", "claude-opus-5-5"), member("engineer", "수석 엔지니어", "claude-sonnet-5-5")],
  trio: () => [...PRESETS.duo(), member("qa", "제품/QA 리뷰어", "claude-haiku-5-5")],
  light: () => [member("architect", "수석 아키텍트", "claude-sonnet-5-5"), member("engineer", "수석 엔지니어", "claude-haiku-5-5")],
};

function renderEngineStatus() {
  const s = CFG.engines.claude?.status || {};
  let html = "";  // 문제가 있을 때만 표시
  if (!s.installed) html = `❌ <b>Claude Code 미설치</b> — PC에 설치 후 <code>claude auth login</code>`;
  else if (!s.logged_in) html = `❌ <b>Claude 로그인 필요</b> — PC에서 <code>claude auth login</code> (구독 계정)`;
  else if (s.method !== "claude.ai") html = `⚠️ Claude 가 구독이 아닌 <b>${esc(s.method)}</b> 방식으로 로그인됨 — 크레딧 과금될 수 있어요`;
  $("#engineStatus").innerHTML = html;
  $("#engineStatus").hidden = !html;
}

// ── 토큰 사용량 ──
const fmtN = n => n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e3 ? Math.round(n / 1e3) + "K" : String(n);
const totalOf = b => b.input + b.output + b.cache_read + b.cache_write;

async function loadUsage() {
  try {
    const d = await api("/api/usage");
    $("#usageLine").textContent = `오늘 ${fmtN(totalOf(d.pc.today))} · 7일 ${fmtN(totalOf(d.pc.week))}`;
    const row = (label, b) => `<tr><td>${label}</td><td>${fmtN(b.output)}</td><td>${fmtN(b.input + b.cache_write)}</td>` +
      `<td>${fmtN(b.cache_read)}</td><td><b>${fmtN(totalOf(b))}</b></td></tr>`;
    const models = b => Object.entries(b.by_model).filter(([, v]) => v.total).map(([k, v]) => `${k} ${fmtN(v.total)}`).join(" · ") || "-";
    $("#usageDetail").innerHTML = `<div class="table-wrap"><table class="usage-table">
      <tr><th></th><th>출력</th><th>입력</th><th>캐시 읽기</th><th>합계</th></tr>
      ${row("PC 전체 · 오늘", d.pc.today)}${row("PC 전체 · 7일", d.pc.week)}
      ${row("Pocket-Dev · 오늘", d.pocketdev.today)}${row("Pocket-Dev · 7일", d.pocketdev.week)}</table></div>
      <p class="muted">모델별 (PC 전체 · 7일): ${models(d.pc.week)}</p>
      <p class="muted">PC 전체 = 이 PC에서 쓴 Claude Code 전부 (직접 대화 포함). 7일 = 지금부터 거꾸로 7일.
      구독 한도(%)는 Claude 앱의 사용량 화면에서 확인할 수 있어요.</p>`;
  } catch (e) { $("#usageLine").textContent = "불러오지 못함"; }
}

// ── 실행 화면 ──
function md(text) {
  if (window.marked && window.DOMPurify) return DOMPurify.sanitize(marked.parse(text || ""));
  return `<pre>${esc(text)}</pre>`;
}
const msgs = new Map();
let sink = null;   // 이전 대화를 그릴 때만 임시 상자 (평소엔 #stream)
const out = el => (sink || $("#stream")).append(el);

function addMsg(cls, whoHtml, text, id) {
  const el = document.createElement("div");
  el.className = `msg ${cls}`;
  el.innerHTML = `<div class="who">${whoHtml}</div><div class="tools"></div><div class="body"></div>`;
  el._raw = text || "";
  $(".body", el).innerHTML = md(el._raw);
  out(el);
  if (id) msgs.set(id, el);
  return el;
}

function collapsible(el) {
  if (el._raw.length < 1200 || $(".toggle", el)) return;
  el.classList.add("collapsed");
  const b = document.createElement("button"); b.className = "toggle"; b.textContent = "펼치기";
  b.onclick = () => { el.classList.toggle("collapsed"); b.textContent = el.classList.contains("collapsed") ? "펼치기" : "접기"; };
  el.append(b);
}

const line = (cls, text) => { const d = document.createElement("div"); d.className = cls; d.textContent = text; out(d); };
const fmtTokens = u => u && (u.output_tokens != null) ? ` · 출력 ${u.output_tokens.toLocaleString()} 토큰` : "";

let renderTimer = null;
// mode: live(실시간) · snapshot(처음 받은 최근 기록) · older(위로 스크롤해서 받은 이전 기록 — 화면 상태는 안 바꿈)
function handle(ev, mode = "live") {
  const nearBottom = innerHeight + scrollY > document.body.scrollHeight - 200;
  const older = mode === "older";
  switch (ev.type) {
    case "user": {
      const el = addMsg("user", "<b>⬜ 나</b>", ev.text);
      if (ev.files?.length) el.insertAdjacentHTML("beforeend", `<div class="file-chips">${ev.files.map(n => `<span class="chip">📎 ${esc(n)}</span>`).join("")}</div>`);
      break;
    }
    case "stage": line("stage", ev.label); if (!older) $("#runStage").textContent = ev.label; break;
    case "message_start":
      addMsg(ev.engine + (ev.kind === "spec" ? " spec" : ""), `<b>${ev.icon} ${esc(ev.name)}</b> · ${esc(ev.model)}`, "", ev.msg_id); break;
    case "tool": {
      const el = msgs.get(ev.msg_id); if (!el) break;
      const t = document.createElement("div"); t.textContent = "▸ " + ev.text; $(".tools", el).append(t);
      el._raw = ""; $(".body", el).innerHTML = "";   // 도구 사용 전 중간 멘트는 지움
      break;
    }
    case "delta": {
      const el = msgs.get(ev.msg_id); if (!el) break;
      el._raw += ev.text;
      clearTimeout(renderTimer);
      renderTimer = setTimeout(() => $(".body", el).innerHTML = md(el._raw), 120);
      break;
    }
    case "message_end": {
      const el = msgs.get(ev.msg_id); if (!el) break;
      el._raw = ev.text; $(".body", el).innerHTML = md(ev.text);
      if (ev.error) el.classList.add("failed");
      $(".who", el).insertAdjacentHTML("beforeend", `<span class="muted">${fmtTokens(ev.usage)}</span>`);
      if (!el.classList.contains("spec")) collapsible(el);
      break;
    }
    case "spec": addMsg("spec", "<b>📄 최종 작업 명세서 (TASK_SPEC.md)</b>", ev.text); break;
    case "log": line("log", ev.text); break;
    case "cc": line("cc" + (ev.tool ? " tool" : ""), ev.tool || ev.text); break;
    case "permission_request": {
      const d = document.createElement("div"); d.className = "perm"; d.id = "perm-" + ev.req_id;
      d.innerHTML = `🔐 <b>권한 요청: ${esc(ev.tool)}</b><pre>${esc(JSON.stringify(ev.input, null, 1).slice(0, 1500))}</pre><div class="votes"></div><div class="final muted">심사 중…</div>`;
      out(d); break;
    }
    case "permission_votes": {
      const d = permBox(ev.req_id); if (!d) break;
      $(".votes", d).innerHTML = ev.votes.map(v =>
        `<div>${v.decision === "allow" ? "✅" : v.decision === "deny" ? "⛔" : "🤔"} ${esc(v.reviewer)}: ${esc(v.reason)}</div>`).join("");
      break;
    }
    case "permission_decision": {
      const d = permBox(ev.req_id);
      if (d) $(".final", d).innerHTML = `<span class="${ev.allow ? "ok" : "no"}">${ev.allow ? "허용" : "거부"}</span> — ${esc(ev.by)}`;
      if (!older) pending.delete("perm:" + ev.req_id);
      break;
    }
    case "awaiting_user":
      if (!older) pending.set(ev.key, ev);
      if (ev.kind === "limit") line("log", `⏳ 구독 사용량 한도 도달 (${ev.label}) — ${new Date(ev.retry_at * 1000).toLocaleTimeString()}에 자동 재시도`);
      break;
    case "user_reply": if (!older) pending.delete(ev.key); break;
    case "terminal": {
      const d = document.createElement("div"); d.className = "term";
      d.innerHTML = `<div class="term-cmd"><code>$ ${esc(ev.command)}</code> <span class="${ev.code ? "no" : "ok"}">${ev.code ? "종료 코드 " + ev.code : "완료"}</span></div>
        <pre>${esc(ev.output || "(출력 없음)")}</pre>`;
      out(d); break;
    }
    case "result": {
      const el = addMsg("user", `<b>${esc(ev.title || "✅ 완료 — 커밋 요약")}</b>`, "```\n" + ev.text + "\n```");
      const l = ev.links || {};
      if (l.compare || l.branch) {
        const box = document.createElement("div"); box.className = "links";
        if (l.compare) box.insertAdjacentHTML("beforeend", `<a class="btn-link" href="${esc(l.compare)}" target="_blank" rel="noopener">🔍 GitHub에서 변경 보기 ↗</a>`);
        if (l.branch) box.insertAdjacentHTML("beforeend", `<a class="btn-link" href="${esc(l.branch)}" target="_blank" rel="noopener">🌿 브랜치 ↗</a>`);
        el.append(box);
      }
      break;
    }
    case "error": line("msg error", "❌ " + ev.text); break;
    case "status":
      if (older) break;
      if (currentRun) currentRun.status = ev.status;
      if (["done", "error", "cancelled"].includes(ev.status)) pending.clear();
      if (ev.status === "done") addRollback();
      break;
  }
  if (mode === "live") { renderComposer(); if (nearBottom) scrollTo(0, document.body.scrollHeight); }
}

// 이전 대화를 그리는 중이면 임시 상자에서 먼저 찾는다 (아직 화면에 안 붙어 있음)
const permBox = id => (sink && $("#perm-" + id, sink)) || $("#perm-" + id);

// ── 작업 화면 채팅창 (상황에 따라 버튼·안내가 바뀜) ──
function renderComposer() {
  const box = $("#composer");
  if (!currentRun || $("#tab-run").hidden) { box.hidden = true; return; }
  box.hidden = false;
  const [key, ev] = [...pending.entries()].pop() || [];
  const status = currentRun.status, alive = ["running", "awaiting_user"].includes(status);
  const info = $("#composerInfo"), acts = $("#composerActions"), ta = $("#composerText");
  info.innerHTML = ""; acts.innerHTML = "";
  const btn = (label, onClick, cls = "") => {
    const b = document.createElement("button"); b.textContent = label; b.className = cls;
    b.onclick = async () => { b.disabled = true; try { await onClick(); } catch (e) { alert(e.message); } b.disabled = false; };
    acts.append(b);
  };
  const reply = async action => { await post(`/api/runs/${currentRun.id}/reply`, { key, action, text: ta.value.trim() }); ta.value = ""; };
  if (ev?.kind === "plan") {
    info.innerHTML = "📋 <b>기획안이 나왔어요.</b> 승인하면 구현 → 리뷰 → push 까지 자동으로 진행해요. 궁금한 건 물어보고, 고칠 점은 적어 보내세요.";
    ta.placeholder = "질문하거나 수정 요청 (예: 결제는 빼고, 로그인은 구글로)";
    btn("🚀 승인하고 진행", () => reply("approve"), "primary");
    btn("취소", () => reply("cancel"), "danger");
  } else if (ev?.kind === "limit") {
    info.innerHTML = `⏳ <b>구독 사용량 한도에 걸렸어요.</b> ${new Date(ev.retry_at * 1000).toLocaleTimeString()}에 자동으로 이어서 진행해요.<div class="muted">${esc(ev.text)}</div>`;
    ta.placeholder = "질문하거나 지시를 남길 수 있어요 (재시도는 버튼)";
    btn("🔁 지금 재시도", () => reply("retry"), "primary");
  } else if (ev) {
    info.innerHTML = `🔐 <b>심사자 의견이 갈렸어요:</b> ${esc(ev.tool)} <code>${esc(String(ev.input.command || ev.input.file_path || "").slice(0, 120))}</code>`;
    ta.placeholder = "질문하거나 지시를 남길 수 있어요";
    btn("✅ 허용", () => reply("allow"), "primary");
    btn("⛔ 거부", () => reply("deny"), "danger");
  } else if (alive) {
    ta.placeholder = "진행 중 — 질문엔 바로 답하고, 지시는 다음 단계에 반영해요";
  } else if (status === "done") {
    ta.placeholder = "질문엔 답하고, 추가 작업 요청이면 한 바퀴 더 진행해요";
  } else {
    info.innerHTML = "⏸ <b>멈춘 작업이에요.</b> 멈춘 단계부터 이어서 진행할 수 있어요. 왜 멈췄는지 물어봐도 돼요.";
    ta.placeholder = "왜 멈췄는지 묻거나, 지시를 적어 보내세요";
    btn("▶ 이어서 진행", () => sendMessage(""), "primary");
  }
  // 터미널 모드: 같은 입력칸을 프로젝트 폴더의 PowerShell 명령 입력으로 쓴다
  $("#termToggle").classList.toggle("active", termMode);
  $("#termChips").hidden = !termMode;
  $(".composer-row .attach-btn").hidden = termMode;
  ta.classList.toggle("mono", termMode);
  $("#sendBtn").textContent = termMode ? "실행" : "보내기";
  if (termMode) ta.placeholder = `$ 명령 (${currentRun.project.split(/[\\/]/).pop()} 폴더에서 실행 · 예: git status)`;
}

// ── 터미널 칸 (git·gh 처럼 직접 날려야 하는 명령) ──
let termMode = false;
const TERM_PRESETS = ["git status", "git log --oneline -10", "git pull", "git push", "gh pr list"];

async function runTerminal(command) {
  const ta = $("#composerText");
  ta.value = ""; $("#sendBtn").textContent = "실행 중…";
  try {
    await post(`/api/runs/${currentRun.id}/terminal`, { command });
    scrollTo(0, document.body.scrollHeight);   // 결과는 대화 기록(실시간 이벤트)으로 붙음
  } catch (e) { ta.value = command; throw e; }
  finally { renderComposer(); }
}

// ── 파일 첨부 ──
const chatFiles = [], ideaFiles = [];

async function uploadFiles(files) {
  if (!files.length) return [];
  const fd = new FormData();
  files.forEach(f => fd.append("files", f));
  const res = await fetch("/api/uploads", { method: "POST", body: fd, credentials: "same-origin" });
  if (res.status === 401) { showGate("login"); throw new Error("로그인이 필요해요"); }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || res.statusText);
  return body.files.map(f => f.id);
}

function renderChips(box, files) {
  box.innerHTML = "";
  files.forEach((f, i) => {
    const chip = document.createElement("span"); chip.className = "chip";
    chip.textContent = `📎 ${f.name} (${f.size >= 1e6 ? (f.size / 1e6).toFixed(1) + "MB" : Math.ceil(f.size / 1e3) + "KB"})`;
    const x = document.createElement("button"); x.textContent = "✕"; x.title = "빼기";
    x.onclick = () => { files.splice(i, 1); renderChips(box, files); };
    chip.append(x); box.append(chip);
  });
}

function bindFileInput(input, files, box) {
  input.onchange = () => { files.push(...input.files); input.value = ""; renderChips(box, files); };
}

async function sendMessage(text) {
  const attachments = await uploadFiles(chatFiles);
  const r = await post(`/api/runs/${currentRun.id}/message`, { text, attachments });
  chatFiles.length = 0; renderChips($("#chatFileList"), chatFiles);
  $("#composerText").value = "";
  if (["resumed", "followup"].includes(r.mode)) { currentRun.status = "running"; $("#rollbackBtn")?.remove(); }
  renderComposer();
  return r;
}

function addRollback() {
  if ($("#rollbackBtn")) return;
  const b = document.createElement("button"); b.id = "rollbackBtn"; b.textContent = "⏪ 마지막 커밋 되돌리기 (revert + push)";
  b.onclick = async () => {
    if (!confirm("마지막 커밋을 revert 하고 push 할까요?")) return;
    try { const r = await post("/api/projects/rollback", { project: $("#project").value }); alert("완료\n" + r.output); }
    catch (e) { alert(e.message); }
  };
  $("#stream").append(b);
}

// ── 진행 중인 작업의 모델 변경 (다음 AI 호출부터 · 원하면 지금 단계부터 다시) ──
async function openModels() {
  const box = $("#modelsPanel");
  if (!box.hidden) { box.hidden = true; return; }
  const cur = await api(`/api/runs/${currentRun.id}/models`);
  box.innerHTML = "<h2>⚙️ 이 작업의 모델 <small>저장하면 다음 AI 호출부터 적용돼요</small></h2>";
  const row = (label, value) => {
    const r = document.createElement("div"); r.className = "member-row model-pick";
    const l = document.createElement("span"); l.className = "role"; l.textContent = label;
    r.append(l); modelPicker(r, value, () => {}); box.append(r);
  };
  cur.participants.forEach(p => row("🗣️ " + (p.name || p.model) + (p.enabled === false ? " (빠짐)" : ""), p));
  row("📝 명세서 작성자", cur.synthesizer);
  cur.reviewers.forEach((r, i) => row(`🔍 리뷰어 ${i + 1}`, r));
  cur.permission_reviewers.forEach((r, i) => row(`🔐 권한 심사 ${i + 1}`, r));
  const er = document.createElement("div"); er.className = "member-row model-pick";
  er.innerHTML = '<span class="role">🤖 구현 (Claude Code)</span>';
  const ex = document.createElement("select"); ex.className = "exec-model";
  models("claude").forEach(m => ex.add(new Option(m.label, m.id)));
  if (![...ex.options].some(o => o.value === cur.executor_model)) ex.add(new Option(cur.executor_model, cur.executor_model));
  ex.value = cur.executor_model; er.append(ex); box.append(er);
  const bl = document.createElement("label"); bl.className = "row";
  bl.innerHTML = '<input type="checkbox"> 🌐 Chrome 사용 <span class="muted">(웹 콘솔 작업 · 다음 구현 실행부터 · 클릭마다 권한 심사)</span>';
  const browserOn = $("input", bl); browserOn.checked = !!cur.browser; box.append(bl);
  let now = null;
  if (cur.status === "running") {
    const lab = document.createElement("label"); lab.className = "row";
    lab.innerHTML = '<input type="checkbox"> 지금 진행 중인 단계도 새 모델로 다시 시작 <span class="muted">(하던 발언·구현은 처음부터 다시 해요)</span>';
    box.append(lab); now = $("input", lab);
  }
  const acts = document.createElement("div"); acts.className = "actions";
  const ok = document.createElement("button"); ok.className = "primary"; ok.textContent = "저장";
  ok.onclick = async () => {
    ok.disabled = true;
    try {
      const r = await post(`/api/runs/${currentRun.id}/models`, {
        participants: cur.participants, synthesizer: cur.synthesizer, reviewers: cur.reviewers,
        permission_reviewers: cur.permission_reviewers, executor_model: ex.value, browser: browserOn.checked,
        apply_now: !!now?.checked });
      box.hidden = true;
      if (!r.changed.length) alert("바뀐 모델이 없어요");
    } catch (e) { alert(e.message); }
    ok.disabled = false;
  };
  const close = document.createElement("button"); close.textContent = "닫기"; close.onclick = () => box.hidden = true;
  acts.append(ok, close); box.append(acts);
  box.hidden = false;
}

// ── 이전 대화: 처음엔 최근 기록만 받고, 위로 스크롤하면 그 앞을 이어 붙인다 ──
let firstIndex = 0, loadingOlder = false, streamGen = 0;

function renderOlderBar() {
  const bar = $("#olderBar"); if (!bar) return;
  bar.hidden = !firstIndex;
  bar.textContent = loadingOlder ? "이전 대화 불러오는 중…" : "▲ 이전 대화 더 보기";
}

async function loadOlder() {
  if (loadingOlder || !firstIndex || !currentRun || $("#tab-run").hidden) return;
  const gen = streamGen;
  loadingOlder = true; renderOlderBar();
  try {
    const d = await api(`/api/runs/${currentRun.id}/older?before=${firstIndex}`);
    if (gen !== streamGen) return;   // 그 사이 다른 작업을 열었거나 다시 연결됨
    const box = document.createElement("div");
    sink = box;
    try { d.events.forEach(e => handle(e, "older")); } finally { sink = null; }
    const bar = $("#olderBar"), anchor = bar.nextElementSibling, top = anchor?.getBoundingClientRect().top;
    bar.after(...box.childNodes);
    if (anchor) scrollBy(0, anchor.getBoundingClientRect().top - top);   // 보던 위치 그대로
    firstIndex = d.first;
  } catch (e) { console.warn("이전 대화를 불러오지 못했어요", e); }
  finally { loadingOlder = false; renderOlderBar(); }
  if (gen === streamGen) fillScreen();
}

// 화면이 아직 안 차서 스크롤할 수 없으면 더 불러온다
const fillScreen = () => { if (firstIndex && (scrollY < 400 || document.body.scrollHeight <= innerHeight + 400)) loadOlder(); };
addEventListener("scroll", () => { if (scrollY < 400) loadOlder(); }, { passive: true });

function openRun(run) {
  currentRun = run; pending.clear(); msgs.clear(); $("#modelsPanel").hidden = true;
  $("#stream").innerHTML = ""; $("#runTitle").textContent = run.idea; $("#runStage").textContent = "";
  firstIndex = 0; streamGen++;
  show("run");
  if (es) es.close();
  es = new EventSource(`/api/runs/${run.id}/events`);
  es.onmessage = m => {
    const ev = JSON.parse(m.data);
    if (ev.type === "snapshot") {
      streamGen++; firstIndex = ev.first || 0;
      $("#stream").innerHTML = '<button id="olderBar" class="older-bar" hidden></button>'; msgs.clear();
      $("#olderBar").onclick = loadOlder;
      currentRun = ev.run;
      ev.events.forEach(e => handle(e, "snapshot"));
      pending.clear();
      (ev.pending || []).forEach(p => pending.set(p.key, p));   // 앞부분이 안 불러와져도 대기 중인 요청은 서버 기준으로
      if (ev.stage_label) $("#runStage").textContent = ev.stage_label;
      if (ev.run.status === "done") addRollback();
      renderOlderBar(); renderComposer(); scrollTo(0, document.body.scrollHeight);
      fillScreen();
    } else handle(ev);
  };
}

// ── 탭 ──
function show(tab) {
  ["new", "runs", "run"].forEach(t => $("#tab-" + t).hidden = t !== tab);
  document.querySelectorAll(".top nav button").forEach(b => b.classList.toggle("active", b.dataset.tab === tab));
  if (tab !== "run") { $("#composer").hidden = true; if (es) { es.close(); es = null; } }
  if (tab === "runs") loadRuns();
}

async function loadRuns() {
  const runs = await api("/api/runs");
  const box = $("#runList"); box.innerHTML = runs.length ? "" : '<p class="muted">아직 실행 기록이 없어요.</p>';
  runs.forEach(r => {
    const row = document.createElement("div"); row.className = "run-row";
    const b = document.createElement("button"); b.className = "run-item";
    b.innerHTML = `<span class="status ${r.status}">${r.status}</span><b>${esc(r.idea.slice(0, 80))}</b>
      <div class="muted">${new Date(r.created * 1000).toLocaleString()} · ${esc(r.project.split(/[\\/]/).pop())} · ${esc(r.stage)}</div>`;
    b.onclick = () => openRun(r);
    const del = document.createElement("button"); del.className = "run-del"; del.textContent = "🗑";
    del.title = r.alive ? "진행 중인 작업은 먼저 중단해야 삭제할 수 있어요" : "기록 삭제";
    del.disabled = r.alive;
    del.onclick = async () => {
      if (!confirm(`이 기록을 삭제할까요?\n\n${r.idea.slice(0, 80)}\n\n대화 기록과 첨부 파일이 지워지고 되돌릴 수 없어요. (프로젝트 코드·커밋은 그대로)`)) return;
      del.disabled = true;
      try { await api(`/api/runs/${r.id}`, { method: "DELETE" }); row.remove(); if (!box.children.length) loadRuns(); }
      catch (e) { alert(e.message); del.disabled = false; }
    };
    row.append(b, del); box.append(row);
  });
}

// ── 서버 재시작 (Pocket-Dev 자기 코드 수정 반영) ──
async function restartServer() {
  if (!confirm("서버를 다시 시작할까요?\n새 코드를 먼저 검사하고, 오류가 있으면 재시작하지 않아요.")) return;
  const btn = $("#restartBtn"); btn.disabled = true;
  try {
    let r;
    try { r = await post("/api/server/restart", {}); }
    catch (e) {
      if (e.status !== 409) throw e;
      const list = (e.body.alive || []).map(t => "· " + t).join("\n");
      if (!confirm(`${e.message}\n${list}\n\n중단하고 재시작할까요? (다시 켜진 뒤 '이어서 진행'으로 재개할 수 있어요)`)) return;
      r = await post("/api/server/restart", { force: true });
    }
    if (es) { es.close(); es = null; }
    document.body.insertAdjacentHTML("beforeend", '<div class="restarting">🔄 서버를 다시 시작하는 중…</div>');
    for (let i = 0; i < 60; i++) {   // 새 서버가 뜰 때까지 (최대 약 90초)
      await new Promise(ok => setTimeout(ok, 1500));
      const s = await fetch("/api/session", { credentials: "same-origin" }).then(x => x.json()).catch(() => null);
      if (s && s.boot !== SESSION.boot) return location.reload();
    }
    $(".restarting").textContent = "⚠️ 서버가 다시 뜨지 않았어요. 집 PC의 Pocket-Dev 창을 확인해 주세요.";
  } catch (e) { alert(e.message); }
  finally { btn.disabled = false; }
}

// ── 로그인 / 잠김 화면 ──
function showGate(kind) {
  if (es) { es.close(); es = null; }
  $("#app").hidden = true;
  $("#tab-login").hidden = kind !== "login";
  $("#tab-locked").hidden = kind !== "locked";
  if (kind === "login") setTimeout(() => $("#loginId").focus(), 50);
}

$("#loginForm").onsubmit = async e => {
  e.preventDefault();
  const err = $("#loginError"); err.hidden = true;
  $("#loginBtn").disabled = true;
  try {
    await api("/api/login", { method: "POST", body: JSON.stringify({ login_id: $("#loginId").value.trim(), password: $("#loginPw").value, token: $("#loginToken").value.trim() }) });
    $("#loginPw").value = ""; $("#loginToken").value = "";
    await boot();
  } catch (ex) {
    if (ex.status === 401) {
      const b = ex.body || {};
      err.textContent = b.locked ? `${b.detail} — 잠겼어요 (사설망 접속이라 계속 시도할 수 있어요)` : `${b.detail} (${b.remaining}번 더 틀리면 잠겨요)`;
      err.hidden = false;
    } else if (ex.status !== 423) { err.textContent = ex.message; err.hidden = false; }
  }
  $("#loginBtn").disabled = false;
};

function renderLockBanner() {
  const box = $("#lockBanner");
  if (!SESSION.locked) { box.hidden = true; return; }
  const when = t => new Date(t * 1000).toLocaleString();
  const log = (SESSION.log || []).map(l => `<div class="muted">${when(l.at)} · ${esc(l.client)} · ${l.via === "public" ? "공개 주소" : "사설망"}</div>`).join("");
  box.innerHTML = `🔒 <b>로그인 실패 ${SESSION.failures}회로 공개 주소가 잠겨 있어요.</b>${log}`;
  if (SESSION.via === "private") {
    const b = document.createElement("button"); b.textContent = "잠금 해제"; b.className = "primary";
    b.onclick = async () => { await post("/api/unlock", {}); SESSION.locked = false; renderLockBanner(); };
    box.append(b);
  } else box.insertAdjacentHTML("beforeend", `<div class="muted">해제는 Tailscale(100.x) 접속이나 집 PC의 unlock.bat 으로만 가능해요.</div>`);
  box.hidden = false;
}

async function boot() {
  SESSION = await api("/api/session");
  if (SESSION.locked && SESSION.via === "public") return showGate("locked");
  if (!SESSION.logged_in) return showGate("login");
  $("#tab-login").hidden = $("#tab-locked").hidden = true;
  $("#app").hidden = false;
  renderLockBanner();
  if (!started) { started = true; await startApp(); }
}

// ── 시작 ──
async function startApp() {
  CFG = await api("/api/config");
  const defaults = {
    participants: structuredClone(CFG.team.participants), synthesizer: { ...CFG.team.synthesizer },
    reviewers: structuredClone(CFG.team.reviewers), permission_reviewers: structuredClone(CFG.team.permission_reviewers),
    executor_model: CFG.executor_model, max_rounds: CFG.debate.max_rounds, branch: CFG.branch, plan_gate: true,
  };
  try { state = { ...defaults, ...JSON.parse(localStorage.getItem(LS_KEY) || "{}") }; } catch { state = defaults; }
  renderTeam();
  renderEngineStatus();
  $("#stitchRow").hidden = !CFG.stitch;
  $("#stitchHint").hidden = !!CFG.stitch;
  $("#stitchOn").checked = !!state.stitch;
  $("#stitchOn").onchange = e => { state.stitch = e.target.checked; save(); };
  $("#browserOn").checked = !!state.browser;
  $("#browserOn").onchange = e => { state.browser = e.target.checked; save(); };
  loadUsage();
  setInterval(() => { if (!$("#tab-new").hidden && !document.hidden) loadUsage(); }, 60000);

  const { projects } = await api("/api/projects");
  const sel = $("#project");
  const NEW = "__new", GH = "gh:";
  let remote = [], privateNames = new Set();   // GitHub 에만 있는 저장소 · 비공개 저장소 이름
  const isPrivate = p => p.github && privateNames.has(p.github.split("/").pop().toLowerCase());
  const fillProjects = () => {
    const keep = sel.value;
    sel.innerHTML = "";
    const mine = document.createElement("optgroup"); mine.label = "내 PC";
    projects.forEach(p => mine.append(new Option((isPrivate(p) ? "🔒 " : "") + p.name, p.path)));
    sel.append(mine);
    if (remote.length) {
      const gh = document.createElement("optgroup"); gh.label = "GitHub · 아직 PC에 없음 (고르면 받아옴)";
      remote.forEach(r => gh.append(new Option(`${r.private ? "🔒 " : ""}${r.name}${r.archived ? " (보관됨)" : ""}`, GH + r.name)));
      sel.append(gh);
    }
    sel.add(new Option("＋ 새 프로젝트 만들기…", NEW));
    if (keep) sel.value = keep;
  };
  const badge = () => {
    const p = projects.find(p => p.path === sel.value);
    $("#branchBadge").textContent = "🌿 " + (p?.branch || "-");
    $("#ghLink").hidden = !p?.github;
    if (p?.github) $("#ghLink").href = p.github;
  };
  fillProjects();
  sel.value = localStorage.getItem("pocketdev.project") || "";
  if (!sel.value || sel.value === NEW || sel.value.startsWith(GH)) sel.value = projects[0]?.path || NEW;
  let last = sel.value;
  sel.onchange = async () => {
    if (sel.value === NEW) {
      const name = prompt("새 프로젝트 이름 (영문, 예: my-app)\n첫 push 때 GitHub에 비공개 저장소가 자동으로 만들어져요.");
      if (!name) { sel.value = last; return; }
      try {
        const p = await post("/api/projects/new", { name: name.trim() });
        projects.push(p); fillProjects(); sel.value = p.path;
      } catch (e) { alert(e.message); sel.value = last; return; }
    } else if (sel.value.startsWith(GH)) {
      const name = sel.value.slice(GH.length);
      if (!confirm(`GitHub의 ${name} 저장소를 집 PC로 받아올까요?`)) { sel.value = last; return; }
      sel.disabled = true; $("#branchBadge").textContent = "⬇️ 받는 중…";
      try {
        const p = await post("/api/github/clone", { name });
        projects.push(p); remote = remote.filter(r => r.name !== name); fillProjects(); sel.value = p.path;
      } catch (e) { alert(e.message); sel.value = last; }
      sel.disabled = false;
    }
    last = sel.value;
    localStorage.setItem("pocketdev.project", sel.value); badge();
  };
  badge();
  // GitHub 저장소 목록(비공개 포함)은 조금 걸려서 화면이 뜬 뒤에 채움
  api("/api/github/repos").then(d => {
    privateNames = new Set(d.repos.filter(r => r.private).map(r => r.name.toLowerCase()));
    remote = d.repos.filter(r => !r.local_path);
    fillProjects(); badge();
  }).catch(() => {});
  $("#restartBtn").onclick = restartServer;
  $("#logoutBtn").onclick = async () => { await post("/api/logout", {}).catch(() => {}); showGate("login"); };

  document.querySelectorAll(".top nav button").forEach(b => b.onclick = () => show(b.dataset.tab));
  document.querySelectorAll("[data-preset]").forEach(b => b.onclick = () => { state.participants = PRESETS[b.dataset.preset](); save(); renderTeam(); });
  $("#addParticipant").onclick = () => { state.participants.push({ ...member("p" + Date.now(), "참가자", "claude-sonnet-5-5"), persona: "" }); save(); renderTeam(); };
  $("#addReviewer").onclick = () => { state.reviewers.push({ engine: "claude", model: "claude-sonnet-5-5", effort: "medium" }); save(); renderTeam(); };
  $("#addPermReviewer").onclick = () => { state.permission_reviewers.push({ engine: "claude", model: "claude-haiku-5-5", effort: "" }); save(); renderTeam(); };
  $("#backBtn").onclick = () => show("runs");
  $("#modelsBtn").onclick = () => openModels().catch(e => alert(e.message));
  bindFileInput($("#chatFiles"), chatFiles, $("#chatFileList"));
  bindFileInput($("#ideaFiles"), ideaFiles, $("#ideaFileList"));
  const send = async () => {
    const text = $("#composerText").value.trim();
    if (termMode ? !text : !text && !chatFiles.length) return;
    $("#sendBtn").disabled = true;
    try { await (termMode ? runTerminal(text) : sendMessage(text)); } catch (e) { alert(e.message); }
    $("#sendBtn").disabled = false;
  };
  $("#sendBtn").onclick = send;
  // 채팅은 Ctrl+Enter, 터미널은 Enter 로 실행 (Shift+Enter 는 줄바꿈)
  $("#composerText").onkeydown = e => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey || (termMode && !e.shiftKey && !e.isComposing))) { e.preventDefault(); send(); }
  };
  $("#termToggle").onclick = () => { termMode = !termMode; renderComposer(); $("#composerText").focus(); };
  TERM_PRESETS.forEach(cmd => {
    const c = document.createElement("button"); c.textContent = cmd;
    c.onclick = () => { $("#composerText").value = cmd; $("#composerText").focus(); };   // 바로 실행하지 않고 채워만 줌
    $("#termChips").append(c);
  });
  $("#cancelBtn").onclick = async () => { if (currentRun && confirm("이 작업을 중단할까요?")) await post(`/api/runs/${currentRun.id}/cancel`, {}); };
  $("#startBtn").onclick = async () => {
    let idea = $("#idea").value.trim();
    if (!idea && ideaFiles.length) idea = "첨부한 문서를 검토해서 기획하고 개발해줘";
    if (!idea) return alert("아이디어를 입력하거나 문서를 첨부하세요");
    if (!sel.value || sel.value === NEW) return alert("프로젝트를 선택하세요");
    $("#startBtn").disabled = true;
    try {
      const attachments = await uploadFiles(ideaFiles);
      const run = await post("/api/runs", { project: sel.value, idea, attachments, ...state });
      $("#idea").value = ""; ideaFiles.length = 0; renderChips($("#ideaFileList"), ideaFiles);
      openRun(run);
    } catch (e) { alert(e.message); }
    $("#startBtn").disabled = false;
  };
}
boot().catch(e => { if (!/로그인|잠겨/.test(e.message)) document.body.insertAdjacentHTML("afterbegin", `<p class="error">초기화 실패: ${esc(e.message)}</p>`); });

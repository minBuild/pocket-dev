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

function addMsg(cls, whoHtml, text, id) {
  const el = document.createElement("div");
  el.className = `msg ${cls}`;
  el.innerHTML = `<div class="who">${whoHtml}</div><div class="tools"></div><div class="body"></div>`;
  el._raw = text || "";
  $(".body", el).innerHTML = md(el._raw);
  $("#stream").append(el);
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

const line = (cls, text) => { const d = document.createElement("div"); d.className = cls; d.textContent = text; $("#stream").append(d); };
const fmtTokens = u => u && (u.output_tokens != null) ? ` · 출력 ${u.output_tokens.toLocaleString()} 토큰` : "";

let renderTimer = null;
function handle(ev, live = true) {
  const nearBottom = innerHeight + scrollY > document.body.scrollHeight - 200;
  switch (ev.type) {
    case "user": {
      const el = addMsg("user", "<b>⬜ 나</b>", ev.text);
      if (ev.files?.length) el.insertAdjacentHTML("beforeend", `<div class="file-chips">${ev.files.map(n => `<span class="chip">📎 ${esc(n)}</span>`).join("")}</div>`);
      break;
    }
    case "stage": line("stage", ev.label); $("#runStage").textContent = ev.label; break;
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
      $("#stream").append(d); break;
    }
    case "permission_votes": {
      const d = $("#perm-" + ev.req_id); if (!d) break;
      $(".votes", d).innerHTML = ev.votes.map(v =>
        `<div>${v.decision === "allow" ? "✅" : v.decision === "deny" ? "⛔" : "🤔"} ${esc(v.reviewer)}: ${esc(v.reason)}</div>`).join("");
      break;
    }
    case "permission_decision": {
      const d = $("#perm-" + ev.req_id);
      if (d) $(".final", d).innerHTML = `<span class="${ev.allow ? "ok" : "no"}">${ev.allow ? "허용" : "거부"}</span> — ${esc(ev.by)}`;
      pending.delete("perm:" + ev.req_id); break;
    }
    case "awaiting_user":
      pending.set(ev.key, ev);
      if (ev.kind === "limit") line("log", `⏳ 구독 사용량 한도 도달 (${ev.label}) — ${new Date(ev.retry_at * 1000).toLocaleTimeString()}에 자동 재시도`);
      break;
    case "user_reply": pending.delete(ev.key); break;
    case "result": {
      const el = addMsg("user", "<b>✅ 완료 — 커밋 요약</b>", "```\n" + ev.text + "\n```");
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
      if (currentRun) currentRun.status = ev.status;
      if (["done", "error", "cancelled"].includes(ev.status)) pending.clear();
      if (ev.status === "done") addRollback();
      break;
  }
  if (live) { renderComposer(); if (nearBottom) scrollTo(0, document.body.scrollHeight); }
}

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

function openRun(run) {
  currentRun = run; pending.clear(); msgs.clear();
  $("#stream").innerHTML = ""; $("#runTitle").textContent = run.idea; $("#runStage").textContent = "";
  show("run");
  if (es) es.close();
  es = new EventSource(`/api/runs/${run.id}/events`);
  es.onmessage = m => {
    const ev = JSON.parse(m.data);
    if (ev.type === "snapshot") {
      $("#stream").innerHTML = ""; msgs.clear(); pending.clear();
      currentRun = ev.run;
      ev.events.forEach(e => handle(e, false));
      for (const k of [...pending.keys()]) if (!ev.run.pending.includes(k)) pending.delete(k);  // 이미 처리된 요청 제거
      renderComposer(); scrollTo(0, document.body.scrollHeight);
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
    const b = document.createElement("button"); b.className = "run-item";
    b.innerHTML = `<span class="status ${r.status}">${r.status}</span><b>${esc(r.idea.slice(0, 80))}</b>
      <div class="muted">${new Date(r.created * 1000).toLocaleString()} · ${esc(r.project.split(/[\\/]/).pop())} · ${esc(r.stage)}</div>`;
    b.onclick = () => openRun(r);
    box.append(b);
  });
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
  $("#logoutBtn").onclick = async () => { await post("/api/logout", {}).catch(() => {}); showGate("login"); };

  document.querySelectorAll(".top nav button").forEach(b => b.onclick = () => show(b.dataset.tab));
  document.querySelectorAll("[data-preset]").forEach(b => b.onclick = () => { state.participants = PRESETS[b.dataset.preset](); save(); renderTeam(); });
  $("#addParticipant").onclick = () => { state.participants.push({ ...member("p" + Date.now(), "참가자", "claude-sonnet-5-5"), persona: "" }); save(); renderTeam(); };
  $("#addReviewer").onclick = () => { state.reviewers.push({ engine: "claude", model: "claude-sonnet-5-5", effort: "medium" }); save(); renderTeam(); };
  $("#addPermReviewer").onclick = () => { state.permission_reviewers.push({ engine: "claude", model: "claude-haiku-5-5", effort: "" }); save(); renderTeam(); };
  $("#backBtn").onclick = () => show("runs");
  bindFileInput($("#chatFiles"), chatFiles, $("#chatFileList"));
  bindFileInput($("#ideaFiles"), ideaFiles, $("#ideaFileList"));
  const send = async () => {
    const text = $("#composerText").value.trim();
    if (!text && !chatFiles.length) return;
    $("#sendBtn").disabled = true;
    try { await sendMessage(text); } catch (e) { alert(e.message); }
    $("#sendBtn").disabled = false;
  };
  $("#sendBtn").onclick = send;
  $("#composerText").onkeydown = e => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); send(); } };
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

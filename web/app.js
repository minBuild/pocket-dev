// Pocket-Dev 웹 UI — 구독 CLI 엔진
const $ = (s, el = document) => el.querySelector(s);
const LS_KEY = "pocketdev.team.v3";  // v3: effort 기본값 medium
const EFFORTS = ["", "low", "medium", "high", "xhigh", "max"];
const FINAL = ["done", "error", "cancelled"];   // 이 외(working·waiting)는 진행 중
// 서버 runs.display_status 와 같은 규칙 — 사람 결정(기획안 승인·권한 결정, Chrome 클릭 포함)을 기다리면 waiting
const dispStatus = s => FINAL.includes(s) ? s
  : ([...pending.values()].some(e => e.kind === "plan" || e.kind === "permission") ? "waiting" : "working");
const paintStatus = s => { const el = $("#runStatus"); if (!el) return; el.className = "status " + s; el.textContent = s; el.hidden = !s; };

let CFG = null, SESSION = null;
let state = null;   // { participants, synthesizer, reviewers, permission_reviewers, executor_model, max_rounds, branch, plan_gate }
let currentRun = null, es = null, pending = new Map(), started = false;

// ── API (로그인 세션 쿠키로 인증) ──
async function api(path, opts = {}) {
  const res = await fetch(path, { ...opts, credentials: "same-origin", headers: { "Content-Type": "application/json", ...(opts.headers || {}) } });
  if (res.status === 401 && path !== "/api/login") { showGate("login"); throw new Error("로그인이 필요해요"); }
  if (res.status === 423) { showGate("locked"); throw new Error("잠겨 있어요"); }
  const body = await res.json().catch(() => ({}));
  if (res.status === 405) throw Object.assign(new Error("서버가 아직 이전 코드예요 — 상단 🔄 로 재시작한 뒤 다시 해 주세요"), { status: 405 });
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
  if (html) $("#engineStatus").append(fixButton());
}

// ── Claude Code 문제 해결 (다시 확인 · 설치 · 로그인) — 상단 안내와 작업 화면 오류 줄에서 ──
const ENGINE_ERROR = /claude CLI 를 찾을 수 없|claude auth login|Please run \/login|Not logged in|Invalid API key|OAuth token/i;

function fixButton() {
  const box = document.createElement("div"); box.className = "engine-fix";
  const b = document.createElement("button"); b.textContent = "🛠 해결하기";
  b.onclick = () => openEngineFix(box);
  box.append(b);
  return box;
}

async function openEngineFix(box) {
  box.innerHTML = '<span class="muted">Claude Code 상태 확인 중…</span>';
  let s;
  try { s = await post("/api/engines/claude/recheck", {}); } catch (e) { box.textContent = "❌ " + e.message; return; }
  CFG.engines.claude.status = s;
  const ok = s.installed && s.logged_in && s.method === "claude.ai";
  box.innerHTML = "";
  const btn = (label, onClick, cls = "") => {
    const b = document.createElement("button"); b.textContent = label; b.className = cls;
    b.onclick = async () => { b.disabled = true; try { await onClick(); } catch (e) { alert(e.message); } b.disabled = false; };
    box.append(b); return b;
  };
  const say = html => box.insertAdjacentHTML("beforeend", `<div class="fix-msg">${html}</div>`);
  if (ok) {
    say(`✅ Claude Code 정상 (${esc(s.plan || "구독")} · claude.ai 로그인). 멈춘 작업은 <b>▶ 이어서 진행</b>으로 재개하세요.`);
    renderEngineStatus(); return;
  }
  if (!s.installed) {
    say("❌ 집 PC에서 Claude Code 를 찾지 못했어요." + (s.detail ? ` <span class="muted">${esc(s.detail)}</span>` : ""));
    btn("⬇️ Claude Code 설치", async () => {
      if (!confirm("집 PC에 Claude Code 를 설치할까요?\n(Anthropic 공식 설치 스크립트 · 1~2분)")) return;
      say("설치 중… 끝날 때까지 기다려 주세요.");
      const r = await post("/api/engines/claude/install", {});
      say(`<pre>${esc(r.output || "(출력 없음)")}</pre>`);
      if (r.status.installed) openEngineFix(box); else say("❌ 설치 뒤에도 찾지 못했어요. 위 출력을 확인해 주세요.");
    }, "primary");
  } else {
    say(s.logged_in ? `⚠️ 구독이 아닌 <b>${esc(s.method)}</b> 방식으로 로그인돼 있어요. 구독 계정으로 다시 로그인하세요.`
                    : "❌ Claude Code 로그인이 필요해요.");
    btn("🔑 구독 계정으로 로그인", () => loginFlow(box), "primary");
  }
  btn("🔍 다시 확인", () => openEngineFix(box));
}

function againButton(box) {
  const b = document.createElement("button"); b.textContent = "🛠 다시 시도";
  b.onclick = () => openEngineFix(box);
  return b;
}

async function loginFlow(box) {
  box.innerHTML = '<span class="muted">로그인 주소 받는 중…</span>';
  let url;
  try { ({ url } = await post("/api/engines/claude/login/start", {})); }
  catch (e) { box.innerHTML = `<div class="fix-msg">❌ ${esc(e.message)}</div>`; box.append(againButton(box)); return; }
  box.innerHTML = `<div class="fix-msg">① 아래 링크를 열어 Claude 구독 계정으로 로그인하세요.<br>
      <a class="btn-link" href="${esc(url)}" target="_blank" rel="noopener">🔑 로그인 페이지 열기 ↗</a></div>
    <div class="fix-msg">② 로그인 후 나오는 코드를 복사해서 붙여 넣으세요.</div>
    <div class="composer-row"><input class="login-code" placeholder="코드 붙여 넣기" autocomplete="off" autocapitalize="none" spellcheck="false">
      <button class="primary">확인</button><button>취소</button></div>`;
  const [okBtn, cancelBtn] = box.querySelectorAll(".composer-row button");
  const input = $(".login-code", box);
  okBtn.onclick = async () => {
    okBtn.disabled = true;
    try {
      const r = await post("/api/engines/claude/login/code", { code: input.value });
      if (r.ok && r.status.logged_in) { openEngineFix(box); return; }
      box.insertAdjacentHTML("beforeend", `<div class="fix-msg">❌ 로그인하지 못했어요.<pre>${esc(r.output || "")}</pre></div>`);
      box.append(againButton(box));
    } catch (e) { alert(e.message); }
    okBtn.disabled = false;
  };
  cancelBtn.onclick = async () => { await post("/api/engines/claude/login/cancel", {}).catch(() => {}); openEngineFix(box); };
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
const threads = new Map();   // chat_id → .thread (내 채팅 말풍선 + 그에 대한 답·처리 결과 묶음)
let sink = null;   // 이전 대화를 그릴 때만 임시 상자 (평소엔 #stream)
let into = null;   // 지금 이벤트가 붙을 채팅 묶음 (reply_to)
const out = el => (into || sink || $("#stream")).append(el);

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

const line = (cls, text) => { const d = document.createElement("div"); d.className = cls; d.textContent = text; out(d); return d; };
const fmtTokens = u => u && (u.output_tokens != null) ? ` · 출력 ${u.output_tokens.toLocaleString()} 토큰` : "";

let renderTimer = null;
// mode: live(실시간) · snapshot(처음 받은 최근 기록) · older(위로 스크롤해서 받은 이전 기록 — 화면 상태는 안 바꿈)
function handle(ev, mode = "live") {
  const nearBottom = innerHeight + scrollY > document.body.scrollHeight - 200;
  const older = mode === "older";
  into = (ev.reply_to && threads.get(ev.reply_to)) || null;   // 묶음이 화면에 없으면 최상위에 그림
  try {
  switch (ev.type) {
    case "user": {
      if (ev.chat_id) {
        const th = document.createElement("div"); th.className = "thread";
        out(th); threads.set(ev.chat_id, th); into = th;
      }
      const el = addMsg("user", "<b>⬜ 나</b>", ev.text);
      if (ev.files?.length) el.insertAdjacentHTML("beforeend", `<div class="file-chips">${ev.files.map(n => `<span class="chip">📎 ${esc(n)}</span>`).join("")}</div>`);
      break;
    }
    case "chat_action": {
      const cls = ev.action === "done" ? " done" : "";
      line("log chat-action" + cls, (!into && ev.quote ? `「${ev.quote}」 → ` : "") + ev.text);   // 묶음이 화면 밖이면 인용으로
      break;
    }
    case "browser_account": {   // 구현 AI 의 로그인 계정 보고 (email 이 빈 건 실행 시작 리셋)
      if (ev.email) {
        const host = (ev.scope || "").split("#")[0];
        const state = !ev.verified ? "⚠️ 지금 페이지에서 읽은 내용에 없는 이메일 — 다시 확인 필요"
          : ev.gate === false ? "✅ (허용 목록 검사 꺼짐)"
          : ev.allowed ? "✅ 허용 계정" : "⛔ 허용 목록에 없음 — 쓰기 작업 안 함";
        line("cc tool bstep account", `👤 로그인 계정: ${ev.email}${host ? " · " + host : ""} ${state}`);
      }
      if (!older) { chrome.account = ev.email ? ev : null; chrome.gate = ev.gate !== false; renderChip(); }
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
      const th = el.closest(".thread");   // 답이 끝나면 묶음을 맨 아래로 (그 사이 쌓인 Chrome 줄보다 아래에 보이게)
      if (th) th.parentNode?.append(th);
      break;
    }
    case "spec": addMsg("spec", "<b>📄 최종 작업 명세서 (TASK_SPEC.md)</b>", ev.text); break;
    case "log": line("log", ev.text); break;
    case "cc": {
      if (ev.step) {   // Chrome 단계 시작 — 끝 이벤트가 먼저 그려졌으면(이전 대화 경계) 건너뜀
        if (!steps.has(ev.step)) {
          const el = line("cc tool bstep running", "⏳ " + ev.tool);
          if (ev.note) { const s = document.createElement("span"); s.className = "muted"; s.textContent = " · 💬 " + ev.note; el.append(s); }
          steps.set(ev.step, el);
        }
        if (!older) { chrome.current = { step: ev.step, text: ev.tool, ts: ev.ts, note: ev.note || "" }; renderChip(); }
      } else line("cc" + (ev.tool ? " tool" : ""), ev.tool || ev.text);
      const i = (ev.text || "").indexOf(BLOCK_MARK);
      if (i >= 0 && !older) {
        chrome.blocked = ev.text.slice(i + BLOCK_MARK.length).split("\n")[0].trim().slice(0, 200) || "이유 없음";
        renderChip();
      }
      break;
    }
    case "todo": {   // 구현 AI 의 할 일 목록 (TodoWrite) — 이 이벤트만으로 줄을 그릴 수 있음
      line("cc tool todo", todoText(ev));
      if (!older) { chrome.todo = ev; renderChip(); if (!$("#browserOverlay").hidden) renderOverlayTodo(); }
      break;
    }
    case "browser": {   // Chrome 단계 끝 — 이 이벤트만으로 줄을 그릴 수 있음
      let el = steps.get(ev.step);
      if (!el) { el = line("cc tool bstep", ""); if (ev.step) steps.set(ev.step, el); }
      fillStep(el, ev);
      if (ev.shot) { shots.push({ url: ev.shot, ts: ev.ts, text: ev.text }); shots.sort((a, b) => a.ts - b.ts); }
      if (!older) {
        if (chrome.current?.step === ev.step) chrome.current = null;
        chrome.count++; chrome.last = ev; chrome.lastTs = ev.ts;
        chrome.noImage ||= !!ev.no_image;
        chrome.recent = [...chrome.recent, ev.status].slice(-8);
        if (ev.status === "ok" && ev.write !== false) { chrome.blocked = null; chrome.suspect = false; }   // 스크린샷·읽기는 막힘을 풀지 않음
        const last3 = chrome.recent.slice(-3);
        if ((last3.length === 3 && last3.every(s => s === "denied" || s === "error"))
            || (ev.tool === "tabs_context_mcp" && ev.status !== "ok")) chrome.suspect = true;
        renderChip();
        if (!$("#browserOverlay").hidden) refreshOverlay(ev);
      }
      break;
    }
    case "permission_request": {
      const d = document.createElement("div"); d.className = "perm"; d.id = "perm-" + ev.req_id;
      d.innerHTML = `🔐 <b>권한 요청: ${esc(ev.label || ev.tool)}</b><details><summary>입력 보기</summary>` +
        `<pre>${esc(JSON.stringify(ev.input, null, 1).slice(0, 1500))}</pre></details><div class="votes"></div><div class="final muted">심사 중…</div>`;
      if (ev.browser) d.dataset.browser = "1";
      const s = shots[shots.length - 1];
      if (!older && s) {
        const box = shotBox(s.url);
        box.insertAdjacentHTML("beforeend", `<span class="shot-ago">📷 ${esc(ago(s.ts))}</span>`);
        $(".votes", d).before(box);
      }
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
      if (d && d.dataset.browser && ev.allow) d.classList.add("compact");   // 허용된 Chrome 카드는 한 줄로
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
      const el = addMsg("result", `<b>${esc(ev.title || "✅ 완료 — 커밋 요약")}</b>`, "```\n" + ev.text + "\n```");
      const l = ev.links || {};
      if (l.compare || l.branch) {
        const box = document.createElement("div"); box.className = "links";
        if (l.compare) box.insertAdjacentHTML("beforeend", `<a class="btn-link" href="${esc(l.compare)}" target="_blank" rel="noopener">🔍 GitHub에서 변경 보기 ↗</a>`);
        if (l.branch) box.insertAdjacentHTML("beforeend", `<a class="btn-link" href="${esc(l.branch)}" target="_blank" rel="noopener">🌿 브랜치 ↗</a>`);
        el.append(box);
      }
      break;
    }
    case "error":
      line("msg error", "❌ " + ev.text);
      if (ENGINE_ERROR.test(ev.text || "")) out(fixButton());   // 화면에서 바로 설치·로그인
      break;
    case "status":
      if (older) break;
      if (currentRun) currentRun.status = ev.status;
      if (FINAL.includes(ev.status)) { pending.clear(); chrome.current = null; }
      if (ev.status === "done") addRollback();
      break;
  }
  } finally { if (into) into.parentNode?.append(into); into = null; }   // 새 요소가 붙은 묶음은 맨 아래로
  if (mode === "live") { renderComposer(); if (nearBottom) scrollTo(0, document.body.scrollHeight); }
}

// 이전 대화를 그리는 중이면 임시 상자에서 먼저 찾는다 (아직 화면에 안 붙어 있음)
const permBox = id => (sink && $("#perm-" + id, sink)) || $("#perm-" + id);

// 권한 결정 자동 거부 시각
const expiry = ev => ev.expires_at ? ` · ${esc(new Date(ev.expires_at * 1000).toLocaleTimeString())}까지 (지나면 자동 거부)` : "";

// ── 작업 화면 채팅창 (상황에 따라 버튼·안내가 바뀜) ──
function renderComposer() {
  const box = $("#composer");
  if (!currentRun || $("#tab-run").hidden) { box.hidden = true; return; }
  paintStatus(dispStatus(currentRun.status));
  box.hidden = false;
  const [key, ev] = [...pending.entries()].pop() || [];
  const status = currentRun.status, alive = !FINAL.includes(status);
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
  } else if (ev?.label) {   // Chrome 동작
    const s = shots[shots.length - 1];
    info.innerHTML = `🔐 <b>심사자 의견이 갈렸어요:</b> ${esc(ev.label)}` +
      (s ? ` <a href="${esc(s.url)}" target=_blank rel=noopener>📷 최근 화면 (${esc(ago(s.ts))})</a>` : "") + expiry(ev);
    ta.placeholder = "질문하거나 지시를 남길 수 있어요";
    btn("✅ 허용", () => reply("allow"), "primary");
    btn("⛔ 거부", () => reply("deny"), "danger");
  } else if (ev) {
    info.innerHTML = `🔐 <b>심사자 의견이 갈렸어요:</b> ${esc(ev.tool)} <code>${esc(String(ev.input?.command || ev.input?.file_path || "").slice(0, 120))}</code>` + expiry(ev);
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
  renderChip();   // 결정 대기·한도 대기 배지
}

// ── 🌐 Chrome 작업 진행 (단계 줄 · 상단 칩 · 스크린샷 오버레이) ──
const steps = new Map();      // step(tool_use_id) → 줄 요소
let shots = [];               // {url, ts, text}
let chrome = null;
const BSTAT = { ok: "✅", denied: "⛔", error: "⚠️", stopped: "⏹" };
const BLOCK_MARK = "[CHROME-BLOCKED]";
const TODO_ICON = { completed: "✅", in_progress: "▶", pending: "○" };
const todoText = t => `📋 할 일 ${t.done}/${t.total}` + (t.current ? ` · ▶ ${t.current}` : t.done === t.total ? " · 모두 완료" : "");

function resetChrome() {
  steps.clear(); shots = [];
  chrome = { count: 0, last: null, lastTs: 0, blocked: null, suspect: false, noImage: false, recent: [],
             account: null, gate: true, current: null, todo: null };
  $(".bc-thumb", $("#browserChip"))?.remove();
  const todoBox = $("#browserOverlay .bo-todo");
  if (todoBox) { todoBox.textContent = ""; todoBox.hidden = true; }
  $("#browserChip").hidden = true; $("#browserOverlay").hidden = true;
}
resetChrome();

function ago(ts) {
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
  return s < 60 ? `${s}초 전` : s < 3600 ? `${Math.floor(s / 60)}분 전` : `${Math.floor(s / 3600)}시간 전`;
}

function shotBox(url) {
  const box = document.createElement("div"); box.className = "shot";
  const a = document.createElement("a"); a.href = url; a.target = "_blank"; a.rel = "noopener";
  const img = document.createElement("img"); img.loading = "lazy"; img.alt = ""; img.src = url;
  img.onerror = () => { box.textContent = "🗑 정리된 화면"; box.classList.add("gone"); };
  a.append(img); box.append(a);
  return box;
}

function fillStep(el, ev) {
  el.className = `cc tool bstep ${ev.status || ""}`;
  el.textContent = `${BSTAT[ev.status] || ""} ${ev.text || ""}`;
  const muted = text => { const s = document.createElement("span"); s.className = "muted"; s.textContent = text; el.append(s); };
  if (ev.note) muted(" · 💬 " + ev.note);
  if (ev.result) muted(" — " + ev.result);
  if (ev.no_image) muted(" (화면 이미지 없음 — 텍스트로만)");
  if (ev.shot) el.append(shotBox(ev.shot));
}

function renderChip() {
  const chip = $("#browserChip");
  if (!chrome || $("#tab-run").hidden) return;
  if (chrome.count === 0 && !shots.length && !chrome.account && !chrome.current && !chrome.todo) { chip.hidden = true; return; }
  const last = chrome.last, shot = shots[shots.length - 1], a = chrome.account, cur = chrome.current, t = chrome.todo;
  let text = `🌐 Chrome`;
  if (t) text += ` · 📋 ${t.done}/${t.total}`;
  text += ` · ${chrome.count}${firstIndex > 0 ? "+" : ""}단계`;
  if (a) text += ` · 👤 ${a.email}` + (chrome.gate === false ? " · 검사 꺼짐" : "");
  else if (chrome.gate === false) text += " · 👤 계정 검사 꺼짐";
  if (cur) text += ` · ⏳ ${cur.text}${cur.note ? " · 💬 " + cur.note : ""} (${ago(cur.ts)} 시작)`;   // 진행 중 동작이 먼저
  else {
    if (t?.current) text += ` · ▶ ${t.current}`;
    if (last) text += ` · ${BSTAT[last.status] || ""} ${last.text} · ${ago(chrome.lastTs)}`;
  }
  if (shot) text += ` · 📷 ${ago(shot.ts)}`;
  const waits = [...pending.values()];
  const badge = a && a.verified && a.gate !== false && !a.allowed ? ["blocked", `🔒 계정 불일치: ${a.email}`]
    : chrome.blocked ? ["blocked", `🔒 집 PC Chrome 에서 처리 필요: ${chrome.blocked}`]
    : waits.some(p => p.kind === "permission" && p.label) ? ["decide", "✋ 내 결정 대기"]
    : waits.some(p => p.kind === "limit") ? ["limit", "⏳ 한도 대기"]
    : chrome.suspect ? ["suspect", "⚠️ 막힘 의심"]
    : chrome.noImage ? ["textonly", "텍스트로만"] : null;
  chip.className = "browser-chip" + (badge ? " " + badge[0] : "");
  // 썸네일 <img> 는 재사용하고 URL 이 바뀔 때만 src 를 바꿈 (10초마다 다시 그려도 no-store 이미지를 다시 받지 않게)
  let thumb = $(".bc-thumb", chip);
  if (shot && !chrome.noImage) {
    if (!thumb) { thumb = document.createElement("img"); thumb.className = "bc-thumb"; thumb.alt = ""; chip.prepend(thumb); }
    if (thumb.dataset.src !== shot.url) { thumb.dataset.src = shot.url; thumb.onerror = () => thumb.remove(); thumb.src = shot.url; }
  } else thumb?.remove();
  chip.querySelectorAll(".bc-badge, .bc-text").forEach(e => e.remove());
  const span = (cls, t) => { const s = document.createElement("span"); s.className = cls; s.textContent = t; chip.append(s); };
  if (badge) span("bc-badge", badge[1]);
  span("bc-text", text);
  chip.title = text;
  chip.hidden = false;
}
setInterval(renderChip, 10000);   // "N초 전" 갱신

function shotFigure(s) {
  const fig = document.createElement("figure"); fig.className = "bo-shot";
  fig.dataset.ts = s.ts; fig.dataset.text = s.text || "";
  const a = document.createElement("a"); a.href = s.url; a.target = "_blank"; a.rel = "noopener";
  const img = document.createElement("img"); img.loading = "lazy"; img.alt = ""; img.src = s.url;
  img.onerror = () => { a.textContent = "🗑 정리된 화면"; a.classList.add("gone"); };
  a.append(img);
  fig.append(a, document.createElement("figcaption"));
  return fig;
}

// 캡션 번호만 다시 씀 (img 는 건드리지 않음)
function captionAll(strip) {
  const figs = strip.querySelectorAll("figure.bo-shot");
  figs.forEach((f, i) => {
    $("figcaption", f).textContent = `${i + 1}/${figs.length} · ${new Date(f.dataset.ts * 1000).toLocaleTimeString()} · ${f.dataset.text}`;
  });
}

function renderOverlaySteps(list) {
  const rows = [...$("#stream").querySelectorAll(".bstep")].slice(-8);   // 화면 순서(시간순) 기준 마지막 8개
  list.innerHTML = `<div class="bo-title">최근 단계 (${rows.length})</div>`;
  rows.forEach(r => { const d = document.createElement("div"); d.className = r.className; d.textContent = r.textContent; list.append(d); });
}

// 구현 AI 의 할 일 목록 (마지막 todo 기준) — 글자는 전부 textContent, 클래스는 고정값만
function renderOverlayTodo() {
  const box = $("#browserOverlay .bo-todo"), t = chrome.todo;
  box.textContent = "";
  if (!t) { box.hidden = true; return; }
  const div = (cls, text) => { const d = document.createElement("div"); d.className = cls; d.textContent = text; box.append(d); };
  const items = Array.isArray(t.items) ? t.items : [];
  div("bo-title", `할 일 (${t.done}/${t.total})`);
  items.forEach(it => {
    const st = Object.hasOwn(TODO_ICON, it?.status) ? it.status : "pending";
    div(`todo-item ${st}`, `${TODO_ICON[st]} ${it.text}`);
  });
  if (items.length < t.total) div("todo-item muted", `… 외 ${t.total - items.length}개`);
  box.hidden = false;
}

function openOverlay() {
  const ov = $("#browserOverlay"), strip = $(".bo-strip", ov), list = $(".bo-steps", ov);
  strip.innerHTML = "";
  if (!shots.length) strip.innerHTML = '<p class="muted">아직 찍은 화면이 없어요 — 텍스트로만 보여요</p>';
  shots.forEach(s => strip.append(shotFigure(s)));
  captionAll(strip);
  renderOverlayTodo();
  renderOverlaySteps(list);
  ov.hidden = false;
  strip.scrollLeft = strip.scrollWidth;   // 마지막 장부터
}

// 오버레이를 연 채로 새 단계가 오면 새 화면 카드만 덧붙임 (이미 있는 img 는 다시 받지 않음)
function refreshOverlay(ev) {
  const ov = $("#browserOverlay"), strip = $(".bo-strip", ov);
  if (ev.shot && !strip.querySelector(`a[href="${CSS.escape(ev.shot)}"]`)) {
    const atEnd = strip.scrollLeft + strip.clientWidth >= strip.scrollWidth - 8;
    strip.querySelector("p.muted")?.remove();                 // "아직 찍은 화면이 없어요" 안내
    strip.append(shotFigure(shots.find(s => s.url === ev.shot) || { url: ev.shot, ts: ev.ts, text: ev.text }));
    captionAll(strip);
    if (atEnd) strip.scrollLeft = strip.scrollWidth;          // 마지막 장을 보던 중일 때만 따라감
  }
  renderOverlaySteps($(".bo-steps", ov));
}
const closeOverlay = () => { $("#browserOverlay").hidden = true; };

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
  if (["resumed", "followup"].includes(r.mode)) { currentRun.status = "working"; $("#rollbackBtn")?.remove(); }
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
  if (cur.status === "working") {
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
  currentRun = run; pending.clear(); msgs.clear(); threads.clear(); resetChrome(); $("#modelsPanel").hidden = true;
  $("#stream").innerHTML = ""; $("#runTitle").textContent = run.title || run.idea; $("#runStage").textContent = "";
  paintStatus(FINAL.includes(run.status) || run.status === "waiting" ? run.status : "working");
  firstIndex = 0; streamGen++;
  show("run");
  if (es) es.close();
  es = new EventSource(`/api/runs/${run.id}/events`);
  es.onmessage = m => {
    const ev = JSON.parse(m.data);
    if (ev.type === "snapshot") {
      streamGen++; firstIndex = ev.first || 0;
      $("#stream").innerHTML = '<button id="olderBar" class="older-bar" hidden></button>'; msgs.clear(); threads.clear(); resetChrome();
      $("#olderBar").onclick = loadOlder;
      currentRun = ev.run;
      $("#runTitle").textContent = ev.run.title || ev.run.idea;
      ev.events.forEach(e => handle(e, "snapshot"));
      if (ev.browser) {   // 계정 보고·막힘은 이벤트 창 밖에 있을 수 있어 서버 집계로 덮어씀
        const acc = ev.browser.account;
        chrome.account = acc?.email ? acc : null;
        chrome.gate = (acc || ev.browser.report || {}).gate !== false;
        chrome.blocked = ev.browser.blocked || null;
        renderChip();
      }
      if ("todo" in ev) { chrome.todo = ev.todo || null; renderChip(); }   // 마지막 할 일 목록도 서버 집계로 (D6)
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
  if (!["new", "runs", "run"].includes(tab)) return;
  ["new", "runs", "run"].forEach(t => $("#tab-" + t).hidden = t !== tab);
  document.querySelectorAll(".top nav button").forEach(b => b.classList.toggle("active", b.dataset.tab === tab));
  if (tab !== "run") { $("#composer").hidden = true; if (es) { es.close(); es = null; } }
  if (tab === "runs") loadRuns();
}

// ── 기록 목록 (폴링 없이: 기록 버튼·탭 복귀 때 1회) ──
let runsGen = 0;
const WAIT_FMT = w => {
  const t = w.until ? new Date(w.until * 1000).toLocaleTimeString() : "";
  if (w.kind === "perm") return `🔐 권한 결정 필요 · ${w.label}${t ? ` · ${t}까지 (지나면 자동 거부)` : ""}`;
  if (w.kind === "plan") return "📋 기획안 승인 대기";
  return `⏳ 한도 대기${w.label ? " · " + w.label : ""}${t ? ` · ${t} 자동 재시도` : ""}`;
};
const CHROME_FMT = c => {
  let s = `🌐 Chrome ${c.steps}단계`;
  if (c.todo) s += ` · 📋 ${c.todo.done}/${c.todo.total}` + (c.todo.current ? ` ▶ ${c.todo.current}` : "");
  if (c.blocked) s += ` · 🔒 막힘: ${c.blocked}`;
  if (c.current) s += ` · ⏳ ${c.current.text}` + (c.current.note ? ` · 💬 ${c.current.note}` : "");
  else if (c.last) s += ` · ${BSTAT[c.last.status] || ""} ${c.last.text} · ${ago(c.last.ts)}`;
  if (c.shot_ts) s += ` · 📷 ${ago(c.shot_ts)}`;
  if (c.account) s += ` · 👤 ${c.account.email}${c.account.ok ? "" : " ⛔"}`;
  return s;
};

async function loadRuns() {
  const gen = ++runsGen, nav = $('.top nav button[data-tab="runs"]'), meta = $("#runsMeta");
  nav.textContent = "기록 ⟳";
  let runs;
  try { runs = await api("/api/runs", { cache: "no-store" }); }
  catch (e) { if (gen === runsGen) meta.textContent = `⚠️ 목록을 불러오지 못했어요: ${e.message}`; return; }   // 이전 목록은 그대로
  finally { if (gen === runsGen) nav.textContent = "기록"; }
  if (gen !== runsGen) return;   // 늦게 온 응답
  meta.textContent = `마지막 갱신 ${new Date().toLocaleTimeString()}`;
  const box = $("#runList"); box.innerHTML = runs.length ? "" : '<p class="muted">아직 실행 기록이 없어요.</p>';
  runs.forEach(r => {
    const name = r.title || r.idea;
    const row = document.createElement("div"); row.className = "run-row";
    const b = document.createElement("button"); b.className = "run-item";
    b.innerHTML = `<span class="status ${esc(r.status)}">${esc(r.status)}</span><b class="run-title">${esc(name)}</b>
      <div class="muted">${new Date(r.created * 1000).toLocaleString()} · ${esc(r.project.split(/[\\/]/).pop())} · ${esc(r.wait ? WAIT_FMT(r.wait) : r.stage)}</div>` +
      (r.chrome ? `<div class="muted run-chrome${r.chrome.blocked ? " blocked" : ""}">${esc(CHROME_FMT(r.chrome))}</div>` : "");
    b.onclick = () => openRun(r);
    const edit = document.createElement("button"); edit.className = "run-edit"; edit.textContent = "✏️"; edit.title = "대화명 바꾸기";
    edit.onclick = () => renameRun(r);
    const del = document.createElement("button"); del.className = "run-del"; del.textContent = "🗑";
    del.title = r.alive ? "진행 중인 작업은 먼저 중단해야 삭제할 수 있어요" : "기록 삭제";
    del.disabled = r.alive;
    del.onclick = async () => {
      if (!confirm(`이 기록을 삭제할까요?\n\n${name.slice(0, 80)}\n\n대화 기록과 첨부 파일이 지워지고 되돌릴 수 없어요. (프로젝트 코드·커밋은 그대로)`)) return;
      del.disabled = true;
      try { await api(`/api/runs/${r.id}`, { method: "DELETE" }); row.remove(); if (!box.children.length) loadRuns(); }
      catch (e) { alert(e.message); del.disabled = false; }
    };
    row.append(b, edit, del); box.append(row);
  });
}

// 대화명 바꾸기 — 서버에 저장 (빈 값이면 처음 아이디어로 표시)
function renameRun(r) {
  const dlg = $("#renameDialog"), input = $("#renameInput"), err = $("#renameError"), form = $("form", dlg);
  if (typeof dlg.showModal !== "function") {   // <dialog> 가 없는 옛 WebView
    const v = prompt("대화명 (비우면 처음 아이디어로 표시)", r.title || "");
    if (v !== null) api(`/api/runs/${r.id}`, { method: "PATCH", body: JSON.stringify({ title: v }) }).then(loadRuns).catch(e => alert(e.message));
    return;
  }
  input.value = r.title || ""; input.placeholder = (r.idea || "").slice(0, 60); err.hidden = true;
  $("#renameCancel", dlg).onclick = () => dlg.close();      // Esc 는 dialog 기본 동작
  form.onsubmit = async e => {
    e.preventDefault();                                     // Enter·키보드 완료 키·✓ 모두 저장
    const btn = $("button[type=submit]", form); btn.disabled = true;
    try {
      await api(`/api/runs/${r.id}`, { method: "PATCH", body: JSON.stringify({ title: input.value }) });
      dlg.close(); loadRuns();
    } catch (ex) {
      if (ex.status === 404) { dlg.close(); alert("이미 삭제된 기록이에요"); loadRuns(); return; }
      err.textContent = `⚠️ ${ex.message}`; err.hidden = false;
    } finally { btn.disabled = false; }
  };
  dlg.showModal(); input.select();
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
    const oldBoot = r.boot || SESSION.boot;   // 재시작을 받은 서버 (화면을 연 뒤 서버가 바뀌었을 수도 있어서 응답 기준)
    for (let i = 0; i < 60; i++) {   // 새 서버가 뜰 때까지 (최대 약 90초)
      await new Promise(ok => setTimeout(ok, 1500));
      const s = await fetch("/api/session", { credentials: "same-origin" }).then(x => x.json()).catch(() => null);
      if (s && s.boot !== oldBoot) return location.reload();
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

function renderStaleBanner() {
  const box = $("#staleBanner");
  if (SESSION.stale === false) { box.hidden = true; return; }   // 옛 서버는 stale 필드가 없어 → 배너가 뜸 (의도)
  box.textContent = "⚠️ 서버 코드가 바뀌었어요 — 진행 중인 작업이 끝난 뒤 🔄 재시작해야 반영돼요 (Chrome 화면 보기·상태 표시 포함).";
  const b = document.createElement("button"); b.className = "primary"; b.textContent = "🔄 지금 재시작";
  b.onclick = restartServer; box.append(b); box.hidden = false;
}

async function boot() {
  SESSION = await api("/api/session");
  if (SESSION.locked && SESSION.via === "public") return showGate("locked");
  if (!SESSION.logged_in) return showGate("login");
  $("#tab-login").hidden = $("#tab-locked").hidden = true;
  $("#app").hidden = false;
  renderLockBanner();
  renderStaleBanner();
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
  document.querySelectorAll(".top nav button[data-tab]").forEach(b => b.onclick = () => show(b.dataset.tab));
  $("#restartBtn").onclick = restartServer;
  $("#logoutBtn").onclick = async () => { await post("/api/logout", {}).catch(() => {}); showGate("login"); };
  document.addEventListener("visibilitychange", () => {   // 기록 탭을 보던 중 앱으로 돌아오면 1회 새로고침
    if (document.visibilityState === "visible" && !$("#app").hidden && !$("#tab-runs").hidden) loadRuns();
  });
  document.querySelectorAll("[data-preset]").forEach(b => b.onclick = () => { state.participants = PRESETS[b.dataset.preset](); save(); renderTeam(); });
  $("#addParticipant").onclick = () => { state.participants.push({ ...member("p" + Date.now(), "참가자", "claude-sonnet-5-5"), persona: "" }); save(); renderTeam(); };
  $("#addReviewer").onclick = () => { state.reviewers.push({ engine: "claude", model: "claude-sonnet-5-5", effort: "medium" }); save(); renderTeam(); };
  $("#addPermReviewer").onclick = () => { state.permission_reviewers.push({ engine: "claude", model: "claude-haiku-5-5", effort: "" }); save(); renderTeam(); };
  $("#backBtn").onclick = () => show("runs");
  $("#modelsBtn").onclick = () => openModels().catch(e => alert(e.message));
  $("#browserChip").onclick = () => $("#browserOverlay").hidden ? openOverlay() : closeOverlay();
  $("#browserOverlay .bo-close").onclick = closeOverlay;
  document.addEventListener("click", e => {   // 오버레이 바깥을 누르면 닫힘
    const ov = $("#browserOverlay");
    if (!ov.hidden && !ov.contains(e.target) && !$("#browserChip").contains(e.target)) closeOverlay();
  });
  document.addEventListener("keydown", e => { if (e.key === "Escape") closeOverlay(); });
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

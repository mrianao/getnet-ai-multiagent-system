/* Assistente Getnet — vanilla JS client for the authenticated FastAPI backend.
 * - Token lives only in sessionStorage (per tab); never hardcoded.
 * - All server text is HTML-escaped before minimal formatting (bold, bullets, [n] citations).
 * - Technical details render ONLY fields the backend actually returned.
 */
"use strict";

const $ = (s) => document.querySelector(s);
const state = { token: null, me: null, convId: null, convStatus: null, busy: false, poll: null, filter: "open" };
const TOKEN_KEY = "getnet_ai_token";

// ---------------------------------------------------------------- API
async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  const res = await fetch(path, { ...opts, headers });
  let body = null;
  try { body = await res.json(); } catch (_) { /* non-JSON */ }
  if (res.status === 401) { logout("Sessão expirada ou token inválido. Entre novamente."); throw new ApiError(401, "unauthorized"); }
  if (!res.ok) throw new ApiError(res.status, detail(body) || res.statusText);
  return body;
}
class ApiError extends Error { constructor(status, msg) { super(msg); this.status = status; } }
function detail(b) {
  if (!b) return null;
  if (typeof b.detail === "string") return b.detail;
  if (Array.isArray(b.detail)) return b.detail.map((d) => d.msg).join("; ");
  return null;
}

// ---------------------------------------------------------------- formatting (escape first!)
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function fmt(text) {
  const lines = esc(text).split("\n");
  let html = "", list = false;
  for (let raw of lines) {
    let l = raw.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/\[(\d{1,2})\]/g, '<span class="cite">[$1]</span>');
    const m = l.match(/^\s*(?:[-*•]|\d+[.)])\s+(.*)$/);
    if (m) { if (!list) { html += "<ul>"; list = true; } html += `<li>${m[1]}</li>`; continue; }
    if (list) { html += "</ul>"; list = false; }
    if (l.trim()) html += `<p>${l}</p>`;
  }
  return html + (list ? "</ul>" : "");
}
const safeUrl = (u) => /^https?:\/\//i.test(u || "") ? u : null;
const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };
const STATUS_PT = { success: "sucesso", partial: "parcial", escalated: "escalado", blocked: "bloqueado", error: "erro",
  awaiting_human: "aguardando especialista", active: "ativa", open: "aberto", claimed: "em atendimento",
  resolved: "resolvido", rejected: "rejeitado" };
const pill = (cls, text) => `<span class="pill ${esc(cls)}">${esc(text)}</span>`;

// ---------------------------------------------------------------- auth
async function login(token) {
  state.token = token;
  try {
    state.me = await api("/me");
  } catch (e) {
    state.token = null;
    $("#login-error").textContent = e.status === 401 ? "Token inválido, expirado ou revogado." : `Erro: ${e.message}`;
    return;
  }
  try { sessionStorage.setItem(TOKEN_KEY, token); } catch (_) { /* storage blocked: session-only in memory */ }
  $("#login").hidden = true; $("#app").hidden = false;
  const op = state.me.role === "operator";
  document.querySelectorAll(".merchant-only").forEach((n) => (n.hidden = op));
  document.querySelectorAll(".operator-only").forEach((n) => (n.hidden = !op));
  $("#who").textContent = op ? `Operador · ${state.me.subject}` : `Lojista · ${state.me.merchant_id}`;
  if (op) { $("#conv-title").textContent = "Fila de atendimento humano"; $("#conv-sub").textContent = "Tickets escalados pelos agentes"; loadTickets(); }
  else { newConversation(); loadConversations(); }
}
function logout(msg) {
  try { sessionStorage.removeItem(TOKEN_KEY); } catch (_) {}
  stopPoll();
  Object.assign(state, { token: null, me: null, convId: null });
  $("#app").hidden = true; $("#login").hidden = false;
  $("#login-error").textContent = msg || "";
  $("#token-input").value = "";
}

// ---------------------------------------------------------------- conversations
async function loadConversations() {
  const { conversations } = await api("/conversations");
  const list = $("#conv-list"); list.innerHTML = "";
  if (!conversations.length) list.append(el("p", "muted small", "Nenhuma conversa ainda."));
  for (const c of conversations) {
    const b = el("button", "conv-item" + (c.id === state.convId ? " active" : ""),
      `<span class="t">${esc(c.title || "Conversa")}</span><span class="m">${esc(new Date(c.updated_at).toLocaleString("pt-BR"))} · ${c.message_count} msgs${c.status === "awaiting_human" ? " · aguardando especialista" : ""}</span>`);
    b.onclick = () => openConversation(c.id);
    list.append(b);
  }
}
function newConversation() {
  stopPoll();
  state.convId = null; state.convStatus = null;
  $("#conv-title").textContent = "Nova conversa"; $("#conv-status").hidden = true;
  const box = $("#messages"); box.innerHTML = "";
  const empty = $("#tpl-empty").content.cloneNode(true);
  empty.querySelectorAll(".chip").forEach((c) => (c.onclick = () => send(c.textContent)));
  box.append(empty);
  document.querySelectorAll(".conv-item").forEach((n) => n.classList.remove("active"));
  $("#input").focus();
}
async function openConversation(id) {
  stopPoll();
  const data = await api(`/conversations/${encodeURIComponent(id)}`);
  state.convId = id;
  $("#messages").innerHTML = "";
  for (const m of data.messages) renderStored(m);
  setConvStatus(data.conversation.status, data.conversation.title);
  closeSidebar();
  loadConversations();
}
function setConvStatus(status, title) {
  state.convStatus = status;
  if (title) $("#conv-title").textContent = title;
  const p = $("#conv-status");
  p.hidden = false; p.className = `pill ${status}`; p.textContent = STATUS_PT[status] || status;
  if (status === "awaiting_human") startPoll(); else stopPoll();
}
function startPoll() {
  if (state.poll) return;
  state.poll = setInterval(async () => {
    if (!state.convId) return stopPoll();
    try {
      const data = await api(`/conversations/${state.convId}`);
      const shown = $("#messages").querySelectorAll(".msg[data-id]").length;
      if (data.messages.length > shown) {
        $("#messages").innerHTML = "";
        data.messages.forEach(renderStored);
        scrollDown();
      }
      setConvStatus(data.conversation.status);
    } catch (_) { /* transient */ }
  }, 5000);
}
function stopPoll() { if (state.poll) clearInterval(state.poll); state.poll = null; }

// ---------------------------------------------------------------- rendering
function scrollDown() { const b = $("#messages"); b.scrollTop = b.scrollHeight; }
function renderStored(m) {
  if (m.role === "user") return addUser(m.content, m.id);
  const meta = m.metadata || {};
  const node = addBubble(m.role === "operator" ? "operator" : "assistant", m.content, m.id,
    m.role === "operator" ? "Especialista Getnet" : "Assistente");
  const row = el("div", "meta-row");
  if (meta.status) row.innerHTML += pill(meta.status, STATUS_PT[meta.status] || meta.status);
  (meta.agents_invoked || []).forEach((a) => (row.innerHTML += pill("agent", a)));
  if (meta.handoff_ticket) row.innerHTML += pill("escalated", `ticket ${meta.handoff_ticket}`);
  if (m.intent) row.innerHTML += pill("", `intenção: ${m.intent}`);
  if (row.innerHTML) node.append(row);
  if (meta.sources && meta.sources.length) node.append(renderSources(meta.sources));
  if (m.trace_id) node.append(el("div", "muted small", `trace <code>${esc(m.trace_id)}</code> · detalhes completos disponíveis na resposta original`));
}
function addUser(text, id) {
  $("#messages").querySelector(".empty")?.remove();
  const n = el("div", "msg user"); if (id) n.dataset.id = id;
  n.append(el("div", "bubble", fmt(text)));
  $("#messages").append(n); scrollDown(); return n;
}
function addBubble(kind, text, id, who) {
  const n = el("div", `msg ${kind}`); if (id) n.dataset.id = id;
  if (who) n.append(el("div", "who", esc(who)));
  n.append(el("div", "bubble", fmt(text)));
  $("#messages").append(n); scrollDown(); return n;
}
function renderSources(sources) {
  const box = el("div", "sources");
  for (const s of sources) {
    const u = safeUrl(s.url);
    const label = `<span class="src-type">${esc(s.type)}</span>`;
    const score = s.score != null ? ` <span class="muted">(${Number(s.score).toFixed(2)})</span>` : "";
    box.append(el("div", "", u ? `${label}<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(s.title)}</a>${score}`
                                 : `${label}${esc(s.title)} <span class="muted">${esc(s.url)}</span>`));
  }
  return box;
}
function renderAnswer(r) {
  const node = addBubble("assistant", r.answer, null, "Assistente");
  const m = r.metadata || {};
  const row = el("div", "meta-row");
  row.innerHTML = pill(r.status, STATUS_PT[r.status] || r.status);
  if (r.agents_invoked.length) r.agents_invoked.forEach((a) => (row.innerHTML += pill("agent", a)));
  else row.innerHTML += pill("agent", "router");
  if (m.route) row.innerHTML += pill("", `intenção: ${m.route.intent} (${m.route.method})`);
  if (m.conversation && m.conversation.followup) row.innerHTML += pill("info", "follow-up resolvido");
  if (m.handoff_ticket) row.innerHTML += pill("escalated", `ticket ${m.handoff_ticket}`);
  node.append(row);
  if (r.sources && r.sources.length) node.append(renderSources(r.sources));
  node.append(renderTech(r));
}
function renderTech(r) {
  const m = r.metadata || {};
  const d = el("details", "tech");
  d.append(el("summary", "", "Detalhes técnicos"));
  let h = '<dl class="kv">';
  if (m.trace_id) h += `<dt>trace_id</dt><dd><code>${esc(m.trace_id)}</code></dd>`;
  if (r.conversation_id) h += `<dt>conversation_id</dt><dd><code>${esc(r.conversation_id)}</code></dd>`;
  if (m.route) h += `<dt>rota</dt><dd>${esc(m.route.intent)} → [${esc((m.route.agents || []).join(", ") || "router")}] via <code>${esc(m.route.method)}</code> — ${esc(m.route.reason || "")}</dd>`;
  if (m.conversation) {
    h += `<dt>memória</dt><dd>${m.conversation.history_messages} mensagens de contexto; follow-up: ${m.conversation.followup ? "sim" : "não"}`;
    if (m.conversation.standalone_message) h += ` · reescrita (${esc(m.conversation.rewrite_method)}): “${esc(m.conversation.standalone_message)}”`;
    h += "</dd>";
  }
  if (m.latency_ms != null) h += `<dt>latência total</dt><dd>${(m.latency_ms / 1000).toFixed(1)} s</dd>`;
  if (m.usage) h += `<dt>tokens</dt><dd>${esc(m.usage.llm_calls)} chamadas LLM · ${esc(m.usage.tokens_in ?? "n/d")} in / ${esc(m.usage.tokens_out ?? "n/d")} out</dd>`;
  if (m.llm_model) h += `<dt>modelo</dt><dd>${esc(m.llm_provider)} / ${esc(m.llm_model)}</dd>`;
  if (m.guardrails) h += `<dt>guardrails</dt><dd>PII mascarada: ${m.guardrails.pii_masked ? "sim" : "não"}${m.guardrails.flags?.length ? " · " + esc(m.guardrails.flags.join(", ")) : ""}</dd>`;
  h += "</dl>";
  if (m.steps && m.steps.length) {
    h += "<table><tr><th>etapa</th><th>agente</th><th>status</th><th>latência</th><th>ferramentas</th><th>tokens</th></tr>";
    m.steps.forEach((s, i) => (h += `<tr><td>${i + 1}</td><td>${esc(s.agent)}</td><td>${esc(s.status)}</td><td>${(s.latency_ms / 1000).toFixed(1)} s</td><td>${esc((s.tools || []).join(", "))}</td><td>${s.tokens_in != null ? esc(s.tokens_in) + "/" + esc(s.tokens_out) : "—"}</td></tr>`));
    h += "</table>";
  }
  if (m.replans && m.replans.length) {
    h += "<p><strong>Replanejamento</strong></p><ul>" + m.replans.map((x) => `<li>após <code>${esc(x.after)}</code>: ${esc(x.action)} — ${esc(x.reason)}</li>`).join("") + "</ul>";
  }
  for (const [agent, g] of Object.entries(m.grounding || {})) {
    h += `<p><strong>Grounding · ${esc(agent)}</strong>: ${esc(g.verdict)}${g.ratio != null ? ` (${Math.round(g.ratio * 100)}% das afirmações sustentadas)` : ""} · método: <code>${esc(g.method)}</code></p>`;
    if (g.removed_claims?.length) h += "<ul>" + g.removed_claims.map((c) => `<li class="removed">${esc(c)}</li>`).join("") + "</ul>";
    if (g.flagged_claims?.length) h += '<ul>' + g.flagged_claims.map((c) => `<li class="flagged">sinalizada: ${esc(c.text)}${c.best_similarity != null ? ` (sim ${Number(c.best_similarity).toFixed(2)})` : ""}</li>`).join("") + "</ul>";
  }
  const rag = (m.evidence || []).filter((e) => e.type === "rag");
  if (rag.length) {
    h += "<p><strong>Evidências usadas</strong></p><table><tr><th>#</th><th>fonte</th><th>cos</th><th>BM25</th><th>RRF</th></tr>";
    rag.forEach((e) => (h += `<tr><td>${e.rank}</td><td>${esc(e.url.replace(/^https?:\/\//, ""))}<br><span class="muted small">${esc((e.text || "").slice(0, 160))}…</span></td><td>${e.score ?? ""}</td><td>${e.bm25 ?? ""}</td><td>${e.rrf ?? ""}</td></tr>`));
    h += "</table>";
  }
  const tools = (m.evidence || []).filter((e) => e.type === "tool");
  if (tools.length) h += "<p><strong>Chamadas de ferramenta</strong> (dados simulados)</p><ul>" + tools.map((t) => `<li><code>${esc(t.tool)}</code>(${esc(JSON.stringify(t.arguments))}) · merchant ${esc(t.merchant_id)}</li>`).join("") + "</ul>";
  d.insertAdjacentHTML("beforeend", h);
  return d;
}

// ---------------------------------------------------------------- send
async function send(text) {
  text = (text ?? $("#input").value).trim();
  if (!text || state.busy) return;
  state.busy = true; $("#send").disabled = true; $("#input").value = ""; autosize();
  addUser(text);
  const t0 = Date.now();
  const typing = el("div", "msg assistant typing", '<div class="bubble"><span class="dots"><span></span><span></span><span></span></span><span class="elapsed">Agentes trabalhando…</span></div>');
  $("#messages").append(typing); scrollDown();
  const timer = setInterval(() => { typing.querySelector(".elapsed").textContent = `Agentes trabalhando… ${Math.round((Date.now() - t0) / 1000)} s`; }, 1000);
  try {
    const body = { message: text, user_id: state.me.merchant_id };
    if (state.convId) body.conversation_id = state.convId;
    const r = await api("/chat", { method: "POST", body: JSON.stringify(body) });
    typing.remove();
    renderAnswer(r);
    const isNew = !state.convId;
    state.convId = r.conversation_id;
    if (isNew) $("#conv-title").textContent = text.slice(0, 80);
    setConvStatus(r.status === "escalated" || state.convStatus === "awaiting_human" ? "awaiting_human" : "active");
    loadConversations();
  } catch (e) {
    typing.remove();
    if (e.status !== 401) addBubble("error", `Não foi possível obter resposta (${e.status || "rede"}): ${e.message}`);
  } finally {
    clearInterval(timer);
    state.busy = false; $("#send").disabled = false; $("#input").focus();
  }
}

// ---------------------------------------------------------------- operator queue
async function loadTickets() {
  const box = $("#tickets"); box.innerHTML = "";
  let data;
  try { data = await api(`/operator/tickets?status=${state.filter}`); }
  catch (e) { box.append(el("p", "error-text", esc(e.message))); return; }
  if (!data.tickets.length) { box.append(el("p", "muted", "Nenhum ticket nesta fila.")); return; }
  for (const t of data.tickets) box.append(renderTicket(t));
}
function renderTicket(t) {
  const card = el("article", "ticket");
  const mine = t.claimed_by === state.me.subject;
  let h = `<header><h4>${esc(t.id)} · lojista ${esc(t.merchant_id)}</h4>${pill(t.status, STATUS_PT[t.status] || t.status)}</header>
    <div class="muted small">criado ${esc(new Date(t.created_at).toLocaleString("pt-BR"))} · motivo: ${esc(t.reason)}${t.claimed_by ? " · responsável: " + esc(t.claimed_by) : ""}</div>
    <div class="ctx"><p><strong>Pedido:</strong> ${esc(t.request_message)}</p>`;
  (t.context?.recent_messages || []).forEach((m) => (h += `<p class="small"><strong>${esc(m.role)}:</strong> ${esc(m.content.slice(0, 300))}</p>`));
  h += "</div>";
  if (t.ai_draft) h += `<div class="ctx"><p><strong>Rascunho da IA</strong> (requer aprovação):</p>${fmt(t.ai_draft)}</div>`;
  if (t.operator_reply) h += `<div class="ctx"><p><strong>Resposta enviada (${esc(t.decision)}):</strong> ${esc(t.operator_reply)}</p></div>`;
  card.innerHTML = h;
  if (t.status === "open") {
    const b = el("button", "btn-primary", "Assumir ticket");
    b.onclick = () => act(`/operator/tickets/${t.id}/claim`, {});
    card.append(b);
  } else if (t.status === "claimed" && mine) {
    const ta = el("textarea"); ta.placeholder = "Resposta ao lojista (obrigatória para responder ou rejeitar)…";
    const actions = el("div", "actions");
    const mk = (label, cls, decision) => { const b = el("button", cls, label); b.onclick = () => act(`/operator/tickets/${t.id}/resolve`, { decision, reply: ta.value }); return b; };
    if (t.ai_draft) actions.append(mk("Aprovar rascunho da IA", "btn-secondary", "approve_draft"));
    actions.append(mk("Responder", "btn-primary", "reply"), mk("Rejeitar", "btn-secondary", "reject"));
    card.append(ta, actions);
  }
  return card;
}
async function act(path, body) {
  try { await api(path, { method: "POST", body: JSON.stringify(body), headers: { "Idempotency-Key": crypto.randomUUID?.() || String(Date.now()) } }); }
  catch (e) { alert(`Falha: ${e.message}`); }
  loadTickets();
}

// ---------------------------------------------------------------- wiring
function autosize() { const t = $("#input"); t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 180) + "px"; }
function closeSidebar() { $("#sidebar").classList.remove("open"); }
document.addEventListener("DOMContentLoaded", () => {
  $("#login-form").onsubmit = (e) => { e.preventDefault(); login($("#token-input").value.trim()); };
  $("#logout").onclick = () => logout();
  $("#new-conv").onclick = () => { newConversation(); closeSidebar(); };
  $("#toggle-sidebar").onclick = () => $("#sidebar").classList.toggle("open");
  $("#composer").onsubmit = (e) => { e.preventDefault(); send(); };
  $("#input").addEventListener("input", autosize);
  $("#input").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); } });
  document.querySelectorAll("#ticket-filters .filter").forEach((b) => (b.onclick = () => {
    document.querySelectorAll("#ticket-filters .filter").forEach((x) => x.classList.remove("active"));
    b.classList.add("active"); state.filter = b.dataset.status; loadTickets();
  }));
  let saved = null;
  try { saved = sessionStorage.getItem(TOKEN_KEY); } catch (_) {}
  if (saved) login(saved); else $("#login").hidden = false;
});

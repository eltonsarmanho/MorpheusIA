"use strict";
const $ = (id) => document.getElementById(id);
const base = new URL("../", location.href).pathname.replace(/\/$/, ""); // funciona com ou sem prefixo do nginx
const api = (p) => `${base}${p}`;
const session = (() => {
  let s = sessionStorage.getItem("tjpa-session");
  if (!s) { s = "web-" + crypto.randomUUID(); sessionStorage.setItem("tjpa-session", s); }
  return s;
})();

function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; }

// ---- abas
for (const [tab, panel] of [["tab-chat", "panel-chat"], ["tab-cur", "panel-cur"]]) {
  $(tab).addEventListener("click", () => {
    for (const [t, p] of [["tab-chat", "panel-chat"], ["tab-cur", "panel-cur"]]) {
      $(t).setAttribute("aria-selected", String(t === tab)); $(p).hidden = p !== panel;
    }
  });
}

const headers = () => ({ Authorization: `Bearer ${$("token").value}`, "Content-Type": "application/json" });
$("token").value = sessionStorage.getItem("tjpa-admin") || "";
$("token").addEventListener("change", () => sessionStorage.setItem("tjpa-admin", $("token").value));

// ---- conversa
function addMsg(text, who, data) {
  const li = el("li", `msg ${who}`);
  li.appendChild(el("div", null, text));
  if (data) {
    const meta = el("div", "meta");
    meta.appendChild(el("span", `badge ${data.kind}`, data.kind));
    if (data.domain) meta.appendChild(el("span", null, `domínio: ${data.domain}`));
    if (data.abstain_reason) meta.appendChild(el("span", null, `motivo: ${data.abstain_reason}`));
    if (data.handoff_team) meta.appendChild(el("span", null, `equipe: ${data.handoff_team}`));
    meta.appendChild(el("span", null, `${data.latency_ms} ms`));
    li.appendChild(meta);
  }
  $("log").appendChild(li); li.scrollIntoView({ block: "end" });
}

$("chat-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const message = $("msg").value.trim(); if (!message) return;
  addMsg(message, "user"); $("msg").value = "";
  try {
    const r = await fetch(api("/api/chat"), { method: "POST", headers: headers(), body: JSON.stringify({ session_id: session, message }) });
    if (!r.ok) { addMsg(r.status === 401 ? "Informe o token de acesso no topo da página." : r.status === 422 ? "Mensagem inválida (vazia ou acima de 1000 caracteres)." : `Erro ${r.status}.`, "bot"); return; }
    const data = await r.json();
    addMsg(data.text, "bot", data);
  } catch { addMsg("Não foi possível falar com o servidor.", "bot"); }
});
$("msg").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("chat-form").requestSubmit(); } });

// ---- curadoria
async function loadDocs() {
  sessionStorage.setItem("tjpa-admin", $("token").value);
  const q = new URLSearchParams({ limit: "200" });
  for (const [k, id] of [["domain", "f-domain"], ["state", "f-state"], ["process", "f-process"]]) if ($(id).value) q.set(k, $(id).value);
  const [rs, rd] = await Promise.all([fetch(api("/api/admin/stats"), { headers: headers() }), fetch(api(`/api/admin/documents?${q}`), { headers: headers() })]);
  if (rs.status === 401 || rd.status === 401) { $("cur-msg").textContent = "Token administrativo ausente ou inválido."; $("docs").replaceChildren(); return; }
  const stats = await rs.json(); const docs = await rd.json();
  $("stats").textContent = `Trechos indexados: ${stats.trechos_indexados} · ` + stats.documentos_por_estado.map((x) => `${x.domain}/${x.review_state}: ${x.n}`).join(" · ");
  const rows = docs.map((d) => {
    const tr = el("tr");
    const c1 = el("td"); c1.appendChild(el("div", null, d.title || d.doc_id)); c1.appendChild(el("small", "hint", `${d.process_number || d.source_url || ""} · pág. ${d.pages[0] ?? "-"}`));
    tr.append(c1, el("td", null, d.doc_type), el("td", `state-${d.state}`, d.state), el("td", null, d.reason));
    const act = el("td", "actions");
    for (const [label, decision] of [["Aprovar", "approved"], ["Rejeitar", "rejected"]]) {
      const b = el("button", null, label); b.type = "button"; b.addEventListener("click", () => review(d.doc_id, decision)); act.appendChild(b);
    }
    tr.appendChild(act); return tr;
  });
  $("docs").replaceChildren(...rows);
  $("cur-msg").textContent = `${docs.length} documento(s) listado(s).`;
}

async function review(docId, decision) {
  const reviewer = prompt("Seu nome (registrado na decisão):"); if (!reviewer) return;
  const reason = prompt(decision === "approved" ? "Motivo da aprovação (ex.: conferido na origem):" : "Motivo da rejeição:"); if (!reason) return;
  const r = await fetch(api(`/api/admin/documents/${encodeURIComponent(docId)}/review`), { method: "POST", headers: headers(), body: JSON.stringify({ decision, reviewer, reason }) });
  $("cur-msg").textContent = r.ok ? `Decisão registrada para ${docId}.` : `Falha (${r.status}) ao registrar a decisão.`;
  if (r.ok) loadDocs();
}
$("load").addEventListener("click", loadDocs);

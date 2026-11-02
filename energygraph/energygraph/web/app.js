const $ = (s) => document.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const keyBox = $("#apikey");
keyBox.value = sessionStorage.getItem("energygraph_key") || "";
keyBox.onchange = () => { sessionStorage.setItem("energygraph_key", keyBox.value.trim()); location.reload(); };

async function api(path, opts = {}) {
  const headers = {...(opts.headers || {})};
  const key = sessionStorage.getItem("energygraph_key");
  if (key) headers["X-API-Key"] = key;
  const r = await fetch(path, {...opts, headers});
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error((body.error && body.error.message) || r.statusText);
  return body;
}
const card = (title, html) => `<div class="card"><h3>${esc(title)}</h3>${html}</div>`;
const chips = (xs) => (xs || []).map(x => `<span class="chip">${esc(x)}</span>`).join(" ");
const table = (rows, cols) => !rows || !rows.length ? '<p class="muted">None visible.</p>' :
  `<table><tr>${cols.map(c => `<th>${esc(c[0])}</th>`).join("")}</tr>${rows.map(r =>
    `<tr>${cols.map(c => `<td>${typeof c[1] === "function" ? c[1](r) : esc(r[c[1]])}</td>`).join("")}</tr>`).join("")}</table>`;
const sources = (s) => s && s.length ? `<p class="muted">Sources: ${s.map(x => esc(x.attribution)).join("; ")}</p>` : "";
const notFound = (d) => d.found === false && d.message ? card("Not found", `<p>${esc(d.message)}</p>`) : null;
const cap = (v, u) => v == null ? "" : `${esc(v)} ${esc(u || "")}`;

document.querySelectorAll("#nav button").forEach(b => b.onclick = () => {
  document.querySelectorAll("#nav button, section").forEach(e => e.classList.remove("active"));
  b.classList.add("active"); $("#" + b.dataset.tab).classList.add("active");
});

function spark(obs) {
  if (!obs || obs.length < 2) return "";
  const vs = obs.map(o => o.value), lo = Math.min(...vs), hi = Math.max(...vs), w = 1000, h = 120;
  const pts = obs.map((o, i) => `${(i / (obs.length - 1)) * w},${h - 10 - ((o.value - lo) / ((hi - lo) || 1)) * (h - 20)}`).join(" ");
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="price history">
    <polyline fill="none" stroke="#0f6e5c" stroke-width="2" points="${pts}"/></svg>`;
}

const renderers = {
  async asset(q) {
    const d = await api(`/api/asset/${encodeURIComponent(q)}`);
    if (notFound(d)) return notFound(d);
    const a = d.asset;
    const alt = d.resolved.alternatives.length ? `<p class="muted">Other matches: ${chips(d.resolved.alternatives.map(x => x.name))}</p>` : "";
    return card(`${a.name} (${a.asset_type})`, `<div class="grid">
        <div><div class="stat">${cap(a.capacity, a.capacity_unit) || "–"}</div><div class="label">Capacity</div></div>
        <div><div class="stat">${esc(a.status)}</div><div class="label">Status</div></div>
        <div><div class="stat">${esc(a.commissioning_year || "–")}</div><div class="label">Commissioned</div></div>
        <div><div class="stat">${esc((d.location || [])[0] || a.country || "–")}</div><div class="label">Location</div></div></div>
        <p>Fuels / technologies: ${chips(d.fuels_technologies)}</p><p>Aliases: ${chips(d.aliases)}</p>${alt}
        <p class="muted">id ${esc(a.asset_id)} · as of ${esc(a.as_of)}</p>`)
      + card("Owners & operators", table(d.owners, [["Owner", "name"], ["Share", r => r.share == null ? "" : `${Math.round(r.share * 100)}%`], ["Source", "source"]])
             + table(d.operators, [["Operator", "name"], ["Source", "source"]]))
      + card("Recent incidents", table(d.recent_incidents, [["Type", "type"], ["Start", "start"], ["End", "end"], ["Offline", r => cap(r.capacity_offline, r.unit)], ["Source", "source"]]) + `<p class="note">${esc(d.incident_note)}</p>`)
      + card("Projects", table(d.projects, [["Project", "name"], ["Status", "status"], ["Capacity", r => cap(r.capacity, r.unit)], ["Expected", "expected_year"]]))
      + card("Network", `<p>Upstream: ${chips(d.network.upstream.map(x => x.name))}</p><p>Downstream: ${chips(d.network.downstream.map(x => x.name))}</p>`)
      + card("Documents", table(d.documents, [["Title", "title"], ["Published", "published_at"], ["Source", "source"]]))
      + sources(d.sources);
  },
  async ownership(q) {
    const d = await api(`/api/ownership/${encodeURIComponent(q)}`);
    if (notFound(d)) return notFound(d);
    if (d.company) return card(d.company.name, `<p>Parent chain: ${chips(d.parent_chain.map(x => x.name)) || "none visible"}</p>
        <p>Ultimate parent: <b>${esc(d.ultimate_parent ? d.ultimate_parent.name : d.company.name)}</b></p>`)
      + card("Subsidiaries", table(d.subsidiaries, [["Company", "name"], ["Source", "source"]]))
      + card(`Assets (${d.asset_count_shown})`, table(d.assets, [["Asset", "name"], ["Type", "asset_type"], ["Country", "country"], ["Capacity", r => cap(r.capacity, r.unit)], ["Via", r => chips(r.links.map(l => `${l.via} (${l.role})`))]]))
      + sources(d.sources);
    return card(`Owners of ${d.asset.name}`, table(d.owners, [["Owner", r => esc(r.owner.name)], ["Role", r => esc(r.owner.role)], ["Share", r => r.owner.share == null ? "" : `${Math.round(r.owner.share * 100)}%`], ["Parent chain", r => chips(r.parent_chain.map(x => x.name))]])) + sources(d.sources);
  },
  async projects(q, f) {
    const p = new URLSearchParams({country: q});
    if (f.asset_type.value) p.set("asset_type", f.asset_type.value);
    if (f.status.value) p.set("status", f.status.value);
    const d = await api(`/api/projects?${p}`);
    return card(`${d.total_projects} projects`, table(d.by_status, [["Status", "status"], ["Projects", "projects"], ["Total capacity", r => cap(r.total_capacity, r.unit)]]))
      + card("Projects", table(d.projects, [["Project", "name"], ["Type", "asset_type"], ["Status", "status"], ["Capacity", r => cap(r.capacity, r.unit)], ["Expected", "expected_year"], ["Developers", r => chips(r.developers)]]))
      + sources(d.sources);
  },
  async prices(q) {
    const d = await api(`/api/prices?series=${encodeURIComponent(q)}`);
    if (notFound(d)) return notFound(d);
    const s = d.summary, u = d.series.unit;
    return card(d.series.name, `<div class="grid">
        <div><div class="stat">${cap(s.mean, u)}</div><div class="label">Mean</div></div>
        <div><div class="stat">${cap(s.min, u)}</div><div class="label">Min</div></div>
        <div><div class="stat">${cap(s.max, u)}</div><div class="label">Max</div></div>
        <div><div class="stat">${esc(s.count)}</div><div class="label">Observations ${esc(s.first_date)} → ${esc(s.last_date)}</div></div></div>
        ${spark(d.observations)}<p class="note">${esc(d.note)}</p>`) + sources(d.sources);
  },
  async search(q) {
    const d = await api(`/api/search?term=${encodeURIComponent(q)}`);
    return card("Keyword vs taxonomy-expanded", `<p>${d.baseline_matches} → ${d.expanded_matches} documents using ${d.expanded_term_count} terms</p>`)
      + card("Documents", table(d.documents, [["Title", "title"], ["Published", "published_at"], ["Snippet", "snippet"], ["Source", "source"]])) + sources(d.sources);
  },
};

const history = [];
async function sendChat(q) {
  const log = $("#chat-log");
  log.insertAdjacentHTML("beforeend", `<div class="msg user">${esc(q)}</div><div class="msg bot muted" id="pending">Thinking…</div>`);
  try {
    const d = await api("/api/chat", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({question: q, history})});
    $("#pending").outerHTML = `<div class="msg bot">${esc(d.answer)}<div class="tools">${d.tool_calls.map(t => `${esc(t.tool)}(${esc(JSON.stringify(t.args))}) → ${esc(t.outcome)}`).join("<br>")}</div></div>`;
    history.push({role: "user", content: q}, {role: "assistant", content: d.answer});
  } catch (e) { $("#pending").outerHTML = `<div class="msg bot err">${esc(e.message)}</div>`; }
}

document.querySelectorAll("form[data-run]").forEach(f => f.onsubmit = async (ev) => {
  ev.preventDefault();
  const kind = f.dataset.run, q = f.q.value.trim();
  if (!q) return;
  if (kind === "chat") { f.q.value = ""; return sendChat(q); }
  const out = $(`#${kind}-out`);
  out.innerHTML = '<p class="muted">Loading…</p>';
  try { out.innerHTML = await renderers[kind](q, f); } catch (e) { out.innerHTML = `<p class="err">${esc(e.message)}</p>`; }
});

(async () => {
  try {
    const me = await api("/api/me");
    $("#me").textContent = `${me.name} · ${me.entitlements.join(", ")}`;
    const s = await api("/api/sources");
    $("#sources-out").innerHTML = card("Data sources", table(s.sources, [["Source", "source"], ["Title", "title"], ["Kind", "kind"], ["Licence", "license"],
      ["Entitled", r => r.entitled ? "yes" : "no"], ["Status", r => esc(r.available ? "ok" : r.status)]]));
  } catch (e) { $("#sources-out").innerHTML = `<p class="err">${esc(e.message)}</p>`; }
})();

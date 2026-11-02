const $ = (s) => document.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const keyBox = document.getElementById("apikey");
keyBox.value = sessionStorage.getItem("medgraph_key") || "";
keyBox.onchange = () => { sessionStorage.setItem("medgraph_key", keyBox.value.trim()); location.reload(); };
async function api(path, opts = {}) {
  const headers = {...(opts.headers || {})};
  const key = sessionStorage.getItem("medgraph_key");
  if (key) headers["X-API-Key"] = key;
  const r = await fetch(path, {...opts, headers});
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error((body.error && body.error.message) || r.statusText);
  return body;
}
const card = (title, html) => `<div class="card"><h3>${esc(title)}</h3>${html}</div>`;
const table = (rows, cols) => !rows || !rows.length ? '<p class="muted">None in the graph.</p>' :
  `<table><tr>${cols.map(c => `<th>${esc(c[0])}</th>`).join("")}</tr>${rows.map(r =>
    `<tr>${cols.map(c => `<td>${typeof c[1] === "function" ? c[1](r) : esc(r[c[1]])}</td>`).join("")}</tr>`).join("")}</table>`;
const chips = (xs) => (xs || []).map(x => `<span class="chip">${esc(x)}</span>`).join(" ");
const notFound = (d) => d.found === false ? card("Not found", `<p>${esc(d.message || "The graph does not contain this.")}</p>`) : null;

document.querySelectorAll("#nav button").forEach(b => b.onclick = () => {
  document.querySelectorAll("#nav button, section").forEach(e => e.classList.remove("active"));
  b.classList.add("active"); $("#" + b.dataset.tab).classList.add("active");
});

const renderers = {
  async drug(q) {
    const d = await api(`/api/drug/${encodeURIComponent(q)}`);
    if (notFound(d)) return notFound(d);
    const atc = d.atc.found ? d.atc.classifications.map(c => `<div class="path">${c.path.map(p =>
        `<span class="chip"><b>${esc(p.code)}</b> ${esc(p.name)}</span>`).join("→")}</div>`).join("") : `<p class="muted">${esc(d.atc.message)}</p>`;
    const s = d.safety;
    return card("1 · Identity", `<p><b>${esc(d.drug.input)}</b> → <b>${esc(d.drug.drug)}</b> <span class="muted">(${esc(d.drug.via)})</span></p>
              <div>Aliases: ${chips(d.aliases)}</div><div>Pharmacologic class: ${chips(d.pharm_classes)}</div>`)
      + card("2 · ATC classification", atc)
      + card(`3 · Indications (${d.indications.length})`, table(d.indications, [["Label term","term"],["MeSH concept", r => chips(r.mesh)]]))
      + card("4 · Safety (FAERS sample)", `<div class="grid">
          ${[["reports","Reports"],["serious","Serious"],["deaths","Deaths"],["hospitalizations","Hospitalisations"],["distinct_reactions","Distinct reactions"]]
            .map(([k,l]) => `<div><div class="stat">${esc(s[k])}</div><div class="label">${l}</div></div>`).join("")}</div>
          ${table(s.top_reactions, [["Reaction","reaction"],["Reports","reports"],["MeSH", r => chips(r.mesh_concepts)]])}<p class="note">${esc(s.note)}</p>`)
      + card(`5 · Similar drugs (${d.similar.total_similar})`, table(d.similar.similar, [["Drug","drug"],["Score","score"],["Shared indications","shared_indications"],["Shared reactions","shared_reactions"],["In common", r => chips(r.indications_in_common)]]) + `<p class="note">${esc(d.similar.note)}</p>`)
      + card(`6 · Clinical trials testing this drug (${d.trials.total_trials || 0})`, table(d.trials.trials, [["NCT","nct_id"],["Title","title"],["Status","status"],["Sponsor","sponsor"]]))
      + card("Labels", table(d.labels, [["Document","title"],["Source","source"]]));
  },
  async search(q) {
    const d = await api(`/api/search?term=${encodeURIComponent(q)}`);
    const gain = d.recall_gain_pct == null ? "n/a" : `+${d.recall_gain_pct}%`;
    return card("Recall: keyword vs graph-expanded", `<div class="grid">
        <div><div class="stat">${d.baseline_matches}</div><div class="label">Keyword-only matches</div></div>
        <div><div class="stat">${d.expanded_matches}</div><div class="label">Graph-expanded matches</div></div>
        <div><div class="stat">${d.expanded_term_count}</div><div class="label">Expanded terms</div></div>
        <div><div class="stat">${gain}</div><div class="label">Recall gain</div></div></div>
        ${d.concept ? `<p>Concept: <b>${esc(d.concept.name)}</b> (${esc(d.concept.ui)})</p>` : ""}`)
      + card("Documents", table(d.documents, [["Label","title"],["Drug","drug"],["Matched terms", r => chips(r.matched_terms)],["Source","source"]]));
  },
  async disease(q) {
    const d = await api(`/api/disease/${encodeURIComponent(q)}`);
    if (notFound(d)) return notFound(d);
    return card(`${d.concept.name} (${d.concept.ui})`, `<div>Broader: ${chips(d.broader)}</div><div>Clinical trials: <b>${d.clinical_trials}</b></div>`)
      + card("Drugs indicated", table(d.drugs, [["Drug","drug"],["Via concept", r => chips(r.via_concepts)]]))
      + card("Most-reported reactions for these drugs", table(d.top_reported_reactions, [["Reaction","reaction"],["Reports","reports"],["Drugs", r => chips(r.drugs)]]) + `<p class="note">${esc(d.note)}</p>`)
      + card("ICD-10-CM", table(d.icd10, [["Code","code"],["Name","name"],["MeSH concept","concept"]]));
  },
  async trials(q, form) {
    const st = form.status.value;
    const d = await api(`/api/trials?condition=${encodeURIComponent(q)}${st ? "&status=" + st : ""}`);
    if (d.found === false && !d.total_trials) return card("No trials", `<p>${esc(d.message)}</p>`);
    return card(`${d.total_trials} trials`, `<div>${(d.by_status||[]).map(s => `<span class="chip">${esc(s.status)}: ${s.trials}</span>`).join(" ")}</div>
        <p>Top sponsors: ${chips((d.top_sponsors||[]).map(s => `${s.sponsor} (${s.trials})`))}</p>`)
      + card("Trials", table(d.trials, [["NCT","nct_id"],["Title","title"],["Status","status"],["Phase", r => chips(r.phases)],["Sponsor","sponsor"],["Conditions", r => chips(r.conditions)]]));
  },
};

const history = [];
async function sendChat(q) {
  const log = $("#chat-log");
  log.insertAdjacentHTML("beforeend", `<div class="msg user">${esc(q)}</div><div class="msg bot muted" id="pending">Thinking…</div>`);
  try {
    const d = await api("/api/chat", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({question:q, history})});
    $("#pending").outerHTML = `<div class="msg bot">${esc(d.answer)}<div class="tools">${d.tool_calls.map(t => `${esc(t.tool)}(${esc(JSON.stringify(t.args))})`).join("<br>")}</div></div>`;
    history.push({role:"user", content:q}, {role:"assistant", content:d.answer});
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
    const s = await api("/api/stats");
    const tiles = [["nodes","Nodes"],["relationships","Relationships"],["vector_indexes","Vector indexes"],["embeddings","Embeddings"]];
    $("#overview-out").innerHTML = card("Graph", `<div class="grid">${tiles.map(([k,l]) => `<div><div class="stat">${s[k].toLocaleString()}</div><div class="label">${l}</div></div>`).join("")}</div>`)
      + card("Nodes by label", table(Object.entries(s.nodes_by_label).map(([k,v]) => ({k, v: v.toLocaleString()})), [["Label","k"],["Count","v"]]))
      + card("Relationships by type", table(Object.entries(s.relationships_by_type).map(([k,v]) => ({k, v: v.toLocaleString()})), [["Type","k"],["Count","v"]]));
  } catch (e) { $("#overview-out").innerHTML = `<p class="err">${esc(e.message)}</p>`; }
  const o = await api("/api/ontology").catch(e => ({error: e}));
  if (o.error) { $("#ontology-out").innerHTML = `<p class="err">${esc(o.error.message)}</p>`; return; }
  $("#ontology-out").innerHTML = card(`Node classes (${o.classes.length})`, table(o.classes, [["Class","name"],["Key","key"],["Vector index", r => r.embedded ? "yes" : ""],["Description","comment"]]))
    + card(`Relationship types (${o.relationships.length})`, table(o.relationships, [["Type","name"],["Domain","domain"],["Range","range"],["Description","comment"]]));
})();

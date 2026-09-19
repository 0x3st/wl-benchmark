/**
 * WL-Benchmark SaaS platform — Cloudflare Worker + KV.
 *
 *   GET  /                run index (server-rendered cards)
 *   GET  /r/<run-id>      one run page (self-contained HTML produced by
 *                         the local CLI; images already inlined)
 *   GET  /api/runs        index metadata as JSON
 *   POST /api/runs        upload a run  { id, meta, html }
 *                         Authorization: Bearer <UPLOAD_TOKEN>
 *
 * Storage (KV namespace bound as RUNS):
 *   run:<id>  -> the run page HTML
 *   index     -> JSON array of run metadata (newest last)
 *
 * Deploy once (see README.md); the CLI then needs nothing but the site
 * URL and the upload token.
 */

// Homepage design language mirrors the single-run report UI
// (see wl_benchmark/site_ui.py and docs/report-ui.md).
const CSS = `
:root { color-scheme: light; --bg:#f4f6f8; --paper:#fff; --ink:#182a3a;
  --muted:#5f6f7f; --line:#dfe6ec; --accent:#116b60; --soft:#eaf5f1;
  --red:#ac3541; --amber:#805918; --mono:ui-monospace,SFMono-Regular,Consolas,monospace; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink); font:14px/1.6
  -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif; }
a { color:var(--accent); text-decoration:none; }
a:hover { text-decoration:underline; }
:focus-visible { outline:3px solid #298bdb; outline-offset:4px; }
.topbar { background:var(--paper); border-bottom:1px solid var(--line); }
.topbar-inner { max-width:1100px; margin:auto; padding:18px 36px; display:flex;
  align-items:center; justify-content:space-between; gap:20px; }
.brand { display:flex; align-items:center; gap:10px; color:var(--ink); font-weight:750;
  letter-spacing:.04em; font-size:13px; }
.brand-mark { display:grid; place-items:center; width:34px; height:34px;
  background:var(--ink); color:white; border-radius:9px; font-size:12px; }
.top-links { display:flex; align-items:center; gap:22px; font-size:12px; }
.shell { max-width:1100px; margin:auto; padding:36px; }
.eyebrow { color:var(--muted); text-transform:uppercase; font-size:10px;
  font-weight:750; letter-spacing:.13em; margin:0 0 10px; }
h1 { font-size:clamp(24px,3vw,36px); line-height:1.25; letter-spacing:-.035em; margin:0 0 12px; }
.meta { color:var(--muted); font-size:12px; }
.hero .meta { margin:4px 0; max-width:72ch; }
.stats { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:14px; margin:26px 0 10px; }
.stat { border:1px solid var(--line); border-radius:12px; background:white; padding:16px 20px; }
.stat-label { color:var(--muted); font-size:11px; font-weight:650; }
.stat-value { display:block; font-size:28px; line-height:1.4; font-weight:650; font-variant-numeric:tabular-nums; }
.stat.warn .stat-value { color:var(--amber); }
.stat.danger .stat-value { color:var(--red); }
.grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(300px,1fr)); gap:14px; margin-top:22px; }
.run-card { display:block; border:1px solid var(--line); border-radius:12px; background:white;
  padding:18px 20px; color:var(--ink); }
.run-card:hover { text-decoration:none; border-color:var(--accent);
  box-shadow:0 3px 8px #182a3a0d; }
.run-id { display:block; font:12px/1.5 var(--mono); color:var(--muted); }
.run-model { display:block; margin-top:4px; font-size:16px; font-weight:650;
  letter-spacing:-.01em; }
.run-meta { display:block; margin-top:10px; }
.badge { display:inline-block; border-radius:5px; padding:3px 8px; font-size:10px;
  font-weight:650; margin-right:6px; }
.badge.pen { background:#fff4dc; color:var(--amber); }
.badge.err { background:#fcecef; color:var(--red); }
.empty-state { text-align:center; padding:38px 20px; border:1px dashed #cbd6de;
  border-radius:10px; background:#f9fbfc; color:var(--muted); font-size:12px; }
.empty-state code { font-family:var(--mono); background:#eef3f5; padding:2px 5px; border-radius:3px; }
footer { max-width:1100px; margin:32px auto 0; padding:0 36px 40px; color:var(--muted);
  font-size:11px; border-top:1px solid var(--line); padding-top:22px; }
footer a { margin-right:16px; }
@media (max-width:720px) {
  .shell,.topbar-inner { padding-left:14px; padding-right:14px; }
  .topbar-inner { padding-top:12px; padding-bottom:12px; }
  .stats { grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }
  .stat { padding:12px 14px; } .stat-value { font-size:24px; }
  footer { padding-left:14px; padding-right:14px; }
}
`;

const ID_RE = /^[A-Za-z0-9_-]{4,64}$/;

// Link to the repository README (set DOCS_URL in wrangler.toml [vars])
const DEFAULT_DOCS_URL = "https://github.com/0x3st/wl-benchmark#readme";

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;",
            "'": "&#39;" }[c]));
}

function indexPage(runs, docsUrl) {
  const num = v => { const n = Number(v); return Number.isFinite(n) ? n : 0; };
  const totals = runs.reduce((a, r) => ({
    tasks: a.tasks + num(r.tasks), pending: a.pending + num(r.pending),
    errors: a.errors + num(r.errors),
  }), { tasks: 0, pending: 0, errors: 0 });
  const stats = [
    ["", "EVALUATION RUNS", runs.length],
    ["", "TASK RESULTS", totals.tasks],
    ["warn", "AWAITING HUMAN REVIEW", totals.pending],
    ["danger", "TASK ERRORS", totals.errors],
  ].map(([cls, label, value]) =>
    `<div class="stat ${cls}"><span class="stat-label">${label}</span>` +
    `<span class="stat-value">${value}</span></div>`).join("");
  const cards = runs.length
    ? `<div class="grid">` + runs.slice().reverse().map(r => `
      <a class="run-card" href="/r/${esc(r.id)}">
        <span class="run-id">${esc(r.id)} · ${esc(r.time)}</span>
        <span class="run-model">${esc(r.model)}</span>
        <span class="run-meta meta">${esc(r.provider)} · ${num(r.tasks)} tasks ·
        recorded mean ${esc(r.auto_avg)}</span>
        ${num(r.pending) ? `<span class="badge pen">${num(r.pending)} awaiting review</span>` : ""}
        ${num(r.errors) ? `<span class="badge err">${num(r.errors)} task errors</span>` : ""}
      </a>`).join("") + `</div>`
    : `<div class="empty-state"><strong>No evaluation runs yet</strong>
       <p>Run <code>wlb run</code> locally — finished runs are published here
       automatically as self-contained reports.</p></div>`;
  return `<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>WL-Benchmark — evaluation archive</title><style>${CSS}</style></head><body>
<div class="topbar"><div class="topbar-inner">
<span class="brand"><span class="brand-mark">WL</span>BENCHMARK</span>
<div class="top-links"><a href="${esc(docsUrl)}" target="_blank" rel="noopener">Documentation</a>
<a href="/api/runs">Index API</a></div></div></div>
<div class="shell"><header class="hero">
<p class="eyebrow">Evaluation archive</p><h1>WL-Benchmark</h1>
<p class="meta">Agentic AI benchmark — essay writing with rubric, SVG drawing,
agentic scheduling with opaque tools, multi-factor quant mining.
One self-contained, human-reviewable report per evaluation run.</p></header>
<section class="stats" aria-label="Archive overview">${stats}</section>
${cards}
<footer><a href="${esc(docsUrl)}" target="_blank" rel="noopener">docs / README</a>
<a href="/api/runs">index API</a><span>Scores shown per run; automatic components
are not final human review scores.</span></footer>
</div></body></html>`;
}

function notFound(msg) {
  return new Response(`<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<title>WL-Benchmark</title><style>${CSS}</style></head><body>
<div class="topbar"><div class="topbar-inner">
<a class="brand" href="/"><span class="brand-mark">WL</span>BENCHMARK</a></div></div>
<div class="shell"><div class="empty-state"><strong>Nothing here</strong>
<p>${esc(msg)}</p><p><a href="/>← all runs</a></p></div></div></body></html>`,
    { status: 404, headers: { "content-type": "text/html; charset=utf-8" } });
}

function jsonResp(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

async function getIndex(env) {
  const raw = await env.RUNS.get("index");
  try {
    const arr = JSON.parse(raw);
    if (Array.isArray(arr)) return arr;
  } catch (_) { /* fresh site */ }
  return [];
}

async function handleUpload(request, env) {
  const token = (request.headers.get("Authorization") || "")
    .replace(/^Bearer\s+/i, "");
  if (!token || token !== env.UPLOAD_TOKEN) {
    return jsonResp({ ok: false, error: "unauthorized" }, 401);
  }
  let body;
  try {
    body = await request.json();
  } catch (_) {
    return jsonResp({ ok: false, error: "invalid JSON body" }, 400);
  }
  const { id, meta, html } = body || {};
  if (!ID_RE.test(id || "")) {
    return jsonResp({ ok: false, error: "bad run id" }, 400);
  }
  if (typeof html !== "string" || html.length < 64) {
    return jsonResp({ ok: false, error: "missing html payload" }, 400);
  }
  if (!meta || typeof meta !== "object") {
    return jsonResp({ ok: false, error: "missing meta" }, 400);
  }

  await env.RUNS.put(`run:${id}`, html);
  const runs = (await getIndex(env)).filter(r => r.id !== id);
  runs.push({ ...meta, id });
  runs.sort((a, b) => (a.id < b.id ? -1 : 1));
  await env.RUNS.put("index", JSON.stringify(runs));
  return jsonResp({ ok: true, id, url: `/r/${id}` });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, "") || "/";

    if (request.method === "DELETE" && path.startsWith("/api/runs/")) {
      const token = (request.headers.get("Authorization") || "")
        .replace(/^Bearer\s+/i, "");
      if (!token || token !== env.UPLOAD_TOKEN) {
        return jsonResp({ ok: false, error: "unauthorized" }, 401);
      }
      const id = path.slice("/api/runs/".length);
      if (!ID_RE.test(id)) {
        return jsonResp({ ok: false, error: "bad run id" }, 400);
      }
      await env.RUNS.delete(`run:${id}`);
      const runs = (await getIndex(env)).filter(r => r.id !== id);
      await env.RUNS.put("index", JSON.stringify(runs));
      return jsonResp({ ok: true, deleted: id });
    }
    if (request.method === "POST" && path === "/api/runs") {
      return handleUpload(request, env);
    }
    if (request.method === "GET" && path === "/api/runs") {
      return jsonResp({ ok: true, runs: await getIndex(env) });
    }
    if (request.method === "GET" && path === "/") {
      const html = indexPage(await getIndex(env),
                             env.DOCS_URL || DEFAULT_DOCS_URL);
      return new Response(html, {
        headers: { "content-type": "text/html; charset=utf-8" },
      });
    }
    const m = path.match(/^\/r\/([A-Za-z0-9_-]+)$/);
    if (request.method === "GET" && m) {
      const page = await env.RUNS.get(`run:${m[1]}`);
      if (!page) return notFound(`run ${m[1]} not found`);
      return new Response(page, {
        headers: { "content-type": "text/html; charset=utf-8" },
      });
    }
    return notFound("nothing here");
  },
};

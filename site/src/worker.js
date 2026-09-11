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

const CSS = `
* { box-sizing: border-box; }
body { font-family: "PingFang SC","Hiragino Sans GB",-apple-system,Georgia,serif;
       font-size: 14px; line-height: 1.65; color: #24292f;
       max-width: 860px; margin: 0 auto; padding: 36px 20px 80px; }
h1 { font-size: 24px; border-bottom: 3px solid #0969da; padding-bottom: 8px; }
h2 { font-size: 17px; border-bottom: 1px solid #d0d7de; padding-bottom: 4px;
     margin: 28px 0 10px; color: #0969da; }
.meta { color: #57606a; font-size: 12.5px; }
.card { border: 1px solid #d0d7de; border-radius: 8px; padding: 12px 16px;
        margin: 10px 0; background: #fff; }
.card a { font-weight: 600; text-decoration: none; color: #0969da;
          font-size: 15px; }
.badge { display: inline-block; padding: 1px 8px; border-radius: 10px;
         font-size: 11px; font-weight: 600; margin-left: 8px;
         background: #f0f4fa; color: #57606a; }
.err { background: #ffebe9; color: #82071d; }
footer { margin-top: 48px; color: #8b949e; font-size: 11.5px;
         border-top: 1px solid #d0d7de; padding-top: 12px; }
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
  const cards = runs.length
    ? runs.slice().reverse().map(r => `
      <div class="card">
        <a href="/r/${esc(r.id)}">${esc(r.id)}</a>
        ${r.errors ? '<span class="badge err">errors: ' + r.errors + "</span>" : ""}
        <br><span class="meta">${esc(r.provider)} / <b>${esc(r.model)}</b>
        · ${r.tasks} tasks · auto-avg ${esc(r.auto_avg)}
        · ${r.pending} pending human review · ${esc(r.time)}</span>
      </div>`).join("")
    : "<p class='meta'>no runs yet — run <code>wlb run</code> locally</p>";
  return `<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>WL-Benchmark — runs</title><style>${CSS}</style></head><body>
<h1>WL-Benchmark</h1>
<p class="meta">Agentic AI benchmark — essay writing with rubric, SVG
drawing, agentic scheduling with opaque tools, multi-factor quant mining.
One page per evaluation run.</p>
${cards}
<footer>WL-Benchmark platform · <a href="/api/runs">index API</a>
· <a href="${esc(docsUrl)}" target="_blank" rel="noopener">docs / README</a></footer>
</body></html>`;
}

function notFound(msg) {
  return new Response(`<!DOCTYPE html><html><head><meta charset="utf-8">
<title>WL-Benchmark</title><style>${CSS}</style></head><body>
<h1>WL-Benchmark</h1><p>${esc(msg)}</p>
<p><a href="/">← all runs</a></p></body></html>`,
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

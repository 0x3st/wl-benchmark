"""Static-site builder: turn a run directory into one self-contained page.

Site layout (deployed to Cloudflare Pages, e.g. benchmark.wulei.org):
  index.html        run index (renders manifest.json client-side)
  manifest.json     {"runs": [...]} — the accumulation log
  runs/<id>.html    one fully self-contained page per run
                    (images inlined as base64, no external assets)

Because every run page is a single file, history survives locally-deleted
data: before deploying, the publisher pulls the existing manifest and run
pages back from the site and re-uploads them together with the new one
(identical files are deduplicated by Cloudflare by content hash).
"""
from __future__ import annotations

import base64
import html
import json
import os
import re

from .review_pdf import md_to_html

# Public link to the repository README (shown on every run page)
DOCS_URL = "https://github.com/0x3st/wl-benchmark#readme"

CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { font-family: "PingFang SC","Hiragino Sans GB",-apple-system,Georgia,serif;
       font-size: 14px; line-height: 1.65; color: #24292f;
       max-width: 860px; margin: 0 auto; padding: 36px 20px 80px; }
h1 { font-size: 24px; border-bottom: 3px solid #0969da; padding-bottom: 8px; }
h2 { font-size: 17px; border-bottom: 1px solid #d0d7de; padding-bottom: 4px;
     margin: 28px 0 10px; color: #0969da; }
h3 { font-size: 14px; margin: 16px 0 6px; color: #333; }
.meta { color: #57606a; font-size: 12.5px; }
table { border-collapse: collapse; width: 100%; margin: 10px 0; font-size: 12.5px; }
th { background: #f0f4fa; text-align: left; }
th, td { border: 1px solid #d0d7de; padding: 5px 9px; vertical-align: top; }
tr:nth-child(even) td { background: #fafbfc; }
img { max-width: 100%; border: 1px solid #d0d7de; border-radius: 4px; }
.badge { display: inline-block; padding: 1px 8px; border-radius: 10px;
         font-size: 11px; font-weight: 600; margin-right: 6px; }
.ok   { background: #dafbe1; color: #116329; }
.pen  { background: #fff8c5; color: #4d2d00; }
.err  { background: #ffebe9; color: #82071d; }
.score { font-size: 15px; font-weight: 700; }
.card { border: 1px solid #d0d7de; border-radius: 8px; padding: 12px 16px;
        margin: 10px 0; background: #fff; }
.card a { font-weight: 600; text-decoration: none; color: #0969da; }
pre { background: #f6f8fa; padding: 10px 14px; border-radius: 6px;
      overflow-x: auto; font-size: 12px; }
code { font-family: Menlo,monospace; font-size: 12px; background: #f0f2f5;
       padding: 1px 4px; border-radius: 3px; }
blockquote { margin: 10px 0; padding: 8px 14px; background: #fff8e6;
             border-left: 4px solid #d4a72c; }
footer { margin-top: 48px; color: #8b949e; font-size: 11.5px;
         border-top: 1px solid #d0d7de; padding-top: 12px; }
"""


def _b64(path: str, mime: str) -> str:
    with open(path, "rb") as f:
        return f"data:{mime};base64," + base64.b64encode(f.read()).decode()


def _badge(r: dict) -> str:
    if r.get("error"):
        return "<span class='badge err'>ERROR</span>"
    if r.get("score") is None:
        return "<span class='badge pen'>PENDING HUMAN REVIEW</span>"
    return (f"<span class='badge ok'>SCORED</span>"
            f"<span class='score'>{r['score']:.2f}</span>")


def _img_artifact(r: dict) -> str:
    png = next((a for a in r.get("artifacts", []) if a.endswith(".png")
                and os.path.exists(a)), None)
    return f"<img src='{_b64(png, 'image/png')}'>" if png else ""


def _md_artifacts(r: dict) -> str:
    out = []
    for a in r.get("artifacts", []):
        if a.endswith(".md") and os.path.exists(a):
            with open(a, encoding="utf-8") as f:
                out.append(md_to_html(f.read()))
    return "".join(out)


def _cons_table(cons: list) -> str:
    if not cons:
        return ""
    n_ok = sum(1 for c in cons if c["ok"])
    rows = "".join(
        f"<tr><td>{html.escape(str(c['constraint']))}</td>"
        f"<td>{'PASS' if c['ok'] else 'FAIL'}</td>"
        f"<td>{html.escape(str(c['detail']))}</td></tr>"
        for c in cons)
    return (f"<h3>HARD CONSTRAINTS — {n_ok}/{len(cons)} passed</h3>"
            f"<table><tr><th>constraint</th><th>result</th><th>detail</th>"
            f"</tr>{rows}</table>")


def _quant_section(r: dict) -> str:
    d = r.get("detail", {})
    auto, rows = d.get("auto_score"), d.get("per_question", [])
    h = ""
    if rows:
        trs = "".join(
            f"<tr><td>{html.escape(str(q['id']))}</td>"
            f"<td>{html.escape(str(q['given']))}</td>"
            f"<td>{html.escape(str(q['answer']))}</td>"
            f"<td>{'OK' if q['correct'] else 'X'}</td></tr>"
            for q in rows)
        head = f" (auto score {auto:.2f})" if auto is not None else ""
        h += (f"<h3>AUTO-SCORED ANSWERS{head}</h3>"
              "<table><tr><th>id</th><th>given</th><th>answer</th>"
              f"<th>correct</th></tr>{trs}</table>")
        if auto is not None:
            h += (f"<p class='meta'>Final = "
                  f"{d.get('auto_weight', 0.8):.0%} x auto ({auto:.2f}) + "
                  f"{d.get('note_weight', 0.2):.0%} x note score.</p>")
    h += "<h3>RESEARCH NOTE</h3>" + _md_artifacts(r)
    return h


def _sched_section(r: dict) -> str:
    d = r.get("detail", {})
    h = f"<p class='score'>Score {r.get('score', 0):.2f}</p>"
    checks = d.get("checks", {})
    if checks:
        rows = "".join(
            f"<tr><td>{html.escape(k.replace('_', ' '))}</td>"
            f"<td>{'PASS' if v else 'FAIL'}</td></tr>"
            for k, v in checks.items())
        h += ("<h3>RULE CHECKS</h3><table>"
              "<tr><th>rule</th><th>result</th></tr>" + rows + "</table>")
    if d.get("conflicts"):
        rows = "".join(f"<li>{html.escape(str(c))}</li>"
                       for c in d["conflicts"])
        h += f"<h3>TIME CONFLICTS</h3><ul>{rows}</ul>"
    if d.get("enrollments"):
        rows = "".join(f"<li>{html.escape(str(e))}</li>"
                       for e in d["enrollments"])
        h += f"<h3>ENROLLED PLAN</h3><ul>{rows}</ul>"
    return h


def build_run_page(results: list, run_id: str, run_dir: str = "") -> str:
    """One self-contained HTML page for a single run."""
    prov = results[0].get("provider", "?") if results else "?"
    model = results[0].get("model", "?") if results else "?"
    n_err = sum(1 for r in results if r.get("error"))
    scored = [r["score"] for r in results
              if not r.get("error") and r.get("score") is not None]
    pending = sum(1 for r in results
                  if not r.get("error") and r.get("score") is None)
    avg = (f"{sum(scored)/len(scored):.3f}" if scored else "—")

    body = [f"<h1>WL-Benchmark — run <code>{html.escape(run_id)}</code></h1>",
            f"<p class='meta'>provider <b>{html.escape(prov)}</b> · model "
            f"<b>{html.escape(model)}</b> · {len(results)} tasks · "
            f"errors {n_err} · auto-avg {avg} · pending human review "
            f"{pending}</p>"]

    # summary table
    rows = []
    for r in results:
        if r.get("error"):
            score = "ERR"
        elif r.get("score") is None:
            score = "pending"
        else:
            score = f"{r['score']:.2f}"
        rows.append(f"<tr><td>{html.escape(r['task_id'])}</td>"
                    f"<td>{html.escape(r['task_type'])}</td>"
                    f"<td>{score}</td><td>{r.get('latency', 0):.1f}s</td>"
                    f"<td>{html.escape((r.get('error') or '')[:80])}</td></tr>")
    body += ["<h2>Summary</h2>",
             "<table><tr><th>task</th><th>type</th><th>score</th>"
             f"<th>latency</th><th>error</th></tr>{''.join(rows)}</table>"]

    for r in results:
        ttype = r.get("task_type")
        body.append(f"<h2>[{ttype}] {html.escape(r['task_id'])}</h2>")
        body.append(_badge(r))
        if r.get("error"):
            body.append(f"<pre>{html.escape(str(r['error']))}</pre>")
            continue
        d = r.get("detail", {})
        if ttype == "essay":
            rub = d.get("rubric_file")
            if rub:
                body.append(f"<p class='meta'>graded by a human against the "
                            f"task rubric.</p>")
            body.append(_cons_table(d.get("constraints", [])))
            body.append("<h3>ESSAY</h3>" + _md_artifacts(r))
        elif ttype == "svg":
            body.append("<p class='meta'>Random picks: <b>"
                        + html.escape(json.dumps(d.get("picks", {}),
                                                 ensure_ascii=False))
                        + "</b></p>")
            body.append(_cons_table(d.get("constraints", [])))
            body.append(_img_artifact(r))
            if d.get("raster_blank"):
                body.append("<p style='color:#82071d'>⚠ the rendered PNG is "
                            "blank — the model's SVG likely failed to parse; "
                            "download the .svg source below to inspect it."
                            "</p>")
            if d.get("raster_error"):
                body.append(f"<p class='meta'>raster error: "
                            f"{html.escape(d['raster_error'])}</p>")
            svg_file = next((a for a in r.get("artifacts", [])
                             if a.endswith(".svg") and os.path.exists(a)), None)
            if svg_file:
                body.append(f"<p><a href='{_b64(svg_file, 'image/svg+xml')}' "
                            f"download='{r['task_id']}.svg'>"
                            f"download the .svg source</a></p>")
        elif ttype == "quant":
            body.append(_quant_section(r))
        elif ttype == "scheduling":
            body.append(_sched_section(r))
        else:
            body.append("<pre>" + html.escape(json.dumps(
                d, ensure_ascii=False, default=str)[:4000]) + "</pre>")

    body.append("<footer>Generated by WL-Benchmark — "
                f"<a href='{DOCS_URL}' target='_blank' rel='noopener'>docs"
                f"</a></footer>")
    return ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>WL-Benchmark · {html.escape(run_id)}</title>"
            f"<style>{CSS}</style></head><body>"
            + "".join(body) + "</body></html>")


INDEX_HTML = """<!DOCTYPE html><html><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>WL-Benchmark — runs</title><style>__CSS__</style></head><body>
<h1>WL-Benchmark</h1>
<p class='meta'>Agentic AI benchmark — essay writing with rubric, SVG
drawing, agentic scheduling with opaque tools, multi-factor quant mining.
One self-contained page per evaluation run; nothing is stored on any
client machine after upload.</p>
<div id='runs'>loading…</div>
<footer>Generated by WL-Benchmark</footer>
<script>
fetch('manifest.json').then(r => r.json()).then(m => {
  const el = document.getElementById('runs');
  if (!m.runs.length) { el.textContent = 'no runs yet'; return; }
  el.innerHTML = m.runs.map(r =>
    `<div class='card'><a href='${r.file}'>${r.id}</a><br>
     <span class='meta'>${r.provider} / <b>${r.model}</b> · ${r.tasks} tasks
     · auto-avg ${r.auto_avg} · ${r.pending} pending human review
     · ${r.time}</span></div>`).join('');
}).catch(e => {
  document.getElementById('runs').textContent = 'manifest unavailable: ' + e;
});
</script></body></html>"""


def build_index() -> str:
    return INDEX_HTML.replace("__CSS__", CSS)


def manifest_entry(results: list, run_id: str) -> dict:
    prov = results[0].get("provider", "?") if results else "?"
    model = results[0].get("model", "?") if results else "?"
    scored = [r["score"] for r in results
              if not r.get("error") and r.get("score") is not None]
    return {
        "id": run_id,
        "file": f"runs/{run_id}.html",
        "time": f"{run_id[:4]}-{run_id[4:6]}-{run_id[6:8]} "
                f"{run_id[9:11]}:{run_id[11:13]}",
        "provider": prov, "model": model,
        "tasks": len(results),
        "errors": sum(1 for r in results if r.get("error")),
        "auto_avg": (round(sum(scored) / len(scored), 3)
                     if scored else "—"),
        "pending": sum(1 for r in results
                       if not r.get("error")
                       and r.get("score") is None),
    }

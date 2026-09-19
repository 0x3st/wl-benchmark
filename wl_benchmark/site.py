"""Build self-contained evaluation reports, embedded by the CLI at upload time.

The Worker stores and serves these pages unchanged. All content and PNGs are
inline; JavaScript only enhances navigation, filtering, printing and image zoom.
No model-generated SVG is inserted into the document as executable markup.
"""
from __future__ import annotations

import base64
import html
import json
import math
import os
import re

from .artifacts import run_artifacts
from .review_pdf import md_to_html
from .site_ui import CSS, SCRIPT

DOCS_URL = "https://github.com/0x3st/wl-benchmark#readme"
# Display order only. Original results and their completion indices never change.
TASK_TYPES = {
    "svg": "SVG drawing",
    "essay": "Essay writing",
    "quant": "Quantitative research",
    "scheduling": "Scheduling",
}
STAGES = {"riding": "Riding illustration", "relation": "Relationship illustration",
          "architecture": "Architecture diagram"}


def _esc(value) -> str:
    return html.escape(str(value), quote=True)


def _number(value, digits: int = 2) -> str:
    try:
        n = float(value)
        return f"{n:.{digits}f}" if math.isfinite(n) else "—"
    except (ValueError, TypeError):
        return "—"


def _b64(path: str, mime: str) -> str:
    with open(path, "rb") as f:
        return f"data:{mime};base64," + base64.b64encode(f.read()).decode()


def _files(r: dict, run_dir: str, suffix: str) -> list:
    return [a for a in run_artifacts(r, run_dir)
            if a.lower().endswith(suffix) and os.path.isfile(a)]


def _status(r: dict) -> str:
    if r.get("error"):
        return "error"
    return "pending" if r.get("score") is None else "scored"


def _badge(r: dict) -> str:
    state = _status(r)
    if state == "error":
        return "<span class='badge err'>Task failed</span>"
    if state == "pending":
        return "<span class='badge pen'>Awaiting human review</span>"
    return ("<span class='badge ok'>Scored</span>"
            f"<span class='score'>{_number(r['score'])}"
            f" <span class='meta'>/ {_number(r.get('max_score', 1))}</span></span>")


def _title(r: dict) -> str:
    task_id = str(r.get("task_id", "Untitled task"))
    if r.get("task_type") == "svg":
        return STAGES.get(r.get("detail", {}).get("stage"), task_id)
    # Keep the exact ID underneath; the heading is a readable navigation label.
    label = re.sub(r"^\d+-|-\d+$", "", task_id).replace("_", " ").replace("-", " ")
    return label[:1].upper() + label[1:]


def _table(headings: list, rows: list) -> str:
    return ("<div class='table-scroll' tabindex='0' role='region' aria-label='Result table'>"
            "<table><thead><tr>" + "".join(f"<th scope='col'>{_esc(h)}</th>" for h in headings)
            + "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>")


def _cons_table(cons: list) -> str:
    if not cons:
        return ""
    n_ok = sum(bool(c.get("ok")) for c in cons)
    rows = []
    for c in cons:
        passed = c.get("ok")
        rows.append(f"<tr><td>{_esc(c.get('constraint', ''))}</td>"
                    f"<td class='{'ok-text' if passed else 'fail-text'}'>"
                    f"{'PASS' if passed else 'FAIL'}</td><td>{_esc(c.get('detail', ''))}</td></tr>")
    return (f"<details class='checks'{' open' if n_ok < len(cons) else ''}>"
            f"<summary>Format checks <span class='{'ok-text' if n_ok == len(cons) else 'fail-text'}'>"
            f"{n_ok}/{len(cons)} passed</span></summary>"
            "<p class='meta'>Structural checks are not a human quality score.</p>"
            + _table(["Constraint", "Result", "Detail"], rows) + "</details>")


def _md_artifacts(r: dict, run_dir: str) -> str:
    out = []
    for a in _files(r, run_dir, ".md"):
        with open(a, encoding="utf-8") as f:
            out.append(md_to_html(f.read()))
    return "\n".join(out)


def _reading(r: dict, run_dir: str, label: str) -> str:
    content = _md_artifacts(r, run_dir)
    if not content and isinstance(r.get("detail", {}).get("note"), str):
        content = md_to_html(r["detail"]["note"])
    if not content:
        return ("<div class='empty-state'><strong>Text artifact unavailable</strong>"
                "<p>No readable Markdown artifact was found for this task.</p></div>")
    downloads = "".join(
        f"<a class='button' href='{_b64(a, 'text/plain;charset=utf-8')}' "
        f"download='{_esc(os.path.basename(a))}'>Download Markdown</a>"
        for a in _files(r, run_dir, ".md"))
    return (f"<details class='reading' open><summary>{_esc(label)}"
            " <span class='meta'>Full model output · click to collapse</span></summary>"
            f"<div class='prose'>{content}</div>"
            f"<div class='artifact-actions'>{downloads}</div></details>")


def _img_artifact(r: dict, run_dir: str, image_id: str = "preview") -> str:
    pngs = _files(r, run_dir, ".png")
    if not pngs:
        return ""
    return (f"<img id='{_esc(image_id)}' src='{_b64(pngs[0], 'image/png')}' "
            f"alt='{_esc(_title(r))} — PNG preview' loading='eager' decoding='sync'>")


def _svg_section(r: dict, run_dir: str, anchor: str) -> str:
    d = r.get("detail", {})
    picks = "".join(f"<span class='pick'><span>{_esc(k)}</span>{_esc(v)}</span>"
                    for k, v in d.get("picks", {}).items())
    h = f"<div class='picks'>{picks}</div>" if picks else ""
    pngs, svgs = _files(r, run_dir, ".png"), _files(r, run_dir, ".svg")
    if pngs:
        h += ("<figure class='artwork'>" + _img_artifact(r, run_dir, anchor + "-image")
              + "<figcaption>PNG review artifact · fixed pixels, no executable SVG content</figcaption></figure>")
    else:
        h += ("<div class='empty-state'><strong>PNG preview unavailable</strong>"
              "<p>The drawing could not be rasterized or its PNG artifact is missing. "
              "This is a preview problem, not a quality score. "
              "Rebuild the report after rendering the SVG with librsvg or Chrome.</p></div>")
    if d.get("raster_blank"):
        h += ("<p class='callout warning'>The renderer flagged this PNG as possibly blank. "
              "Inspect it and the source before reviewing.</p>")
    if d.get("raster_error"):
        h += ("<details class='checks'><summary>Rasterization diagnostic</summary>"
              f"<pre>{_esc(d['raster_error'])}</pre></details>")
    actions = []
    if pngs:
        actions.append(f"<button hidden class='button primary' data-preview-target='{anchor}-image'>"
                       "Enlarge preview</button>")
        actions.append(f"<a class='button' href='{_b64(pngs[0], 'image/png')}' "
                       f"download='{_esc(os.path.basename(pngs[0]))}'>Download PNG</a>")
    if svgs:
        actions.append(f"<a class='button' href='{_b64(svgs[0], 'image/svg+xml')}' "
                       f"download='{_esc(os.path.basename(svgs[0]))}'>Download SVG source</a>")
    h += "<div class='artifact-actions'>" + "".join(actions) + "</div>"
    h += _cons_table(d.get("constraints", []))
    return h


def _quant_section(r: dict, run_dir: str) -> str:
    d = r.get("detail", {})
    auto, rows = d.get("auto_score"), d.get("per_question", [])
    h = ""
    if auto is not None:
        h += ("<div class='quant-summary'><div><div class='eyebrow'>Automatic component</div>"
              f"<strong>{_number(auto)}</strong><span class='meta'> / 1.00</span></div>"
              f"<div class='meta'>Final = {_number(100 * d.get('auto_weight', 0.8), 0)}% automatic + "
              f"{_number(100 * d.get('note_weight', 0.2), 0)}% research note. "
              "The automatic component alone is not the final task score.</div></div>")
    if rows:
        trs = []
        for q in rows:
            passed = q.get("correct")
            trs.append(f"<tr{' class=failed-row' if not passed else ''}>"
                       f"<td>{_esc(q.get('id', ''))}</td>"
                       f"<td class='num'>{_esc(q.get('given'))}</td>"
                       f"<td class='num'>{_esc(q.get('answer'))}</td>"
                       f"<td class='{'ok-text' if passed else 'fail-text'}'>"
                       f"{'PASS' if passed else 'FAIL'}</td></tr>")
        h += "<h3>Auto-scored answers</h3>" + _table(["Metric", "Model answer", "Reference", "Result"], trs)
    sampling = d.get("sampling") or {}
    if sampling:
        h += ("<div class='callout sampling'><strong>Sampling disclosure</strong>"
              f"<div>Mode: {_esc(sampling.get('mode', '—'))} · per-attempt auto scores: "
              f"{_esc(' / '.join(str(x) for x in sampling.get('attempt_scores', [])))}"
              f" · reasoning effort: {_esc(sampling.get('reasoning_effort', '—'))}</div></div>")
    return h + _reading(r, run_dir, "Research note")


def _sched_section(r: dict) -> str:
    d = r.get("detail", {})
    checks = d.get("checks", {})
    h = ""
    if checks:
        rows = [f"<tr><td>{_esc(k.replace('_', ' '))}</td>"
                f"<td class='{'ok-text' if v else 'fail-text'}'>{'PASS' if v else 'FAIL'}</td></tr>"
                for k, v in checks.items()]
        h += "<h3>Rule checks</h3>" + _table(["Rule", "Result"], rows)
    if d.get("conflicts"):
        h += ("<div class='callout error'><strong>Time conflicts</strong><ul>"
              + "".join(f"<li>{_esc(c)}</li>" for c in d["conflicts"]) + "</ul></div>")
    if d.get("enrollments"):
        h += "<h3>Enrolled plan</h3><ul>" + "".join(
            f"<li>{_esc(e)}</li>" for e in d["enrollments"]) + "</ul>"
    if not h:
        h = "<p class='meta'>No scheduling details were recorded.</p>"
    return h


def _groups(results: list) -> list:
    types = list(dict.fromkeys(str(r.get("task_type", "other")) for r in results))
    return sorted(types, key=lambda kind: list(TASK_TYPES).index(kind) if kind in TASK_TYPES else len(TASK_TYPES))


def _task_card(r: dict, i: int, group: int, run_dir: str) -> str:
    kind = str(r.get("task_type", "other"))
    anchor = f"task-{i}"
    title = _title(r)
    missing = kind == "svg" and not r.get("error") and not _files(r, run_dir, ".png")
    search = " ".join(str(x) for x in [r.get("task_id", ""), title, kind,
                                       r.get("model", ""), r.get("provider", "")]).lower()
    h = (f"<article class='task-card' id='{anchor}' data-kind='{_esc(kind)}' "
         f"data-order='{i}' data-group='{group}' data-status='{_status(r)}' "
         f"data-preview='{'missing' if missing else 'ok'}' data-search='{_esc(search)}' "
         f"aria-labelledby='{anchor}-title'>"
         "<header class='task-header'><div class='task-topline'>"
         f"<span class='task-type'>{_esc(TASK_TYPES.get(kind, kind))}</span>"
         f"<span class='meta'>Finished #{i + 1:02d} · {_number(r.get('latency', 0), 1)}s</span></div>"
         f"<div class='task-title-row'><div><h2 id='{anchor}-title'>{_esc(title)}</h2>"
         f"<span class='task-id'>{_esc(r.get('task_id', ''))}</span></div>"
         f"<div class='badge-wrap'>{_badge(r)}</div></div></header><div class='task-content'>")
    if r.get("error"):
        h += ("<div class='callout error'><strong>This task did not complete</strong>"
              "<p>No quality score was assigned. The original diagnostic is preserved below.</p></div>"
              f"<pre>{_esc(r['error'])}</pre>")
    elif kind == "svg":
        h += _svg_section(r, run_dir, anchor)
    elif kind == "essay":
        h += ("<p class='meta'>Human-reviewed against the task rubric. "
              "Format checks below do not replace a quality assessment.</p>"
              + _cons_table(r.get("detail", {}).get("constraints", []))
              + _reading(r, run_dir, "Essay"))
    elif kind == "quant":
        h += _quant_section(r, run_dir)
    elif kind == "scheduling":
        h += _sched_section(r)
    else:
        h += "<pre>" + _esc(json.dumps(r.get("detail", {}), ensure_ascii=False, default=str)) + "</pre>"
    return h + "</div></article>"


def _summary(results: list) -> str:
    rows = []
    for i, r in enumerate(results):
        rows.append(f"<tr><td class='num'>{i + 1:02d}</td>"
                    f"<td><a href='#task-{i}'>{_esc(r.get('task_id', ''))}</a></td>"
                    f"<td>{_esc(r.get('task_type', ''))}</td><td>{_badge(r)}</td>"
                    f"<td class='num'>{_number(r.get('latency', 0), 1)}s</td></tr>")
    return ("<details class='panel completion-log'><summary>Completion log"
            "<span class='meta'>Original execution order</span></summary>"
            + _table(["#", "Task", "Type", "Status / score", "Latency"], rows) + "</details>")


def build_run_page(results: list, run_id: str, run_dir: str = "") -> str:
    """One portable HTML report; grouped display never mutates input results."""
    prov = results[0].get("provider", "Unknown") if results else "Unknown"
    model = results[0].get("model", "Unknown model") if results else "No results"
    n_err = sum(_status(r) == "error" for r in results)
    scored = [r["score"] for r in results if _status(r) == "scored"]
    pending = sum(_status(r) == "pending" for r in results)
    avg = _number(sum(scored) / len(scored), 3) if scored else "—"
    groups = _groups(results)
    grouped = sorted(enumerate(results), key=lambda pair: groups.index(str(pair[1].get("task_type", "other"))))
    nav, tabs = [], [f"<button class='tab' data-filter='all' aria-pressed='true'>All tasks"
                     f"<span class='count'>{len(results)}</span></button>"]
    for kind in groups:
        members = [(i, r) for i, r in enumerate(results) if str(r.get("task_type", "other")) == kind]
        label = TASK_TYPES.get(kind, kind)
        nav.append(f"<div class='nav-heading'><span>{_esc(label)}</span><span>{len(members)}</span></div>")
        tabs.append(f"<button class='tab' data-filter='{_esc(kind)}' aria-pressed='false'>{_esc(kind.title())}"
                    f"<span class='count'>{len(members)}</span></button>")
        for i, r in members:
            state = {"pending": "Human review", "scored": "Scored", "error": "Task failed"}[_status(r)]
            if kind == "svg" and not r.get("error") and not _files(r, run_dir, ".png"):
                state += " · no preview"
            nav.append(f"<a class='nav-link' href='#task-{i}'><span class='nav-number'>{i + 1:02d}</span>"
                       f"<span>{_esc(_title(r))}<small>{state}</small></span></a>")
    cards = "".join(_task_card(r, i, groups.index(str(r.get("task_type", "other"))), run_dir)
                    for i, r in grouped)
    stats = "".join(
        f"<div class='stat {cls}'><span class='stat-label'>{label}</span>"
        f"<span class='stat-value'>{value}</span><span class='stat-note'>{note}</span></div>"
        for cls, label, value, note in [
            ("", "TASK RESULTS", len(results), f"{len(groups)} task types"),
            ("", "SCORED TASKS", len(scored), f"Recorded score mean: {avg}"),
            ("warn" if pending else "", "HUMAN REVIEW", pending, "Final score not assigned"),
            ("danger" if n_err else "", "TASK ERRORS", n_err, "Excluded from score mean"),
        ])
    empty = ("<div class='empty-state'><strong>No task results yet</strong>"
             "<p>This report contains no recorded tasks.</p></div>" if not results else "")
    return ("<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{_esc(model)} · {_esc(run_id)} · WL-Benchmark</title><style>{CSS}</style></head><body>"
            "<a class='skip-link' href='#report-content'>Skip to report</a>"
            "<div class='topbar'><div class='topbar-inner'>"
            "<a class='brand' href='/'><span class='brand-mark'>WL</span>BENCHMARK</a>"
            "<div class='top-links'><a href='/'>All runs ↗</a>"
            f"<a class='docs-link' href='{DOCS_URL}' target='_blank' rel='noopener'>Documentation</a>"
            "<button class='button js-only' id='print-report'>Print report</button></div></div></div>"
            "<div class='shell'><header class='hero'><div>"
            f"<p class='eyebrow'>Evaluation report / {_esc(run_id)}</p><h1>{_esc(model)}</h1>"
            f"<p class='meta'>Provider: {_esc(prov)} · Run: <code>{_esc(run_id)}</code></p></div>"
            "<aside class='hero-note'><strong>Evidence before a leaderboard.</strong>"
            "Inspect the model outputs, check automated results, then review what requires human judgment.</aside></header>"
            f"<section class='stats' aria-label='Run overview'>{stats}</section>"
            "<p class='score-note'>The mean includes only tasks with a recorded score; "
            "it is not an overall benchmark score. Partial automatic scores are shown inside each task.</p>"
            "<div class='workspace'><aside class='sidebar'><details id='directory' open>"
            "<summary class='eyebrow'>Task directory</summary><nav aria-label='Task directory'>"
            + "".join(nav) + "</nav></details></aside><main class='main-column' id='report-content'>"
            + _summary(results)
            + "<section class='toolbar js-only' aria-label='Report controls'><div class='tabs' aria-label='Task type'>"
            + "".join(tabs) + "</div><div class='filter-row'>"
            "<label class='field search' for='task-search'>Find a task"
            "<input id='task-search' type='search' placeholder='Search task, model or type…'></label>"
            "<label class='field' for='status-filter'>Status<select id='status-filter'>"
            "<option value='all'>Any status</option><option value='pending'>Human review</option>"
            "<option value='scored'>Scored</option><option value='error'>Task errors</option>"
            "<option value='preview'>Missing PNG preview</option></select></label>"
            "<label class='field' for='task-order'>Display order<select id='task-order'>"
            "<option value='group'>Task groups</option><option value='completion'>Completion order</option>"
            "</select></label></div><p class='results-count' id='results-count' role='status' aria-live='polite'></p></section>"
            "<div id='no-matches' class='empty-state' hidden><strong>No matching tasks</strong>"
            "<p>Try another search or filter.</p><button class='button' id='reset-filters'>Reset filters</button></div>"
            f"<div id='task-list'>{cards}{empty}</div></main></div>"
            "<footer class='footer'>WL-Benchmark · Self-contained report · "
            "Original completion indices preserved · No external fonts, scripts or image requests</footer></div>"
            "<dialog id='image-dialog' aria-labelledby='zoom-title'><div class='dialog-head'>"
            "<h2 id='zoom-title'>PNG preview</h2><button class='button' id='zoom-close'>Close</button></div>"
            "<button class='button' id='zoom-toggle' aria-pressed='false'>Original pixel size</button>"
            "<p class='zoom-hint'>Review the fixed PNG artifact. Press Escape to close.</p>"
            "<div class='zoom-canvas' id='zoom-canvas'><img id='zoom-image' alt=''></div></dialog>"
            f"<script>{SCRIPT}</script></body></html>")


# Compatibility index for static exports. The live Worker has its own run index
# (site/src/worker.js) sharing the same design language.
INDEX_HTML = """<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>WL-Benchmark — evaluation archive</title><style>__CSS__</style></head><body>
<div class='topbar'><div class='topbar-inner'>
<span class='brand'><span class='brand-mark'>WL</span>BENCHMARK</span></div></div>
<main class='shell'><header class='hero'>
<p class='eyebrow'>Evaluation archive</p><h1>WL-Benchmark</h1>
<p class='meta'>One self-contained, human-reviewable report per evaluation run.</p></header>
<section class='stats' aria-label='Archive overview' id='stats'></section>
<div id='runs' role='status'>Loading runs…</div></main>
<footer class='footer'>Automatic score components are not final human review scores.</footer>
<script>
fetch('manifest.json').then(r => r.json()).then(m => {
  const runs = (m.runs || []).filter(r => /^[A-Za-z0-9_-]{4,64}$/.test(r.id));
  const num = v => { const n = Number(v); return Number.isFinite(n) ? n : 0; };
  const totals = runs.reduce((a, r) => ({
    tasks: a.tasks + num(r.tasks), pending: a.pending + num(r.pending),
    errors: a.errors + num(r.errors)}), {tasks: 0, pending: 0, errors: 0});
  const stats = [['', 'Evaluation runs', runs.length],
    ['', 'Task results', totals.tasks],
    ['warn', 'Awaiting human review', totals.pending],
    ['danger', 'Task errors', totals.errors]];
  const statBox = document.getElementById('stats');
  stats.forEach(([cls, label, value]) => {
    const div = document.createElement('div'); div.className = 'stat ' + cls;
    const l = document.createElement('span'); l.className = 'stat-label';
    l.textContent = label;
    const v = document.createElement('span'); v.className = 'stat-value';
    v.textContent = value;
    div.append(l, v); statBox.append(div);
  });
  const el = document.getElementById('runs');
  el.textContent = runs.length ? '' : 'No runs yet.';
  el.className = runs.length ? 'grid' : 'empty-state';
  runs.slice().reverse().forEach(r => {
    const card = document.createElement('a');
    card.className = 'run-card'; card.href = 'runs/' + r.id + '.html';
    const rid = document.createElement('span'); rid.className = 'run-id';
    rid.textContent = r.id + ' · ' + r.time;
    const model = document.createElement('span'); model.className = 'run-model';
    model.textContent = r.model;
    const meta = document.createElement('span'); meta.className = 'run-meta meta';
    meta.textContent = r.provider + ' · ' + num(r.tasks) + ' tasks · recorded mean ' + r.auto_avg;
    card.append(rid, model, meta);
    if (num(r.pending)) {
      const b = document.createElement('span'); b.className = 'badge pen';
      b.textContent = num(r.pending) + ' awaiting review'; card.append(b);
    }
    if (num(r.errors)) {
      const b = document.createElement('span'); b.className = 'badge err';
      b.textContent = num(r.errors) + ' task errors'; card.append(b);
    }
    el.append(card);
  });
}).catch(e => { document.getElementById('runs').textContent = 'Manifest unavailable: ' + e; });
</script></body></html>"""


def build_index() -> str:
    return INDEX_HTML.replace("__CSS__", CSS)


def manifest_entry(results: list, run_id: str) -> dict:
    """Keep the upload metadata contract stable for existing Worker indices."""
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
        "auto_avg": round(sum(scored) / len(scored), 3) if scored else "—",
        "pending": sum(1 for r in results if not r.get("error") and r.get("score") is None),
    }

"""Compile a finished run into a single human-review PDF.

One PDF per run: summary table, then one section per task —
- tool_use / scheduling: auto score + what the model did
- essay: the rubric (image embedded or markdown converted) + the full essay
  + a blank scoring box
- svg: the instruction + the rendered PNG + a blank scoring box

Rendered with headless Chrome (same dependency as SVG rasterization).
"""
from __future__ import annotations

import base64
import html
import json
import os
import re
import shutil
import subprocess
import tempfile
from typing import Optional

CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)


def _chrome() -> str:
    for cand in CHROME_CANDIDATES:
        if os.path.exists(cand):
            return cand
    which = shutil.which("chromium") or shutil.which("google-chrome")
    if which:
        return which
    raise RuntimeError("Chrome/Chromium not found (required to render the review PDF)")


# ------------------------------------------------------------ minimal md → html
def _inline(s: str) -> str:
    s = html.escape(s, quote=False)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", s)
    return s


def md_to_html(md: str) -> str:
    """Tiny markdown subset renderer: headings, bold/italic/code, lists,
    pipe tables, fenced code, hr, paragraphs. Good enough for review."""
    out, i = [], 0
    lines = md.splitlines()
    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            i += 1
            buf = []
            while i < len(lines) and not lines[i].startswith("```"):
                buf.append(lines[i]); i += 1
            i += 1
            out.append("<pre><code>" + html.escape("\n".join(buf)) + "</code></pre>")
            continue
        if re.match(r"^#{1,4} ", line):
            level = len(line) - len(line.lstrip("#"))
            out.append(f"<h{level+1}>{_inline(line.lstrip('#').strip())}</h{level+1}>")
            i += 1
            continue
        if re.match(r"^(-{3,}|\*{3,})$", line.strip()):
            out.append("<hr>")
            i += 1
            continue
        if line.lstrip().startswith("|") and i + 1 < len(lines) \
                and re.match(r"^\s*\|[\s:|-]+\|\s*$", lines[i + 1]):
            rows = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not re.match(r"^[\s:|-]+$", lines[i]):
                    rows.append(cells)
                i += 1
            t = ["<table>", "<tr>" + "".join(f"<th>{_inline(c)}</th>" for c in rows[0]) + "</tr>"]
            for r in rows[1:]:
                t.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>")
            out.append("".join(t) + "</table>")
            continue
        if re.match(r"^\s*[-*] ", line):
            buf = []
            while i < len(lines) and re.match(r"^\s*[-*] ", lines[i]):
                buf.append("<li>" + _inline(re.sub(r"^\s*[-*] ", "", lines[i])) + "</li>")
                i += 1
            out.append("<ul>" + "".join(buf) + "</ul>")
            continue
        if re.match(r"^\s*\d+\. ", line):
            buf = []
            while i < len(lines) and re.match(r"^\s*\d+\. ", lines[i]):
                buf.append("<li>" + _inline(re.sub(r"^\s*\d+\. ", "", lines[i])) + "</li>")
                i += 1
            out.append("<ol>" + "".join(buf) + "</ol>")
            continue
        if not line.strip():
            i += 1
            continue
        buf = [line]
        while i + 1 < len(lines) and lines[i + 1].strip() \
                and not re.match(r"^(```|#{1,4} |-{3,}|\s*[-*] |\s*\d+\. |\|)", lines[i + 1]):
            i += 1
            buf.append(lines[i])
        out.append("<p>" + _inline(" ".join(buf)) + "</p>")
        i += 1
    return "\n".join(out)


def _img64(path: str) -> str:
    with open(path, "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode()


def _artifact(run_dir: str, rel: str) -> str:
    return os.path.join(run_dir, rel)


# ------------------------------------------------------------------ sections
def _score_box(max_note: str = "100") -> str:
    return (f"<div class='scorebox'>Human score: ______ / {max_note} "
            f"&nbsp;&nbsp; Reviewer: ____________ &nbsp;&nbsp; "
            f"Notes: ______________________________________________</div>")


def _task_section(r: dict, run_dir: str) -> str:
    tid, ttype = r["task_id"], r["task_type"]
    h = [f"<div class='task'>",
         f"<h2>[{ttype}] {html.escape(tid)}</h2>"]
    if r.get("error"):
        h.append(f"<p class='err'>ERROR: {html.escape(str(r['error']))}</p></div>")
        return "".join(h)

    d = r.get("detail", {})
    if ttype in ("tool_use", "scheduling"):
        h.append(f"<p class='auto'>Auto score: <b>{r['score']:.2f}</b> "
                 f"({r['latency']:.1f}s)</p>")
        if ttype == "tool_use":
            for c in d.get("calls", []):
                args = ", ".join(f"{k}={str(v)[:60]}" for k, v in c["args"].items())
                h.append(f"<p><code>{html.escape(c['tool'])}({html.escape(args)})</code></p>")
            if not d.get("calls"):
                h.append("<p><i>No tool calls were made.</i></p>")
        else:
            h.append("<p>Chosen sections: <b>"
                     + html.escape(json.dumps(d.get("answer", {})))
                     + "</b></p>")
            h.append("<p>Conflicts found: <b>"
                     + html.escape(json.dumps(d.get("conflicts", []), ensure_ascii=False))
                     + "</b></p>")
        for a in r.get("artifacts", []):
            if a.endswith(".md") and os.path.exists(a):
                with open(a, encoding="utf-8") as f:
                    h.append(md_to_html(f.read()))
    elif ttype == "essay":
        h.append(_score_box())
        rub = d.get("rubric_file")
        if rub:
            h.append(f"<p class='meta'>Rubric (for the reviewer): "
                     f"{html.escape(os.path.relpath(rub))} — the model "
                     f"received it as an image (multimodal test).</p>")
        cons = d.get("constraints", [])
        if cons:
            n_ok = sum(1 for c in cons if c["ok"])
            h.append(f"<h3>HARD CONSTRAINTS — {n_ok}/{len(cons)} passed "
                     f"(each violation: reviewer judgement, suggested "
                     f"-5)</h3>")
            h.append("<table><tr><th>constraint</th><th>result</th>"
                     "<th>detail</th></tr>")
            for c in cons:
                h.append(f"<tr><td>{html.escape(str(c['constraint']))}</td>"
                         f"<td>{'PASS' if c['ok'] else 'FAIL'}</td>"
                         f"<td>{html.escape(str(c['detail']))}</td></tr>")
            h.append("</table>")
        h.append("<h3>ESSAY</h3>")
        for a in r.get("artifacts", []):
            if a.endswith(".md") and os.path.exists(a):
                with open(a, encoding="utf-8") as f:
                    h.append(md_to_html(f.read()))
    elif ttype == "svg":
        picks = d.get("picks", {})
        h.append("<p>Random picks: <b>" + html.escape(json.dumps(picks, ensure_ascii=False))
                 + "</b></p>")
        h.append(_score_box())
        cons = d.get("constraints", [])
        if cons:
            n_ok = sum(1 for c in cons if c["ok"])
            h.append(f"<h3>HARD CONSTRAINTS — {n_ok}/{len(cons)} passed "
                     f"(each violation: reviewer judgement, suggested "
                     f"-5)</h3>")
            h.append("<table><tr><th>constraint</th><th>result</th>"
                     "<th>detail</th></tr>")
            for c in cons:
                h.append(f"<tr><td>{html.escape(str(c['constraint']))}</td>"
                         f"<td>{'PASS' if c['ok'] else 'FAIL'}</td>"
                         f"<td>{html.escape(str(c['detail']))}</td></tr>")
            h.append("</table>")
        for a in r.get("artifacts", []):
            if a.endswith(".png") and os.path.exists(a):
                h.append(f"<img src='file://{a}' "
                         f"style='max-width:100%;border:1px solid #999'>")
        rerr = d.get("raster_error")
        if rerr:
            h.append(f"<p class='meta'>raster error: {html.escape(rerr)}</p>")
    elif ttype == "quant":
        auto = r.get("detail", {}).get("auto_score")
        rows = r.get("detail", {}).get("per_question", [])
        if rows:
            h.append("<h3>AUTO-SCORED ANSWERS (" +
                     (f"auto score {auto:.2f}" if auto is not None else "") +
                     ")</h3>")
            h.append("<table><tr><th>id</th><th>given</th><th>answer</th>"
                     "<th>correct</th></tr>")
            for row in rows:
                h.append(f"<tr><td>{html.escape(str(row['id']))}</td>"
                         f"<td>{html.escape(str(row['given']))}</td>"
                         f"<td>{html.escape(str(row['answer']))}</td>"
                         f"<td>{'OK' if row['correct'] else 'X'}</td></tr>")
            h.append("</table>")
        if auto is not None:
            w_auto = r.get("detail", {}).get("auto_weight", 0.8)
            w_note = r.get("detail", {}).get("note_weight", 0.2)
            h.append(f"<p class='meta'>Final = {w_auto:.0%} x auto ({auto:.2f}) "
                     f"+ {w_note:.0%} x note score.</p>")
        h.append("<h3>RESEARCH NOTE</h3>")
        for a in r.get("artifacts", []):
            if a.endswith(".md") and os.path.exists(a):
                with open(a, encoding="utf-8") as f:
                    h.append(md_to_html(f.read()))
        h.append(_score_box("100 (note)"))
    h.append("</div>")
    return "".join(h)


# --------------------------------------------------------------------- html
CSS = """
@page { size: A4; margin: 0; }
body { font-family: "PingFang SC","Hiragino Sans GB",Georgia,serif; font-size: 12px;
       line-height: 1.65; color: #24292f; max-width: 760px; margin: 0 auto; padding: 40px 48px; }
h1 { font-size: 22px; border-bottom: 3px solid #0969da; padding-bottom: 8px; }
h2 { font-size: 16px; border-bottom: 1px solid #d0d7de; padding-bottom: 4px; margin: 24px 0 10px; color: #0969da; }
h3 { font-size: 13.5px; margin: 14px 0 6px; color: #333; }
table { border-collapse: collapse; width: 100%; margin: 10px 0; font-size: 11px; }
th { background: #f0f4fa; text-align: left; }
th, td { border: 1px solid #d0d7de; padding: 4px 8px; }
img { max-width: 100%; border: 1px solid #eee; margin: 8px 0; }
pre { background: #f6f8fa; padding: 10px 12px; border-radius: 6px; font-size: 10.5px; overflow-x: hidden; }
code { font-family: Menlo,monospace; font-size: 10.5px; background: #f0f2f5; padding: 1px 4px; border-radius: 3px; }
pre code { background: none; padding: 0; }
.task { page-break-before: always; }
.scorebox { border: 2px solid #0969da; border-radius: 6px; padding: 10px 14px; margin: 12px 0;
            font-weight: bold; background: #f0f6ff; }
.auto { background: #e6f4ea; border-radius: 4px; padding: 6px 10px; }
.err { color: #c62828; font-weight: bold; }
.meta { color: #666; font-size: 11px; }
"""


def build_review_pdf(run_dir: str, out_path: Optional[str] = None) -> str:
    """Compile results.json + artifacts into <run_dir>/review.pdf."""
    with open(os.path.join(run_dir, "results.json"), encoding="utf-8") as f:
        results = json.load(f)

    prov = results[0]["provider"] if results else "?"
    model = results[0]["model"] if results else "?"
    h = [f"<h1>WL-Benchmark — Human Review Sheet</h1>",
         f"<p class='meta'>provider: {html.escape(str(prov))} &nbsp;|&nbsp; "
         f"model: {html.escape(str(model))} &nbsp;|&nbsp; run: "
         f"{html.escape(os.path.basename(run_dir))}</p>"]

    # summary table
    from .reporter import summarize
    h.append(md_to_html(summarize(os.path.join(run_dir, "results.json"))))

    for r in results:
        h.append(_task_section(r, run_dir))

    html_doc = (f"<!DOCTYPE html><html><head><meta charset='utf-8'>"
                f"<style>{CSS}</style></head><body>" + "\n".join(h) +
                "</body></html>")

    chrome = _chrome()
    out_path = out_path or os.path.join(run_dir, "review.pdf")
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False,
                                     encoding="utf-8") as f:
        f.write(html_doc)
        tmp = f.name
    try:
        subprocess.run(
            [chrome, "--headless", "--disable-gpu", "--no-pdf-header-footer",
             f"--print-to-pdf={os.path.abspath(out_path)}",
             "file://" + os.path.abspath(tmp)],
            check=True, capture_output=True, timeout=120)
    finally:
        os.remove(tmp)
    return out_path

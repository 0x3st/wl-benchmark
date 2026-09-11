"""Markdown reporter: aggregate results.json into a readable summary.

essay / svg use human review: the score column shows "pending human review"
and lists artifact paths for the reviewer.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict


def summarize(results_path: str) -> str:
    with open(results_path, encoding="utf-8") as f:
        results = json.load(f)

    lines = ["# WL-Benchmark Run Summary", ""]

    by_model = defaultdict(list)
    for r in results:
        by_model[(r["provider"], r["model"])].append(r)

    for (prov, model), rs in sorted(by_model.items()):
        lines += [f"## {prov} / {model}", "",
                  "| task | type | score | latency | artifacts | error |",
                  "|---|---|---|---|---|---|"]
        for r in rs:
            if r["error"]:
                score = "ERR"
            elif r["score"] is None:
                score = "pending human review"
            else:
                score = f"{r['score']:.2f}"
            lat = f"{r['latency']}s" if r.get("latency") else "-"
            arts = "; ".join(os.path.relpath(a) for a in r.get("artifacts", []))
            arts = arts.replace("|", "\\|") or "-"
            err = (r["error"] or "")[:60].replace("|", "\\|")
            lines.append(f"| {r['task_id']} | {r['task_type']} | {score} "
                         f"| {lat} | {arts} | {err} |")

        scored = [r["score"] for r in rs
                  if not r["error"] and r["score"] is not None]
        manual = [r for r in rs if not r["error"] and r["score"] is None]
        if scored:
            avg = sum(scored) / len(scored)
            lines += ["", f"**Auto-scored average: {avg:.3f} "
                          f"({len(scored)} auto + {len(manual)} pending human "
                          f"review)**"]
        elif manual:
            lines += ["", f"**All {len(manual)} tasks pending human review**"]
        lines.append("")

    return "\n".join(lines)


def write_report(run_dir: str) -> str:
    src = os.path.join(run_dir, "results.json")
    dst = os.path.join(run_dir, "summary.md")
    with open(dst, "w", encoding="utf-8") as f:
        f.write(summarize(src))
    return dst

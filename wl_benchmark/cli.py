"""CLI entry: wl-bench {run|list-tasks|report|doctor}

`run` / `doctor` interactively ask for the target under test
(endpoint / key / model) every time — the tool stores no provider presets.
Use --endpoint/--key/--model flags to skip the interactive prompts.

Run as:  ./wl-bench <cmd>   |   python3 -m wl_benchmark <cmd>
         |   wl-bench <cmd> (after pip install)
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import urllib.request

from .publisher import (cleanup_run, load_site_config, prompt_site_config,
                        publish_run)
from .reporter import write_report
from .review_pdf import build_review_pdf
from .runner import run_all
from .tasks import TASK_TYPES, build_tasks

PKG_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_TASKS_ROOT = os.path.join(PKG_DIR, "tasks_data")
DEFAULT_CONFIG = os.path.join("config", "bench.json")   # optional run params
VERSION = "0.4.0"
BRAND = "WL-Benchmark"


# ------------------------------------------------------- interactive input
def _list_models(base_url: str, api_key: str) -> list:
    try:
        req = urllib.request.Request(
            base_url.rstrip("/") + "/models",
            headers={"Authorization": f"Bearer {api_key}"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return [m.get("id") for m in json.load(r).get("data", []) if m.get("id")]
    except Exception:
        return []


def _prompt_provider(args) -> dict:
    """Interactively collect endpoint / key / model.
    --endpoint/--key/--model flags skip the matching prompt."""
    print(f"{BRAND} — target under test (nothing is stored)")
    base_url = (args.endpoint or "").strip()
    while not base_url.startswith(("http://", "https://")):
        base_url = input("Endpoint (OpenAI-compatible, e.g. "
                         "https://api.example.com/v1): ").strip()

    api_key = (args.key or "").strip()
    while not api_key:
        api_key = getpass.getpass("API key (input hidden): ").strip()

    model = (args.model or "").strip()
    if not model:
        models = _list_models(base_url, api_key)
        if models:
            print(f"The endpoint offers {len(models)} models:")
            for i, m in enumerate(models, 1):
                print(f"  {i:2d}. {m}")
            sel = input("Pick a number, or type a model name: ").strip()
            if sel.isdigit() and 1 <= int(sel) <= len(models):
                model = models[int(sel) - 1]
            elif sel:
                model = sel
    while not model:
        model = input("Model: ").strip()

    return {"name": model, "base_url": base_url, "api_key": api_key,
            "model": model}


def _load_run_cfg(path: str) -> dict:
    """Optional run-parameter overrides; sensible defaults otherwise."""
    cfg = {"tasks_data_root": DEFAULT_TASKS_ROOT,
           "rubric_modality": "auto",
           "max_tokens": 2048, "essay_max_tokens": 4096,
           "svg_max_tokens": 16384, "scheduling_max_tokens": 4096,
           "scheduling_max_turns": 16, "quant_max_turns": 16,
           "quant_max_tokens": 8192,
           "temperature": 0.2, "timeout": 300}
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        cfg["tasks_data_root"] = raw.get("tasks_data_root",
                                         cfg["tasks_data_root"])
        cfg.update(raw.get("run", {}))
    return cfg


def cmd_run(args) -> None:
    provider = _prompt_provider(args)
    run_cfg = _load_run_cfg(args.config)
    out_dir = run_all(provider, run_cfg,
                      only_types=args.tasks.split(",") if args.tasks else None,
                      out_root=args.out)
    summary = write_report(out_dir)
    print(f"[cli] summary -> {summary}")
    _maybe_publish(out_dir, keep=args.keep, skip=args.no_upload)


def _maybe_publish(run_dir: str, keep: bool = False, skip: bool = False) -> None:
    """Default flow: upload the run to the benchmark site, then wipe the
    local data. Falls back to keeping everything if upload is skipped or
    fails (a later `wlb publish <run-dir>` can retry)."""
    run_id = os.path.basename(run_dir.rstrip("/"))
    if skip:
        print(f"[cli] upload skipped (--no-upload); local data kept at {run_dir}")
        return
    cfg = load_site_config()
    if cfg is None and sys.stdin.isatty():
        cfg = prompt_site_config()
    if cfg is None:
        print(f"[cli] site upload not configured — local data kept at {run_dir}")
        print("      (set WL_BENCH_URL / WL_BENCH_TOKEN, or delete the")
        print("       run dir manually)")
        return
    try:
        url = publish_run(run_dir, cfg)
    except Exception as e:  # noqa: BLE001
        print(f"[cli] UPLOAD FAILED — local data kept at {run_dir}")
        print(f"      {e}")
        print("      retry later with: wlb publish " + run_dir)
        return
    print(f"[cli] published -> {url}")
    print(f"[cli] share link: {url}")
    if keep:
        print(f"[cli] local data kept (--keep): {run_dir}")
    elif cleanup_run(run_dir):
        print(f"[cli] local run data deleted: {run_dir}")


def cmd_list_tasks(args) -> None:
    run_cfg = _load_run_cfg(args.config)
    tasks = build_tasks(run_cfg.get("tasks_data_root", DEFAULT_TASKS_ROOT),
                        run_cfg, artifacts_root="/tmp/wl-bench-list")
    print(f"{len(tasks)} task(s):")
    for t in tasks:
        extra = ""
        if t.task_type == "essay":
            extra = f"  rubric={os.path.basename(t.spec.get('rubric') or '-')}"
        elif t.task_type == "svg":
            extra = f"  stage={t.spec.get('stage')}"
        print(f"  [{t.task_type:10s}] {t.task_id}{extra}")


def cmd_publish(args) -> None:
    cfg = load_site_config()
    if cfg is None:
        cfg = prompt_site_config()
    if cfg is None:
        print("[publish] no Cloudflare configuration — nothing uploaded")
        return
    url = publish_run(args.run_dir, cfg)
    print(f"[publish] {url}")
    if args.keep:
        print(f"[publish] local data kept: {args.run_dir}")
    elif cleanup_run(args.run_dir):
        print(f"[publish] local run data deleted: {args.run_dir}")


def cmd_report(args) -> None:
    write_report(args.run_dir)
    pdf = build_review_pdf(args.run_dir)
    print(f"summary  -> {os.path.relpath(os.path.join(args.run_dir, 'summary.md'))}")
    print(f"review   -> {pdf}")


def cmd_doctor(args) -> None:
    """Connectivity check: interactive too; runs no tasks, stores nothing."""
    provider = _prompt_provider(args)
    models = _list_models(provider["base_url"], provider["api_key"])
    print(f"\nendpoint : {provider['base_url']}")
    print(f"model    : {provider['model']}")
    if models:
        found = provider["model"] in models
        print(f"reachable: YES, {len(models)} models; "
              f"model {'found' if found else 'NOT in list (may still work)'}")
    else:
        print("reachable: /models unavailable (may still work for chat)")

    run_cfg = _load_run_cfg(args.config)
    tasks = build_tasks(run_cfg.get("tasks_data_root", DEFAULT_TASKS_ROOT),
                        run_cfg, artifacts_root="/tmp/wl-bench-doctor")
    print(f"tasks    : {len(tasks)} loaded "
          f"({', '.join(sorted({t.task_type for t in tasks}))})")
    print("review   : essay/svg are graded by humans "
          "(artifacts under results/<run>/artifacts/)")
    print(f"\nverdict: {'READY' if models else 'READY (unverified)'} "
          f"— run `wlb run` to start")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(
        prog="wlb",
        description=f"{BRAND} — one provider, one model, "
                    "interactive target input")
    p.add_argument("--config", default=DEFAULT_CONFIG,
                   help="optional run-parameter JSON "
                        "(default config/bench.json; may not exist)")
    p.add_argument("-V", "--version", action="version",
                   version=f"{BRAND} {VERSION} (wl-benchmark)")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run",
                       help="ask for endpoint/key/model, then run all tasks")
    r.add_argument("--endpoint", help="skip prompt: OpenAI-compatible base_url")
    r.add_argument("--key", help="skip prompt: API key")
    r.add_argument("--model", help="skip prompt: model name")
    r.add_argument("--tasks", help=f"comma list of {TASK_TYPES}")
    r.add_argument("--out", default="results")
    r.add_argument("--no-upload", action="store_true",
                   help="do not upload to the benchmark site")
    r.add_argument("--keep", action="store_true",
                   help="keep local run data even after a successful upload")
    r.set_defaults(fn=cmd_run)

    l = sub.add_parser("list-tasks", help="list discovered tasks")
    l.set_defaults(fn=cmd_list_tasks)

    pb = sub.add_parser("publish",
                        help="upload a run dir to the benchmark site "
                             "(then delete it locally)")
    pb.add_argument("run_dir")
    pb.add_argument("--keep", action="store_true",
                    help="keep the local run dir after upload")
    pb.set_defaults(fn=cmd_publish)

    rp = sub.add_parser("report",
                        help="rebuild summary.md + review.pdf for a run dir")
    rp.add_argument("run_dir")
    rp.set_defaults(fn=cmd_report)

    d = sub.add_parser("doctor",
                       help="connectivity check (interactive, runs nothing)")
    d.add_argument("--endpoint")
    d.add_argument("--key")
    d.add_argument("--model")
    d.set_defaults(fn=cmd_doctor)

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()

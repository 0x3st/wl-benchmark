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
from .selfupdate import check_background, maybe_upgrade
from .reporter import write_report
from .review_pdf import build_review_pdf
from .runner import run_all
from .tasks import TASK_TYPES, build_tasks

PKG_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_TASKS_ROOT = os.path.join(PKG_DIR, "tasks_data")
DEFAULT_CONFIG = os.path.join("config", "bench.json")   # optional run params
try:
    from importlib.metadata import version as _pkg_version
    VERSION = _pkg_version("wl-benchmark")
except Exception:   # running from a source checkout without installation
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


def _ask_parallel(run_cfg: dict, args) -> None:
    """Interactive question: how many parallel workers (default 3)."""
    if args.jobs is not None:
        run_cfg["parallel_jobs"] = args.jobs
        return
    if not sys.stdin.isatty():
        return                                  # scripted run: keep default
    default = int(run_cfg.get("parallel_jobs", 3))
    raw = input(f"Parallel workers [{default}] (1 = sequential): ").strip()
    if raw.isdigit() and int(raw) >= 1:
        run_cfg["parallel_jobs"] = int(raw)
    else:
        run_cfg["parallel_jobs"] = default


def cmd_run(args) -> None:
    interactive = sys.stdin.isatty()
    update_th, update_box = (check_background() if interactive
                             else (None, None))

    provider = _prompt_provider(args)
    run_cfg = _load_run_cfg(DEFAULT_CONFIG)
    _ask_parallel(run_cfg, args)

    # the user spent seconds typing endpoint/key — the PyPI lookup had
    # time to finish; surface an upgrade offer before any test runs
    if update_th is not None:
        update_th.join(timeout=2.0)
        maybe_upgrade(update_box[0], interactive=interactive)
    out_dir = run_all(provider, run_cfg,
                      only_types=args.tasks.split(",") if args.tasks else None,
                      out_root=args.out)
    summary = write_report(out_dir)
    print(f"[cli] summary -> {summary}")
    _maybe_publish(out_dir, keep=args.keep, skip=args.no_upload)


def _maybe_publish(run_dir: str, keep: bool = False, skip: bool = False) -> None:
    """Ask before uploading (nothing is sent automatically). On success
    print the share link and wipe the local data; on decline keep
    everything locally (`wlb publish <run-dir>` uploads later)."""
    run_id = os.path.basename(run_dir.rstrip("/"))
    if skip:
        print(f"[cli] upload skipped (--no-upload); local data kept at {run_dir}")
        return
    n = len(json.load(open(os.path.join(run_dir, "results.json"))))
    print(f"\n[cli] run complete: {n} task results in {run_dir}")
    if not sys.stdin.isatty():
        print("[cli] non-interactive session — upload skipped; local data kept")
        print(f"      upload later with: wlb publish {run_dir}")
        return
    ans = input("Upload results to the benchmark platform now? [Y/n] ").strip().lower()
    if ans in ("n", "no"):
        print(f"[cli] local data kept at {run_dir}")
        print(f"      upload later with: wlb --upload {run_dir}")
        return
    cfg = load_site_config()
    if cfg is None:
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



def do_upload(run_dir: str, keep: bool = False) -> None:
    """--upload RUN_DIR: send a local run to the platform (retry path)."""
    cfg = load_site_config()
    if cfg is None:
        cfg = prompt_site_config()
    if cfg is None:
        print("[upload] no platform configuration — nothing sent")
        return
    try:
        url = publish_run(run_dir, cfg)
    except Exception as e:  # noqa: BLE001
        print(f"[upload] FAILED — local data kept at {run_dir}")
        print(f"         {e}")
        raise SystemExit(1)
    print(f"[cli] share link: {url}")
    if keep:
        print(f"[cli] local data kept: {run_dir}")
    elif cleanup_run(run_dir):
        print(f"[cli] local run data deleted: {run_dir}")


def do_report(run_dir: str) -> None:
    """--report RUN_DIR: rebuild summary.md + review.pdf locally."""
    write_report(run_dir)
    pdf = build_review_pdf(run_dir)
    print(f"summary -> {os.path.relpath(os.path.join(run_dir, 'summary.md'))}")
    print(f"review  -> {pdf}")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(
        prog="wlb", add_help=False,
        description=f"{BRAND} — just run `wlb`; -V prints the version")
    p.add_argument("-V", "--version", action="version",
                   version=f"wl-benchmark {VERSION}")
    # power options — undocumented on purpose, the guided flow is the surface
    p.add_argument("--endpoint", help=argparse.SUPPRESS)
    p.add_argument("--key", help=argparse.SUPPRESS)
    p.add_argument("--model", help=argparse.SUPPRESS)
    p.add_argument("--tasks", help=argparse.SUPPRESS)
    p.add_argument("--out", default="results", help=argparse.SUPPRESS)
    p.add_argument("--jobs", type=int, default=None, help=argparse.SUPPRESS)
    p.add_argument("--no-upload", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--keep", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--upload", metavar="RUN_DIR", help=argparse.SUPPRESS)
    p.add_argument("--report", metavar="RUN_DIR", help=argparse.SUPPRESS)
    args, extra = p.parse_known_args(argv)
    if extra:
        p.error(f"unknown arguments: {' '.join(extra)} — just run `wlb`; "
                f"-V prints the version")

    if args.upload:
        do_upload(args.upload, keep=args.keep)
        return
    if args.report:
        do_report(args.report)
        return

    try:
        cmd_run(args)
    except KeyboardInterrupt:
        print("\n[cli] interrupted — nothing was uploaded, local data kept")
        raise SystemExit(130)
    except EOFError:
        print("\n[cli] input closed — nothing was uploaded, local data kept")
        raise SystemExit(130)



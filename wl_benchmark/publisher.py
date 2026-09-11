"""Publisher: upload a finished run to the WL-Benchmark platform
(a Cloudflare Worker, see site/), then wipe the local run directory.

Default flow of `wlb run`:
  1. build one self-contained page for the new run (images inlined);
  2. POST it to  <site>/api/runs  (Bearer upload token);
  3. print the share link  <site>/r/<run-id>;
  4. delete the local run directory — the platform is the single
     source of truth.

Configuration (config/site.json, gitignored; env vars win):
  WL_BENCH_URL    e.g. https://benchmark.wulei.org
  WL_BENCH_TOKEN  the platform's UPLOAD_TOKEN
"""
from __future__ import annotations

import json
import os
import re
import shutil
import urllib.request

from .site import build_run_page, manifest_entry

SITE_CONFIG = os.path.join("config", "site.json")


# --------------------------------------------------------------- config
def load_site_config() -> dict | None:
    cfg = {}
    if os.path.exists(SITE_CONFIG):
        try:
            with open(SITE_CONFIG, encoding="utf-8") as f:
                cfg = json.load(f)
        except json.JSONDecodeError:
            cfg = {}
    cfg["url"] = os.environ.get("WL_BENCH_URL", cfg.get("url", "")).rstrip("/")
    cfg["token"] = os.environ.get("WL_BENCH_TOKEN", cfg.get("token", ""))
    if cfg["url"] and cfg["url"].startswith(("http://", "https://")):
        return cfg
    return None


def prompt_site_config() -> dict | None:
    """Interactive first-time setup; returns None if the user declines."""
    print("\n[publish] the benchmark platform is not configured yet.")
    print("           every run is uploaded to your own site and the local")
    print("           data is deleted afterwards.")
    ans = input("Configure it now? [Y/n] ").strip().lower()
    if ans in ("n", "no"):
        return None
    url = ""
    while not url.startswith(("http://", "https://")):
        url = input("Platform URL (e.g. https://benchmark.wulei.org): "
                    ).strip().rstrip("/")
    token = input("Upload token (UPLOAD_TOKEN secret of the worker): ").strip()
    if not token:
        print("[publish] empty token — upload will likely be rejected (401)")
    cfg = {"url": url, "token": token}
    os.makedirs(os.path.dirname(SITE_CONFIG), exist_ok=True)
    with open(SITE_CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=1)
    print(f"[publish] saved -> {SITE_CONFIG} (gitignored)")
    return cfg


# -------------------------------------------------------------- publish
def publish_run(run_dir: str, cfg: dict) -> str:
    """Upload one run; returns the share link."""
    run_id = os.path.basename(run_dir.rstrip("/"))
    with open(os.path.join(run_dir, "results.json"), encoding="utf-8") as f:
        results = json.load(f)

    page = build_run_page(results, run_id, run_dir)
    payload = json.dumps({
        "id": run_id,
        "meta": manifest_entry(results, run_id),
        "html": page,
    }, ensure_ascii=False).encode()

    req = urllib.request.Request(
        cfg["url"] + "/api/runs", data=payload, method="POST",
        headers={"Content-Type": "application/json; charset=utf-8",
                 "Authorization": f"Bearer {cfg['token']}"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            resp = json.load(r)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:200]
        raise RuntimeError(f"upload rejected: HTTP {e.code} — {detail}") from e
    if not resp.get("ok"):
        raise RuntimeError(f"platform refused the run: {resp}")
    return f"{cfg['url']}/r/{run_id}"


# --------------------------------------------------------------- cleanup
def cleanup_run(run_dir: str) -> bool:
    """Delete a run directory (results/<YYYYMMDD-HHMMSS>) after a
    successful upload. Refuses anything that does not look like a run."""
    name = os.path.basename(run_dir.rstrip("/"))
    parent = os.path.basename(os.path.dirname(run_dir.rstrip("/")))
    if parent != "results" or not re.fullmatch(r"\d{8}-\d{6}", name):
        print(f"[cleanup] refusing to delete {run_dir!r} — not a run dir")
        return False
    shutil.rmtree(run_dir, ignore_errors=True)
    try:
        os.rmdir(os.path.dirname(run_dir))   # drop empty results root
    except OSError:
        pass
    return True

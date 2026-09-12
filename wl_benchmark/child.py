"""Sandboxed child entry point.

Spawned by wl_benchmark.sandbox.spawn_sandboxed with:
  WL_BENCH_CHILD      json payload {provider, run_cfg, only_types, out,
                          keep, report}
  WL_BENCH_TUNNEL_SOCK  path of the parent's Unix-socket proxy

Installs the network tunnel, runs the benchmark, publishes the
results, exits with the run's exit code (130 on interrupt).
"""
from __future__ import annotations

import json
import os
import sys


def main() -> int:
    from . import tunnel_client
    tunnel_client.install(os.environ["WL_BENCH_TUNNEL_SOCK"])

    payload = json.loads(os.environ["WL_BENCH_CHILD"])
    from .runner import run_all
    from .reporter import write_report
    # tasks only: the review PDF and the upload happen in the parent
    # (trusted, unsandboxed) — Chrome's print pipeline aborts under the
    # seatbelt profile, and the tunnel is endpoint-only anyway
    out_dir = run_all(payload["provider"], payload["run_cfg"],
                      only_types=payload.get("only_types"),
                      out_root=payload.get("out"), review=False)
    write_report(out_dir)
    done_file = payload.get("done_file")
    if done_file:
        with open(done_file, "w", encoding="utf-8") as f:
            f.write(out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())

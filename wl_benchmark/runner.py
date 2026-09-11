"""Runner: one evaluation = one provider x one model x the task list,
with live progress output.

The provider dict is collected interactively by the CLI (endpoint/key/model);
nothing is persisted.
"""
from __future__ import annotations

import json
import os
import time
from typing import Optional

from .client import ChatClient
from .tasks import build_tasks


def run_all(provider: dict, run_cfg: dict, only_types: Optional[list] = None,
            out_root: str = "results") -> str:
    """Run the whole task list; returns the run output directory."""
    tasks_data_root = run_cfg.get("tasks_data_root", "tasks_data")
    model = provider["model"]

    run_id = time.strftime("%Y%m%d-%H%M%S")
    out_dir = os.path.join(out_root, run_id)
    os.makedirs(out_dir, exist_ok=True)
    context_store: dict = {}

    tasks = build_tasks(tasks_data_root, run_cfg,
                        artifacts_root=os.path.join(out_dir, "artifacts"),
                        only_types=only_types)
    if not tasks:
        print("[runner] no tasks found under", tasks_data_root)
        return out_dir

    client = ChatClient(provider["base_url"], provider["api_key"],
                        timeout=run_cfg.get("timeout", 300))
    client.label = provider.get("name") or model

    t0 = time.time()
    print(f"provider : {client.label}  ({provider['base_url']})")
    print(f"model    : {model}")
    print(f"tasks    : {len(tasks)}   output: {out_dir}")

    all_results = []
    for i, task in enumerate(tasks, 1):
        print(f"\n[{i}/{len(tasks)}] [{task.task_type}] {task.task_id}",
              flush=True)
        try:
            r = task.run(client, model, context=context_store)
        except Exception as e:  # noqa: BLE001
            from .tasks.base import TaskResult
            r = TaskResult(task_id=task.task_id, task_type=task.task_type,
                           model=model, provider=client.label, error=str(e))

        if r.error:
            print(f"    x ERROR  {str(r.error)[:110]}")
        elif r.score is None:
            arts = ", ".join(os.path.relpath(a) for a in r.artifacts) or "-"
            print(f"    o pending human review  ({r.latency:.1f}s)  "
                  f"artifacts: {arts}")
        else:
            print(f"    OK {r.score:.2f}  ({r.latency:.1f}s)")

        all_results.append(r.to_dict())
        # chained tasks (research proposal) read prior outputs from here;
        # drop stale entries on failure so they never leak across models
        if r.error or not r.artifacts:
            context_store.pop(task.task_id, None)
        else:
            context_store[task.task_id] = r

        # incremental dump — a crashed run keeps its data
        with open(os.path.join(out_dir, "results.json"), "w",
                  encoding="utf-8") as f:
            json.dump(all_results, f, ensure_ascii=False, indent=1)

    print(f"\n[runner] done in {(time.time()-t0)/60:.1f} min -> {out_dir}")

    # compile the single human-review PDF
    try:
        from .review_pdf import build_review_pdf
        pdf = build_review_pdf(out_dir)
        print(f"[runner] review pdf -> {pdf}")
    except Exception as e:  # noqa: BLE001
        print(f"[runner] review pdf skipped ({e})")
    return out_dir

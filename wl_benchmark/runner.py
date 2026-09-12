"""Runner: one evaluation = one provider x one model x the task list,
with live progress output.

Tasks are I/O-bound (waiting on the provider), so independent tasks run
in parallel via a thread pool. Chained tasks (research proposal reads
the outputs of the two earlier writings) are scheduled after their
dependencies finish: the task list forms a DAG derived from each spec's
context_from, executed topologically.

The provider dict is collected interactively by the CLI (endpoint/key/model);
nothing is persisted.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
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
    print(f"provider  {client.label} · {model}")
    print(f"output    {out_dir}")

    all_results: list = []
    dump_lock = threading.Lock()

    def dump():
        # incremental dump — a crashed run keeps its data
        with dump_lock:
            with open(os.path.join(out_dir, "results.json"), "w",
                      encoding="utf-8") as f:
                json.dump(all_results, f, ensure_ascii=False, indent=1)

    # ---- dependency DAG (context_from chains) --------------------------
    deps_of = {t.task_id: list(t.spec.get("context_from") or [])
               for t in tasks}
    by_id = {t.task_id: t for t in tasks}
    dependents: dict = defaultdict(list)
    for tid, ds in deps_of.items():
        for d in ds:
            if d in by_id:
                dependents[d].append(tid)

    jobs = max(1, int(run_cfg.get("parallel_jobs", 3)))

    # ---- live task table: 8 fixed rows, one per task ------------------
    # row layout: index · task id · status · elapsed. redrawn in place
    # (ANSI cursor-up) on every state change and every 5 s.
    states = {t.task_id: {"s": "pending", "t0": None, "t1": None, "note": ""}
              for t in tasks}
    draw_lock = threading.Lock()
    drew = {"lines": 0}

    def _fmt_row(i, tid):
        st = states[tid]
        if st["s"] == "running":
            t = f"{(time.time() - st['t0']) / 60:5.1f} min"
        elif st["t0"] is not None:
            t = f"{(st['t1'] - st['t0']) / 60:5.1f} min"
        else:
            t = "       ·"
        return (f"  {i + 1}  {tid:<28} {st['s']:<8} {t}  {st['note']}")

    def redraw():
        if not sys.stdout.isatty():
            return
        with draw_lock:
            rows = [f"  #   task                          status   time"]
            rows += [_fmt_row(i, tid) for i, tid in enumerate(states)]
            # every row ends with \n, so the cursor lands one line BELOW
            # the table; \x1b[{n}F (up n lines) returns to the header.
            # without the newlines all rows collapse onto one physical
            # line and each redraw creeps upward, eating earlier output.
            up = "" if not drew["lines"] else f"\x1b[{len(rows)}F"
            sys.stdout.write(up + "".join("\r\x1b[K" + r + "\n"
                                           for r in rows))
            sys.stdout.flush()
            drew["lines"] = len(rows)

    def clear_table():
        if sys.stdout.isatty() and drew["lines"]:
            with draw_lock:
                sys.stdout.write(f"\x1b[{drew['lines']}F\r\x1b[J")
                sys.stdout.flush()
                drew["lines"] = 0

    def touch(tid, status, note=""):
        st = states.get(tid)
        if st is None:
            return
        st["s"] = status
        now = time.time()
        if status == "running":
            st["t0"] = now
        else:
            if st["t0"] is None:
                st["t0"] = now
            st["t1"] = now
            st["note"] = note
        redraw()

    stop_status = threading.Event()

    def _status_loop():
        while not stop_status.wait(5):
            redraw()

    def report(r):
        with dump_lock:
            all_results.append(r.to_dict())
        dump()
        err = str(r.error or "")
        if not err:
            touch(r.task_id, "done")
        elif err.startswith("skipped"):
            touch(r.task_id, "skip", err.splitlines()[0][:40])
        else:
            touch(r.task_id, "fail", err.splitlines()[0][:40])

    def run_one(task, snapshot):
        try:
            return task.run(client, model, context=snapshot)
        except Exception as e:  # noqa: BLE001
            from .tasks.base import TaskResult
            return TaskResult(task_id=task.task_id,
                              task_type=task.task_type,
                              model=model, provider=client.label,
                              error=str(e))

    finished: set = set()   # task_ids with a recorded result
    failed: set = set()     # error/skipped — blocks dependents
    context_store: dict = {}
    dump()                  # results.json exists from second zero

    interrupted = False
    redraw()
    threading.Thread(target=_status_loop, daemon=True).start()
    pool = ThreadPoolExecutor(max_workers=jobs)
    try:
        futures = {}          # future -> task_id
        while len(finished) < len(tasks):
            # submit every task whose deps are satisfied
            for t in tasks:
                tid = t.task_id
                if tid in finished or any(f == tid for f in futures.values()):
                    continue
                blocked = [d for d in deps_of[tid] if d in failed]
                if blocked:
                    from .tasks.base import TaskResult
                    r = TaskResult(
                        task_id=tid, task_type=t.task_type, model=model,
                        provider=client.label,
                        error=f"skipped: dependency '{blocked[0]}' failed")
                    finished.add(tid)
                    failed.add(tid)
                    report(r)
                    continue
                if all(d in finished for d in deps_of[tid] if d in by_id):
                    # snapshot: the task must not see later context writes
                    snap = dict(context_store)
                    touch(tid, "running")
                    futures[pool.submit(run_one, t, snap)] = tid
            if not futures:
                # nothing running and nothing submittable — unsatisfiable
                # deps (e.g. --tasks filtered out a dependency); finish the
                # rest as skipped so the run always terminates
                for t in tasks:
                    if t.task_id not in finished:
                        missing = [d for d in deps_of[t.task_id]
                                   if d in by_id and d not in finished]
                        from .tasks.base import TaskResult
                        r = TaskResult(
                            task_id=t.task_id, task_type=t.task_type,
                            model=model, provider=client.label,
                            error=f"skipped: dependency '{missing[0]}' "
                                  f"was not run")
                        finished.add(t.task_id)
                        failed.add(t.task_id)
                        report(r)
                continue
            try:
                done_futs, _ = wait(list(futures),
                                    return_when=FIRST_COMPLETED)
            except KeyboardInterrupt:
                # cancel everything queued; running API calls cannot be
                # interrupted, so exit hard after dumping what we have —
                # otherwise the executor's atexit join would hang until
                # the provider timeout
                for f in futures:
                    f.cancel()
                interrupted = True
                break
            for fut in done_futs:
                tid = futures.pop(fut)
                r = fut.result()
                finished.add(tid)
                if r.error or not r.artifacts:
                    # drop stale entries so they never leak across models
                    failed.add(tid)
                    context_store.pop(tid, None)
                else:
                    context_store[tid] = r
                report(r)

        stop_status.set()
        clear_table()
        if not interrupted:
            mins = (time.time() - t0) / 60
            n_err = sum(1 for r in all_results if r.get("error"))
            print(f"done      {len(all_results)}/{len(tasks)} tasks · "
                  f"{n_err} failed · {mins:.1f} min\n")
    finally:
        stop_status.set()
        # on Ctrl+C: drop the queue, do NOT wait for in-flight API calls —
        # the incremental dump already persisted every finished result
        pool.shutdown(wait=interrupted is False, cancel_futures=interrupted)

    if interrupted:
        stop_status.set()
        clear_table()
        print("[runner] interrupted by user — completed results kept "
              f"at {out_dir} (upload later with: wlb --upload {out_dir})",
              flush=True)
        os._exit(130)    # skip the executor's atexit join; data is dumped

    # compact result table (the only per-task output, all at once)
    for r in all_results:
        if r.get("error"):
            tail = "FAIL " + str(r["error"]).splitlines()[0][:52]
        elif r.get("score") is None:
            tail = "pending human review"
        else:
            tail = f"score {r['score']:.2f}"
        print(f"  {r['task_type']:<10} {r['task_id']:<24} {tail}")
    print()

    # compile the single human-review PDF
    try:
        from .review_pdf import build_review_pdf
        pdf = build_review_pdf(out_dir)
        print(f"review  {pdf}")
    except Exception as e:  # noqa: BLE001
        print(f"review  skipped ({e})")
    return out_dir

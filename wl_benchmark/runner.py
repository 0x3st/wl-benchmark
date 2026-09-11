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


class _Progress:
    """Per-task progress block, docker-pull style (TTY; pipes get terse
    one-liners).

    While tasks run, the block shows one line per RUNNING worker slot:
    a spinner, the task, its elapsed time and its API call count — plus
    a header line with done/failed totals. A daemon thread redraws the
    whole block every 0.25 s using ANSI cursor moves.
    """

    SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, total: int, t0: float, workers: int = 3):
        self.total = total
        self.workers = max(1, workers)
        self.finished = 0
        self.failed = 0
        self.entries: dict = {}          # task_id -> dict(type, t0, calls)
        self.t0 = t0
        self.tty = sys.stdout.isatty()
        self.stop_evt = threading.Event()
        self._lock = threading.Lock()
        self._last_lines = 0
        self._frame = 0
        if self.tty:
            threading.Thread(target=self._spin, daemon=True).start()

    # ---- worker-thread API -------------------------------------------
    def start(self, task):
        with self._lock:
            self.entries[task.task_id] = {"type": task.task_type,
                                          "t0": time.time(), "calls": 0}

    def end_task(self, task):
        with self._lock:
            self.entries.pop(task.task_id, None)

    def bump(self, task_id):
        """One more API call inside this task."""
        with self._lock:
            if task_id in self.entries:
                self.entries[task_id]["calls"] += 1

    def finish(self, r):
        with self._lock:
            self.finished += 1
            if getattr(r, "error", None):
                self.failed += 1
        if not self.tty:
            word = "fail" if getattr(r, "error", None) else "done"
            print(f"{word} {self.finished}/{self.total} {r.task_id}",
                  flush=True)

    # ---- rendering -----------------------------------------------------
    def _fmt_elapsed(self, seconds: float) -> str:
        m, s = divmod(int(seconds), 60)
        return f"{m}:{s:02d}"

    def _render(self):
        self._frame = (self._frame + 1) % len(self.SPIN)
        el_total = self._fmt_elapsed(time.time() - self.t0)
        lines = [f"{self.finished}/{self.total} done · {self.failed} failed"
                 f" · {el_total} elapsed"]
        with self._lock:
            entries = list(self.entries.items())
        for tid, e in entries[:self.workers]:
            spin = self.SPIN[self._frame]
            el = self._fmt_elapsed(time.time() - e["t0"])
            calls = e["calls"]
            lines.append(f"  {spin} {e['type']:<10} {tid:<24} "
                         f"{el:>6} · {calls} call{'s' if calls != 1 else ''}")
        # clear the previously drawn block, then draw the new one
        out = ["\r\x1b[2K"]
        out += ["\x1b[1A\x1b[2K"] * (self._last_lines - 1)
        out.append("\n".join(lines))
        sys.stdout.write("".join(out))
        sys.stdout.flush()
        self._last_lines = len(lines)

    def _spin(self):
        while not self.stop_evt.wait(0.25):
            self._render()

    def close(self):
        self.stop_evt.set()
        if self.tty and self._last_lines:
            sys.stdout.write("\r\x1b[2K"
                             + "\x1b[1A\x1b[2K" * (self._last_lines - 1))
            self._last_lines = 0
            sys.stdout.flush()


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

    # ---- single-line progress bar (no streaming logs) ------------------
    progress = _Progress(len(tasks), t0, workers=jobs)

    def report(r):
        progress.finish(r)
        with dump_lock:
            all_results.append(r.to_dict())
        dump()

    class _CountingClient:
        """Delegates to the shared client; counts API calls per task."""
        def __init__(self, inner, task):
            self._inner, self._task = inner, task
        def chat(self, *a, **k):
            progress.bump(self._task.task_id)
            return self._inner.chat(*a, **k)
        def __getattr__(self, name):
            return getattr(self._inner, name)

    def run_one(task, snapshot):
        progress.start(task)
        try:
            result = task.run(_CountingClient(client, task), model,
                              context=snapshot)
        except Exception as e:  # noqa: BLE001
            from .tasks.base import TaskResult
            result = TaskResult(task_id=task.task_id,
                                task_type=task.task_type,
                                model=model, provider=client.label,
                                error=str(e))
        progress.end_task(task)
        return result

    finished: set = set()   # task_ids with a recorded result
    failed: set = set()     # error/skipped — blocks dependents
    context_store: dict = {}
    dump()                  # results.json exists from second zero

    interrupted = False
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

        progress.close()
        if not interrupted:
            mins = (time.time() - t0) / 60
            n_err = sum(1 for r in all_results if r.get("error"))
            print(f"done      {len(all_results)}/{len(tasks)} tasks · "
                  f"{n_err} failed · {mins:.1f} min\n")
    finally:
        progress.close()
        # on Ctrl+C: drop the queue, do NOT wait for in-flight API calls —
        # the incremental dump already persisted every finished result
        pool.shutdown(wait=interrupted is False, cancel_futures=interrupted)

    if interrupted:
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

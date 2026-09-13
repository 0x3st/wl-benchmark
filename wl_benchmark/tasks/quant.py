"""Factor-mining research-practice task (agentic; auto + human scoring).

The model does NOT receive any data. It must discover the data universe
through two opaque tools (no descriptions — infer by trying):

  meta_list()           -> available series (tickers + descriptions)
  series_fetch(tick)    -> 24 months of monthly returns (%, 2024-01..2025-12)

The universe contains NOISE: SZTECH_V2 (revised-methodology decoy index),
BTC (distraction), and three style factors (SIZE/VOL/LIQ) that are pure
noise. The true driver of SZTECH (2024 window) is the market plus exactly
one library factor (MOM) — verified at generation time (t(MOM) > 3,
decoys |t| < 1.5).

Deliverables:
  1. numeric answers (strict JSON, named keys) — auto-scored with
     tolerances against the programmatically computed ground truth;
  2. a research note (150-400 words: method, identification evidence,
     results, limitation) — human-reviewed.

Final score convention: 0.8 x auto_score + 0.2 x note score. TaskResult
score is None (pending human review); the auto part is stored in
detail.auto_score.
"""
from __future__ import annotations

import glob
import itertools
import json
import os
import re
import time
from typing import Any, Dict, List, Optional

from .base import BaseTask, TaskResult

TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "meta_list",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "series_fetch",
        "parameters": {"type": "object", "properties": {
            "tick": {"type": "string"}}, "required": ["tick"]}}},
]


def discover(root: str) -> List[Dict[str, Any]]:
    out = []
    for f in sorted(glob.glob(os.path.join(root, "*.json"))):
        with open(f, encoding="utf-8") as fh:
            out.append(json.load(fh))
    return out


class QuantTask(BaseTask):
    task_type = "quant"

    # ------------------------------------------------------------ prompt
    def _build_prompt(self) -> str:
        return (
            "WL-Benchmark — quantitative research practice (multi-factor "
            "mining, inference and out-of-sample test). You are a research "
            "assistant at the SDS Quantitative Finance Lab. Your teammate "
            "believes the monthly returns of the synthetic stock SZTECH are "
            "driven by the market benchmark MKT3000 plus AT MOST TWO style "
            "factors from the lab's library (MOM / VAL / SIZE / VOL / LIQ). "
            "Your job: mine the library, identify the true factors, "
            "estimate the model with formal inference, then validate it "
            "out of sample.\n\n"
            "You have a set of tools available. Their names and parameter "
            "names are intentionally terse — no documentation is provided. "
            "Figure out what each tool does and how to use it by trying "
            "them; if a call fails or returns something unexpected, adjust "
            "and retry. Do not ask the user for anything.\n\n"
            "CONSTRAINTS & CONVENTIONS:\n"
            "- Estimation window: 2024-01 to 2024-12. The series cover "
            "2024-01 to 2025-12 — slice the right window; the 2025 months "
            "are reserved for the out-of-sample test.\n"
            "- Returns are in percent (2.0 = +2%). The market benchmark is "
            "MKT3000; the risk-free series is RF.\n"
            "- OLS with intercept. Report R-squared on a 0-1 scale; sample "
            "statistics use the (n-1) denominator; annualization multiplies "
            "by sqrt(12); maximum drawdown is a positive percent (largest "
            "peak-to-trough loss of the compounded price series, base "
            "100); t-statistics are the usual beta / standard-error ratios.\n"
            "- OUT-OF-SAMPLE: after estimating on 2024, apply those "
            "coefficients to the 2025 months (using that month's "
            "regressors) and report the RMSE of the prediction errors, in "
            "percentage points.\n"
            "- Round numeric answers to 4 decimal places.\n"
            "- CAUTION: the universe contains series that do not belong in "
            "your model (revised indexes, unrelated assets, pure-noise "
            "factors) — model selection is your responsibility.\n\n"
            "OUTPUT REQUIREMENTS (follow strictly):\n"
            "1. When done researching, output one fenced JSON block with "
            "EXACTLY these keys: "
            '{"factor_ids": ["<ticker>", ...], "alpha": <monthly %>, '
            '"beta_mkt": <num>, "beta_mom": <num>, "beta_val": <num>, '
            '"t_mom": <num>, "t_val": <num>, "r2": <0-1>, '
            '"std_stk": <monthly %>, "sharpe_ann": <num>, '
            '"max_dd": <positive %>, "es3": <monthly %>, '
            '"oos_rmse": <pct points>}'
            " — beta_mom/t_mom refer to the momentum-type factor you "
            "selected, beta_val/t_val to the value-type factor.\n"
            "2. Then output a research note in markdown (200-450 words) "
            "covering: your factor-mining and model-selection method, the "
            "identification evidence (t-statistics), the out-of-sample "
            "performance, and at least one limitation.")

    # ------------------------------------------------------ tool back-end
    def _tool_meta_list(self) -> dict:
        return {"series": self.spec["series_meta"]}

    def _tool_series_fetch(self, args) -> dict:
        tick = str(args.get("tick", "")).strip()
        rows = self.spec["series"].get(tick)
        if rows is None:
            return {"error": f"unknown ticker '{tick}'",
                    "hint": "call the listing tool first"}
        return {"ticker": tick, "rows": rows}

    def _dispatch_tool(self, name: str, args: dict) -> dict:
        try:
            if name == "meta_list":
                return self._tool_meta_list()
            if name == "series_fetch":
                return self._tool_series_fetch(args)
            return {"error": f"unknown tool {name}"}
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}

    # ------------------------------------------------------------ parsing
    def _parse_answer(self, text: str) -> Dict[str, Any]:
        from ..client import parse_judge_json
        parsed = parse_judge_json(text or "") or {}
        keys = {q["id"] for q in self.spec["questions"]}
        ans = {k: v for k, v in parsed.items() if k in keys}
        if "factor_ids" not in ans:
            # regex fallback: collect every library ticker the reply names
            found = [f for f in ("MOM", "VAL", "SIZE", "VOL", "LIQ")
                     if re.search(rf"\b{f}\b", (text or "").upper())]
            if found:
                ans["factor_ids"] = found
        if isinstance(ans.get("factor_ids"), list):
            # the question asks for LIBRARY factors — the market
            # benchmark (MKT3000) and any other non-library ticker are
            # not valid answer elements; normalize the format
            library = {"MOM", "VAL", "SIZE", "VOL", "LIQ"}
            ans["factor_ids"] = [f for f in ans["factor_ids"]
                                 if str(f).upper() in library]
        return ans

    # -------------------------------------------------------------- eval
    @staticmethod
    def _num(v) -> Optional[float]:
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def _check(self, q, v) -> bool:
        if q["id"] == "factor_ids":
            if isinstance(v, str):
                v = [s for s in re.split(r"[,\s]+", v) if s]
            if not isinstance(v, (list, tuple)):
                return False
            return sorted(str(s).strip().upper() for s in v) == \
                sorted(str(s).strip().upper() for s in q["answer"])
        x = self._num(v)
        if x is None:
            return False
        ans, tol = q["answer"], q["tol"]
        if abs(x - ans) <= tol:
            return True
        # tolerate percent/decimal-fraction scale confusion
        return abs(x * 100 - ans) <= tol or abs(x / 100 - ans) <= tol

    def _evaluate(self, ans: Dict[str, Any]):
        rows, score, wsum = [], 0.0, 0.0
        for q in self.spec["questions"]:
            wsum += q["weight"]
            ok = q["id"] in ans and self._check(q, ans[q["id"]])
            if ok:
                score += q["weight"]
            rows.append({"id": q["id"], "given": ans.get(q["id"]),
                         "answer": q["answer"], "correct": ok})
        return score / wsum if wsum else 0.0, {"per_question": rows}

    # --------------------------------------------------------------- run
    def _tool_loop(self, client, model: str):
        """One full agentic attempt: returns (final_content, error,
        usage_acc, latencies, tool_trace)."""
        messages: List[Dict[str, Any]] = [
            {"role": "user", "content": self._build_prompt()}]
        tool_trace: List[Dict[str, Any]] = []
        latencies, usage_acc = [], {}
        # the only limit is wall-clock time: the model may use as many
        # tool turns as it likes until the task budget runs out
        deadline = time.time() + self.run_cfg.get("task_minutes", 30) * 60
        final_content = None
        error = None
        while True:
            force_final = time.time() >= deadline
            res = client.chat(
                model, messages,
                tools=None if force_final else TOOL_SCHEMAS,
                max_tokens=self.run_cfg.get("quant_max_tokens"),
                temperature=self.run_cfg.get("temperature"),
                reasoning_effort=self.run_cfg.get("quant_reasoning_effort",
                                                 "low"))
            latencies.append(res.latency)
            for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
                usage_acc[k] = usage_acc.get(k, 0) + \
                    int(res.usage.get(k, 0) or 0)
            if not res.ok:
                error = res.error
                break
            if res.tool_calls and not force_final:
                messages.append({"role": "assistant",
                                 "content": res.content or "",
                                 "tool_calls": res.tool_calls})
                for tc in res.tool_calls:
                    fn = tc.get("function", {})
                    name = fn.get("name", "")
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    result = self._dispatch_tool(name, args)
                    tool_trace.append({"tool": name, "args": args})
                    messages.append({"role": "tool",
                                     "tool_call_id": tc.get("id"),
                                     "name": name,
                                     "content": json.dumps(
                                         result, ensure_ascii=False)})
                continue
            final_content = res.content
            break
        return final_content, error, usage_acc, latencies, tool_trace

    def run(self, client, model: str,
            context: Optional[Dict[str, Any]] = None) -> TaskResult:
        # multi-attempt sampling: effort=low analysis quality varies run
        # to run, so take the best-scoring of N independent attempts
        attempts = max(1, int(self.run_cfg.get("quant_attempts", 3)))
        os.makedirs(self.artifacts_dir, exist_ok=True)
        best = None            # (auto_score, final_content, usage, lat, trace)
        error = None
        for i in range(attempts):
            final_content, err, usage_acc, latencies, tool_trace = \
                self._tool_loop(client, model)
            if err:
                error = err
                continue
            ans = self._parse_answer(final_content or "")
            auto, _ = self._evaluate(ans)
            print(f"[quant] attempt {i + 1}/{attempts}: auto={auto:.2f}",
                  flush=True)
            if best is None or auto > best[0]:
                best = (auto, final_content, usage_acc, latencies,
                        tool_trace, ans)
        if best is None:
            return TaskResult(task_id=self.task_id, task_type=self.task_type,
                              model=model, provider=getattr(client, "label", "?"),
                              error=error or "all quant attempts failed",
                              latency=0, usage={})

        auto, final_content, usage_acc, latencies, tool_trace, ans = best

        reply_path = os.path.join(self.artifacts_dir,
                                  f"{self.task_id}__{model}.md")
        with open(reply_path, "w", encoding="utf-8") as f:
            f.write(final_content or "")

        auto, detail = self._evaluate(ans)
        detail["answer"] = ans
        detail["auto_score"] = round(auto, 4)
        detail["auto_weight"] = self.spec.get("auto_weight", 0.8)
        detail["note_weight"] = self.spec.get("note_spec", {}).get(
            "weight", 0.2)
        detail["note"] = final_content or ""
        detail["tool_trace"] = tool_trace
        detail["turns"] = len(latencies)
        return TaskResult(
            task_id=self.task_id, task_type=self.task_type,
            model=model, provider=getattr(client, "label", "?"),
            score=None,          # 0.8*auto + 0.2*note, synthesized after human review
            max_score=1.0, detail=detail,
            artifacts=[reply_path], latency=sum(latencies), usage=usage_acc)

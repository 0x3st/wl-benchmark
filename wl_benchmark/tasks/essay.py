"""Essay writing with RUBRIC task (human-reviewed).

README design notes:
- The RUBRIC is provided as pdf / docx / photo / md (storytelling derives
  from the school-provided RUBIC1.docx)
- Three genres: storytelling / argument / proposal (the numeric prefix of
  each directory name defines execution order)
- Research Proposal receives the outputs of the previous two writings as
  context (spec.context_from)
- Review: **human**. The harness only generates the essay and saves it;
  a human grades it against the rubric

Scenario directory layout (one directory per sub-task):
<tasks_data_root>/essay/<NN-name>/
  ├── task.md     # essay instruction
  └── rubric.pdf / rubric.docx / rubric.png / rubric.md   # rubric, pick one

Optional spec fields (in that directory's spec.json):
  {"context_from": ["01-storytelling", "02-argument"]}   # inject prior outputs
"""
from __future__ import annotations

import glob
import json
import os
import re
from typing import Any, Dict, List, Optional

from .base import BaseTask, TaskResult

RUBRIC_EXTS = ("*.pdf", "*.docx", "*.png", "*.jpg", "*.jpeg", "*.webp",
               "*.md", "*.txt")


def discover(root: str) -> List[Dict[str, Any]]:
    """Each subdirectory of root (with task.md) is one essay scenario."""
    out = []
    if not os.path.isdir(root):
        return out
    for name in sorted(os.listdir(root)):
        d = os.path.join(root, name)
        if not os.path.isdir(d) or name.startswith("_"):
            continue
        task_file = os.path.join(d, "task.md")
        if not os.path.exists(task_file):
            continue
        cands: List[str] = []
        for pat in RUBRIC_EXTS:
            cands += sorted(glob.glob(os.path.join(d, pat)))
        # RUBRIC_EXTS order is the priority: pdf > docx > png > ... > md
        spec: Dict[str, Any] = {"id": name, "dir": d,
                                "instruction_file": task_file,
                                "rubric": cands[0] if cands else None,
                                "rubric_candidates": cands}
        spec_file = os.path.join(d, "spec.json")
        if os.path.exists(spec_file):
            with open(spec_file, encoding="utf-8") as f:
                spec.update(json.load(f))
        out.append(spec)
    return out


MODALITY_EXTS = {
    "image": (".png", ".jpg", ".jpeg", ".webp"),
    "doc": (".pdf", ".docx", ".doc"),
    "text": (".md", ".txt"),
}


def select_rubric(spec: Dict[str, Any], modality: str) -> Optional[str]:
    """Pick the rubric file by configured modality; fallback to auto default."""
    if modality == "auto":
        return spec.get("rubric")
    exts = MODALITY_EXTS.get(modality)
    if not exts:
        return spec.get("rubric")
    for cand in spec.get("rubric_candidates", []):
        if cand.lower().endswith(exts):
            return cand
    return spec.get("rubric")  # fall back to the default if this modality has no file


def _count_words(seg: str) -> int:
    return len(re.findall(r"[A-Za-z][A-Za-z'\-]*", seg))


def _count_cjk(seg: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", seg))


def _slice_by_heading(text: str, heading: str) -> str:
    """Text under `heading` up to the next heading of the same or higher
    level (subsection headings belong to the section)."""
    m = re.search(rf"^{re.escape(heading)}[ \t]*$", text, re.M)
    if not m:
        return ""
    rest = text[m.end():]
    if rest.startswith("\n"):
        rest = rest[1:]
    level = len(heading) - len(heading.lstrip("#"))
    nxt = re.search(rf"^#{{1,{level}}} ", rest, re.M)
    return rest[:nxt.start()] if nxt else rest


def check_constraints(text: str, specs: list) -> list:
    """Evaluate machine-checkable presentation constraints.

    Supported spec types:
      sections_present: {values: [...]}                 substring match
      word_range:       {section, min, max}             English word count
      char_range:       {section, min, max, script}     CJK char count
      min_distinct:     {patterns: [...], min}          distinct tag count
      keyword_groups:   {groups: [[...], ...], min}     keyword alternatives
    """
    out = []
    for spec in specs or []:
        kind = spec.get("type")
        ok, note = True, ""
        if kind == "sections_present":
            missing = [v for v in spec["values"]
                       if v.lower() not in text.lower()]
            ok = not missing
            note = "missing: " + ", ".join(missing) if missing else "all present"
        elif kind == "word_range":
            seg = _slice_by_heading(text, spec["section"])
            n = _count_words(seg)
            ok = spec["min"] <= n <= spec["max"]
            note = f"{n} words (need {spec['min']}-{spec['max']})"
        elif kind == "char_range":
            seg = _slice_by_heading(text, spec["section"])
            n = _count_cjk(seg)
            ok = spec["min"] <= n <= spec["max"]
            note = f"{n} CJK chars (need {spec['min']}-{spec['max']})"
        elif kind == "min_distinct":
            found = [p for p in spec["patterns"] if p.lower() in text.lower()]
            ok = len(found) >= spec["min"]
            note = f"{len(found)} distinct of {spec['min']} required"
        elif kind == "keyword_groups":
            hits = sum(1 for g in spec["groups"]
                       if any(k.lower() in text.lower() for k in g))
            ok = hits >= spec["min"]
            note = f"{hits} of {spec['min']} required items covered"
        else:
            note = f"unknown constraint type {kind}"
        out.append({"constraint": spec.get("desc", kind), "ok": ok,
                    "detail": note})
    return out


class EssayTask(BaseTask):
    task_type = "essay"

    # ------------------------------------------------------------ helpers
    def _rubric_parts_for_model(self) -> List[Dict[str, Any]]:
        """RUBRIC content parts sent to the *model under test*."""
        path = self.spec.get("rubric")
        if not path:
            return []
        from ..client import ChatClient
        if path.endswith((".png", ".jpg", ".jpeg", ".webp")):
            return [ChatClient.image_part(path)]
        if path.endswith(".pdf"):
            return [ChatClient.file_part(path)]
        if path.endswith(".docx"):
            # pass the docx through unchanged (the provider's docx support is itself under test)
            return [ChatClient.file_part(path)]
        with open(path, encoding="utf-8") as f:
            return [{"type": "text", "text": "=== RUBRIC ===\n" + f.read()}]

    def _prior_texts(self, context: Optional[Dict[str, TaskResult]]) -> List[str]:
        """Load previous chained writings (markdown artifacts)."""
        blocks = []
        for tid in self.spec.get("context_from", []):
            r = (context or {}).get(tid)
            text = r.load_text() if r else ""
            blocks.append(f"--- Output of previous task [{tid}] ---\n"
                          f"{text or '(missing)'}")
        return blocks

    # ---------------------------------------------------------------- run
    def run(self, client, model: str,
            context: Optional[Dict[str, TaskResult]] = None) -> TaskResult:
        with open(self.spec["instruction_file"], encoding="utf-8") as f:
            instruction = f.read().strip()

        model_parts: List[Dict[str, Any]] = [
            {"type": "text", "text": instruction}]
        prior = self._prior_texts(context)
        if prior:
            model_parts.append({"type": "text", "text": "\n\n".join(prior)})
        model_parts += self._rubric_parts_for_model()

        res = client.chat(model, [{"role": "user", "content": model_parts}],
                          max_tokens=self.run_cfg.get("essay_max_tokens"),
                          temperature=self.run_cfg.get("temperature", 0.4))

        essay_path = None
        if res.ok and res.content:
            os.makedirs(self.artifacts_dir, exist_ok=True)
            essay_path = os.path.join(
                self.artifacts_dir, f"{self.task_id}__{model}.md")
            with open(essay_path, "w", encoding="utf-8") as f:
                f.write(res.content)

        if not res.ok:
            return TaskResult(task_id=self.task_id, task_type=self.task_type,
                              model=model, provider=getattr(client, "label", "?"),
                              error=res.error, latency=res.latency,
                              usage=res.usage)

        _cons = check_constraints(res.content or "",
                                  self.spec.get("constraints", []))
        return TaskResult(
            task_id=self.task_id, task_type=self.task_type,
            model=model, provider=getattr(client, "label", "?"),
            score=None,   # graded by a human against the RUBRIC
            detail={"status": "pending-human-review",
                    "constraints": _cons,
                    "constraints_passed": sum(1 for c in _cons if c["ok"]),
                    "constraints_total": len(_cons),
                    "rubric_file": self.spec.get("rubric"),
                    "used_context": [t for t in self.spec.get("context_from", [])
                                     if (context or {}).get(t)]},
            artifacts=[essay_path] if essay_path else [],
            latency=res.latency, usage=res.usage,
        )

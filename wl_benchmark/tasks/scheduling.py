"""Term-planning scheduling task — agentic search + planning.

The model does NOT receive the study scheme / transcript / offering list in
the prompt. It must gather every fact itself through four pseudo tools
(fake data, production-like shapes):

  search_registry(query)       - registry / study-scheme rule documents
  query_transcript(term?)      - the student's official transcript
  query_prerequisite(course)   - catalog prerequisites of one course
  search_course_offering(q)    - 2026-27 Term 1 offering with section times

The search corpus contains NOISE:
  - the OLD GE rule version (2017-22 admits: "GEW counts toward the GE core")
    next to the current one (2023-24+ admits: "GEW does not count") — only
    the current one applies to a 2024-admit student;
  - the FinTech-stream study scheme next to the Quantitative Finance one;
  - FIN4120 is offered although its prerequisite is only in progress —
    the prerequisite trap must be discovered via tools and excluded;
  - the remaining major-required courses that are NOT offered this term
    must be discovered as "not offered" through the offering search.

The instance is planted-then-noise constructed so EXACTLY ONE valid plan
exists. Scoring is pure program check: nine weighted rule checks (required
coverage 0.20, prereq-trap avoidance 0.10, no unknown/extra courses 0.10,
GE foundation GFH1000 0.10, CEC4000 0.15, one GE-area course 0.10, GE-core
per-term limit 0.05, zero conflicts 0.10, exact 18-unit load 0.10).
"""
from __future__ import annotations

import glob
import itertools
import json
import os
import re
from typing import Any, Dict, List, Optional

from .base import BaseTask, TaskResult


def _norm(v):
    return str(v).strip().lower() if isinstance(v, str) else v

CHECK_WEIGHTS = {
    "required_covered": 0.15,
    "trap_avoided": 0.10,
    "no_unknown_or_extra": 0.10,
    "gfh_foundation": 0.10,
    "cec4000": 0.15,
    "area_course": 0.10,
    "ge_limit": 0.05,
    "no_conflicts": 0.10,
    "unit_exact": 0.10,
    "friday_free": 0.05,
}

ENROLL_TOOL = {"type": "function", "function": {
    "name": "enroll",
    "description": "Submit your course enrollment for this term. This is the "
                   "official way to submit your final registration plan: "
                   "call it once with all your enrollments, or call it once "
                   "per course.",
    "parameters": {"type": "object", "properties": {
        "enrollments": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "course": {"type": "string"},
                "section": {"type": "string"}},
            "required": ["course", "section"]}},
        "course": {"type": "string"},
        "section": {"type": "string"}},
}}}

TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "kb_retrieve",
        "parameters": {"type": "object", "properties": {
            "q": {"type": "string"}}, "required": ["q"]}}},
    {"type": "function", "function": {
        "name": "sis_fetch",
        "parameters": {"type": "object", "properties": {
            "flt": {"type": "string"}}, "required": []}}},
    {"type": "function", "function": {
        "name": "cat_map",
        "parameters": {"type": "object", "properties": {
            "cid": {"type": "string"}}, "required": ["cid"]}}},
    {"type": "function", "function": {
        "name": "crs_feed",
        "parameters": {"type": "object", "properties": {
            "q": {"type": "string"}}, "required": ["q"]}}},
    ENROLL_TOOL,
]



# registry / study-scheme knowledge base (search corpus, includes noise)
REGISTRY_KB = [
    {"keys": ["ge", "general education", "通识", "area", "foundation"],
     "doc": "Registry 通识教育 (/page/21) — applicable to 2023-24 and later "
            "admits",
     "text": "GE = 18 units: foundations GFH1000 (In Dialogue with Humanity, "
             "3u) + GFN1000 (In Dialogue with Nature, 3u); plus one course "
             "from EACH area: A (Chinese culture heritage), B (nature, "
             "science & technology), C (society & culture), D (self & "
             "humanity). At most 2 GE courses may be taken in one term. "
             "GEW-type courses do NOT count toward the GE core (at most 6 "
             "units may be counted as free electives). GFH and GFN cannot be "
             "taken in the same term."},
    {"keys": ["gew", "2017", "old", "ge"],
     "doc": "Registry 通识教育修读规则 2017-18 to 2022-23 admits (OLD VERSION)",
     "text": "OLD RULE (2017-22 admits): GEW-type courses COUNT toward the "
             "GE core. Applicable to students admitted in 2017-18, "
             "2018-19, 2019-20, 2020-21, 2021-22 and 2022-23."},
    {"keys": ["cec", "国情"],
     "doc": "Registry GE notes / course offering appendix",
     "text": "CEC requirement: exactly 2 CEC courses — one at 1000/2000 "
             "level and one at 3000/4000 level. CEC1000/CEC2000 may be "
             "declared as GEW1001/GEW2001 (GEW-type, not GE core)."},
    {"keys": ["unit", "credit", "load", "学分"],
     "doc": "Registry 选课和改选 (/page/24)",
     "text": "Term load: at least 9 and at most 18 units per regular term; "
             "at most 6 units per summer session; at most 39 units per "
             "academic year."},
    {"keys": ["financial engineering", "study scheme", "major requirement",
              "quantitative"],
     "doc": "Study Scheme — Financial Engineering, 2024-25 admit, "
            "Quantitative Finance stream",
     "text": "Major required: ECO3121, FIN3080, FIN3210, FIN4110, FIN4120, "
             "MAT2002, MAT3007, MAT3300, STA2002. Major electives: 5 courses "
             "from the QF elective pool. School package: 10 courses / 28 "
             "units."},
    {"keys": ["fintech"],
     "doc": "Study Scheme — Financial Engineering, FinTech stream "
            "(DIFFERENT STREAM)",
     "text": "FinTech stream required: CSC3001, CSC3100, ECO3121, FIN3080, "
             "FIN3210, DDA3020, MAT3007, STA2002. Major electives: 6 courses "
             "from the FinTech pool. [Only applies to FinTech-stream "
             "students.]"},
    {"keys": ["prerequis", "先修", "registration", "选课", "withdraw", "w "],
     "doc": "Registry 选课和改选 (/page/24)",
     "text": "Prerequisites must be COMPLETED (grade earned) before a course "
             "can be taken; a course in progress does not satisfy a "
             "prerequisite; withdrawn (W) courses do not count as completed. "
             "Term load: at least 9 and at most 18 units per term."},
]

PREREQ_TABLE = {
    "ECO3121": ["ECO2011"],
    "FIN3080": ["FIN2010"],
    "FIN3210": ["ECO3121", "FIN2010"],
    "FIN4110": ["FIN3080"],
    "FIN4120": ["FIN3080"],
    "MAT3007": ["MAT2040"],
    "MAT3300": ["MAT2040"],
}


def discover(root: str) -> List[Dict[str, Any]]:
    out = []
    for f in sorted(glob.glob(os.path.join(root, "*.json"))):
        with open(f, encoding="utf-8") as fh:
            out.append(json.load(fh))
    return out


def to_min(t: str) -> int:
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def sessions_overlap(s1: dict, s2: dict) -> bool:
    if not set(s1["days"]) & set(s2["days"]):
        return False
    return to_min(s1["start"]) < to_min(s2["end"]) and \
        to_min(s2["start"]) < to_min(s1["end"])


class SchedulingTask(BaseTask):
    task_type = "scheduling"

    # ------------------------------------------------------------ prompt
    def _build_prompt(self) -> str:
        st = self.spec["student"]
        return (
            f"You are {st['name']} ({st['id']}), a {st['major']} student "
            f"({st['stream']} stream, admitted {st['admitted']}). Plan your "
            f"course registration for {self.spec['term']}.\n\n"
            "You have a set of tools available. Their names and parameter "
            "names are intentionally terse — no documentation is provided. "
            "Figure out what each tool does and how to use it by trying "
            "them; if a call fails or returns something unexpected, adjust "
            "and retry. Do not ask the user for anything, and do not stop "
            "at a plain-text answer: gather the facts yourself through the "
            "tools:\n"
            "- your remaining requirements (which major-required courses are "
            "still missing, including retakes of withdrawn courses);\n"
            "- the GE structure and rules, and your GE progress;\n"
            "- the CEC requirement;\n"
            "- the term unit limits;\n"
            "- which courses are offered this term, their sections and "
            "session times;\n"
            "- the prerequisites of the courses you consider.\n\n"
            "CAUTION: whatever the tools return may include documents that "
            "do NOT apply to you (rules for other admission years, other "
            "streams, other programmes) — use only what applies to a "
            "2024-admit Quantitative Finance student. Some offered courses "
            "may be ineligible for you (e.g. their prerequisite is only in "
            "progress).\n\n"
            "TASK: audit your remaining requirements and produce your "
            "registration plan for this term.\n\n"
            "OUTPUT REQUIREMENTS (follow strictly):\n"
            "1. When you finish researching, output one fenced JSON block "
            "mapping every chosen course code to its chosen section_id, "
            'e.g. {"ECO3121": "ECO3121-01", "GFH1000": "GFH1000-01", ...}.\n'
            "2. Then output a readable markdown timetable grouped by "
            "weekday, followed by a short audit justification (why each "
            "course is taken / why some are excluded).")

    # ------------------------------------------------------ fake tool back-end
    def _tool_search_registry(self, args) -> dict:
        q = str(args.get("query", "")).lower()
        hits = [f"[{item['doc']}]\n{item['text']}"
                for item in REGISTRY_KB
                if any(k in q for k in item["keys"])]
        if not hits:
            hits = ["No matching registry document found. Try keywords like "
                    "'GE', 'CEC', 'units', 'study scheme', 'registration'."]
        return {"results": hits}

    def _tool_query_transcript(self, args) -> dict:
        term = str(args.get("term", "") or "").lower()
        rows = [t for t in self.spec["transcript"]
                if not term or term in t["term"].lower()]
        return {"transcript": rows,
                "note": "W = withdrawn; DI = distinction."}

    def _tool_query_prerequisite(self, args) -> dict:
        code = str(args.get("course_code", "")).replace(" ", "").upper()
        if code in PREREQ_TABLE:
            return {"course": code, "prerequisites": PREREQ_TABLE[code]}
        return {"course": code, "error": "course not found in catalog"}

    def _tool_search_offering(self, args) -> dict:
        q = str(args.get("query", "")).lower()
        out = []
        for c in self.spec["courses"]:
            hay = (c["code"] + " " + c["title"]).lower()
            tag_hit = ("ge" in q.split() and c.get("ge")) or \
                      ("cec" in q.split() and c.get("cec")) or \
                      ("foundation" in q and c.get("ge_foundation"))
            prefix = len(q) >= 2 and c["code"].lower().startswith(q)
            if c["code"].lower() in q or tag_hit or prefix or \
                    any(w in hay for w in q.split() if len(w) > 3):
                out.append({
                    "code": c["code"], "title": c["title"],
                    "units": c["units"],
                    "tags": [t for t, on in (
                        ("GE", c.get("ge")), ("CEC", c.get("cec"))) if on],
                    "sections": c["sections"]})
        for code in self.spec["audit"].get("required_not_offered", []):
            if code.lower() in q:
                out.append({"code": code, "offered": False,
                            "note": "not offered in 2026-27 Term 1"})
        if not out:
            return {"results": [],
                    "hint": "no match — try a course code or keywords like "
                            "'GE', 'CEC', 'FIN', 'MAT', 'foundation'"}
        return {"term": "2026-27 Term 1", "results": out}

    def _dispatch_tool(self, name: str, args: dict,
                       enrollments: Optional[list] = None) -> dict:
        try:
            if name == "enroll":
                if enrollments is None:
                    return {"error": "enrollment channel not open"}
                entries = args.get("enrollments")
                if entries is None and args.get("course"):
                    entries = [{"course": args.get("course"),
                                "section": args.get("section")}]
                if not isinstance(entries, list):
                    return {"error": "invalid enroll arguments"}
                accepted = []
                for e in entries:
                    if not isinstance(e, dict) or not e.get("course")                             or not e.get("section"):
                        continue
                    entry = {"course": str(e["course"]).replace(" ", "").upper(),
                             "section": str(e["section"]).replace(" ", "").upper()}
                    enrollments.append(entry)
                    accepted.append(entry)
                # blind accept: no existence/conflict/prereq validation here —
                # correctness is judged by the scorer; otherwise the model
                # could brute-force the unique solution via SIS feedback
                return {"status": "submitted (pending audit)",
                        "accepted": accepted}
            if name == "kb_retrieve":
                return self._tool_search_registry(args)
            if name == "sis_fetch":
                return self._tool_query_transcript(args)
            if name == "cat_map":
                return self._tool_query_prerequisite(args)
            if name == "crs_feed":
                return self._tool_search_offering(args)
            return {"error": f"unknown tool {name}"}
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}

    # ------------------------------------------------------------ parsing
    def _parse_answer(self, text: str, all_text: str = "") -> Dict[str, str]:
        from ..client import parse_judge_json
        codes = [c["code"] for c in self.spec["courses"]]
        ans: Dict[str, str] = {}
        parsed = parse_judge_json(text or "") or {}
        if isinstance(parsed, dict):
            for k, v in parsed.items():
                code = str(k).replace(" ", "").upper()
                if code in codes:
                    ans[code] = str(v).replace(" ", "").upper()
        if len(ans) < 2:  # regex fallback over final + accumulated text
            pat = r"(" + "|".join(codes) + r")\s*[:=\-]*\s*(?:section\s*)?" \
                  r"([A-Z]*-?\d{2})\b"
            for m in re.finditer(pat, ((text or "") + " " + all_text).upper()):
                ans.setdefault(m.group(1), m.group(2))
        # normalize short section ids: "02" -> "CODE-02"
        return {c: (s if s.startswith(c) else f"{c}-{s.lstrip('-')}")
                for c, s in ans.items()}

    # -------------------------------------------------------------- eval
    def _evaluate(self, ans: Dict[str, str]):
        audit = self.spec["audit"]
        by_code = {c["code"]: c for c in self.spec["courses"]}
        chosen: Dict[str, dict] = {}
        unknown: List[str] = []
        for code, sid in ans.items():
            sec = next((s for s in by_code[code]["sections"]
                        if s["section_id"].upper() == sid), None) \
                if code in by_code else None
            if sec is None:
                unknown.append(code)
            else:
                chosen[code] = sec

        checks = {}
        miss = [c for c in audit["required_take"] if c not in chosen]
        checks["required_covered"] = not miss
        checks["trap_avoided"] = not any(
            c in chosen for c in audit["required_ineligible"])
        allowed = set(audit["required_take"]) \
            | {audit["gfh_required"], audit["cec4000_required"]} \
            | set(audit["area_choose_from"])
        extra = [c for c in chosen if c not in allowed]
        checks["no_unknown_or_extra"] = not unknown and not extra
        checks["gfh_foundation"] = audit["gfh_required"] in chosen
        checks["cec4000"] = audit["cec4000_required"] in chosen
        areas = [c for c in chosen if c in audit["area_choose_from"]]
        checks["area_course"] = len(areas) == 1
        ge_core = [c for c in chosen
                   if by_code[c].get("ge") and not by_code[c].get("cec")]
        checks["ge_limit"] = len(ge_core) <= audit["ge_max_per_term"]
        conflicts = []
        for a, b in itertools.combinations(list(chosen), 2):
            for s1 in chosen[a]["sessions"]:
                for s2 in chosen[b]["sessions"]:
                    if sessions_overlap(s1, s2):
                        conflicts.append({
                            "pair": [a, b],
                            "why": (f"{'+'.join(s1['days'])} "
                                    f"{s1['start']}-{s1['end']} overlaps "
                                    f"{'+'.join(s2['days'])} "
                                    f"{s2['start']}-{s2['end']}")})
                        break
        checks["no_conflicts"] = not conflicts
        # Friday must stay completely free (term timetable rule)
        fri = [(c, '+'.join(s['days']), s['start'])
               for c, sec in chosen.items() for s in sec["sessions"]
               if "Fr" in s["days"]]
        checks["friday_free"] = not fri
        units = sum(by_code[c]["units"] for c in chosen)
        checks["unit_exact"] = units == audit["unit_exact"]

        score = sum(w for k, w in CHECK_WEIGHTS.items() if checks.get(k))
        detail = {"checks": checks, "score": round(score, 4),
                  "chosen": {k: v["section_id"] for k, v in chosen.items()},
                  "unknown_or_missing": unknown, "extra": extra,
                  "ge_core": ge_core, "conflicts": conflicts, "friday_sessions": fri, "units": units,
                  "reference_solution": self.spec.get("unique_solution")}
        return score, detail

    # --------------------------------------------------------------- run
    def run(self, client, model: str,
            context: Optional[Dict[str, Any]] = None) -> TaskResult:
        messages: List[Dict[str, Any]] = [
            {"role": "user", "content": self._build_prompt()}]
        tool_trace: List[Dict[str, Any]] = []
        self._enrollments: List[Dict[str, str]] = []   # reset per run
        all_content: List[str] = []
        latencies, usage_acc = [], {}
        max_turns = self.run_cfg.get("scheduling_max_turns", 16)
        final_content = None
        error = None

        os.makedirs(self.artifacts_dir, exist_ok=True)
        for turn in range(max_turns + 1):
            force_final = turn >= max_turns
            res = client.chat(
                model, messages,
                tools=None if force_final else TOOL_SCHEMAS,
                max_tokens=self.run_cfg.get("scheduling_max_tokens", 32768),
                temperature=self.run_cfg.get("temperature", 0.2))
            latencies.append(res.latency)
            for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
                usage_acc[k] = usage_acc.get(k, 0) + \
                    int(res.usage.get(k, 0) or 0)
            if not res.ok:
                error = res.error
                break
            if res.content:
                all_content.append(res.content)

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
                    result = self._dispatch_tool(name, args,
                                                 enrollments=self._enrollments)
                    tool_trace.append({"tool": name, "args": args})
                    messages.append({"role": "tool",
                                     "tool_call_id": tc.get("id"),
                                     "name": name,
                                     "content": json.dumps(
                                         result, ensure_ascii=False)})
                continue

            final_content = res.content
            break

        if error:
            return TaskResult(task_id=self.task_id, task_type=self.task_type,
                              model=model, provider=getattr(client, "label", "?"),
                              error=error, latency=sum(latencies),
                              usage=usage_acc)

        reply_path = os.path.join(self.artifacts_dir,
                                  f"{self.task_id}__{model}.md")
        with open(reply_path, "w", encoding="utf-8") as f:
            f.write(final_content or "")

        # answer channel: enroll submissions take priority; fall back to parsing the final text
        if self._enrollments:
            channel = "enroll_tool"
            codes = [c["code"] for c in self.spec["courses"]]
            ans: Dict[str, str] = {}
            for e in self._enrollments:            # last-wins per course
                if e["course"] in codes:
                    sec = e["section"]
                    ans[e["course"]] = sec if sec.startswith(e["course"])                         else f"{e['course']}-{sec.lstrip('-')}"
        else:
            channel = "text"
            ans = self._parse_answer(final_content, " ".join(all_content))
        score, detail = self._evaluate(ans)
        detail["answer"] = ans
        detail["answer_channel"] = channel
        detail["enrollments"] = list(self._enrollments)
        detail["tool_trace"] = tool_trace
        detail["turns"] = len(latencies)
        return TaskResult(
            task_id=self.task_id, task_type=self.task_type,
            model=model, provider=getattr(client, "label", "?"),
            score=score, max_score=1.0, detail=detail,
            artifacts=[reply_path], latency=sum(latencies), usage=usage_acc)

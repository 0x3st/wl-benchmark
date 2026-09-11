#!/usr/bin/env python3
"""Generate the term-planning scheduling instance (v2, planted unique solution).

Story: a Financial Engineering (Quantitative Finance stream) student plans
their 2026-27 Term 1 registration. The model receives:
  1. a condensed study scheme (major requirements + university core +
     registration rules — grounded in the official study scheme circular and
     the Registry pages 选课和改选 /page/24 and 通识教育 /page/21),
  2. the transcript through 2025-26 Term 2 (2026-27 has not been selected),
  3. the course offering list for 2026-27 Term 1 with fully randomized
     session times.

Construction (plant-then-noise, deterministic):
  1. PLANT the intended plan: ECO3121, FIN3080, MAT3007 (major required,
     incl. two W retakes) + GFH1000 (last foundation course) + CEC4000
     (remaining CEC) + one GE-area course — pairwise non-overlapping times.
  2. NOISE A: alternate sections for planted courses, each deliberately
     overlapping a planted session of ANOTHER course (taking the alternate
     instead of the planted section always clashes).
  3. NOISE B: decoy GE-area courses — every session overlaps a planted
     session, so they are unusable.
  4. TRAP: FIN4120 is offered although its prerequisite (FIN3080) is only
     in progress — it must be excluded.
  5. VERIFY with the solver: exactly one valid plan, the planted one.

Usage:
  python3 tools/gen_scheduling.py [--seed 20260916] \
      [--out wl_benchmark/tasks_data/scheduling/term-plan-2627t1.json]
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import random

# --------------------------------------------------------------------- data
STUDENT = {
    "name": "WU, Lei",
    "id": "124020372",
    "admitted": "Sep 2024",
    "major": "Financial Engineering",
    "stream": "Quantitative Finance",
}

# transcript through 2025-26 Term 2 (2026-27 not selected yet).
# W = withdrawn (does NOT count as passed).
TRANSCRIPT = [
    ("2024-25 T1", "ACT2111", "Introductory Financial Accounting", 3, "B+"),
    ("2024-25 T1", "CSC1001", "Introduction to Computer Science", 3, "A"),
    ("2024-25 T1", "ECO2011", "Basic Microeconomics", 3, "A-"),
    ("2024-25 T1", "ENG1001", "English Bridge Program", 3, "B+"),
    ("2024-25 T1", "MAT1001", "Calculus I", 3, "B+"),
    ("2024-25 T1", "PED1001", "Physical Education", 1, "A"),
    ("2024-25 T2", "CHI1000", "Chinese", 3, "B+"),
    ("2024-25 T2", "CSC1002", "Computational Laboratory", 1, "A-"),
    ("2024-25 T2", "ENG1002", "English for Academic Purposes I", 3, "B+"),
    ("2024-25 T2", "FIN2010", "Financial Management", 3, "B+"),
    ("2024-25 T2", "ITE1000", "Information Technology", 1, "DI"),
    ("2024-25 T2", "MAT1002", "Calculus II", 3, "B"),
    ("2024-25 T2", "PED1002", "Fitness and Health", 1, "B+"),
    ("2024-25 T2", "STA2001", "Probability and Statistics I", 3, "B+"),
    ("2024-25 Summer", "STA2002", "Probability and Statistics II", 3, "B+"),
    ("2025-26 T1", "ECO3121", "Introductory Econometrics", 3, "W"),
    ("2025-26 T1", "ENG2001", "English for Academic Purposes II", 3, "B"),
    ("2025-26 T1", "GEA2000", "Modern Chinese History and Culture", 3, "B+"),
    ("2025-26 T1", "GFN1000", "In Dialogue with Nature", 3, "B"),
    ("2025-26 T1", "MAT2040", "Linear Algebra", 3, "C"),
    ("2025-26 T1", "MAT3007", "Optimization", 3, "W"),
    ("2025-26 T2", "CSC3002", "C/C++ Programming", 3, "A-"),
    ("2025-26 T2", "CSC3100", "Data Structures", 3, "B+"),
    ("2025-26 T2", "DDA3020", "Machine Learning", 3, "B-"),
    ("2025-26 T2", "ENG2002S", "English for Science and Engineering", 3, "A-"),
    ("2025-26 T2", "FIN2020", "Foundation of Finance", 3, "A-"),
    ("2025-26 T2", "GEW2001", "Introduction to Marxism", 3, "DI"),
    ("2025-26 T2", "MAT2002", "Ordinary Differential Equations", 3, "B-"),
]

MAJOR_REQUIRED = ["ECO3121", "FIN3080", "FIN3210", "FIN4110", "FIN4120",
                  "MAT2002", "MAT3007", "MAT3300", "STA2002"]
MAJOR_ELECTIVE_POOL = ["ACT4253", "CSC2003", "CSC3001", "CSC3002", "CSC3100",
                       "CSC3150", "CSC3170", "CSC3180", "CSC4008", "DDA3020",
                       "DDA4210", "DMS3002", "ECO3160", "ECE4007", "ERG3020",
                       "FIN4060", "FIN4080", "FIN4110", "FIN4120", "FIN4210",
                       "FIN4231", "FMA4200", "FMA4800", "FTE4003", "FTE4312",
                       "FTE4999", "MAT3280", "MAT4500", "MKT4220", "RMS4060",
                       "STA3001", "STA3020", "STA4001", "STA4003", "STA4020"]
PREREQS = {
    "ECO3121": ["ECO2011"],
    "FIN3080": ["FIN2010"],
    "FIN3210": ["ECO3121", "FIN2010"],
    "FIN4110": ["FIN3080"],
    "FIN4120": ["FIN3080"],
    "MAT3007": ["MAT2040"],
    "MAT3300": ["MAT2040"],
}

# courses offered in 2026-27 Term 1 (times synthesized)
OFFERED = {
    # remaining major required, offered & eligible this term
    "ECO3121": {"title": "Introductory Econometrics", "units": 3,
                "prereq": ["ECO2011"]},
    "FIN3080": {"title": "Investment Analysis and Portfolio Management",
                "units": 3, "prereq": ["FIN2010"]},
    "MAT3007": {"title": "Optimization", "units": 3,
                "prereq": ["MAT2040"]},
    # prereq trap: FIN3080 only in progress
    "FIN4120": {"title": "Fixed Income Securities Analysis", "units": 3,
                "prereq": ["FIN3080"]},
    # GE foundation (the last one: GFN1000 already completed)
    "GFH1000": {"title": "In Dialogue with Humanity", "units": 3,
                "prereq": [], "ge": True, "ge_foundation": True},
    # GE area courses (GE-C usable; GE-B / GE-D planted as conflict decoys)
    "GEC2002": {"title": "Living Sociology", "units": 3,
                "prereq": [], "ge": True, "ge_area": "C"},
    "GEB2001": {"title": "Environmental Science", "units": 3,
                "prereq": [], "ge": True, "ge_area": "B"},
    "GED2001": {"title": "Introduction to Philosophy", "units": 3,
                "prereq": [], "ge": True, "ge_area": "D"},
    # CEC: the remaining 3000/4000-level CEC course, mandatory this term
    "CEC4000": {"title": "Socialism with Chinese Characteristics for A New Era",
                "units": 3, "prereq": [], "cec": True},
}

# audit ground truth (scoring only; never printed in the model prompt)
AUDIT = {
    "required_take": ["ECO3121", "FIN3080", "MAT3007"],
    "required_ineligible": ["FIN4120"],        # prereq FIN3080 in progress
    "required_not_offered": ["FIN3210", "FIN4110", "MAT3300"],
    "gfh_required": "GFH1000",                 # last GE foundation course
    "cec4000_required": "CEC4000",             # remaining CEC course
    "area_choose_from": ["GEC2002", "GEB2001", "GED2001"],
    "ge_max_per_term": 2,
    "unit_exact": 18,
    "friday_free": True,
}

# randomized session times: starts on a 15-min grid between 08:00 and 19:00,
# mixed durations — deliberately more irregular than a fixed lecture grid.
START_MIN, END_MIN = 8 * 60, 19 * 60
STEP = 15
DURATIONS = [45, 50, 55, 60, 75, 80, 90, 105, 120, 150]
NO_FRIDAY_POOL = [["Mo"], ["Tu"], ["We"], ["Th"], ["Sa"],
    ["Mo", "Tu"], ["Tu", "Th"], ["Mo", "We"], ["Mo", "Th"], ["We", "Th"]]
DAY_POOL = [["Mo"], ["Tu"], ["We"], ["Th"], ["Fr"], ["Sa"],
            ["Mo", "Tu"], ["Tu", "Th"], ["We", "Fr"], ["Mo", "We"],
            ["Mo", "Th"], ["Tu", "Fr"], ["We", "Th"]]


# ------------------------------------------------------------------- helpers
def to_min(t: str) -> int:
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def to_hhmm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def sessions_overlap(s1: dict, s2: dict) -> bool:
    if not set(s1["days"]) & set(s2["days"]):
        return False
    return to_min(s1["start"]) < to_min(s2["end"]) and \
        to_min(s2["start"]) < to_min(s1["end"])


def gen_planted_sections(rng: random.Random, code: str, busy: list,
                         friday_free: bool = True) -> list:
    """The planted (usable) section: Lecture + Tutorial (+ optional Lab),
    deliberately placed away from every already-planted session.
    friday_free=True keeps every session off Friday (the term rule)."""
    pool = NO_FRIDAY_POOL if friday_free else DAY_POOL
    sessions, plan = [], ["Lecture", "Tutorial"]
    if rng.random() < 0.4:
        plan.append("Lab")
    for stype in plan:
        for _try in range(200):
            days = rng.choice(pool)
            start = rng.randrange(START_MIN, END_MIN - 44, STEP)
            dur = rng.choice(DURATIONS)
            cand = {"days": days, "start": to_hhmm(start),
                    "end": to_hhmm(min(start + dur, 21 * 60)), "type": stype}
            if not any(sessions_overlap(cand, s) for s in busy):
                sessions.append(cand)
                busy.append(cand)
                break
        else:
            raise RuntimeError(f"cannot place {code} {stype}")
    return [{"section_id": f"{code}-01", "sessions": sessions}]


def gen_conflicting_sessions(rng: random.Random, planted_by_course: dict,
                             exclude_course: str, plan: list) -> list:
    """Noise sessions: each deliberately overlaps some planted session of a
    DIFFERENT course, so this section can never be part of a valid plan
    (every valid plan contains all planted sessions)."""
    sessions = []
    for stype in plan:
        others = {c: ss for c, ss in planted_by_course.items()
                  if c != exclude_course}
        target = rng.choice(list(others.values()))
        anchor = rng.choice(target)
        dur = rng.choice(DURATIONS)
        tstart, tend = to_min(anchor["start"]), to_min(anchor["end"])
        day = rng.choice(anchor["days"])
        days = [day] if rng.random() < 0.5 else list(anchor["days"])
        lo = max(START_MIN, tstart - dur + STEP)
        hi = min(tend - STEP, END_MIN - dur)
        start = rng.randint(lo, hi) if lo <= hi else tstart
        sessions.append({"days": days, "start": to_hhmm(start),
                         "end": to_hhmm(min(start + dur, 21 * 60)),
                         "type": stype})
    return sessions


def sections_overlap(a: list, b: list) -> bool:
    return any(sessions_overlap(x, y) for x in a for y in b)


def enumerate_plans(offered: dict, friday_free: bool = True) -> list:
    """Enumerate every registration plan that obeys all rules; the list must
    contain exactly the planted solution."""
    audit = AUDIT
    chosen_fixed = audit["required_take"] + \
        [audit["gfh_required"], audit["cec4000_required"]]
    plans = []
    for area in audit["area_choose_from"]:
        chosen_courses = chosen_fixed + [area]
        section_lists = [offered[c]["sections"] for c in chosen_courses]
        for combo in itertools.product(*section_lists):
            units = sum(offered[c]["units"] for c in chosen_courses)
            if units != audit["unit_exact"]:
                continue
            ge_core = [c for c in chosen_courses
                       if offered[c].get("ge") and not offered[c].get("cec")]
            if len(ge_core) > audit["ge_max_per_term"]:
                continue
            ok = all(not sections_overlap(a["sessions"], b["sessions"])
                     for a, b in itertools.combinations(combo, 2))
            if ok and friday_free and any(
                    "Fr" in s["days"] for sec in combo for s in sec["sessions"]):
                ok = False
            if ok:
                plans.append({c: s["section_id"]
                              for c, s in zip(chosen_courses, combo)})
    return plans


def generate(seed: int) -> dict:
    rng = random.Random(seed)
    planted_by_course: dict = {}
    offered: dict = {}

    # --- 1. plant the unique solution ---
    area = rng.choice(AUDIT["area_choose_from"])
    solution_courses = AUDIT["required_take"] + \
        [AUDIT["gfh_required"], AUDIT["cec4000_required"], area]
    busy: list = []
    solution = {}
    for code in solution_courses:
        secs = gen_planted_sections(rng, code, busy, friday_free=True)
        offered[code] = {**OFFERED[code], "sections": secs}
        planted_by_course[code] = secs[0]["sessions"]
        solution[code] = secs[0]["section_id"]

    # 让"周五空闲"约束真正起决定作用：另一门 area 课时间上完全可行，
    # 但它的两节课都排在周五 —— 只有遵守"周五全空"的方案才唯一。
    alt_area = next(c for c in AUDIT["area_choose_from"]
                    if c not in solution_courses)
    fri_sessions = []
    for stype, start in (("Lecture", 9 * 60), ("Tutorial", 14 * 60)):
        fri_sessions.append({"days": ["Fr"], "start": to_hhmm(start),
                             "end": to_hhmm(start + 90), "type": stype})
    offered[alt_area] = {**OFFERED[alt_area],
                         "sections": [{"section_id": f"{alt_area}-01",
                                       "sessions": fri_sessions}]}
    planted_by_course[alt_area] = fri_sessions

    # --- 2. noise A: alternate sections for planted courses ---
    for code in solution_courses:
        if rng.random() < 0.7:
            alt = gen_conflicting_sessions(
                rng, planted_by_course, code, ["Lecture", "Tutorial"])
            offered[code]["sections"].append(
                {"section_id": f"{code}-02", "sessions": alt})

    # --- 3. noise B: decoy GE-area courses (all sessions conflict) ---
    for code in OFFERED:
        if OFFERED[code].get("ge_area") and code not in solution_courses \
                and code not in offered:
            plan = ["Lecture", "Tutorial"]
            if rng.random() < 0.4:
                plan.append("Lab")
            secs = gen_conflicting_sessions(rng, planted_by_course, code, plan)
            offered[code] = {**OFFERED[code],
                             "sections": [{"section_id": f"{code}-01",
                                           "sessions": secs}]}

    # --- 4. trap course FIN4120: excluded by the prereq rule, times free ---
    trap_sessions, plan = [], ["Lecture", "Tutorial"]
    if rng.random() < 0.4:
        plan.append("Lab")
    for stype in plan:
        days = rng.choice(DAY_POOL)
        start = rng.randrange(START_MIN, END_MIN - 44, STEP)
        dur = rng.choice(DURATIONS)
        trap_sessions.append({"days": days, "start": to_hhmm(start),
                              "end": to_hhmm(min(start + dur, 21 * 60)),
                              "type": stype})
    offered["FIN4120"] = {**OFFERED["FIN4120"],
                          "sections": [{"section_id": "FIN4120-01",
                                        "sessions": trap_sessions}]}

    # --- 5. verify: exactly one valid plan, and it is the planted one ---
    plans = enumerate_plans(offered, friday_free=True)
    if len(plans) != 1 or plans[0] != solution:
        raise RuntimeError(
            f"verification failed: {len(plans)} plans, expected the planted one")
    relaxed = enumerate_plans(offered, friday_free=False)
    if len(relaxed) < 2:
        raise RuntimeError(
            f"Friday constraint is not binding ({len(relaxed)} plans relaxed)")

    return {
        "id": "term-plan-2627t1-01",
        "term": "2026-27 Term 1",
        "seed": seed,
        "student": STUDENT,
        "study_scheme": _scheme_summary(),
        "transcript": [{"term": t, "code": c, "title": ti,
                        "units": u, "grade": g}
                       for t, c, ti, u, g in TRANSCRIPT],
        "courses": [
            {"code": c, "title": m["title"], "units": m["units"],
             "prereq": m["prereq"], "ge": m.get("ge", False),
             "ge_area": m.get("ge_area"), "ge_foundation":
                 m.get("ge_foundation", False),
             "cec": m.get("cec", False),
             "sections": m["sections"]}
            for c, m in offered.items()],
        "audit": AUDIT,
        "unique_solution": solution,
        "_n_solutions": len(plans),
        "_n_solutions_relaxed": len(relaxed),
    }


def _scheme_summary() -> dict:
    return {
        "programme": "Financial Engineering — Quantitative Finance stream",
        "major_required": MAJOR_REQUIRED,
        "major_electives": {
            "needed": 2,
            "note": "3 of 5 already completed (CSC3002, CSC3100, DDA3020); "
                    "no eligible major elective is offered this term",
            "pool": MAJOR_ELECTIVE_POOL,
        },
        "university_core_done": {
            "Chinese (3u)": "CHI1000",
            "English (12u)": "ENG1001/1002/2001/2002S",
            "IT (1u)": "ITE1000",
            "PE (2u)": "PED1001/1002",
        },
        "ge": {
            "total_units": 18,
            "structure": "foundations: GFH1000 (with humanity, 3u) + "
                         "GFN1000 (with nature, 3u); areas: one course from "
                         "each of GE-A (Chinese culture heritage), GE-B "
                         "(nature, science & technology), GE-C (society & "
                         "culture), GE-D (self & humanity)",
            "completed": ["GFN1000 (foundation)", "GEA2000 (area A)"],
            "remaining": ["GFH1000 (foundation — the last one)",
                          "one course from each of areas B / C / D"],
            "rules": [
                "At most 2 GE courses may be taken in one term (Registry).",
                "GEW-type courses (e.g. CEC declared as GEW, GEW2001) do NOT "
                "count toward the GE core — at most 6 units may be counted as "
                "free electives (Registry).",
                "GFH and GFN cannot be taken in the same term "
                "(GFN1000 is already completed).",
            ],
            "source": "registry.cuhk.edu.cn 通识教育 (/page/21)",
        },
        "cec": {
            "requirement": "exactly 2 CEC courses overall; you completed the "
                           "2000-level one (GEW2001 = CEC2000)",
            "remaining": "CEC4000 must be taken this term (per your "
                         "graduation audit)",
        },
        "registration_rules": [
            "You are planning for 2026-27 Term 1 ONLY.",
            "Prerequisites must be COMPLETED (grade earned) before the course "
            "can be taken; a course in progress does not satisfy a "
            "prerequisite; withdrawn (W) courses do not count as completed.",
            "Take every remaining major-required course that is offered this "
            "term and whose prerequisites you have completed. Remaining "
            "required courses not offered this term are taken in later terms.",
            "Register ONLY for courses in your remaining-requirements audit — "
            "no free electives this term.",
            "Term load: the Registry allows 9-18 units per term; your plan "
            "must take a FULL 18-unit load.",
            "Timetable constraint: your weekly timetable must leave FRIDAY "
            "completely free — no Friday sessions in any chosen section.",
        ],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=20260916)
    ap.add_argument("--out",
                    default="wl_benchmark/tasks_data/scheduling/"
                            "term-plan-2627t1.json")
    args = ap.parse_args()
    inst = generate(args.seed)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(inst, f, ensure_ascii=False, indent=1)
    print(f"wrote {args.out} (seed={inst['seed']}, "
          f"solutions={inst['_n_solutions']})")
    print("unique solution:", json.dumps(inst["unique_solution"]))


if __name__ == "__main__":
    main()

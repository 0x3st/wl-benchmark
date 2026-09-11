#!/usr/bin/env python3
"""Generate the factor-mining research instance (v4 — harder).

Difficulty jump vs v3:
  * the true model carries TWO library factors (MOM + VAL) out of five
    candidates -> the model must mine a SUBSET, not pick one;
  * the estimated regression has four unknowns (const + MKT + 2 factors)
    -> 4x4 normal equations by hand;
  * t-statistics of both factor betas are asked -> (X'X)^-1 diagonal and
    the residual variance must be computed by hand;
  * an OUT-OF-SAMPLE test is asked: estimate on 2024, predict the 2025
    months, report RMSE -> the second year of history stops being noise.

Universe (fetched through opaque tools):
  SZTECH / SZTECH_V2 (decoy) / MKT3000 / RF /
  factor library: MOM, VAL (true) + SIZE, VOL, LIQ (noise) / BTC (distraction)

Identification is rejection-sampled until clean:
  t(MOM) > 3 and t(VAL) > 3 in the true model,
  |t(decoy)| < 1.5 when appended to the true model,
  |corr(MOM, VAL)| < 0.4.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random

MONTHS = [f"2024-{m:02d}" for m in range(1, 13)] + \
         [f"2025-{m:02d}" for m in range(1, 13)]

MKT3000 = [2.0, -1.0, 3.0, 1.0, -2.0, 4.0, 0.0, -3.0, 2.0, 1.0, -1.0, 3.0,
           1.0, 0.5, -0.5, 2.0, 1.5, -1.0, 0.5, 2.5, -2.0, 1.0, 0.0, 2.0]
RF = [0.15] * 24
MOM = [1.2, -0.8, 1.5, -1.0, 0.9, -1.2, 0.8, -0.9, 1.1, -0.7, 1.3, -0.6,
       0.7, -1.1, 0.9, -0.8, 1.0, -0.9, 1.2, -0.7, 0.8, -1.0, 1.1, -0.8]
VAL = [0.6, 1.0, 0.4, -0.6, -0.4, -0.2, -0.4, -1.0, 0.8, 0.6, -1.0, 0.0,
       0.5, -0.8, 0.7, -0.5, 0.9, -0.6, 0.3, 0.8, -0.7, 0.6, -0.4, 0.5]
ALPHA, B_MKT, B_MOM, B_VAL = 0.30, 1.00, 1.20, 0.90
EPS24 = [0.2, -0.2, 0.4, -0.4, 0.2, -0.2, 0.0, 0.4, -0.4, 0.2, 0.0, -0.2]
EPS25 = [-0.3, 0.5, 0.2, -0.5, 0.4, -0.2, 0.3, -0.4, 0.1, 0.2, -0.3, 0.0]

SZTECH = [round(ALPHA + B_MKT * m + B_MOM * f + B_VAL * v + e, 2)
          for m, f, v, e in zip(MKT3000[:12], MOM[:12], VAL[:12], EPS24)]
SZTECH += [round(ALPHA + B_MKT * m + B_MOM * f + B_VAL * v + e, 2)
           for m, f, v, e in zip(MKT3000[12:], MOM[12:], VAL[12:], EPS25)]
SZTECH_V2 = [round(v + 0.1, 2) for v in SZTECH]


# ------------------------------------------------------- linear algebra
def solve(a, b):
    n = len(b)
    m = [row[:] + [bv] for row, bv in zip(a, b)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        m[col], m[piv] = m[piv], m[col]
        pv = m[col][col]
        m[col] = [v / pv for v in m[col]]
        for r in range(n):
            if r != col and m[r][col]:
                f = m[r][col]
                m[r] = [v - f * w for v, w in zip(m[r], m[col])]
    return [m[i][n] for i in range(n)]


def inverse(a):
    n = len(a)
    m = [row[:] + [1.0 if i == j else 0.0 for j in range(n)]
         for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        m[col], m[piv] = m[piv], m[col]
        pv = m[col][col]
        m[col] = [v / pv for v in m[col]]
        for r in range(n):
            if r != col and m[r][col]:
                f = m[r][col]
                m[r] = [v - f * w for v, w in zip(m[r], m[col])]
    return [row[n:] for row in m]


def ols(y, xs):
    """OLS with intercept; xs = list of regressor columns.
    Returns (coefs[a, b1..bk], tstats[b1..bk], r2, resid)."""
    n = len(y)
    cols = [[1.0] * n] + [list(x) for x in xs]
    k = len(cols)
    xx = [[sum(ci[t] * cj[t] for t in range(n)) for cj in cols] for ci in cols]
    xy = [sum(ci[t] * y[t] for t in range(n)) for ci in cols]
    coefs = solve(xx, xy)
    fitted = [sum(c * col[t] for c, col in zip(coefs, cols)) for t in range(n)]
    resid = [y[t] - fitted[t] for t in range(n)]
    ssr = sum(r * r for r in resid)
    my = sum(y) / n
    sst = sum((v - my) ** 2 for v in y)
    r2 = 1 - ssr / sst
    s2 = ssr / (n - k)
    inv = inverse(xx)
    ts = [coefs[i] / math.sqrt(s2 * inv[i][i]) for i in range(1, k)]
    return coefs, ts, r2, resid


def mean(xs):
    return sum(xs) / len(xs)


def sample_std(xs):
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def corr(y, x):
    my, mx = mean(y), mean(x)
    return sum((a - my) * (b - mx) for a, b in zip(y, x)) / math.sqrt(
        sum((a - my) ** 2 for a in y) * sum((b - mx) ** 2 for b in x))


# ----------------------------------------------------------- generation
def gen_noise(rng):
    return [round(rng.gauss(0, 1.0), 2) for _ in range(24)]


def build_universe(seed: int):
    stk, mkt, mom, val = (s[:12] for s in (SZTECH, MKT3000, MOM, VAL))
    _, ts, _, _ = ols(stk, [mkt, mom, val])
    if min(abs(t) for t in ts[1:]) <= 3.0:
        raise RuntimeError(f"true factors not identified: t={ts}")
    if abs(corr(mom, val)) >= 0.4:
        raise RuntimeError("true factors too collinear")
    for attempt in range(5000):
        rng = random.Random(seed * 104729 + attempt)
        noise = {f: gen_noise(rng) for f in ("SIZE", "VOL", "LIQ")}
        ok = True
        for f, xs in noise.items():
            _, t_all, _, _ = ols(stk, [mkt, mom, val, xs[:12]])
            if abs(t_all[-1]) >= 1.5:
                ok = False
                break
        if ok:
            return noise, rng, ts, attempt
    raise RuntimeError("no clean identification in 5000 attempts")


def ground_truth():
    stk, mkt, mom, val, rf = (s[:12] for s in (SZTECH, MKT3000, MOM, VAL, RF))
    coefs, ts, r2, _ = ols(stk, [mkt, mom, val])
    alpha, b_mkt, b_mom, b_val = coefs
    prices, p = [], 100.0
    for r in stk:
        p *= (1 + r / 100)
        prices.append(p)
    peak, mdd = prices[0], 0.0
    for p in prices:
        peak = max(peak, p)
        mdd = min(mdd, (p - peak) / peak)
    sharpe = (mean(stk) - mean(rf)) / sample_std(stk) * math.sqrt(12)
    es3 = mean(sorted(stk)[:3])
    # out-of-sample: 2024 coefficients applied to the 2025 months
    oos_err = [SZTECH[12 + i] - (alpha + b_mkt * MKT3000[12 + i]
                                 + b_mom * MOM[12 + i] + b_val * VAL[12 + i])
               for i in range(12)]
    rmse = math.sqrt(sum(e * e for e in oos_err) / 12)
    return {
        "factor_ids": ["MOM", "VAL"],
        "alpha": round(alpha, 6), "beta_mkt": round(b_mkt, 6),
        "beta_mom": round(b_mom, 6), "beta_val": round(b_val, 6),
        "t_mom": round(ts[1], 4), "t_val": round(ts[2], 4),
        "r2": round(r2, 6),
        "std_stk": round(sample_std(stk), 6),
        "sharpe_ann": round(sharpe, 6),
        "max_dd": round(-mdd * 100, 6),
        "es3": round(es3, 6),
        "oos_rmse": round(rmse, 6),
    }


QUESTIONS = [
    ("factor_ids", 0.12, None,
     "Which library factors enter the true model of SZTECH (besides the "
     "market)? Answer as a JSON list of tickers, e.g. [\"MOM\", \"VAL\"]."),
    ("alpha", 0.08, 0.06,
     "Regression of SZTECH on [const, MKT3000, your factors]: alpha "
     "(monthly, percent)."),
    ("beta_mkt", 0.08, 0.04, "Same regression: beta on MKT3000."),
    ("beta_mom", 0.08, 0.04,
     "Same regression: beta on the momentum-type factor you selected."),
    ("beta_val", 0.08, 0.04,
     "Same regression: beta on the other factor you selected."),
    ("t_mom", 0.08, 0.6,
     "t-statistic of the momentum-type factor beta in that regression."),
    ("t_val", 0.08, 0.6,
     "t-statistic of the other factor beta in that regression."),
    ("r2", 0.05, 0.02, "R-squared of that regression (0 to 1)."),
    ("std_stk", 0.05, 0.02,
     "Sample standard deviation of the SZTECH returns in the window "
     "(n-1), in percent."),
    ("sharpe_ann", 0.08, 0.06,
     "Annualized Sharpe ratio of SZTECH in the window: (mean SZTECH - "
     "mean RF) / sample std x sqrt(12)."),
    ("max_dd", 0.05, 0.5,
     "Maximum drawdown of the compounded SZTECH price series in the "
     "window, in percent (positive number)."),
    ("es3", 0.05, 0.06,
     "Mean of the three worst SZTECH monthly returns in the window, in "
     "percent."),
    ("oos_rmse", 0.12, 0.08,
     "OUT-OF-SAMPLE test: keep the coefficients estimated on the 2024 "
     "window, predict every 2025 month of SZTECH from that month's "
     "regressors, and report the RMSE of the prediction errors (in "
     "percentage points)."),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out",
                    default="wl_benchmark/tasks_data/quant/fe-mining-01.json")
    args = ap.parse_args()
    noise, rng, ts, attempt = build_universe(args.seed)
    a = ground_truth()
    series = {"MKT3000": MKT3000, "RF": RF, "MOM": MOM, "VAL": VAL,
              "SZTECH": SZTECH, "SZTECH_V2": SZTECH_V2,
              "SIZE": noise["SIZE"], "VOL": noise["VOL"], "LIQ": noise["LIQ"],
              "BTC": [round(rng.gauss(0, 8.0), 2) for _ in range(24)]}
    meta = [
        ("SZTECH", "Synthetic Tech Index v1, monthly returns (%)."),
        ("SZTECH_V2", "Synthetic Tech Index v2 (revised methodology), "
                      "monthly returns (%)."),
        ("MKT3000", "Market benchmark index, monthly returns (%)."),
        ("RF", "Risk-free rate, monthly (%)."),
        ("MOM", "Style factor — momentum (%)."),
        ("VAL", "Style factor — value (%)."),
        ("SIZE", "Style factor — size (%)."),
        ("VOL", "Style factor — volatility (%)."),
        ("LIQ", "Style factor — liquidity (%)."),
        ("BTC", "Bitcoin, monthly returns (%)."),
    ]
    questions = []
    for qid, w, tol, prompt in QUESTIONS:
        q = {"id": qid, "weight": w, "prompt": prompt, "answer": a[qid]}
        if tol is not None:
            q["tol"] = tol
        questions.append(q)
    inst = {
        "id": "quant-fe-mining-01",
        "title": "FE research practice — multi-factor mining, inference and "
                 "out-of-sample test",
        "window": {"start": "2024-01", "end": "2024-12"},
        "oos_window": {"start": "2025-01", "end": "2025-12"},
        "series_meta": [{"tick": t, "desc": d + " Covers 2024-01 to 2025-12."}
                        for t, d in meta],
        "series": {t: [{"month": m, "ret": v} for m, v in zip(MONTHS, vals)]
                   for t, vals in series.items()},
        "questions": questions,
        "note_spec": {"min_words": 200, "max_words": 450, "weight": 0.20,
                      "must_cover": ["factor mining and model selection",
                                     "identification evidence (t-stats)",
                                     "out-of-sample performance",
                                     "limitations"]},
        "auto_weight": 0.80,
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(inst, f, ensure_ascii=False, indent=1)
    print("wrote", args.out, f"(noise attempt={attempt})")
    for q in questions:
        print(f"  {q['id']}: {q['answer']} (tol {q.get('tol', 'exact')})")
    print(f"  identification: t(MKT)={ts[0]:.2f} t(MOM)={ts[1]:.2f} "
          f"t(VAL)={ts[2]:.2f} | corr(MOM,VAL)="
          f"{corr(MOM[:12], VAL[:12]):.3f}")


if __name__ == "__main__":
    main()

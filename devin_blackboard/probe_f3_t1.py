"""F3-T1 probe (offline, fixture only — no network).

Verifies:
1. Original artifact mechanism reproduced: rebuilding the
   TSM fixture per-expiry dfs with exported OI==0 treated
   as NaN makes the ORIGINAL (unfiltered) payout argmin
   return the lowest strike — the reported 225/230/235
   artifacts on expiries 10-23 / 10-30 / 11-27.
2. The FIXED compute_max_pain on the same NaN-poisoned dfs
   returns the independently computed finite-row argmin,
   NOT the lowest strike.
3. On clean data (zeros kept as zero) the canonical
   max_pain is unchanged: 435/450/445/460/470/420/450.
4. Between-strikes band-edge flags: lowest/highest
   eligible-candidate selection reported even when no
   listed strike equals a band edge.
"""

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from GexDashboard import (
    compute_max_pain,
    compute_max_pain_constrained,
)

FIXTURE = ROOT / "tests" / "fixtures" / "TSM_ai_packet.json"

ARTIFACTS = {
    "2026-10-23": 225.0,
    "2026-10-30": 230.0,
    "2026-11-27": 235.0,
}

CLEAN_MAX_PAIN = {
    "2026-10-16": 435.0,
    "2026-10-23": 450.0,
    "2026-10-30": 445.0,
    "2026-11-06": 460.0,
    "2026-11-13": 470.0,
    "2026-11-20": 420.0,
    "2026-11-27": 450.0,
}

failures = []


def check(name, ok, detail=""):
    print(
        ("PASS" if ok else "FAIL"),
        name,
        ("| " + detail if detail else ""),
    )
    if not ok:
        failures.append(name)


def oi_df(expiry_obj, zero_as_nan=False):
    rows = []
    for s in expiry_obj["strikes"]:
        call_oi = s["call_oi"]
        put_oi = s["put_oi"]
        if zero_as_nan:
            call_oi = np.nan if call_oi == 0 else call_oi
            put_oi = np.nan if put_oi == 0 else put_oi
        rows.append({
            "strike": s["strike"],
            "type": "call",
            "openInterest": call_oi,
        })
        rows.append({
            "strike": s["strike"],
            "type": "put",
            "openInterest": put_oi,
        })
    return pd.DataFrame(rows)


def naive_max_pain(df):
    """The ORIGINAL algorithm — no nonfinite filtering."""
    strikes = sorted(df["strike"].unique())
    pains = []
    for s in strikes:
        pain = 0.0
        for _, r in df.iterrows():
            if r["type"] == "call":
                pain += max(0, s - r["strike"]) * r["openInterest"]
            else:
                pain += max(0, r["strike"] - s) * r["openInterest"]
        pains.append(pain)
    return strikes[int(np.argmin(pains))]


def independent_max_pain(df):
    """Plain-Python argmin over finite rows only."""
    frame = df[
        df["strike"].map(math.isfinite)
        & df["openInterest"].map(math.isfinite)
    ]
    best_s, best_p = None, None
    for s in sorted(frame["strike"].unique()):
        pain = 0.0
        for _, r in frame.iterrows():
            if r["type"] == "call":
                pain += max(0.0, s - r["strike"]) * r["openInterest"]
            else:
                pain += max(0.0, r["strike"] - s) * r["openInterest"]
        if best_p is None or pain < best_p:
            best_s, best_p = s, pain
    return best_s


packet = json.load(open(FIXTURE, encoding="utf-8"))
expiries = {
    e["expiry"]: e
    for e in packet["market_snapshot"]["expiries"]
}

# --- 1+2: artifact mechanism + fix on NaN-poisoned dfs ---
for exp, artifact in ARTIFACTS.items():
    df = oi_df(expiries[exp], zero_as_nan=True)

    naive = naive_max_pain(df)
    check(
        f"{exp} naive-original reproduces artifact",
        naive == artifact,
        f"naive={naive} artifact={artifact}",
    )

    indep = independent_max_pain(df)
    fixed = compute_max_pain(df)
    lowest = min(
        s for s in df["strike"].unique() if math.isfinite(s)
    )
    check(
        f"{exp} fixed returns independent argmin",
        fixed == indep and fixed != lowest,
        f"fixed={fixed} indep={indep} lowest={lowest}",
    )

# --- 3: clean data unchanged ---
for exp, want in CLEAN_MAX_PAIN.items():
    got = compute_max_pain(oi_df(expiries[exp]))
    check(
        f"{exp} clean canonical max_pain",
        got == want,
        f"got={got} want={want}",
    )

# --- 4: between-strikes band-edge flags ---
df = pd.DataFrame({
    "strike": [100.0, 150.0, 200.0, 100.0, 150.0, 200.0],
    "type": ["call", "call", "call", "put", "put", "put"],
    "openInterest": [10, 10000, 10, 10, 10, 10],
})
res = compute_max_pain_constrained(df, spot=210.0)
check(
    "between-strikes lowest-candidate flags",
    res["candidate_band"] == [147.0, 273.0]
    and res["max_pain"] == 150.0
    and res["band_boundary_hit"] is False
    and res["band_fell_back"] is False
    and res["selected_lowest_candidate"] is True
    and res["selected_highest_candidate"] is False,
    str(res),
)

df2 = df.copy()
df2["openInterest"] = [10, 10, 10, 10, 10, 90000]
res2 = compute_max_pain_constrained(df2, spot=210.0)
check(
    "between-strikes highest-candidate flags",
    res2["max_pain"] == 200.0
    and res2["band_boundary_hit"] is False
    and res2["selected_lowest_candidate"] is False
    and res2["selected_highest_candidate"] is True,
    str(res2),
)

print()
if failures:
    print(f"{len(failures)} FAILURES: {failures}")
    sys.exit(1)
print("ALL PASS")

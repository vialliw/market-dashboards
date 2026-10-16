"""Critic gate probe F3-T3 (Agent D) — fully independent verification.

Plain-Python implementations written from scratch; imports the real
GexDashboard functions ONLY as the units under test. No network.

Checks:
 A. Per-expiry df rebuilt from tests/fixtures/TSM_ai_packet.json with
    exported zeros AS-IS -> compute_max_pain == independent argmin ==
    435/450/445/460/470/420/450 (unchanged from F2).
 B. Same dfs with exported 0-OI rows treated as NaN -> the CURRENT
    functions return the independent finite-only argmin (not the
    lowest listed strike); simulated PRE-F3 naive algorithm returns
    the artifact (lowest strike) — 225 expected for 2026-10-23.
 C. Band-edge flags: band endpoints between listed strikes ->
    selected_lowest/highest fire, boundary_hit does not; literal
    edge equality -> boundary_hit.
 D. Packet fields: max_pain, max_pain_constrained, candidate_band,
    band_fell_back, band_boundary_hit, selected_lowest/highest,
    raw_implied_volatility provenance, meta notes — on a fresh
    build_ai_packet over a synthetic df.
"""

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from GexDashboard import (
    build_ai_packet,
    compute_max_pain,
    compute_max_pain_constrained,
)

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "tests" / "fixtures" / "TSM_ai_packet.json"
)

EXPECTED_CLEAN = {
    "2026-10-16": 435.0,
    "2026-10-23": 450.0,
    "2026-10-30": 445.0,
    "2026-11-06": 460.0,
    "2026-11-13": 470.0,
    "2026-11-20": 420.0,
    "2026-11-27": 450.0,
}

# Reported packet artifacts (pre-fix max_pain) per expiry
ARTIFACT = {
    "2026-10-16": None,
    "2026-10-23": 225.0,
    "2026-10-30": 230.0,
    "2026-11-06": None,
    "2026-11-13": None,
    "2026-11-20": None,
    "2026-11-27": 235.0,
}

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), name, "|", detail)


def oi_df(expiry_obj, zeros_as_nan=False):
    """Rebuild call/put OI rows for one expiry from the fixture."""
    rows = []
    for s in expiry_obj["strikes"]:
        c = float(s["call_oi"])
        p = float(s["put_oi"])
        if zeros_as_nan:
            c = np.nan if c == 0 else c
            p = np.nan if p == 0 else p
        rows.append({
            "strike": float(s["strike"]),
            "type": "call",
            "openInterest": c,
        })
        rows.append({
            "strike": float(s["strike"]),
            "type": "put",
            "openInterest": p,
        })
    return pd.DataFrame(rows)


def indep_argmin(df, drop_nonfinite=True):
    """Independent plain-Python payout argmin. With
    drop_nonfinite=False this replicates the PRE-F3 naive
    behavior (NaN pains -> np.argmin picks index 0)."""
    if drop_nonfinite:
        df = df[
            df["strike"].map(lambda x: math.isfinite(x))
            & df["openInterest"].map(lambda x: math.isfinite(x))
        ]
    strikes = sorted(df["strike"].unique())
    pains = []
    for s in strikes:
        pain = 0.0
        for _, r in df.iterrows():
            if r["type"] == "call":
                pain += max(0.0, s - r["strike"]) * r["openInterest"]
            else:
                pain += max(0.0, r["strike"] - s) * r["openInterest"]
        pains.append(pain)
    # np.argmin semantics: first index of the minimum; NaN counts
    # as minimum in numpy's argmin implementation.
    return strikes[int(np.argmin(pains))]


packet = json.load(open(FIXTURE, encoding="utf-8"))
expiries = packet["market_snapshot"]["expiries"]

# ---------- A. clean (zeros as-is) canonical argmin ----------
print("=== A. clean dfs: canonical compute_max_pain ===")
for e in expiries:
    name = e["expiry"]
    df = oi_df(e, zeros_as_nan=False)
    lib = float(compute_max_pain(df))
    ind = float(indep_argmin(df, drop_nonfinite=True))
    want = EXPECTED_CLEAN[name]
    check(
        f"clean {name} max_pain",
        lib == ind == want,
        f"lib={lib} indep={ind} expected={want}",
    )

# ---------- B. zeros-as-NaN: current vs pre-F3 naive ----------
print("=== B. zeros-as-NaN dfs: artifact vs fixed ===")
for e in expiries:
    name = e["expiry"]
    df = oi_df(e, zeros_as_nan=True)
    n_nan = int(df["openInterest"].isna().sum())
    lib = float(compute_max_pain(df))
    ind = float(indep_argmin(df, drop_nonfinite=True))
    naive = float(indep_argmin(df, drop_nonfinite=False))
    lowest = min(s["strike"] for s in e["strikes"])
    check(
        f"nan {name} current==independent",
        lib == ind,
        f"lib={lib} indep={ind} naive(pre-F3)={naive} "
        f"nan_rows={n_nan}",
    )
    art = ARTIFACT[name]
    if art is not None:
        check(
            f"nan {name} pre-F3 naive reproduces artifact",
            naive == art == lowest,
            f"naive={naive} artifact={art} lowest={lowest}",
        )
        check(
            f"nan {name} current != artifact",
            lib != art,
            f"lib={lib} artifact={art}",
        )

res_c = compute_max_pain_constrained(
    oi_df(expiries[1], zeros_as_nan=True), spot=422.05
)
check(
    "nan 2026-10-23 constrained==independent",
    float(res_c["max_pain"]) == 450.0,
    f"constrained={res_c['max_pain']}",
)

# ---------- C. band-edge flags ----------
print("=== C. band-edge flags ===")
# Band [147, 273] via spot=210 falls BETWEEN listed strikes
# (eligible = 150..270-ish); heavy put OI at 150 drags the argmin
# to the lowest eligible candidate.
df_lo = pd.DataFrame({
    "strike": [150.0, 200.0, 250.0, 300.0] * 2,
    "type": ["call"] * 4 + ["put"] * 4,
    # heavy CALL oi at 150 penalizes every S>150 -> argmin = 150
    "openInterest": [90000, 10, 10, 10, 10, 10, 10, 10],
})
r_lo = compute_max_pain_constrained(df_lo, spot=210.0)
check(
    "between-strikes: lowest-candidate flag",
    r_lo["max_pain"] == 150.0
    and r_lo["selected_lowest_candidate"] is True
    and r_lo["selected_highest_candidate"] is False
    and r_lo["band_boundary_hit"] is False
    and r_lo["band_fell_back"] is False,
    str(r_lo),
)

df_hi = pd.DataFrame({
    "strike": [150.0, 200.0, 250.0, 300.0] * 2,
    "type": ["call"] * 4 + ["put"] * 4,
    # heavy PUT oi at 300 (above band) penalizes every S<300 ->
    # argmin pushed to the highest eligible candidate (250)
    "openInterest": [10, 10, 10, 10, 10, 10, 10, 90000],
})
r_hi = compute_max_pain_constrained(df_hi, spot=210.0)
check(
    "between-strikes: highest-candidate flag",
    r_hi["max_pain"] == 250.0
    and r_hi["selected_highest_candidate"] is True
    and r_hi["selected_lowest_candidate"] is False
    and r_hi["band_boundary_hit"] is False
    and r_hi["band_fell_back"] is False,
    str(r_hi),
)

# Literal edge equality: spot=200 -> band [140,260]; strike 140 is
# listed and heavy put OI drags argmin onto it.
df_edge = pd.DataFrame({
    "strike": [140.0, 200.0, 260.0] * 2,
    "type": ["call"] * 3 + ["put"] * 3,
    # heavy CALL oi at 140 (the literal low band edge) drags
    # the argmin onto the edge strike itself
    "openInterest": [90000, 10, 10, 10, 10, 10],
})
r_edge = compute_max_pain_constrained(df_edge, spot=200.0)
check(
    "literal edge -> boundary_hit + selected_lowest",
    r_edge["max_pain"] == 140.0
    and r_edge["band_boundary_hit"] is True
    and r_edge["selected_lowest_candidate"] is True
    and r_edge["band_fell_back"] is False,
    str(r_edge),
)

# ---------- D. packet fields on fresh build_ai_packet ----------
print("=== D. packet fields ===")
exp = "2026-10-16"
exp_dt = pd.Timestamp(exp, tz="UTC")
df_pkt = pd.DataFrame({
    "strike": [100.0, 110.0, 420.0, 430.0,
               100.0, 110.0, 420.0, 430.0, 115.0],
    "type": ["call", "call", "call", "call",
             "put", "put", "put", "put", "call"],
    "openInterest": [10, 30, 100, 50, 20, 40, 10, 10, np.nan],
    "gex": [1e5, 3e5, 2e5, 1e5, -2e5, -1e5, -2e5, -1e5, 5e4],
    "impliedVolatility": [0.30, 3.5, 0.30, 0.30,
                          0.30, 3.5, 0.30, 0.30, 0.25],
    "expiry": [exp_dt] * 9,
})
pkt = build_ai_packet("TEST", df_pkt, 105.0, [exp])
e0 = pkt["market_snapshot"]["expiries"][0]
keys = [
    "max_pain", "max_pain_constrained", "max_pain_candidate_band",
    "max_pain_band_fell_back", "max_pain_band_boundary_hit",
    "max_pain_selected_lowest_candidate",
    "max_pain_selected_highest_candidate",
]
missing = [k for k in keys if k not in e0]
check("packet: all max_pain fields present", not missing,
      f"missing={missing}")
check(
    "packet: flag fields are bools",
    all(isinstance(e0[k], bool) for k in keys[3:]),
    str({k: e0[k] for k in keys[3:]}),
)
check(
    "packet: candidate_band == [0.7*spot, 1.3*spot]",
    e0["max_pain_candidate_band"] == [0.7 * 105.0, 1.3 * 105.0],
    str(e0["max_pain_candidate_band"]),
)
strikes = {s["strike"]: s for s in e0["strikes"]}
st = strikes[110.0]
check(
    "packet: stale IV provenance",
    st["implied_volatility"] is None
    and st["stale_iv"] is True
    and st["raw_implied_volatility"] == 3.5
    and st["open_interest"] == 70,
    str(st),
)
na = strikes[100.0]
check(
    "packet: normal strike has no provenance keys",
    na["implied_volatility"] == 0.30
    and "stale_iv" not in na
    and "raw_implied_volatility" not in na,
    str(na),
)
check(
    "packet: NaN-OI strike exported as 0 (conflation)",
    strikes[115.0]["open_interest"] == 0,
    f"strike115 oi={strikes[115.0]['open_interest']}",
)
dq = pkt["meta"]["data_quality"].lower()
md = pkt["meta"]["metric_definitions"]
check(
    "packet: meta.data_quality OI-conflation + stale_iv notes",
    "conflate" in dq and "missing" in dq
    and "raw_implied_volatility" in dq,
    "",
)
check(
    "packet: metric_definitions documents flags",
    "max_pain" in md
    and "max_pain_constrained" in md
    and "selected_lowest" in md["max_pain_constrained"]
    and "boundary_hit" in md["max_pain_constrained"],
    "",
)

print()
fails = [r for r in results if not r[1]]
print(f"{len(results) - len(fails)}/{len(results)} checks passed")
if fails:
    print("FAILURES:")
    for n, _, d in fails:
        print("  -", n, d)
    sys.exit(1)
print("ALL PASS")

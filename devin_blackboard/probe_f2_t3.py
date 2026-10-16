"""Agent D (critic) independent verification probe for F2-T3.

Verification only — does NOT edit source/tests. Independently
recomputes, from tests/fixtures/TSM_ai_packet.json:

 1. Canonical UNCONSTRAINED max-pain payout totals for the
    TSM 2026-10-16 expiry (sum over K of
    call_OI*max(S-K,0) + put_OI*max(K-S,0), evaluated at EVERY
    candidate strike S) -> true argmin + tie behavior; checks
    whether reported 435 is the true argmin or a band artifact.
 2. compute_gamma_flip edge cases: alternating +/-1e-12 noise,
    all-zero book, and values just below / at / above the
    1e-3 absolute floor — expectations derived by a separate
    plain-Python implementation of the spec, then compared to
    the real library call.
 3. build_ai_packet emits BOTH max_pain (unconstrained) and
    max_pain_constrained with candidate_band / band_fell_back /
    band_boundary_hit; stale strikes carry
    raw_implied_volatility provenance; meta.metric_definitions
    documents proxy semantics.

Offline only. Run: python devin_blackboard/probe_f2_t3.py
"""

import json
import math
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent)
)

from GexDashboard import (
    MIN_MATERIAL_GEX,
    build_ai_packet,
    compute_gamma_flip,
    compute_max_pain,
    compute_max_pain_constrained,
)

FIXTURE = (
    Path(__file__).resolve().parent.parent
    / "tests" / "fixtures" / "TSM_ai_packet.json"
)
SPOT = 422.04998779296875
EXPIRY = "2026-10-16"

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


packet = json.load(open(FIXTURE, encoding="utf-8"))
exp_obj = next(
    e for e in packet["market_snapshot"]["expiries"]
    if e["expiry"] == EXPIRY
)
strikes_fixture = exp_obj["strikes"]

# ---- 1. Independent canonical unconstrained max pain ----
# Plain-Python payout sum, evaluated at EVERY unique strike S.
call_oi = {s["strike"]: s["call_oi"] for s in strikes_fixture}
put_oi = {s["strike"]: s["put_oi"] for s in strikes_fixture}
all_strikes = sorted(set(call_oi) | set(put_oi))

payouts = {}
for S in all_strikes:
    total = 0
    for K in all_strikes:
        total += call_oi.get(K, 0) * max(S - K, 0)
        total += put_oi.get(K, 0) * max(K - S, 0)
    payouts[S] = total

min_payout = min(payouts.values())
argmins = sorted(
    s for s, v in payouts.items() if v == min_payout
)
indep_argmin = argmins[0]

# Library value on an equivalently reconstructed df.
oi_rows = []
for s in strikes_fixture:
    oi_rows.append({
        "strike": s["strike"], "type": "call",
        "openInterest": s["call_oi"],
    })
    oi_rows.append({
        "strike": s["strike"], "type": "put",
        "openInterest": s["put_oi"],
    })
oi_df = pd.DataFrame(oi_rows)
lib_mp = compute_max_pain(oi_df)
lib_mp_spot = compute_max_pain(oi_df, spot=SPOT)
constrained = compute_max_pain_constrained(oi_df, spot=SPOT)
band = [0.7 * SPOT, 1.3 * SPOT]

# In-band-only independent argmin (what a band artifact would be)
in_band = {s: v for s, v in payouts.items()
           if band[0] <= s <= band[1]}
inband_min = min(in_band.values())
inband_argmins = sorted(
    s for s, v in in_band.items() if v == inband_min
)

print(f"2026-10-16 strikes={len(all_strikes)} "
      f"range=[{all_strikes[0]},{all_strikes[-1]}]")
print(f"independent unconstrained argmin={indep_argmin} "
      f"payout={min_payout:.2f} ties={argmins}")
print(f"lib compute_max_pain={lib_mp} (spot arg ignored: "
      f"{lib_mp_spot}) constrained={constrained}")
print(f"independent in-band argmin={inband_argmins} "
      f"band={band}")
reported = exp_obj["max_pain"]
print(f"fixture-reported max_pain={reported}")

check("unconstrained_argmin_matches_lib",
      float(indep_argmin) == float(lib_mp) == float(lib_mp_spot),
      f"indep={indep_argmin} lib={lib_mp} lib_spot={lib_mp_spot}")
check("reported_435_is_true_argmin_not_band_artifact",
      reported == indep_argmin and indep_argmin in inband_argmins,
      f"reported={reported} uncon_argmin={indep_argmin} "
      f"inband_argmins={inband_argmins} -> true argmin that "
      f"also lies inside the band")
check("constrained_matches_unconstrained_when_argmin_in_band",
      float(constrained["max_pain"]) == float(indep_argmin)
      and constrained["band_fell_back"] is False
      and constrained["band_boundary_hit"] is False
      and constrained["candidate_band"] == band,
      f"{constrained}")

# Tie behavior across ALL 7 expiries for completeness.
for e in packet["market_snapshot"]["expiries"]:
    co = {s["strike"]: s["call_oi"] for s in e["strikes"]}
    po = {s["strike"]: s["put_oi"] for s in e["strikes"]}
    ks = sorted(set(co) | set(po))
    tot = {}
    for S in ks:
        t = 0
        for K in ks:
            t += co.get(K, 0) * max(S - K, 0)
            t += po.get(K, 0) * max(K - S, 0)
        tot[S] = t
    m = min(tot.values())
    am = sorted(s for s, v in tot.items() if v == m)
    rows = []
    for s in e["strikes"]:
        rows.append({"strike": s["strike"], "type": "call",
                     "openInterest": s["call_oi"]})
        rows.append({"strike": s["strike"], "type": "put",
                     "openInterest": s["put_oi"]})
    lv = compute_max_pain(pd.DataFrame(rows))
    check(f"expiry_{e['expiry']}_argmin",
          float(lv) == float(am[0]),
          f"lib={lv} indep_argmins={am} n_ties={len(am)}")

# ---- 2. gamma_flip edge cases (independent expectation) ----
def expected_flip(strike_gex, spot):
    """Separate plain-Python implementation of the spec:
    drop nonfinite, group-sum, max_abs<1e-3 -> None,
    material = |g| >= max(1%*max_abs, 1e-3), neg->pos
    crossings among material strikes, nearest spot."""
    grouped = {}
    for k, g in strike_gex:
        if math.isfinite(k) and math.isfinite(g):
            grouped[k] = grouped.get(k, 0.0) + g
    pairs = sorted(grouped.items())
    if not pairs:
        return None
    max_abs = max(abs(g) for _, g in pairs)
    if max_abs < MIN_MATERIAL_GEX:
        return None
    floor = max(0.01 * max_abs, MIN_MATERIAL_GEX)
    mat = [(k, g) for k, g in pairs if abs(g) >= floor]
    crossings = [
        mat[i][0] for i in range(1, len(mat))
        if mat[i - 1][1] < 0 < mat[i][1]
    ]
    if not crossings:
        return None
    if spot is None:
        return crossings[0]
    return min(crossings, key=lambda s: abs(s - spot))


def flip_of(pairs, spot=SPOT):
    df = pd.DataFrame({
        "strike": [k for k, _ in pairs],
        "gex": [g for _, g in pairs],
    })
    return compute_gamma_flip(df, spot=spot)


alt = [(400.0 + i * 10.0,
        (-1e-12 if i % 2 == 0 else 1e-12))
       for i in range(6)]
check("alternating_pm_1e-12_noise",
      flip_of(alt) is None
      and flip_of(alt, spot=None) is None
      and expected_flip(alt, SPOT) is None,
      f"lib={flip_of(alt)} exp=None")

zero = [(400.0, 0.0), (420.0, 0.0), (440.0, 0.0)]
check("all_zero_book",
      flip_of(zero) is None
      and flip_of(zero, spot=None) is None
      and expected_flip(zero, SPOT) is None,
      "lib=None exp=None")

below = [(400.0, -9e-4), (420.0, 9e-4)]
at = [(400.0, -1e-3), (420.0, 1e-3)]
above = [(400.0, -1.1e-3), (420.0, 1.1e-3)]
just_below = [(400.0, -9.999e-4), (420.0, 9.999e-4)]
check("floor_below_9e-4_none",
      flip_of(below) is None
      and expected_flip(below, SPOT) is None,
      f"lib={flip_of(below)}")
check("floor_just_below_9.999e-4_none",
      flip_of(just_below) is None
      and expected_flip(just_below, SPOT) is None,
      f"lib={flip_of(just_below)}")
check("floor_at_1e-3_material",
      flip_of(at) == 420.0
      and expected_flip(at, SPOT) == 420.0,
      f"lib={flip_of(at)}")
check("floor_above_1.1e-3_material",
      flip_of(above) == 420.0
      and expected_flip(above, SPOT) == 420.0,
      f"lib={flip_of(above)}")

# ---- 3. build_ai_packet fields + provenance on REAL fixture ----
# Reconstruct a raw (strike,type) df for the real 10-16 expiry
# and run the real packet builder end-to-end.
raw = []
exp_ts = pd.Timestamp(EXPIRY, tz="UTC")
for s in strikes_fixture:
    raw.append({
        "strike": s["strike"], "type": "call",
        "openInterest": s["call_oi"],
        "gex": s["call_gex"],
        "impliedVolatility": s["implied_volatility"],
        "expiry": exp_ts,
    })
    raw.append({
        "strike": s["strike"], "type": "put",
        "openInterest": s["put_oi"],
        "gex": s["put_gex"],
        "impliedVolatility": s["implied_volatility"],
        "expiry": exp_ts,
    })
raw_df = pd.DataFrame(raw)
pkt = build_ai_packet("TSM", raw_df, SPOT, [EXPIRY])
e_out = pkt["market_snapshot"]["expiries"][0]

check("packet_both_max_pain_fields",
      e_out["max_pain"] == float(indep_argmin)
      and "max_pain_constrained" in e_out
      and "max_pain_candidate_band" in e_out
      and "max_pain_band_fell_back" in e_out
      and "max_pain_band_boundary_hit" in e_out,
      f"max_pain={e_out['max_pain']} "
      f"constrained={e_out.get('max_pain_constrained')} "
      f"band={e_out.get('max_pain_candidate_band')} "
      f"fell_back={e_out.get('max_pain_band_fell_back')} "
      f"boundary_hit={e_out.get('max_pain_band_boundary_hit')}")

stale_out = [
    s for s in e_out["strikes"] if s.get("stale_iv")
]
stale_ok = all(
    s["implied_volatility"] is None
    and isinstance(s.get("raw_implied_volatility"), float)
    and s["raw_implied_volatility"] > 2.0
    for s in stale_out
)
n_fixture_stale = sum(
    1 for s in strikes_fixture
    if s["implied_volatility"] > 2.0
)
check("stale_iv_provenance",
      len(stale_out) == n_fixture_stale > 0 and stale_ok,
      f"stale strikes={len(stale_out)} "
      f"(fixture iv>2.0: {n_fixture_stale}); "
      f"all have iv=null + raw_implied_volatility>2.0 "
      f"+ stale_iv=true")

md = pkt["meta"].get("metric_definitions", {})
dq = pkt["meta"].get("data_quality", "")
check("meta_metric_definitions",
      "gamma_flip" in md
      and "strike-profile" in md.get("gamma_flip", "").lower()
      and "max_pain_constrained" in md
      and "constrained payout minimum"
      in md.get("max_pain_constrained", "").lower()
      and "materiality_floor" in md,
      f"keys={sorted(md.keys())}")
check("meta_data_quality_stale_doc",
      "stale_iv" in dq and "raw_implied_volatility" in dq,
      "stale_iv + raw_implied_volatility documented")

print()
n_fail = sum(1 for _, ok, _ in results if not ok)
print(f"{len(results) - n_fail}/{len(results)} checks passed")
if n_fail:
    raise SystemExit(1)
print("ALL PASS")

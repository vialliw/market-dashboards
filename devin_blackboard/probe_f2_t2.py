"""Adversarial probe for F2-T2 (Challenger review of F2 amendments).

Part 1 attacks the real GexDashboard module with hostile inputs:
  materiality floor (uniform/alternating/scaled noise, mixed books,
  nonfinite rows, spot=None), constrained-vs-canonical max_pain
  semantics (empty band, boundary hit, spot=None/NaN), stale-IV
  provenance, and packet/meta field honesty.

Part 2 is a mutation harness: it writes mutated copies of
GexDashboard.py into devin_blackboard/_mutants/, loads each via
importlib, and replays the assertions the corresponding test makes —
confirming each mutation is actually caught (non-vacuous tests).

Fully offline. Exits 0 on all-pass, 1 otherwise.
"""
import importlib.util
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
MUT_DIR = Path(__file__).resolve().parent / "_mutants"
sys.path.insert(0, str(REPO))

import GexDashboard as G  # noqa: E402

SPOT = 422.05
failures = []
notes = []


def check(name, cond, detail=""):
    tag = "PASS" if cond else "FAIL"
    print(f"[{tag}] {name} {detail}")
    if not cond:
        failures.append(name)


def gex_df(strikes, gexs):
    return pd.DataFrame({"strike": strikes, "gex": gexs})


# ============ PART 1: adversarial inputs ============
print("== PART 1: hostile inputs against real module ==")

# --- materiality floor attacks ---
for label, vals in [
    ("uniform-1e-12", [-1e-12, 1e-12, -1e-12, 1e-12]),
    ("uniform-1e-6", [-5e-6, 4e-6, -6e-6, 5e-6]),
    ("just-below-floor", [-9.999e-4, 9.999e-4, -9e-4, 9e-4]),
    ("alternating-pm1e-12", [-1e-12, 1e-12, -1e-12, 1e-12,
                           -1e-12, 1e-12]),
    ("all-zero", [0.0, 0.0, 0.0, 0.0]),
]:
    n = len(vals)
    df = gex_df([float(400 + 10 * i) for i in range(n)], vals)
    r1 = G.compute_gamma_flip(df, spot=SPOT)
    r2 = G.compute_gamma_flip(df)
    check(f"noise-slice {label} -> None",
          r1 is None and r2 is None, f"got {r1}/{r2}")

# just-above floor is signal (floor is the definition of noise)
df = gex_df([400.0, 420.0], [-1.1e-3, 1.1e-3])
check("just-above-floor -> crossing", G.compute_gamma_flip(df) == 420.0,
      f"got {G.compute_gamma_flip(df)}")

# mixed real+noise: noise strikes near spot, real crossing far away
df = gex_df(
    [165.0, 170.0, 400.0, 430.0, 500.0],
    [-1e-12, 1e-12, -5e6, 3e6, -1e6],
)
check("mixed noise+real -> real crossing 430",
      G.compute_gamma_flip(df, spot=SPOT) == 430.0,
      f"got {G.compute_gamma_flip(df, spot=SPOT)}")

# mixed: real book all-negative + noise crossing -> None (no artifact)
df = gex_df(
    [170.0, 400.0, 500.0],
    [1e-12, -5e6, -3e6],
)
check("noise crossing beside all-neg real book -> None",
      G.compute_gamma_flip(df, spot=SPOT) is None,
      f"got {G.compute_gamma_flip(df, spot=SPOT)}")

# immaterial strike wedged INSIDE a material pair still yields crossing
df = gex_df([400.0, 410.0, 420.0], [-5e6, 5e-6, 4e6])
check("noise strike between material pair -> crossing at 420",
      G.compute_gamma_flip(df, spot=SPOT) == 420.0,
      f"got {G.compute_gamma_flip(df, spot=SPOT)}")

# nonfinite attacks
df = gex_df(
    [-np.inf, 400.0, 410.0, 420.0, 430.0, np.nan],
    [-9e9, -5e6, -3e6, 4e6, np.inf, 1e6],
)
check("nonfinite rows dropped -> 420",
      G.compute_gamma_flip(df, spot=SPOT) == 420.0,
      f"got {G.compute_gamma_flip(df, spot=SPOT)}")

df = gex_df([np.nan, np.nan], [1e6, -1e6])
check("all-NaN strikes -> None", G.compute_gamma_flip(df) is None,
      f"got {G.compute_gamma_flip(df)}")

# spot=None -> FIRST ascending material crossing
df = gex_df([250.0, 300.0, 400.0, 430.0], [-5e6, 2e6, -3e6, 4e6])
check("spot=None -> first ascending (300)",
      G.compute_gamma_flip(df) == 300.0,
      f"got {G.compute_gamma_flip(df)}")
check("spot set -> nearest (430)",
      G.compute_gamma_flip(df, spot=SPOT) == 430.0,
      f"got {G.compute_gamma_flip(df, spot=SPOT)}")

# nonfinite spot wart: silently degenerates to first crossing
df2 = gex_df([250.0, 300.0, 400.0, 430.0], [-5e6, 2e6, -3e6, 4e6])
r = G.compute_gamma_flip(df2, spot=np.nan)
notes.append(
    f"spot=NaN silently returns first crossing {r} "
    "(argmin over all-NaN distances); spec only mandates spot=None")
print(f"  note: spot=NaN -> {r}")

# --- max pain semantics ---
oi = pd.DataFrame({
    "strike": [200.0, 420.0, 430.0, 200.0, 420.0, 430.0],
    "type": ["call", "call", "call", "put", "put", "put"],
    "openInterest": [100000, 100, 50, 10, 10, 10],
})
check("canonical ignores spot arg",
      G.compute_max_pain(oi) == 200.0
      and G.compute_max_pain(oi, spot=SPOT) == 200.0
      and G.compute_max_pain(oi, spot=99999.0) == 200.0)
res = G.compute_max_pain_constrained(oi, spot=SPOT)
check("constrained distinct from canonical",
      res["max_pain"] == 420.0
      and res["candidate_band"] == [0.7 * SPOT, 1.3 * SPOT]
      and res["band_fell_back"] is False
      and res["band_boundary_hit"] is False,
      f"got {res}")

# spot=None on constrained -> canonical, honest metadata
res = G.compute_max_pain_constrained(oi, spot=None)
check("constrained spot=None -> canonical + band None",
      res["max_pain"] == 200.0
      and res["candidate_band"] is None
      and res["band_fell_back"] is False
      and res["band_boundary_hit"] is False,
      f"got {res}")

# empty band -> fell_back, distinct from boundary hit
res = G.compute_max_pain_constrained(oi, spot=1000.0)
check("empty band -> fell_back=True, boundary_hit=False",
      res["band_fell_back"] is True
      and res["band_boundary_hit"] is False
      and res["max_pain"] == 200.0,
      f"got {res}")

# degenerate spots -> graceful fell_back, not crash
res = G.compute_max_pain_constrained(oi, spot=-10.0)
check("negative spot -> fell_back canonical",
      res["band_fell_back"] is True and res["max_pain"] == 200.0,
      f"got {res}")
res = G.compute_max_pain_constrained(oi, spot=np.nan)
notes.append(
    f"constrained spot=NaN -> fell_back={res['band_fell_back']} "
    f"max_pain={res['max_pain']} (NaN band empties -> fallback)")
print(f"  note: constrained spot=NaN -> {res}")

# boundary hit on lower edge (0.7*25 == 17.5 exact in fp)
edge = pd.DataFrame({
    "strike": [17.5, 20.0, 30.0, 32.5] * 2,
    "type": ["call"] * 4 + ["put"] * 4,
    "openInterest": [10000, 10, 10, 10, 5, 5, 5, 5],
})
res = G.compute_max_pain_constrained(edge, spot=25.0)
check("edge argmin -> boundary_hit=True, not fell_back",
      res["max_pain"] == 17.5
      and res["band_boundary_hit"] is True
      and res["band_fell_back"] is False,
      f"got {res}")

# boundary hit where canonical ALSO lands on edge -> flag still literal
res_u = G.compute_max_pain(edge)
notes.append(
    f"edge-case canonical={res_u}: boundary_hit fires even though "
    f"unconstrained argmin is the same strike (literal edge-landing "
    f"semantics, docstring says exactly that)")
print(f"  note: edge canonical={res_u}")

# independent payout argmin check on fixture expiries
packet = json.loads(
    (REPO / "tests/fixtures/TSM_ai_packet.json")
    .read_text(encoding="utf-8"))


def oi_df(e):
    rows = []
    for s in e["strikes"]:
        rows.append({"strike": s["strike"], "type": "call",
                     "openInterest": s["call_oi"]})
        rows.append({"strike": s["strike"], "type": "put",
                     "openInterest": s["put_oi"]})
    return pd.DataFrame(rows)


def payout(df, s):
    p = 0.0
    for _, r in df.iterrows():
        if r["type"] == "call":
            p += max(0.0, s - r["strike"]) * r["openInterest"]
        else:
            p += max(0.0, r["strike"] - s) * r["openInterest"]
    return p


for e in packet["market_snapshot"]["expiries"]:
    d = oi_df(e)
    chain = sorted(d["strike"].unique())
    indep = min(chain, key=lambda s: payout(d, s))
    lib = G.compute_max_pain(d, spot=SPOT)  # must ignore spot
    band = [s for s in chain
            if 0.7 * SPOT <= s <= 1.3 * SPOT]
    indep_c = min(band, key=lambda s: payout(d, s))
    rc = G.compute_max_pain_constrained(d, spot=SPOT)
    check(f"{e['expiry']} canonical==indep & constrained==indep",
          lib == indep and rc["max_pain"] == indep_c
          and rc["band_fell_back"] is False,
          f"uncon={lib}/{indep} con={rc['max_pain']}/{indep_c}")

# --- packet field honesty: fell_back emitted end-to-end ---
exp = "2026-10-16"
exp_dt = pd.Timestamp(exp, tz="UTC")
far = pd.DataFrame({
    "strike": [100.0, 200.0, 300.0] * 2,
    "type": ["call"] * 3 + ["put"] * 3,
    "openInterest": [10, 50, 10, 10, 5, 90],
    "gex": [1e5] * 6,
    "impliedVolatility": [0.3] * 6,
    "expiry": [exp_dt] * 6,
})
pk = G.build_ai_packet("TEST", far, 1000.0, [exp])
e = pk["market_snapshot"]["expiries"][0]
check("packet fell_back fields emitted correctly",
      e["max_pain_band_fell_back"] is True
      and e["max_pain_band_boundary_hit"] is False
      and e["max_pain_candidate_band"] == [700.0, 1300.0]
      and e["max_pain"] == e["max_pain_constrained"],
      f"mp={e['max_pain']} mpc={e['max_pain_constrained']} "
      f"fb={e['max_pain_band_fell_back']} "
      f"bh={e['max_pain_band_boundary_hit']}")

# boundary-hit emitted end-to-end through packet
edge2 = pd.DataFrame({
    "strike": [17.5, 20.0, 30.0, 32.5] * 2,
    "type": ["call"] * 4 + ["put"] * 4,
    "openInterest": [10000, 10, 10, 10, 5, 5, 5, 5],
    "gex": [1e5, -2e5, 3e5, -1e5, 1e5, -2e5, 3e5, -1e5],
    "impliedVolatility": [0.3] * 8,
    "expiry": [exp_dt] * 8,
})
pk = G.build_ai_packet("TEST", edge2, 25.0, [exp])
e = pk["market_snapshot"]["expiries"][0]
check("packet boundary_hit=True end-to-end",
      e["max_pain_band_boundary_hit"] is True
      and e["max_pain_band_fell_back"] is False
      and e["max_pain_constrained"] == 17.5,
      f"mpc={e['max_pain_constrained']} "
      f"bh={e['max_pain_band_boundary_hit']}")

# --- stale IV boundary + provenance ---
for iv, want_stale in [(2.0, False), (2.0000001, True),
                       (4.5, True), (0.5, False)]:
    dfiv = pd.DataFrame({
        "strike": [110.0, 110.0],
        "type": ["call", "put"],
        "openInterest": [10, 20],
        "gex": [1e5, -1e5],
        "impliedVolatility": [iv, iv],
        "expiry": [exp_dt] * 2,
    })
    pk = G.build_ai_packet("TEST", dfiv, 105.0, [exp])
    s = pk["market_snapshot"]["expiries"][0]["strikes"][0]
    ok = s.get("stale_iv", False) == want_stale
    if want_stale:
        ok = (ok and s["implied_volatility"] is None
              and s["raw_implied_volatility"] == iv
              and s["open_interest"] == 30
              and s["net_gex"] == 0.0)
    else:
        ok = (ok and s["implied_volatility"] == iv
              and "raw_implied_volatility" not in s)
    check(f"iv={iv} stale={want_stale}", ok,
          f"iv_field={s.get('implied_volatility')} "
          f"raw={s.get('raw_implied_volatility')}")

# NaN IV wart (pre-existing: NaN > 2.0 is False -> NaN emitted)
dfiv = pd.DataFrame({
    "strike": [110.0, 110.0], "type": ["call", "put"],
    "openInterest": [10, 20], "gex": [1e5, -1e5],
    "impliedVolatility": [np.nan, np.nan],
    "expiry": [exp_dt] * 2,
})
pk = G.build_ai_packet("TEST", dfiv, 105.0, [exp])
s = pk["market_snapshot"]["expiries"][0]["strikes"][0]
notes.append(
    f"NaN IV -> implied_volatility={s['implied_volatility']} "
    f"stale_iv={s.get('stale_iv')} (NaN>2.0 False; emitted raw NaN — "
    "pre-existing semantics, unchanged by F2)")
print(f"  note: NaN IV -> {s['implied_volatility']}")

# meta fields
pk = G.build_ai_packet("TEST", dfiv, 105.0, [exp])
md = pk["meta"]["metric_definitions"]
check("meta.metric_definitions complete+honest",
      all(k in md for k in
          ("gamma_flip", "materiality_floor",
           "max_pain", "max_pain_constrained"))
      and "strike-profile" in md["gamma_flip"].lower()
      and "not a" in md["gamma_flip"].lower()
      and "constrained payout minimum"
      in md["max_pain_constrained"].lower())
check("meta.data_quality documents retained-with-flag + provenance",
      "retained-with-flag" in pk["meta"]["data_quality"]
      and "raw_implied_volatility" in pk["meta"]["data_quality"])

# ============ PART 2: mutation harness ============
print("\n== PART 2: mutation checks (each must be caught) ==")
MUT_DIR.mkdir(exist_ok=True)
src = (REPO / "GexDashboard.py").read_text(encoding="utf-8")

MUTANTS = {
    # Pure-relative F1 filter: removes the combined max() floor AND
    # the early noise-slice return, reintroducing the F1 artifact.
    "M1_no_abs_floor": (
        """    max_abs = np.abs(gex_vals).max()

    # The whole slice is numerical noise — there
    # is no honest flip here.
    if max_abs < MIN_MATERIAL_GEX:
        return None""",
        """    max_abs = np.abs(gex_vals).max()""",
    ),
    "M1b_relative_only": (
        """    materiality = max(
        0.01 * max_abs,
        MIN_MATERIAL_GEX
    )""",
        """    materiality = 0.01 * max_abs""",
    ),
    "M2_strict_ineq": (
        "np.abs(gex_vals) >= materiality",
        "np.abs(gex_vals) > materiality",
    ),
    "M3_no_nonfinite_drop": (
        """    df = df[
        np.isfinite(df['strike'])
        & np.isfinite(df['gex'])
    ]""",
        """    df = df[df['strike'].notna()]""",
    ),
    "M4_swap_packet_mp": (
        '"max_pain": float(max_pain),',
        '"max_pain": float('
        'max_pain_constrained["max_pain"]),',
    ),
    "M5_no_boundary_hit": (
        "and best in result[\"candidate_band\"]",
        "and False",
    ),
    "M6_no_fell_back": (
        'result["band_fell_back"] = True',
        'pass  # mutant: never flag',
    ),
    "M7_canonical_banded": (
        """    strikes = sorted(df['strike'].unique())

    pains = _payout_pain(df, strikes)

    return strikes[int(np.argmin(pains))]""",
        """    strikes = sorted(df['strike'].unique())

    if spot is not None:
        bounded = [
            s for s in strikes
            if 0.7 * spot <= s <= 1.3 * spot
        ]
        if bounded:
            strikes = bounded

    pains = _payout_pain(df, strikes)

    return strikes[int(np.argmin(pains))]""",
    ),
    "M8_first_crossing": (
        """    return crossings[
        np.argmin(np.abs(crossings - spot))
    ]""",
        """    return crossings[0]""",
    ),
    "M9_no_provenance": (
        'strike_obj["raw_implied_volatility"] = iv',
        'pass  # mutant: drop provenance',
    ),
    "M10_metric_key_rename": (
        '"max_pain_constrained": (\n'
        '                    "Constrained payout minimum: "',
        '"mp_banded": (\n'
        '                    "Constrained payout minimum: "',
    ),
}


def load_mutant(name, old, new):
    if src.count(old) != 1:
        raise RuntimeError(
            f"{name}: pattern occurs {src.count(old)}x, need exactly 1")
    path = MUT_DIR / f"{name}.py"
    path.write_text(src.replace(old, new), encoding="utf-8")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def mutant_caught(name, fn, expect):
    """fn(mut) should RAISE/differ from expected -> test catches."""
    try:
        mut = load_mutant(name, *MUTANTS[name])
        out = fn(mut)
    except Exception as ex:  # noqa: BLE001 - crash counts as caught
        print(f"[PASS] {name} caught (raised {type(ex).__name__})")
        return
    if out == expect:
        print(f"[FAIL] {name} NOT caught: mutant returns {out!r} "
              f"matching expected")
        failures.append(f"mutant {name}")
    else:
        print(f"[PASS] {name} caught: mutant {out!r} != {expect!r}")


alt = gex_df([400.0, 410.0, 420.0, 430.0, 440.0, 450.0],
             [-1e-12, 1e-12, -1e-12, 1e-12, -1e-12, 1e-12])
# M1: drop early-return only. NOTE: expected to be UNCAUGHT here —
# the combined materiality floor alone still blocks the slice, so
# the early return is dead code (informational finding, not a
# vacuous test). We report it rather than assert a catch.
mut = load_mutant("M1_no_abs_floor", *MUTANTS["M1_no_abs_floor"])
r = mut.compute_gamma_flip(alt, spot=SPOT)
print(f"[INFO] M1_no_abs_floor (early-return only): alternating "
      f"noise -> {r!r} — early return is redundant given the "
      f"combined floor; tests cannot catch its removal alone")
notes.append(
    "compute_gamma_flip early-return `max_abs < MIN_MATERIAL_GEX` "
    "is functionally redundant: the combined floor "
    "max(0.01*max_abs, 1e-3) already makes every sub-floor strike "
    "immaterial. Removal alone is uncatchable; harmless.")

# M1b: revert materiality to pure-relative (keep early return).
# Alternating-noise still hits the early return, so distinguish on
# the relative-only above-floor case: gex=[-5e-2, +5e-4, -1e-2]
# where the +5e-4 strike is material under 1%-of-max but below the
# absolute floor. Correct answer None; mutant returns 420.
rel_only = gex_df([400.0, 420.0, 440.0], [-5e-2, 5e-4, -1e-2])
mutant_caught("M1b_relative_only",
              lambda m: m.compute_gamma_flip(rel_only, spot=SPOT),
              None)

# M1c: full F1 revert — remove BOTH the early return and the
# absolute floor. Alternating +/-1e-12 noise must then yield an
# artifact strike; the alternating-noise test catches it.
m1c_src = src.replace(*MUTANTS["M1_no_abs_floor"]).replace(
    *MUTANTS["M1b_relative_only"])
m1c_path = MUT_DIR / "M1c_full_revert.py"
m1c_path.write_text(m1c_src, encoding="utf-8")
spec = importlib.util.spec_from_file_location("M1c", m1c_path)
m1c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m1c)
r = m1c.compute_gamma_flip(alt, spot=SPOT)
if r is None:
    print("[FAIL] M1c_full_revert NOT caught: still None")
    failures.append("mutant M1c_full_revert")
else:
    print(f"[PASS] M1c_full_revert caught: artifact {r!r}")
# M2: at-floor test expects 420.0; mutant -> None
flr = gex_df([400.0, 420.0], [-1e-3, 1e-3])
mutant_caught("M2_strict_ineq",
              lambda m: m.compute_gamma_flip(flr, spot=SPOT), 420.0)
# M3: nonfinite test expects 420.0
nf = gex_df([-np.inf, 400.0, 410.0, 420.0, 430.0, np.nan],
            [-9e9, -5e6, -3e6, 4e6, np.inf, 1e6])
mutant_caught("M3_no_nonfinite_drop",
              lambda m: m.compute_gamma_flip(nf, spot=SPOT), 420.0)


def packet_mp(m):
    df = pd.DataFrame({
        "strike": [200.0, 420.0, 430.0] * 2,
        "type": ["call"] * 3 + ["put"] * 3,
        "openInterest": [100000, 100, 50, 10, 10, 10],
        "gex": [1e5] * 6,
        "impliedVolatility": [0.3] * 6,
        "expiry": [exp_dt] * 6,
    })
    p = m.build_ai_packet("TEST", df, SPOT, [exp])
    e = p["market_snapshot"]["expiries"][0]
    return e["max_pain"]


mutant_caught("M4_swap_packet_mp", packet_mp, 200.0)


def bound_hit(m):
    return m.compute_max_pain_constrained(edge, spot=25.0)[
        "band_boundary_hit"]


mutant_caught("M5_no_boundary_hit", bound_hit, True)


def fell_back(m):
    return m.compute_max_pain_constrained(oi, spot=1000.0)[
        "band_fell_back"]


mutant_caught("M6_no_fell_back", fell_back, True)
mutant_caught("M7_canonical_banded",
              lambda m: m.compute_max_pain(oi, spot=SPOT), 200.0)

mc = gex_df([250.0, 300.0, 400.0, 430.0],
            [-5e6, 2e6, -3e6, 4e6])
mutant_caught("M8_first_crossing",
              lambda m: m.compute_gamma_flip(mc, spot=SPOT), 430.0)


def provenance(m):
    df = pd.DataFrame({
        "strike": [110.0, 110.0], "type": ["call", "put"],
        "openInterest": [10, 20], "gex": [1e5, -1e5],
        "impliedVolatility": [3.5, 3.5],
        "expiry": [exp_dt] * 2,
    })
    p = m.build_ai_packet("TEST", df, 105.0, [exp])
    return p["market_snapshot"]["expiries"][0]["strikes"][0].get(
        "raw_implied_volatility")


mutant_caught("M9_no_provenance", provenance, 3.5)


def metric_keys(m):
    p = m.build_ai_packet("TEST", dfiv, 105.0, [exp])
    return "max_pain_constrained" in p["meta"]["metric_definitions"]


mutant_caught("M10_metric_key_rename", metric_keys, True)

print("\n== NOTES ==")
for n in notes:
    print(" *", n)

if failures:
    print(f"\nFAILURES: {failures}")
    sys.exit(1)
print("\nALL ADVERSARIAL CHECKS PASS")
sys.exit(0)

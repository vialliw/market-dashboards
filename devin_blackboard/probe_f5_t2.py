"""F5-T2 CHALLENGER probe (offline, fixture + synthetic — no network).

Adversarial review of F5-T1 (bb9f7b0, fc4989d, 084bebf):
degenerate-chain payout eligibility + scoped ValueError absorption.

A. Eligibility rule (_payout_ineligibility): thin-but-REAL chains —
   2/3-strike tiny-OI books, one-sided OI, zero-OI second strike,
   boundary cases at MIN_PAYOUT_STRIKES=2, negative/inf OI, empty
   slices. Can the rule flag a usable chain? Is <2 the right cut?
B. Scoped absorption: ONLY the exact numpy empty-argmin ValueError
   message is tolerated — unrelated ValueErrors (exact + substring
   variants), non-ValueErrors, and failures in gamma_flip /
   _payout_ineligibility must propagate.
C. Partial availability: degenerate payout + computable gamma_flip
   -> metrics_unavailable:true with REAL flip; consumers can still
   distinguish; healthy expiries byte-identical vs bb9f7b0^ module.
D. INTC live case: 1-strike call_oi=2/put_oi=0 -> insufficient_data;
   a normal expiry in the same book unaffected.
E. Mutation harness: source-mutated module exec'd in-process, REAL
   pytest tests run against it via sys.modules injection.
F. Internal consistency + diff/network audit helpers.
"""

import json
import subprocess
import sys
import types
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import GexDashboard
from GexDashboard import (
    _payout_ineligibility,
    _per_expiry_metrics,
    build_ai_packet,
    compute_gamma_flip,
    compute_max_pain,
    compute_max_pain_constrained,
)

FIXTURE = ROOT / "tests" / "fixtures" / "TSM_ai_packet.json"
SPOT = 422.05
MAGIC = "attempt to get argmin of an empty sequence"

failures = []
notes = []


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL"), name,
          ("| " + detail if detail else ""))
    if not ok:
        failures.append(name)


def note(text):
    print("NOTE", text)
    notes.append(text)


def raise_(exc):
    def f(*a, **k):
        raise exc
    return f


def book(rows, exp="2026-10-16"):
    dt = pd.Timestamp(exp, tz="UTC")
    return pd.DataFrame([
        {"strike": st, "type": t, "openInterest": oi,
         "gex": g, "impliedVolatility": iv, "expiry": dt}
        for st, t, oi, g, iv in rows
    ])


def independent_max_pain(df):
    frame = df[
        df["strike"].map(np.isfinite)
        & df["openInterest"].map(np.isfinite)
    ]
    best_s = best_p = None
    for s in sorted(frame["strike"].unique()):
        pain = 0.0
        for _, r in frame.iterrows():
            if r["type"] == "call":
                pain += (
                    max(0.0, s - r["strike"]) * r["openInterest"]
                )
            else:
                pain += (
                    max(0.0, r["strike"] - s) * r["openInterest"]
                )
        if best_p is None or pain < best_p:
            best_s, best_p = s, pain
    return best_s


def first_expiry(df, spot, exp="2026-10-16"):
    p = build_ai_packet("TEST", df, spot, [exp])
    return p["market_snapshot"]["expiries"][0]


PAYOUT_FIELDS = (
    "max_pain", "max_pain_constrained",
    "max_pain_candidate_band", "max_pain_band_fell_back",
    "max_pain_band_boundary_hit",
    "max_pain_selected_lowest_candidate",
    "max_pain_selected_highest_candidate",
)

print("=== A. eligibility: thin-but-real chains ===")

# A1: 2 distinct strikes, tiny OI (1 contract each) -> eligible
df = book([
    (100.0, "call", 1, -5e3, 0.30),
    (110.0, "call", 1, 1e3, 0.30),
    (100.0, "put", 1, 1e3, 0.30),
    (110.0, "put", 1, 1e3, 0.30),
])
e = first_expiry(df, 105.0)
check("A1 2-strike tiny-OI (total 4) is eligible",
      e["metrics_unavailable"] is False
      and e["max_pain"] == independent_max_pain(df),
      f"mp={e['max_pain']} vs {independent_max_pain(df)}")

# A2: 3 distinct strikes, tiny OI -> eligible
df = book([
    (100.0, "call", 1, -5e3, 0.30),
    (110.0, "put", 2, 1e3, 0.30),
    (120.0, "call", 1, 1e3, 0.30),
])
e = first_expiry(df, 105.0)
check("A2 3-strike tiny-OI is eligible",
      e["metrics_unavailable"] is False
      and e["max_pain"] == independent_max_pain(df),
      f"mp={e['max_pain']}")

# A3: one-sided OI — calls only, 2 strikes -> eligible; argmin is
# the lowest strike (calls can only add pain above their strike)
df = book([
    (100.0, "call", 5, -5e3, 0.30),
    (110.0, "call", 5, 1e3, 0.30),
])
e = first_expiry(df, 105.0)
check("A3 one-sided (calls-only) 2-strike eligible",
      e["metrics_unavailable"] is False
      and e["max_pain"] == 100.0
      == independent_max_pain(df),
      f"mp={e['max_pain']}")
note("A3: calls-only chain -> argmin is mechanically the lowest "
     "strike; puts-only -> highest. Mathematically the true argmin "
     "of a one-sided book — flagged eligible. Whether a one-sided "
     "2-strike chain carries economic signal is a policy call; the "
     "metric is honest about what it computes.")

# A4: 2 distinct strikes but the 2nd carries only OI=0 rows ->
# still eligible (OI=0 is finite; the strike is a real argmin
# candidate whose payout is computed from the other side)
df = book([
    (100.0, "call", 500, -5e4, 0.30),
    (100.0, "put", 100, 1e4, 0.30),
    (110.0, "call", 0, 0.0, 0.30),
    (110.0, "put", 0, 0.0, 0.30),
])
e = first_expiry(df, 105.0)
check("A4 2nd strike OI=0 -> still 2 distinct strikes, eligible",
      e["metrics_unavailable"] is False
      and e["max_pain"] == independent_max_pain(df),
      f"mp={e['max_pain']}")

# A5 boundary: exactly 1 distinct strike, BOTH rows finite OI ->
# insufficient_data (vacuous single-candidate argmin)
df = book([
    (100.0, "call", 10, -5e4, 0.30),
    (100.0, "put", 20, 1e4, 0.30),
])
e = first_expiry(df, 105.0)
check("A5 1-strike both-rows-finite -> insufficient_data",
      e["metrics_unavailable"] is True
      and e["data_quality"] == "insufficient_data"
      and all(e[f] is None for f in PAYOUT_FIELDS))

# A6: 2 strikes, only one has finite OI (NaN at the other)
df = book([
    (100.0, "call", 500, -5e4, 0.30),
    (110.0, "call", np.nan, np.nan, 0.30),
    (100.0, "put", 100, 1e4, 0.30),
    (110.0, "put", np.nan, np.nan, 0.30),
])
e = first_expiry(df, 105.0)
check("A6 2 strikes but 1 finite-OI -> insufficient_data",
      e["metrics_unavailable"] is True
      and e["data_quality"] == "insufficient_data")

# A7: 2 distinct finite-OI strikes, ALL OI zero -> sum<=0
df = book([
    (100.0, "call", 0, -5e4, 0.30),
    (110.0, "call", 0, 5e4, 0.30),
    (100.0, "put", 0, 1e4, 0.30),
    (110.0, "put", 0, 1e4, 0.30),
])
e = first_expiry(df, 105.0)
check("A7 zero-total-OI 2-strike -> insufficient_data",
      e["metrics_unavailable"] is True
      and e["data_quality"] == "insufficient_data"
      and e["max_pain"] is None)

# A8: negative total finite OI -> sum<=0 -> insufficient_data
df = book([
    (100.0, "call", -10, -5e4, 0.30),
    (110.0, "put", 5, 1e4, 0.30),
])
e = first_expiry(df, 105.0)
check("A8 negative total OI -> insufficient_data",
      e["metrics_unavailable"] is True
      and e["data_quality"] == "insufficient_data")

# A9: mixed sign OI summing positive -> eligible (garbage-in
# passes the gate; negative OI is nonsense input anyway)
df = book([
    (100.0, "call", -2, -5e4, 0.30),
    (110.0, "put", 15, 1e4, 0.30),
])
e = first_expiry(df, 105.0)
note(f"A9 mixed-sign OI summing +13 -> metrics_unavailable="
     f"{e['metrics_unavailable']} mp={e['max_pain']} "
     "(negative OI is invalid input; gate passes it — "
     "garbage-in, pre-existing class)")
check("A9 mixed-sign positive-total is eligible (documented)",
      e["metrics_unavailable"] is False)

# A10: inf OI rows drop -> 1 finite strike -> insufficient_data
# (metric layer check — the packet exporter itself raises
# OverflowError on int(inf) openInterest, a PRE-EXISTING wart
# verified against the bb9f7b0^ module in C4 below)
df = book([
    (100.0, "call", np.inf, np.nan, 0.30),
    (110.0, "put", 5, 1e4, 0.30),
])
check("A10 inf-OI row dropped -> insufficient_data",
      _payout_ineligibility(df) == "insufficient_data")
m_inf = _per_expiry_metrics(df, 105.0)
check("A10b metric bundle marks inf-OI slice unavailable",
      m_inf["unavailable"] == "insufficient_data")
try:
    build_ai_packet("X", df, 105.0, ["2026-10-16"])
    note("A10c packet exporter tolerated inf OI (unexpected)")
except OverflowError as oe:
    note(f"A10c packet exporter OverflowError on inf OI "
         f"(int(inf) in strike export — PRE-EXISTING, "
         f"unrelated to metrics path): {oe}")

# A11: all-NaN OI -> no_finite_oi (reason preserved)
df = book([
    (100.0, "call", np.nan, np.nan, 0.30),
    (110.0, "put", np.nan, np.nan, 0.30),
])
e = first_expiry(df, 105.0)
check("A11 all-NaN OI -> no_finite_oi (unchanged reason)",
      e["metrics_unavailable"] is True
      and e["data_quality"] == "no_finite_oi")

# A12: empty slice (absent expiry) -> no_finite_oi
p = build_ai_packet("X", book([
    (100.0, "call", 5, -5e4, 0.30),
    (110.0, "put", 5, 1e4, 0.30),
]), 105.0, ["2026-10-16", "2027-01-15"])
exs = {e["expiry"]: e
       for e in p["market_snapshot"]["expiries"]}
check("A12 absent expiry -> no_finite_oi + strikes []",
      exs["2027-01-15"]["metrics_unavailable"] is True
      and exs["2027-01-15"]["data_quality"] == "no_finite_oi"
      and exs["2027-01-15"]["strikes"] == [])

# A13: direct _payout_ineligibility checks on edge shapes
sub1 = book([(190.0, "call", 2, 5e4, 0.30),
             (190.0, "put", 0, -1e4, 0.30)])
check("A13 _payout_ineligibility: INTC 1-strike -> "
      "insufficient_data",
      _payout_ineligibility(sub1) == "insufficient_data")
sub0 = book([(190.0, "call", np.nan, np.nan, 0.30)])
check("A13b _payout_ineligibility: all-NaN -> no_finite_oi",
      _payout_ineligibility(sub0) == "no_finite_oi")
sub_ok = book([(100.0, "call", 5, -5e4, 0.30),
               (110.0, "put", 5, 1e4, 0.30)])
check("A13c _payout_ineligibility: healthy -> None",
      _payout_ineligibility(sub_ok) is None)

note("Threshold verdict: <2 distinct finite-OI strikes is the "
     "minimal non-vacuous cut — a 1-candidate argmin carries no "
     "information, a 2-candidate argmin does discriminate. <3 "
     "would additionally flag honest 2-strike chains (e.g. real "
     "2-strike books DO resolve a payout minimum); spec asked for "
     "a defensible rule and '<2' matches both spec letter and the "
     "vacuity argument. DEFENSIBLE.")

print("=== B. scoped absorption ===")

healthy = book([
    (100.0, "call", 500, -2e5, 0.30),
    (100.0, "put", 200, 1e5, 0.30),
    (110.0, "call", 300, 1e5, 0.30),
    (110.0, "put", 50, 1e5, 0.30),
])
saved_mp = GexDashboard.compute_max_pain
saved_mpc = GexDashboard.compute_max_pain_constrained
saved_gf = GexDashboard.compute_gamma_flip
saved_pe = GexDashboard._payout_ineligibility

try:
    # B1: unrelated ValueError propagates
    GexDashboard.compute_max_pain = raise_(
        ValueError("totally unrelated"))
    try:
        build_ai_packet("X", healthy, 105.0, ["2026-10-16"])
        check("B1 unrelated ValueError propagates", False)
    except ValueError as exc:
        check("B1 unrelated ValueError propagates",
              "totally unrelated" in str(exc), str(exc))

    # B2: more unrelated ValueError shapes propagate
    for msg in ("x", "invalid literal", "", "attempt to get "
                "argmin of an empty SEQUENCE",
                "argmin of empty"):
        GexDashboard.compute_max_pain = raise_(ValueError(msg))
        try:
            build_ai_packet("X", healthy, 105.0,
                            ["2026-10-16"])
            check(f"B2 ValueError({msg!r}) propagates", False)
        except ValueError:
            check(f"B2 ValueError({msg!r}) propagates", True)

    # B3: exact magic message -> absorbed -> insufficient_data
    GexDashboard.compute_max_pain = raise_(ValueError(MAGIC))
    p = build_ai_packet("X", healthy, 105.0, ["2026-10-16"])
    e = p["market_snapshot"]["expiries"][0]
    check("B3 exact empty-argmin msg absorbed -> "
          "insufficient_data",
          e["metrics_unavailable"] is True
          and e["data_quality"] == "insufficient_data"
          and e["max_pain"] is None)

    # B3b: magic message as SUBSTRING is also absorbed ('in' check)
    GexDashboard.compute_max_pain = raise_(ValueError(
        "wrapped: " + MAGIC + " (trace-id 7)"))
    p = build_ai_packet("X", healthy, 105.0, ["2026-10-16"])
    e = p["market_snapshot"]["expiries"][0]
    note("B3b substring-magic ValueError -> "
         f"unavailable={e['metrics_unavailable']} (substring "
         "match absorbs any ValueError CONTAINING numpy's text)")
    check("B3b substring-magic absorbed (documented)",
          e["metrics_unavailable"] is True
          and e["data_quality"] == "insufficient_data")

    # B4: magic ValueError from the CONSTRAINED fn only — max_pain
    # already computed; packet must still emit payout nulls
    GexDashboard.compute_max_pain = saved_mp
    GexDashboard.compute_max_pain_constrained = raise_(
        ValueError(MAGIC))
    p = build_ai_packet("X", healthy, 105.0, ["2026-10-16"])
    e = p["market_snapshot"]["expiries"][0]
    check("B4 constrained-fn magic raise -> packet nulls anyway",
          e["metrics_unavailable"] is True
          and e["data_quality"] == "insufficient_data"
          and all(e[f] is None for f in PAYOUT_FIELDS))
    m = _per_expiry_metrics(
        healthy[healthy["expiry"] == healthy["expiry"].iloc[0]],
        105.0)
    note("B4 internal: metrics dict after constrained-only "
         f"failure -> max_pain={m['max_pain']} + "
         f"unavailable={m['unavailable']} (internally "
         "inconsistent but both consumers null it — external "
         "state consistent)")

    # B5: non-ValueError types propagate
    GexDashboard.compute_max_pain_constrained = saved_mpc
    for exc in (IndexError("i"), KeyError("k"), TypeError("t"),
                RuntimeError("r"), ZeroDivisionError("z"),
                AssertionError("a"), OSError("o"),
                FloatingPointError("f")):
        GexDashboard.compute_max_pain = raise_(exc)
        try:
            build_ai_packet("X", healthy, 105.0,
                            ["2026-10-16"])
            check(f"B5 {type(exc).__name__} propagates", False)
        except type(exc):
            check(f"B5 {type(exc).__name__} propagates", True)
        except ValueError:
            check(f"B5 {type(exc).__name__} propagates", False,
                  "arrived as ValueError?")

    # B6: ValueError raised by gamma_flip is NEVER absorbed
    # (computed outside the try) — correct: empty-argmin cannot
    # occur there (crossing guard), so any raise is a bug
    GexDashboard.compute_max_pain = saved_mp
    GexDashboard.compute_gamma_flip = raise_(ValueError(MAGIC))
    try:
        build_ai_packet("X", healthy, 105.0, ["2026-10-16"])
        check("B6 gamma_flip magic-ValueError propagates",
              False, "absorbed?!")
    except ValueError:
        check("B6 gamma_flip magic-ValueError propagates "
              "(outside try)", True)

    # B7: failure inside _payout_ineligibility propagates
    GexDashboard.compute_gamma_flip = saved_gf
    GexDashboard._payout_ineligibility = raise_(
        RuntimeError("eligibility bug"))
    try:
        build_ai_packet("X", healthy, 105.0, ["2026-10-16"])
        check("B7 eligibility-fn failure propagates", False)
    except RuntimeError:
        check("B7 eligibility-fn failure propagates", True)
finally:
    GexDashboard.compute_max_pain = saved_mp
    GexDashboard.compute_max_pain_constrained = saved_mpc
    GexDashboard.compute_gamma_flip = saved_gf
    GexDashboard._payout_ineligibility = saved_pe

# B8: real numpy message check (version-dependence sanity)
try:
    np.argmin(np.array([]))
except ValueError as exc:
    check("B8 this numpy emits the magic message",
          MAGIC in str(exc), str(exc))

print("=== C. partial availability + consumer distinction ===")

# C1: payout-degenerate (1 finite-OI strike) + material gex
# crossing at 110 -> flag true + REAL flip emitted
df = book([
    (100.0, "call", 2, -1e5, 0.30),
    (110.0, "call", np.nan, 2e5, 0.30),
])
e = first_expiry(df, 105.0)
check("C1 degenerate payout + real flip coexists",
      e["metrics_unavailable"] is True
      and e["data_quality"] == "insufficient_data"
      and all(e[f] is None for f in PAYOUT_FIELDS)
      and e["gamma_flip"] == 110.0,
      f"flip={e['gamma_flip']}")

# C1b: strike-level is_gamma_flip flag still set on flagged expiry
flags = [s["is_gamma_flip"] for s in e["strikes"]
         if s["strike"] == 110.0]
check("C1b is_gamma_flip strike flag set on flagged expiry",
      flags == [True], str(e["strikes"]))

# C2: distinguishability matrix —
# healthy+flip: flag F, flip real
# healthy+noflip: flag F, flip null
# degenerate+flip: flag T, flip real
# degenerate+noflip: flag T, flip null
df_hn = book([
    (100.0, "call", 500, -1e5, 0.30),
    (110.0, "put", 200, -5e4, 0.30),   # all-neg gex: no cross
])
e_hn = first_expiry(df_hn, 105.0)
df_dn = book([
    (100.0, "call", 2, -1e5, 0.30),
    (110.0, "call", np.nan, -2e5, 0.30),  # no pos side
])
e_dn = first_expiry(df_dn, 105.0)
check("C2 four-state distinction works",
      e_dn["metrics_unavailable"] is True
      and e_dn["gamma_flip"] is None
      and e_hn["metrics_unavailable"] is False
      and e_hn["gamma_flip"] is None)

# C3: no_finite_oi with synthetic finite gex -> flip still emitted
df = book([
    (100.0, "call", np.nan, -1e5, 0.30),
    (110.0, "call", np.nan, 2e5, 0.30),
])
e = first_expiry(df, 105.0)
check("C3 no_finite_oi + finite gex -> flip emitted",
      e["metrics_unavailable"] is True
      and e["data_quality"] == "no_finite_oi"
      and e["gamma_flip"] == 110.0,
      f"flip={e['gamma_flip']}")

note("C-reachability: on the REAL pipeline compute_gex derives "
     "gex = gamma*OI*... so a MATERIAL |gex|>=1e-3 strike needs "
     "nonzero finite OI; 2 material strikes => 2 finite-OI "
     "strikes => eligible. A flagged expiry with a real flip is "
     "therefore unreachable via fetch_options+compute_gex — the "
     "per-metric availability is defense-in-depth for cross-fed/"
     "synthetic frames, honestly documented in test docstring.")

# C4: A/B vs pre-F5 committed module — healthy fixture expiries
print("=== C4. A/B healthy-expiry equality vs bb9f7b0^ ===")
old_src = subprocess.run(
    ["git", "show", "bb9f7b0^:GexDashboard.py"],
    capture_output=True, text=True, cwd=ROOT,
).stdout
check("C4-0 pre-F5 source retrieved",
      "MIN_PAYOUT_STRIKES" not in old_src
      and "_payout_ineligibility" not in old_src)
old_mod = types.ModuleType("GexDashboard_old")
old_mod.__file__ = str(ROOT / "GexDashboard.py")
exec(compile(old_src, "GexDashboard_old.py", "exec"),
     old_mod.__dict__)

fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
rows = []
expiries = []
for ex in fixture["market_snapshot"]["expiries"]:
    expiries.append(ex["expiry"])
    dt = pd.Timestamp(ex["expiry"], tz="UTC")
    for s in ex["strikes"]:
        iv = s.get("raw_implied_volatility",
                   s.get("implied_volatility"))
        iv = np.nan if iv is None else iv
        rows.append({"strike": s["strike"], "type": "call",
                     "openInterest": s["call_oi"],
                     "gex": s["call_gex"],
                     "impliedVolatility": iv, "expiry": dt})
        rows.append({"strike": s["strike"], "type": "put",
                     "openInterest": s["put_oi"],
                     "gex": s["put_gex"],
                     "impliedVolatility": iv, "expiry": dt})
book7 = pd.DataFrame(rows)

old_pkt = old_mod.build_ai_packet("TSM", book7, SPOT, expiries)
new_pkt = build_ai_packet("TSM", book7, SPOT, expiries)
old_ex = {e["expiry"]: e
          for e in old_pkt["market_snapshot"]["expiries"]}
new_ex = {e["expiry"]: e
          for e in new_pkt["market_snapshot"]["expiries"]}
byte_mismatch = [
    exp for exp in old_ex
    if json.dumps(old_ex[exp], sort_keys=True, default=str)
    != json.dumps(new_ex[exp], sort_keys=True, default=str)
]
check("C4-1 all 7 per-expiry dicts BYTE-identical "
      "(incl. new keys)",
      not byte_mismatch, str(byte_mismatch))
check("C4-2 no fixture expiry falsely flagged",
      all(e["metrics_unavailable"] is False
          for e in new_pkt["market_snapshot"]["expiries"]))
old_snap = dict(old_pkt["market_snapshot"])
new_snap = dict(new_pkt["market_snapshot"])
old_snap.pop("expiries"); new_snap.pop("expiries")
check("C4-3 whole-book market_snapshot identical",
      old_snap == new_snap)
meta_diff = {k for k in old_pkt["meta"]
             if k != "generated_utc"  # wall-clock, always differs
             and json.dumps(
                 old_pkt["meta"].get(k), sort_keys=True,
                 default=str)
             != json.dumps(
                 new_pkt["meta"].get(k), sort_keys=True,
                 default=str)}
check("C4-4 meta diff limited to doc strings",
      meta_diff <= {"data_quality", "metric_definitions"},
      str(meta_diff))
note(f"C4 meta keys changed: {sorted(meta_diff)} "
     "(documentation text — declared)")

# C4-5: the A10 inf-OI OverflowError is pre-existing — the old
# module's exporter must fail identically
_inf_df = book([
    (100.0, "call", np.inf, np.nan, 0.30),
    (110.0, "put", 5, 1e4, 0.30),
])
try:
    old_mod.build_ai_packet("X", _inf_df, 105.0,
                            ["2026-10-16"])
    check("C4-5 inf-OI OverflowError is pre-existing", False,
          "old module tolerated it")
except OverflowError:
    check("C4-5 inf-OI OverflowError is pre-existing", True)
except Exception as exc:
    check("C4-5 inf-OI OverflowError is pre-existing", False,
          f"old raised {type(exc).__name__}")

print("=== D. INTC live case ===")
df_intc = book([
    (190.0, "call", 2, 5e4, 0.30),
    (190.0, "put", 0, -1e4, 0.30),
], exp="2026-10-19")
p = build_ai_packet("INTC", df_intc, 185.0, ["2026-10-19"])
e = p["market_snapshot"]["expiries"][0]
check("D1 INTC 1-strike/2-OI -> insufficient_data + nulls",
      e["metrics_unavailable"] is True
      and e["data_quality"] == "insufficient_data"
      and all(e[f] is None for f in PAYOUT_FIELDS))
check("D2 vacuous argmin NOT emitted; strike row kept",
      e["strikes"][0]["strike"] == 190.0
      and e["strikes"][0]["call_oi"] == 2)
check("D3 raw compute_max_pain still returns vacuous 190 "
      "(documented loud-artifact-on-direct-misuse)",
      compute_max_pain(df_intc) == 190.0)

# D4: normal expiry in same book unaffected
df_mix = pd.concat([df_intc, healthy.assign(
    expiry=pd.Timestamp("2026-11-20", tz="UTC"))])
p = build_ai_packet("INTC", df_mix, 185.0,
                    ["2026-10-19", "2026-11-20"])
exs = {e["expiry"]: e
       for e in p["market_snapshot"]["expiries"]}
check("D4 healthy expiry beside degenerate unaffected",
      exs["2026-11-20"]["metrics_unavailable"] is False
      and exs["2026-11-20"]["max_pain"] is not None
      and "data_quality" not in exs["2026-11-20"]
      and exs["2026-10-19"]["metrics_unavailable"] is True)

print("=== E. mutation harness (real tests vs mutant module) ===")
SRC = (ROOT / "GexDashboard.py").read_text(encoding="utf-8")
NEW_TESTS = ("degenerate or payout_eligibility or zero_total_oi "
             "or partial_availability or unrelated_valueerror or "
             "empty_argmin or non_valueerror or all_nan_oi or "
             "unavailable_differs")


def run_new_tests(mut_src, label):
    real = sys.modules.get("GexDashboard")
    mod = types.ModuleType("GexDashboard")
    mod.__file__ = str(ROOT / "GexDashboard.py")
    exec(compile(mut_src, "GexDashboard.py", "exec"),
         mod.__dict__)
    sys.modules["GexDashboard"] = mod
    for name in [m for m in sys.modules
                 if "test_gex_metrics" in m]:
        sys.modules.pop(name, None)
    try:
        rc = pytest.main(
            ["tests/test_gex_metrics.py", "-k", NEW_TESTS,
             "-q", "--no-header", "-p", "no:cacheprovider"]
        )
    finally:
        if real is not None:
            sys.modules["GexDashboard"] = real
        else:
            sys.modules.pop("GexDashboard", None)
    print(f"    mutant {label}: pytest rc={rc}")
    return rc


rc0 = run_new_tests(SRC, "control")
check("E0 control run of F5 tests passes", rc0 == 0)

# M1: threshold 2 -> 1 (1-strike chains become eligible)
m1 = SRC.replace("MIN_PAYOUT_STRIKES = 2",
                 "MIN_PAYOUT_STRIKES = 1")
assert m1 != SRC
modm = types.ModuleType("m1")
exec(compile(m1, "m1", "exec"), modm.__dict__)
em = modm.build_ai_packet("X", df_intc, 185.0,
                          ["2026-10-19"])["market_snapshot"][
    "expiries"][0]
check("E-M1 mutant emits vacuous 190 (sanity)",
      em["max_pain"] == 190.0
      and em["metrics_unavailable"] is False,
      f"mp={em['max_pain']}")
check("E-M1 suite catches threshold 2->1",
      run_new_tests(m1, "M1") != 0)

# M2: absorb ALL ValueError (drop the message check)
anchor = """        if (
            "attempt to get argmin of an "
            "empty sequence"
        ) not in str(exc):
            raise
"""
assert anchor in SRC
m2 = SRC.replace(anchor, "        pass\n")
modm = types.ModuleType("m2")
exec(compile(m2, "m2", "exec"), modm.__dict__)
try:
    modm.compute_max_pain = raise_(
        ValueError("totally unrelated"))
    pm = modm.build_ai_packet("X", healthy, 105.0,
                              ["2026-10-16"])
    em = pm["market_snapshot"]["expiries"][0]
    check("E-M2 mutant swallows unrelated ValueError "
          "(sanity)", em["metrics_unavailable"] is True)
except ValueError:
    check("E-M2 mutant swallows unrelated ValueError "
          "(sanity)", False, "mutant still raised")
check("E-M2 suite catches absorb-all-ValueError",
      run_new_tests(m2, "M2") != 0)

# M3: null gamma_flip on the unavailable branch (revert partial
# availability)
anchor = """                "gamma_flip": (
                    float(gamma_flip)
                    if gamma_flip
                    else None
                ),

                "data_quality": metrics[
                    "unavailable"
                ]"""
assert anchor in SRC
m3 = SRC.replace(anchor, """                "gamma_flip": None,

                "data_quality": metrics[
                    "unavailable"
                ]""")
check("E-M3 suite catches null-flip-on-unavailable",
      run_new_tests(m3, "M3") != 0)

# M4: distinct -> row count (nunique -> len) — 1-strike 2-row
# chains become eligible
anchor = "        fin['strike'].nunique()"
assert anchor in SRC
m4 = SRC.replace(anchor, "        len(fin)")
check("E-M4 suite catches nunique->len",
      run_new_tests(m4, "M4") != 0)

# M5: total-OI guard weakened (<= -> <) — zero-OI chains eligible
anchor = "or fin['openInterest'].sum() <= 0"
assert anchor in SRC
m5 = SRC.replace(anchor, "or fin['openInterest'].sum() < 0")
modm = types.ModuleType("m5")
exec(compile(m5, "m5", "exec"), modm.__dict__)
zdf = book([
    (100.0, "call", 0, -5e4, 0.30),
    (110.0, "call", 0, 5e4, 0.30),
    (100.0, "put", 0, 1e4, 0.30),
    (110.0, "put", 0, 1e4, 0.30),
])
em = modm.build_ai_packet("X", zdf, 105.0,
                          ["2026-10-16"])["market_snapshot"][
    "expiries"][0]
check("E-M5 mutant emits artifact on zero-OI (sanity)",
      em["max_pain"] == 100.0
      and em["metrics_unavailable"] is False,
      f"mp={em['max_pain']}")
check("E-M5 suite catches <= -> <",
      run_new_tests(m5, "M5") != 0)

# M6: eligibility pre-check bypassed — payout fns called on
# ineligible slices
anchor = """    if metrics["unavailable"] is not None:
        return metrics
"""
assert anchor in SRC
m6 = SRC.replace(anchor, "")
modm = types.ModuleType("m6")
exec(compile(m6, "m6", "exec"), modm.__dict__)
m_int = modm._per_expiry_metrics(df_intc, 185.0)
em = modm.build_ai_packet("X", df_intc, 185.0,
                          ["2026-10-19"])["market_snapshot"][
    "expiries"][0]
check("E-M6 internal: payout fn now runs on ineligible slice "
      "(vacuous 190 computed, still flagged)",
      m_int["max_pain"] == 190.0
      and m_int["unavailable"] == "insufficient_data")
check("E-M6 degenerate packet still emits nulls + flag",
      em["max_pain"] is None
      and em["metrics_unavailable"] is True)
# ...BUT the early return also preserves the more specific
# 'no_finite_oi' reason: without it, an all-NaN slice calls the
# payout fns, the magic ValueError is absorbed, and 'unavailable'
# is OVERWRITTEN to 'insufficient_data' — observable reason loss
nan_df = book([
    (100.0, "call", np.nan, np.nan, 0.30),
    (110.0, "put", np.nan, np.nan, 0.30),
])
em_nan = modm.build_ai_packet(
    "X", nan_df, 105.0, ["2026-10-16"]
)["market_snapshot"]["expiries"][0]
check("E-M6 all-NaN reason degrades no_finite_oi -> "
      "insufficient_data (sanity)",
      em_nan["data_quality"] == "insufficient_data",
      em_nan.get("data_quality"))
check("E-M6 suite catches pre-check removal",
      run_new_tests(m6, "M6") != 0)

# M7: data_quality reason flattened to 'no_finite_oi'
anchor = """                "data_quality": metrics[
                    "unavailable"
                ]"""
assert anchor in SRC
m7 = SRC.replace(anchor,
                 '                "data_quality": '
                 '"no_finite_oi"')
check("E-M7 suite catches wrong reason string",
      run_new_tests(m7, "M7") != 0)

# M8: flag inverted — healthy expiries flagged, degenerate not
anchor = """            "metrics_unavailable": bool(
                metrics["unavailable"]
                is not None
            ),"""
assert anchor in SRC
m8 = SRC.replace(anchor, """            "metrics_unavailable": bool(
                metrics["unavailable"]
                is None
            ),""")
check("E-M8 suite catches inverted flag",
      run_new_tests(m8, "M8") != 0)

print("=== F. misc internal consistency ===")
# metrics dict contract: unavailable expiry -> payout keys null
m = _per_expiry_metrics(df_intc, 185.0)
check("F1 _per_expiry_metrics dict contract on degenerate",
      m["max_pain"] is None
      and m["max_pain_constrained"] is None
      and m["unavailable"] == "insufficient_data"
      and "gamma_flip" in m)
m = _per_expiry_metrics(healthy, 105.0)
check("F2 healthy: full dict + unavailable None",
      m["unavailable"] is None
      and m["max_pain"] == independent_max_pain(healthy)
      and isinstance(m["max_pain_constrained"], dict))

print()
print("=" * 50)
if failures:
    print(f"{len(failures)} FAILURES:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print(f"ALL CHECKS PASSED ({len(notes)} notes)")

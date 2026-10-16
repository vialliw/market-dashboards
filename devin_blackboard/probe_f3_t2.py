"""F3-T2 CHALLENGER probe (offline, fixture + synthetic — no network).

Adversarial review of F3-T1 (commits 9a8f573, bde2c93, b01a26d):

A. Clean-data equivalence: A/B new code vs the PRE-F3 committed
   GexDashboard (git show 9a8f573^) on every TSM fixture expiry —
   _finite_oi_rows must be identity on finite data.
B. NaN semantics: one-sided NaN OI, NaN-strike rows, inf OI,
   all-NaN / empty slices -> loud ValueError, argmin tie -> lowest
   strike, int dtype, zero-kept vs NaN-dropped candidate sets.
C. Band-edge flags: interior argmin, constraint-driven lowest/
   highest between strikes, COINCIDENTAL endpoint (unconstrained
   argmin == lowest eligible — band not binding), single eligible
   candidate, empty-band fell_back (guard), spot=None, spot=NaN,
   literal edge.
D. compute_gamma_flip df path: NaN-gex rows dropped, all-NaN gex
   -> None, NaN OI with finite gex is irrelevant (OI unused).
E. Mutation checks: (M1) revert _finite_oi_rows -> artifact
   returns AND the real regression test fails; (M2) simulate
   filter-only-in-payout revert -> NaN-strike candidate hijacks
   argmin (outer filter is load-bearing); (M3) swap selected_*
   flags -> real between-strikes test fails; (M4) simulate
   removing the fell_back guard -> flags would misfire.
F. Packet end-to-end: new flag keys emitted as bools, NaN-OI
   collapse provenance accurate, meta texts present.
"""

import importlib.util
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import GexDashboard
from GexDashboard import (
    build_ai_packet,
    compute_gamma_flip,
    compute_max_pain,
    compute_max_pain_constrained,
)

FIXTURE = ROOT / "tests" / "fixtures" / "TSM_ai_packet.json"
SPOT = 422.05

failures = []


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL"), name,
          ("| " + detail if detail else ""))
    if not ok:
        failures.append(name)


def info(name, detail):
    print("INFO", name, "|", detail)


def oi_df(expiry_obj, zero_as_nan=False):
    rows = []
    for s in expiry_obj["strikes"]:
        c, p = s["call_oi"], s["put_oi"]
        if zero_as_nan:
            c = np.nan if c == 0 else c
            p = np.nan if p == 0 else p
        rows.append({"strike": s["strike"], "type": "call",
                     "openInterest": c})
        rows.append({"strike": s["strike"], "type": "put",
                     "openInterest": p})
    return pd.DataFrame(rows)


def indep_argmin(df):
    f = df[df["strike"].map(math.isfinite)
           & df["openInterest"].map(math.isfinite)]
    best_s, best_p = None, None
    for s in sorted(f["strike"].unique()):
        pain = 0.0
        for _, r in f.iterrows():
            if r["type"] == "call":
                pain += max(0.0, s - r["strike"]) * r["openInterest"]
            else:
                pain += max(0.0, r["strike"] - s) * r["openInterest"]
        if best_p is None or pain < best_p:
            best_s, best_p = s, pain
    return best_s


packet = json.load(open(FIXTURE, encoding="utf-8"))
expiries = {e["expiry"]: e
            for e in packet["market_snapshot"]["expiries"]}

# ---------- A. clean-data A/B vs pre-F3 committed module ----------
prev_src = subprocess.run(
    ["git", "show", "9a8f573^:GexDashboard.py"],
    capture_output=True, text=True, cwd=ROOT, check=True,
).stdout
prev_path = Path(tempfile.mkdtemp()) / "gex_prev.py"
prev_path.write_text(prev_src, encoding="utf-8")
spec = importlib.util.spec_from_file_location("gex_prev", prev_path)
gex_prev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gex_prev)

for exp, e in expiries.items():
    df = oi_df(e)  # all finite
    new_mp = compute_max_pain(df)
    old_mp = gex_prev.compute_max_pain(df)
    new_c = compute_max_pain_constrained(df, spot=SPOT)
    old_c = gex_prev.compute_max_pain_constrained(df, spot=SPOT)
    check(
        f"{exp} clean A/B identical",
        new_mp == old_mp
        and new_c["max_pain"] == old_c["max_pain"]
        and new_c["band_fell_back"] == old_c["band_fell_back"]
        and new_c["band_boundary_hit"]
        == old_c["band_boundary_hit"],
        f"new={new_mp}/{new_c['max_pain']} "
        f"old={old_mp}/{old_c['max_pain']}",
    )
    # informational: zero-kept vs NaN-dropped candidate sets
    nan_mp = compute_max_pain(oi_df(e, zero_as_nan=True))
    if nan_mp != new_mp:
        info(f"{exp} zero-vs-NaN candidate-set shift",
             f"zeros-kept={new_mp} NaN-dropped={nan_mp}")

# ---------- B. NaN semantics ----------
# one-sided NaN: call NaN at 150, put finite -> 150 stays a
# candidate and only the put side contributes
df = pd.DataFrame({
    "strike": [100.0, 150.0, 200.0, 100.0, 150.0, 200.0],
    "type": ["call", "call", "call", "put", "put", "put"],
    "openInterest": [10, np.nan, 10, 10, 5000, 10],
})
mp = compute_max_pain(df)
ind = indep_argmin(df)
check("one-sided NaN: strike survives, matches independent",
      mp == ind, f"mp={mp} indep={ind}")

# NaN-strike row with finite OI is dropped from candidates
df = pd.DataFrame({
    "strike": [np.nan, 100.0, 200.0, np.nan, 100.0, 200.0],
    "type": ["call", "call", "call", "put", "put", "put"],
    "openInterest": [99999, 10, 10, 99999, 10, 10],
})
mp = compute_max_pain(df)
check("NaN-strike rows excluded (finite result, in {100,200})",
      mp in (100.0, 200.0), f"mp={mp}")

# inf OI treated as nonfinite -> dropped
df_inf = pd.DataFrame({
    "strike": [100.0, 200.0, 100.0, 200.0],
    "type": ["call", "call", "put", "put"],
    "openInterest": [np.inf, 10, np.inf, 10],
})
mp_inf = compute_max_pain(df_inf)
check("inf-OI rows dropped, argmin on finite rows only",
      mp_inf == indep_argmin(df_inf), f"mp={mp_inf}")

# all-NaN / empty / all-NaN-strike -> loud ValueError (all 3 fns)
def raises_valueerror(fn, *a, **k):
    try:
        fn(*a, **k)
        return False
    except ValueError:
        return True
    except Exception as e:  # noqa: BLE001
        print("   unexpected:", type(e).__name__, e)
        return False


nan_only = pd.DataFrame({
    "strike": [100.0, 200.0],
    "type": ["call", "put"],
    "openInterest": [np.nan, np.nan],
})
empty = nan_only.iloc[0:0]
nan_strike = pd.DataFrame({
    "strike": [np.nan], "type": ["call"],
    "openInterest": [5.0],
})
check("all-NaN OI -> ValueError (max_pain)",
      raises_valueerror(compute_max_pain, nan_only))
check("all-NaN OI -> ValueError (constrained)",
      raises_valueerror(
          compute_max_pain_constrained, nan_only, SPOT))
check("empty df -> ValueError (max_pain)",
      raises_valueerror(compute_max_pain, empty))
check("empty df -> ValueError (constrained)",
      raises_valueerror(
          compute_max_pain_constrained, empty, SPOT))
check("all-NaN strike -> ValueError",
      raises_valueerror(compute_max_pain, nan_strike))

# argmin tie -> lowest strike wins (np.argmin first-min)
df_tie = pd.DataFrame({
    "strike": [100.0, 200.0, 100.0, 200.0],
    "type": ["call", "call", "put", "put"],
    "openInterest": [10, 10, 10, 10],
})
# pain(100)=put200:10*100=1000 ; pain(200)=call100:10*100=1000
check("pain tie -> lowest strike wins",
      compute_max_pain(df_tie) == 100.0,
      f"mp={compute_max_pain(df_tie)}")

# int dtype df works
df_int = pd.DataFrame({
    "strike": [100, 200, 100, 200],
    "type": ["call", "call", "put", "put"],
    "openInterest": [10, 5, 10, 5],
})
check("int-dtype df works",
      compute_max_pain(df_int) in (100, 200),
      f"mp={compute_max_pain(df_int)}")

# ---------- C. band-edge flag battery ----------
base = pd.DataFrame({
    "strike": [100.0, 150.0, 200.0, 100.0, 150.0, 200.0],
    "type": ["call", "call", "call", "put", "put", "put"],
    "openInterest": [10, 10000, 10, 10, 10, 10],
})

# interior argmin -> neither flag
r = compute_max_pain_constrained(base, spot=160.0)
# band [112,208] -> eligible {150,200}; argmin=150? compute:
# actually pick a case where argmin is interior: heavy OI at 200
df_int2 = base.copy()
df_int2["openInterest"] = [10, 10, 10, 10, 10, 90000]
r = compute_max_pain_constrained(df_int2, spot=210.0)
check("driven highest between-strikes",
      r["max_pain"] == 200.0
      and r["selected_highest_candidate"] is True
      and r["selected_lowest_candidate"] is False
      and r["band_boundary_hit"] is False, str(r))

r = compute_max_pain_constrained(base, spot=210.0)
check("driven lowest between-strikes",
      r["max_pain"] == 150.0
      and r["selected_lowest_candidate"] is True
      and r["selected_highest_candidate"] is False
      and r["band_boundary_hit"] is False, str(r))

# COINCIDENTAL endpoint: unconstrained argmin IS the lowest
# eligible candidate (band not binding) — flag still fires.
df_co = pd.DataFrame({
    "strike": [100.0, 150.0, 160.0, 100.0, 150.0, 160.0],
    "type": ["call", "call", "call", "put", "put", "put"],
    "openInterest": [10, 90000, 10, 10, 10, 10],
})
unc = compute_max_pain(df_co)
r = compute_max_pain_constrained(df_co, spot=160.0)
# band [112,208] -> eligible {150,160}; argmin overall = 150
info("coincidental-endpoint semantics",
     f"unconstrained={unc} constrained={r['max_pain']} "
     f"lowest_flag={r['selected_lowest_candidate']}")
check("coincidental endpoint: flag literal (not proof band "
      "bound) — fires when argmin IS lowest eligible",
      unc == 150.0 and r["max_pain"] == 150.0
      and r["selected_lowest_candidate"] is True, str(r))

# single eligible candidate -> BOTH flags fire (1-elem set)
r = compute_max_pain_constrained(base, spot=140.0)
# band [98,182] -> eligible {100,150}? 100>=98 yes -> two.
# use spot=100 -> band [70,130] -> eligible {100} only
r = compute_max_pain_constrained(base, spot=100.0)
check("single eligible candidate -> both flags True",
      r["candidate_band"] == [70.0, 130.0]
      and r["max_pain"] == 100.0
      and r["selected_lowest_candidate"] is True
      and r["selected_highest_candidate"] is True, str(r))

# empty band -> fell_back; even when argmin is the LOWEST
# strike, selected_* must stay False (band inactive)
r = compute_max_pain_constrained(base, spot=10.0)
check("empty band fell_back suppresses selected_* flags",
      r["band_fell_back"] is True
      and r["max_pain"] == 150.0
      and r["selected_lowest_candidate"] is False
      and r["selected_highest_candidate"] is False, str(r))

# spot=None -> band None -> flags False
r = compute_max_pain_constrained(base, spot=None)
check("spot=None -> no band, no flags",
      r["candidate_band"] is None
      and r["selected_lowest_candidate"] is False
      and r["selected_highest_candidate"] is False, str(r))

# spot=NaN -> band NaN -> empty -> fell_back, flags False
r = compute_max_pain_constrained(base, spot=float("nan"))
check("spot=NaN -> fell_back, flags False",
      r["band_fell_back"] is True
      and r["selected_lowest_candidate"] is False
      and r["selected_highest_candidate"] is False, str(r))

# literal edge hit implies matching selected_* flag
df_edge = pd.DataFrame({
    "strike": [10.0, 17.5, 25.0, 10.0, 17.5, 25.0],
    "type": ["call", "call", "call", "put", "put", "put"],
    "openInterest": [10, 90000, 10, 10, 10, 10],
})
r = compute_max_pain_constrained(df_edge, spot=25.0)
check("literal edge hit -> boundary_hit AND lowest_selected",
      r["max_pain"] == 17.5
      and r["band_boundary_hit"] is True
      and r["selected_lowest_candidate"] is True
      and r["selected_highest_candidate"] is False, str(r))

# ---------- D. gamma_flip df path ----------
df_g = pd.DataFrame({
    "strike": [400.0, 420.0, 440.0, 460.0],
    "gex": [-5.0, np.nan, 3.0, 8.0],
})
check("NaN-gex row dropped; flip over finite rows",
      compute_gamma_flip(df_g, spot=430.0) == 440.0,
      f"flip={compute_gamma_flip(df_g, spot=430.0)}")

df_gnan = pd.DataFrame({
    "strike": [400.0, 420.0], "gex": [np.nan, np.nan]})
check("all-NaN gex -> None",
      compute_gamma_flip(df_gnan, spot=SPOT) is None
      and compute_gamma_flip(df_gnan) is None)

# NaN OI is irrelevant to gamma_flip (it consumes gex only)
df_g2 = pd.DataFrame({
    "strike": [400.0, 420.0, 440.0],
    "gex": [-5.0, -2.0, 7.0],
    "openInterest": [np.nan, 10.0, np.nan],
})
check("NaN-OI w/ finite gex -> flip unaffected",
      compute_gamma_flip(df_g2, spot=430.0) == 440.0,
      f"flip={compute_gamma_flip(df_g2, spot=430.0)}")

# ---------- E. mutation checks ----------
# M1: revert _finite_oi_rows -> artifact returns AND the real
# regression test fails.
orig_filter = GexDashboard._finite_oi_rows
GexDashboard._finite_oi_rows = lambda d: d
try:
    art_df = oi_df(expiries["2026-10-23"], zero_as_nan=True)
    mutant = compute_max_pain(art_df)
    check("M1 revert -> artifact lowest-strike 225 returns",
          mutant == 225.0, f"mutant={mutant}")

    tspec = importlib.util.spec_from_file_location(
        "tg", ROOT / "tests" / "test_gex_metrics.py")
    tg = importlib.util.module_from_spec(tspec)
    tspec.loader.exec_module(tg)
    try:
        tg.test_max_pain_nan_open_interest_regression()
        check("M1 regression test catches revert", False,
              "test PASSED under mutant — vacuous")
    except AssertionError as e:
        msg = str(e).splitlines()
        check("M1 regression test catches revert", True,
              (msg[0] if msg else "AssertionError")[:90])
finally:
    GexDashboard._finite_oi_rows = orig_filter

# sanity: real test passes unmutated
tspec = importlib.util.spec_from_file_location(
    "tg2", ROOT / "tests" / "test_gex_metrics.py")
tg2 = importlib.util.module_from_spec(tspec)
tspec.loader.exec_module(tg2)
try:
    tg2.test_max_pain_nan_open_interest_regression()
    check("regression test passes unmutated", True)
except AssertionError as e:
    check("regression test passes unmutated", False, str(e)[:90])

# M2: simulate partial revert — filter ONLY inside _payout_pain,
# candidates computed from unfiltered df. A NaN-strike row then
# becomes a candidate and NaN pain hijacks argmin.
def partial_revert(df):
    strikes = sorted(df["strike"].unique())  # UNFILTERED
    pains = GexDashboard._payout_pain(df, strikes)  # filtered
    return strikes[int(np.argmin(pains))]

df_ns = pd.DataFrame({
    "strike": [np.nan, 100.0, 200.0, np.nan, 100.0, 200.0],
    "type": ["call", "call", "call", "put", "put", "put"],
    "openInterest": [5.0, 10.0, 10.0, 5.0, 10.0, 10.0],
})
m2 = partial_revert(df_ns)
info("M2 partial-revert (candidates unfiltered)",
     f"returns {m2} — outer filter is load-bearing")
check("M2 partial-revert produces artifact (NaN candidate)",
      not isinstance(m2, float) or math.isnan(float(m2)),
      f"mutant={m2}")

# M3: swap selected_* flags -> real between-strikes test fails
orig_cmp = GexDashboard.compute_max_pain_constrained

def swapped(d, spot=None):
    r = dict(orig_cmp(d, spot))
    (r["selected_lowest_candidate"],
     r["selected_highest_candidate"]) = (
        r["selected_highest_candidate"],
        r["selected_lowest_candidate"])
    return r

try:
    tg2.compute_max_pain_constrained = swapped
    tg2.test_max_pain_constrained_between_strikes_edge()
    check("M3 flag-swap caught by between-strikes test", False,
          "test PASSED under mutant — vacuous")
except AssertionError as e:
    msg = str(e).splitlines()
    check("M3 flag-swap caught by between-strikes test", True,
          (msg[0] if msg else "AssertionError")[:90])

# M4: simulate removing the 'not band_fell_back' guard —
# selected_lowest would misfire on fell_back argmin.
m4_df = pd.DataFrame({
    "strike": [100.0, 150.0, 200.0, 100.0, 150.0, 200.0],
    "type": ["call", "call", "call", "put", "put", "put"],
    "openInterest": [10, 90000, 10, 10, 10, 10],
})
r = compute_max_pain_constrained(m4_df, spot=10.0)
unguarded = r["max_pain"] == 150.0  # lowest overall strike
info("M4 fell_back guard",
     f"real flags lowest={r['selected_lowest_candidate']} "
     f"unguarded-mutant would be {unguarded}")
check("M4 guard prevents fell_back flag misfire",
      r["band_fell_back"] is True
      and r["selected_lowest_candidate"] is False
      and unguarded, str(r))

# ---------- F. packet end-to-end ----------
exp = "2026-10-16"
exp_dt = pd.Timestamp(exp, tz="UTC")
dfp = pd.DataFrame({
    "strike": [200.0, 420.0, 430.0, 999.0,
               200.0, 420.0, 430.0, 999.0],
    "type": ["call"] * 4 + ["put"] * 4,
    "openInterest": [100000, 100, 50, np.nan,
                     10, 10, 10, np.nan],
    "gex": [1e5, 2e5, 1e5, np.nan,
            -1e5, -2e5, -1e5, np.nan],
    "impliedVolatility": [0.30] * 8,
    "expiry": [exp_dt] * 8,
})
pkt = build_ai_packet("TEST", dfp, 422.05, [exp])
e = pkt["market_snapshot"]["expiries"][0]

check("packet emits selected_* keys as bool",
      isinstance(e["max_pain_selected_lowest_candidate"], bool)
      and isinstance(
          e["max_pain_selected_highest_candidate"], bool),
      f"low={e['max_pain_selected_lowest_candidate']} "
      f"high={e['max_pain_selected_highest_candidate']}")

s999 = [s for s in e["strikes"] if s["strike"] == 999.0][0]
check("NaN-OI strike exported collapsed to 0 (conflation real)",
      s999["open_interest"] == 0
      and s999["call_oi"] == 0 and s999["put_oi"] == 0,
      str({k: s999[k] for k in
           ("open_interest", "call_oi", "put_oi")}))

check("NaN-OI strike excluded from payout argmin",
      e["max_pain"] == 200.0,
      f"max_pain={e['max_pain']}")

dq = pkt["meta"]["data_quality"].lower()
check("data_quality documents OI conflation + exclusion",
      "conflate" in dq and "missing" in dq
      and "open_interest" in dq and "excluded" in dq)
md = pkt["meta"]["metric_definitions"]["max_pain_constrained"]
check("metric_definitions documents selected_* + exclusion",
      "selected" in md.lower() and "eligible" in md.lower()
      and "nonfinite" in md.lower())

print()
if failures:
    print(f"{len(failures)} FAILURES: {failures}")
    sys.exit(1)
print("ALL PASS")

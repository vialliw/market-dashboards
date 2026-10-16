"""F4-T3 critic-gate probe — independent verification.

Run from repo root:  python devin_blackboard/probe_f4_t3.py

Verifies on a synthetic mixed book (one healthy expiry +
one all-NaN-OI expiry + one absent expiry) that:

  (A) build_ai_packet emits REAL metrics for the healthy
      expiry and explicit null metrics + metrics_unavailable
      :true + a per-expiry data_quality reason for the dead
      one; the dead expiry's strikes are still exported and
      no artifact strike (e.g. the lowest listed strike)
      appears as max_pain.
  (B) raw compute_max_pain / compute_max_pain_constrained
      still raise ValueError loudly on an all-NaN-OI slice.
  (C) a NON-ValueError failure propagates through the packet
      path (missing required column -> KeyError out of
      build_ai_packet; missing column -> KeyError out of
      _per_expiry_metrics; an injected RuntimeError inside
      the metric bundle is not absorbed).

Synthetic values here are chosen independently of the
committed test (strikes 90/100/110 healthy, 95/105/115 dead,
spot=100) and the expected max_pain is recomputed with a
plain-Python payout loop, not via the library.
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import GexDashboard as gd  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"{'PASS' if cond else 'FAIL'} {name}"
          + (f" | {detail}" if detail else ""))


def independent_max_pain(df):
    """Plain-Python finite-only payout argmin."""
    rows = [
        (float(r["strike"]), r["type"], float(r["openInterest"]))
        for _, r in df.iterrows()
        if np.isfinite(r["strike"])
        and np.isfinite(r["openInterest"])
    ]
    strikes = sorted({s for s, _, _ in rows})
    best_s, best_p = None, None
    for s in strikes:
        pain = sum(
            max(0.0, s - k) * oi if t == "call"
            else max(0.0, k - s) * oi
            for k, t, oi in rows
        )
        if best_p is None or pain < best_p:
            best_p, best_s = pain, s
    return best_s, best_p


E_OK = "2026-11-20"
E_DEAD = "2026-10-29"   # mirrors the live SPY incident expiry
E_ABSENT = "2027-01-15"
SPOT = 100.0

d_ok = pd.Timestamp(E_OK, tz="UTC")
d_dead = pd.Timestamp(E_DEAD, tz="UTC")

rows = []

# --- healthy expiry: strikes 90/100/110 ---
# net gex per strike: -3e5 @90, -1e5 @100, +4e5 @110
# -> sole material neg->pos crossing anchored at 110.
for st, t, oi, g in [
    (90.0,  "call", 400, -2e5),
    (90.0,  "put",   60, -1e5),
    (100.0, "call", 250, -5e4),
    (100.0, "put",  450, -5e4),
    (110.0, "call", 100,  2e5),
    (110.0, "put",  300,  2e5),
]:
    rows.append({
        "strike": st, "type": t,
        "openInterest": oi, "gex": g,
        "impliedVolatility": 0.25,
        "expiry": d_ok,
    })

# --- dead expiry: strikes 95/105/115, every OI NaN ---
for st, t in [
    (95.0, "call"), (95.0, "put"),
    (105.0, "call"), (105.0, "put"),
    (115.0, "call"), (115.0, "put"),
]:
    rows.append({
        "strike": st, "type": t,
        "openInterest": np.nan, "gex": np.nan,
        "impliedVolatility": 0.30,
        "expiry": d_dead,
    })

df = pd.DataFrame(rows)
ok_sub = df[df["expiry"] == d_ok]
dead_sub = df[df["expiry"] == d_dead]

indep_mp, indep_pain = independent_max_pain(ok_sub)
print(f"independent argmin healthy expiry: "
      f"{indep_mp} (payout {indep_pain})")

# ============ (A) packet behavior ============
packet = gd.build_ai_packet(
    "CRITIC", df, SPOT, [E_OK, E_DEAD, E_ABSENT])
exs = {e["expiry"]: e
       for e in packet["market_snapshot"]["expiries"]}

ok = exs[E_OK]
dead = exs[E_DEAD]
absent = exs[E_ABSENT]

check("A1 healthy metrics_unavailable is False",
      ok["metrics_unavailable"] is False)
check("A2 healthy has no data_quality key",
      "data_quality" not in ok)
check("A3 healthy max_pain == independent argmin",
      ok["max_pain"] == indep_mp,
      f"packet={ok['max_pain']} indep={indep_mp}")
check("A4 healthy max_pain is interior, not lowest strike",
      ok["max_pain"] == 100.0)
check("A5 healthy constrained == 100, band [70,130]",
      ok["max_pain_constrained"] == 100.0
      and ok["max_pain_candidate_band"] == [70.0, 130.0])
check("A6 healthy gamma_flip == 110 (material crossing)",
      ok["gamma_flip"] == 110.0)
check("A7 healthy atm_iv == 0.25", ok["atm_iv"] == 0.25)

NULL_FIELDS = [
    "max_pain", "max_pain_constrained",
    "max_pain_candidate_band", "max_pain_band_fell_back",
    "max_pain_band_boundary_hit",
    "max_pain_selected_lowest_candidate",
    "max_pain_selected_highest_candidate",
    "gamma_flip",
]
print("\n--- dead expiry emitted fields ---")
print(json.dumps(
    {k: dead[k] for k in
     ["expiry", "metrics_unavailable", "data_quality",
      "atm_iv", "net_gex", "gamma_regime"] + NULL_FIELDS},
    indent=2, default=str))
print(f"strikes exported: {len(dead['strikes'])} -> "
      + json.dumps(dead["strikes"], default=str))

check("B1 dead metrics_unavailable is True",
      dead["metrics_unavailable"] is True)
check("B2 dead data_quality == 'no_finite_oi'",
      dead.get("data_quality") == "no_finite_oi")
check("B3 all 8 metric fields are explicit null",
      all(dead[f] is None for f in NULL_FIELDS),
      f"{[f for f in NULL_FIELDS if dead[f] is not None]}")
check("B4 dead strikes still exported (3 rows, OI->0)",
      len(dead["strikes"]) == 3
      and all(s["open_interest"] == 0
              for s in dead["strikes"]))
check("B5 no artifact strike as max_pain "
      "(None, not lowest 95/105/115)",
      dead["max_pain"] is None
      and dead["max_pain"] not in {95.0, 105.0, 115.0})
check("B6 dead atm_iv still emitted (0.30)",
      dead["atm_iv"] == 0.30)

check("C1 absent expiry -> nulls + flag + strikes []",
      absent["metrics_unavailable"] is True
      and absent["data_quality"] == "no_finite_oi"
      and absent["max_pain"] is None
      and absent["strikes"] == []
      and absent["atm_iv"] is None)

snap = packet["market_snapshot"]
check("C2 whole-book fields unaffected "
      "(spot/total_net_gex/nearest_gamma_flip)",
      snap["spot"] == 100.0
      and snap["total_net_gex"] == 0.0
      and snap["nearest_gamma_flip"] == 110.0,
      f"spot={snap['spot']} gex={snap['total_net_gex']} "
      f"flip={snap['nearest_gamma_flip']}")

check("C3 metric_definitions documents the flag",
      "metrics_unavailable" in
      packet["meta"]["metric_definitions"])

# ============ (B) raw fns still loud ============
for fn, kw in [
    (gd.compute_max_pain, {}),
    (gd.compute_max_pain_constrained, {"spot": SPOT}),
]:
    try:
        fn(dead_sub, **kw)
        check(f"D1 {fn.__name__} raises ValueError", False,
              "no exception")
    except ValueError as e:
        check(f"D1 {fn.__name__} raises ValueError", True,
              str(e)[:80])
    except Exception as e:
        check(f"D1 {fn.__name__} raises ValueError", False,
              f"wrong type {type(e).__name__}")

# ============ (C) non-ValueError propagates ============
# natural missing-column KeyError through the packet path
df_no_oi = df.drop(columns=["openInterest"])
try:
    gd.build_ai_packet("X", df_no_oi, SPOT, [E_OK])
    check("E1 missing 'openInterest' -> KeyError out of "
          "build_ai_packet", False, "returned normally")
except KeyError as e:
    check("E1 missing 'openInterest' -> KeyError out of "
          "build_ai_packet", True, f"KeyError {e}")
except Exception as e:
    check("E1 missing 'openInterest' -> KeyError out of "
          "build_ai_packet", False,
          f"wrong type {type(e).__name__}: {e}")

# missing 'strike' hits _per_expiry_metrics directly —
# must propagate (not be absorbed into null metrics)
try:
    gd._per_expiry_metrics(
        ok_sub.drop(columns=["strike"]), SPOT)
    check("E2 missing 'strike' -> KeyError out of "
          "_per_expiry_metrics", False, "returned")
except KeyError as e:
    check("E2 missing 'strike' -> KeyError out of "
          "_per_expiry_metrics", True, f"KeyError {e}")
except ValueError:
    check("E2 missing 'strike' -> KeyError out of "
          "_per_expiry_metrics", False,
          "ValueError (would be swallowed -> None)")
except Exception as e:
    check("E2 missing 'strike' -> KeyError out of "
          "_per_expiry_metrics", False,
          f"other {type(e).__name__}: {e}")

# injected non-ValueError at the exact tolerance boundary
orig = gd.compute_max_pain
try:
    def boom(_df):
        raise RuntimeError("injected critic failure")
    gd.compute_max_pain = boom
    out = "no exception"
    try:
        gd._per_expiry_metrics(ok_sub, SPOT)
    except Exception as e:
        out = f"{type(e).__name__}: {e}"
    check("E3 injected RuntimeError propagates "
          "(not -> None)", out.startswith("RuntimeError"),
          out)
finally:
    gd.compute_max_pain = orig

# and a ValueError through the same injection IS absorbed —
# confirms the boundary still tolerates data failures
try:
    def ve(_df):
        raise ValueError("injected insufficient data")
    gd.compute_max_pain = ve
    check("E4 injected ValueError -> None (absorbed)",
          gd._per_expiry_metrics(ok_sub, SPOT) is None)
finally:
    gd.compute_max_pain = orig

fails = [n for n, c in results if not c]
print(f"\n{len(results) - len(fails)}/{len(results)} "
      f"checks passed")
if fails:
    print("FAILED:", fails)
    sys.exit(1)
print("ALL PASS")

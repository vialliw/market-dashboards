"""Agent D (critic) independent gate probe for F5.

Verification only — imports the REAL GexDashboard module and
re-derives expected values with plain-Python recomputation.

Sections:
  A. INTC live case in a packet input: a 1-strike expiry
     (strike 190, call_oi=2, put_oi=0, 2026-10-19) inside a
     two-expiry packet -> metrics_unavailable:true +
     data_quality:'insufficient_data' + null payout fields;
     the healthy expiry in the SAME packet computes normally
     (cross-checked against an independent payout argmin).
  B. Scoped absorption: injected unrelated ValueError ->
     PROPAGATES; injected numpy empty-argmin ValueError ->
     absorbed as insufficient_data; KeyError propagates.
  C. Partial availability: degenerate payout expiry with a
     computable gamma_flip emits the real flip value under
     metrics_unavailable:true.
  D. Extra scoping: gamma_flip-side failures propagate;
     constrained-fn injection behaves the same; all-NaN-OI
     slice -> 'no_finite_oi'; raw compute_max_pain still
     returns the vacuous 190 on direct misuse.
"""

import sys
import json

import numpy as np
import pandas as pd

sys.path.insert(0, ".")

import GexDashboard as G

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"PASS {name} {detail}")
    else:
        FAIL += 1
        print(f"FAIL {name} {detail}")


def indep_max_pain(df):
    """Plain-Python canonical payout argmin:
    pain(S) = sum_K callOI[K]*max(S-K,0) + putOI[K]*max(K-S,0)
    over distinct finite-OI strikes; first lowest on tie."""
    call_oi, put_oi = {}, {}
    for _, r in df.iterrows():
        k, oi = r["strike"], r["openInterest"]
        if not (
            np.isfinite(k)
            and np.isfinite(oi)
        ):
            continue
        if r["type"] == "call":
            call_oi[k] = call_oi.get(k, 0) + oi
        else:
            put_oi[k] = put_oi.get(k, 0) + oi
    candidates = sorted(set(call_oi) | set(put_oi))
    if not candidates:
        raise ValueError("no candidates")
    best, best_pain = None, None
    for S in candidates:
        pain = sum(
            oi * max(S - K, 0) for K, oi in call_oi.items()
        ) + sum(
            oi * max(K - S, 0) for K, oi in put_oi.items()
        )
        if best_pain is None or pain < best_pain:
            best, best_pain = S, pain
    return float(best), float(best_pain)


def indep_flip(df, spot):
    """Plain-Python material neg->pos crossing, nearest spot."""
    agg = {}
    for _, r in df.iterrows():
        k, g = r["strike"], r["gex"]
        if np.isfinite(k) and np.isfinite(g):
            agg[k] = agg.get(k, 0) + g
    strikes = sorted(agg)
    if not strikes:
        return None
    max_abs = max(abs(agg[k]) for k in strikes)
    floor = max(0.01 * max_abs, 1e-3)
    crossings = []
    prev = None
    for k in strikes:
        g = agg[k]
        if prev is not None and prev < 0 < g:
            if (
                abs(prev) >= floor
                and abs(g) >= floor
            ):
                crossings.append(k)
        if g != 0:
            prev = g
    if not crossings:
        return None
    return float(
        min(crossings, key=lambda k: abs(k - spot))
    )


MAGIC = (
    "attempt to get argmin of an empty sequence"
)

# ---------- packet input: healthy + INTC-degenerate -------
EXP_H, EXP_D = "2026-11-20", "2026-10-19"
hdt = pd.Timestamp(EXP_H, tz="UTC")
ddt = pd.Timestamp(EXP_D, tz="UTC")
SPOT = 100.0

healthy = pd.DataFrame({
    "strike": [90.0, 100.0, 110.0,
               90.0, 100.0, 110.0],
    "type": ["call"] * 3 + ["put"] * 3,
    "openInterest": [100, 500, 300,
                     400, 200, 50],
    "gex": [-1e5, -2e5, 1e5,
            -2e4, 1e4, 3e4],
    "impliedVolatility": [0.25] * 6,
    "expiry": [hdt] * 6,
})

# live INTC 2026-10-19 shape: ONE strike, call_oi=2, put_oi=0
intc = pd.DataFrame({
    "strike": [190.0, 190.0],
    "type": ["call", "put"],
    "openInterest": [2, 0],
    "gex": [5e4, -1e4],
    "impliedVolatility": [0.30, 0.30],
    "expiry": [ddt] * 2,
})

book = pd.concat([healthy, intc], ignore_index=True)

print("=== A. INTC live case in packet input ===")
packet = G.build_ai_packet(
    "INTC", book, SPOT, [EXP_D, EXP_H]
)
exps = {
    e["expiry"]: e
    for e in packet["market_snapshot"]["expiries"]
}
dead, good = exps[EXP_D], exps[EXP_H]

print("dead expiry emitted fields:")
print(json.dumps(
    {k: v for k, v in dead.items()
     if k != "strikes"},
    indent=1, default=str,
))
print("healthy expiry emitted fields:")
print(json.dumps(
    {k: v for k, v in good.items()
     if k != "strikes"},
    indent=1, default=str,
))

check(
    "A1 dead expiry metrics_unavailable:true",
    dead["metrics_unavailable"] is True,
)
check(
    "A2 dead expiry data_quality:insufficient_data",
    dead["data_quality"] == "insufficient_data",
)
null_fields = [
    "max_pain", "max_pain_constrained",
    "max_pain_candidate_band",
    "max_pain_band_fell_back",
    "max_pain_band_boundary_hit",
    "max_pain_selected_lowest_candidate",
    "max_pain_selected_highest_candidate",
]
check(
    "A3 all 7 payout fields explicit null",
    all(dead[f] is None for f in null_fields),
    f"-> {[dead[f] for f in null_fields]}",
)
check(
    "A4 dead gamma_flip honest null (1 strike,"
    " no crossing possible)",
    dead["gamma_flip"] is None,
)
check(
    "A5 dead strike row still exported",
    len(dead["strikes"]) == 1
    and dead["strikes"][0]["strike"] == 190.0
    and dead["strikes"][0]["call_oi"] == 2
    and dead["strikes"][0]["put_oi"] == 0,
)
check(
    "A6 vacuous 190 NEVER emitted as max_pain",
    dead["max_pain"] != 190.0,
)

imp, imp_pain = indep_max_pain(healthy)
check(
    "A7 healthy expiry metrics_unavailable:false",
    good["metrics_unavailable"] is False,
)
check(
    "A8 healthy has no data_quality key",
    "data_quality" not in good,
)
check(
    "A9 healthy max_pain == independent argmin",
    good["max_pain"] == imp,
    f"packet={good['max_pain']} indep={imp}"
    f" (payout={imp_pain})",
)
check(
    "A10 healthy constrained max_pain real",
    good["max_pain_constrained"] is not None
    and isinstance(
        good["max_pain_constrained"], float
    ),
    f"= {good['max_pain_constrained']} band="
    f"{good['max_pain_candidate_band']}",
)
check(
    "A11 healthy bool flags are bools",
    isinstance(good["max_pain_band_fell_back"], bool)
    and isinstance(
        good["max_pain_band_boundary_hit"], bool
    )
    and isinstance(
        good["max_pain_selected_lowest_candidate"],
        bool,
    )
    and isinstance(
        good["max_pain_selected_highest_candidate"],
        bool,
    ),
)
check(
    "A12 raw compute_max_pain still returns"
    " vacuous 190 on direct misuse",
    float(G.compute_max_pain(intc)) == 190.0,
)

print("=== B. Scoped absorption ===")
hsub = book[book["expiry"] == hdt].copy()
orig_mp = G.compute_max_pain
orig_mpc = G.compute_max_pain_constrained

# B1: unrelated ValueError must PROPAGATE
def boom_unrelated(frame, spot=None):
    raise ValueError("totally unrelated message")

G.compute_max_pain = boom_unrelated
prop = False
try:
    G.build_ai_packet("T", book, SPOT, [EXP_H])
except ValueError as e:
    prop = "totally unrelated" in str(e)
finally:
    G.compute_max_pain = orig_mp
check(
    "B1 ValueError('totally unrelated message')"
    " propagates out of build_ai_packet",
    prop,
)

# B2: numpy empty-argmin ValueError absorbed
def boom_magic(frame, spot=None):
    raise ValueError(MAGIC)

G.compute_max_pain = boom_magic
try:
    p2 = G.build_ai_packet("T", book, SPOT, [EXP_H])
    e2 = p2["market_snapshot"]["expiries"][0]
    absorbed = (
        e2["metrics_unavailable"] is True
        and e2["data_quality"] == "insufficient_data"
        and e2["max_pain"] is None
    )
    gf2 = e2["gamma_flip"]
except Exception as e:
    absorbed, gf2 = False, repr(e)
finally:
    G.compute_max_pain = orig_mp
check(
    "B2 empty-argmin ValueError absorbed ->"
    " metrics_unavailable + insufficient_data",
    absorbed,
)
check(
    "B3 gamma_flip still computed under absorbed"
    " payout failure",
    gf2 is not None,
    f"gamma_flip={gf2}",
)

# B4: KeyError propagates (non-ValueError)
def boom_key(frame, spot=None):
    raise KeyError("openInterest")

G.compute_max_pain = boom_key
prop = False
try:
    G.build_ai_packet("T", book, SPOT, [EXP_H])
except KeyError:
    prop = True
except Exception:
    prop = False
finally:
    G.compute_max_pain = orig_mp
check("B4 injected KeyError propagates", prop)

# B5: same behavior via constrained fn
G.compute_max_pain_constrained = boom_unrelated
prop = False
try:
    G._per_expiry_metrics(hsub, SPOT)
except ValueError as e:
    prop = "totally unrelated" in str(e)
finally:
    G.compute_max_pain_constrained = orig_mpc
check(
    "B5 unrelated ValueError in constrained fn"
    " propagates",
    prop,
)
G.compute_max_pain_constrained = boom_magic
try:
    m = G._per_expiry_metrics(hsub, SPOT)
    absorbed = m["unavailable"] == "insufficient_data"
except Exception:
    absorbed = False
finally:
    G.compute_max_pain_constrained = orig_mpc
check(
    "B6 empty-argmin ValueError in constrained fn"
    " absorbed -> insufficient_data",
    absorbed,
)

# B7: case-mangled magic message is NOT absorbed
def boom_mangled(frame, spot=None):
    raise ValueError(
        "ATTEMPT TO GET ARGMIN OF AN EMPTY SEQUENCE"
    )

G.compute_max_pain = boom_mangled
prop = False
try:
    G._per_expiry_metrics(hsub, SPOT)
except ValueError:
    prop = True
finally:
    G.compute_max_pain = orig_mp
check(
    "B7 case-mangled message propagates"
    " (not a blanket-ValueError catch)",
    prop,
)

print("=== C. Partial availability ===")
# payout-degenerate (1 distinct finite-OI strike) but the
# gex profile has a material neg->pos crossing at 110.
partial = pd.DataFrame({
    "strike": [100.0, 110.0],
    "type": ["call", "call"],
    "openInterest": [2, np.nan],
    "gex": [-1e5, 2e5],
    "impliedVolatility": [0.30, 0.30],
    "expiry": [hdt] * 2,
})
p3 = G.build_ai_packet("T", partial, SPOT, [EXP_H])
e3 = p3["market_snapshot"]["expiries"][0]
expected_flip = indep_flip(partial, SPOT)
check(
    "C1 partial expiry metrics_unavailable:true"
    " + insufficient_data",
    e3["metrics_unavailable"] is True
    and e3["data_quality"] == "insufficient_data",
)
check(
    "C2 payout fields null while gamma_flip real",
    all(e3[f] is None for f in null_fields)
    and e3["gamma_flip"] is not None,
)
check(
    "C3 emitted gamma_flip == independent"
    " crossing recompute",
    e3["gamma_flip"] == expected_flip,
    f"emitted={e3['gamma_flip']} indep="
    f"{expected_flip}",
)
flip_strike = [
    s for s in e3["strikes"] if s["is_gamma_flip"]
]
check(
    "C4 is_gamma_flip flag set on flip strike",
    len(flip_strike) == 1
    and flip_strike[0]["strike"]
    == expected_flip,
)

print("=== D. Extra scoping ===")
# D1: gamma_flip-side failure propagates (outside try)
orig_gf = G.compute_gamma_flip
def boom_gf(frame, spot=None):
    raise RuntimeError("gex boom")

G.compute_gamma_flip = boom_gf
prop = False
try:
    G.build_ai_packet("T", book, SPOT, [EXP_H])
except RuntimeError:
    prop = True
finally:
    G.compute_gamma_flip = orig_gf
check(
    "D1 gamma_flip-side RuntimeError propagates"
    " (outside the payout try)",
    prop,
)

# D2: all-NaN-OI slice -> 'no_finite_oi' reason
nan_df = pd.DataFrame({
    "strike": [90.0, 110.0],
    "type": ["call", "put"],
    "openInterest": [np.nan, np.nan],
    "gex": [np.nan, np.nan],
    "impliedVolatility": [0.3, 0.3],
    "expiry": [hdt] * 2,
})
p4 = G.build_ai_packet("T", nan_df, SPOT, [EXP_H])
e4 = p4["market_snapshot"]["expiries"][0]
check(
    "D2 all-NaN-OI expiry -> no_finite_oi"
    " (reason distinguishable from"
    " insufficient_data)",
    e4["metrics_unavailable"] is True
    and e4["data_quality"] == "no_finite_oi",
)

# D3: meta documents the flag/reasons
meta = packet["meta"]
blob = json.dumps(meta, default=str)
check(
    "D3 meta documents metrics_unavailable +"
    " insufficient_data + payout_eligibility",
    "metrics_unavailable" in blob
    and "insufficient_data" in blob
    and "payout_eligibility" in blob,
)

print(f"\n{PASS} PASS, {FAIL} FAIL")
sys.exit(1 if FAIL else 0)

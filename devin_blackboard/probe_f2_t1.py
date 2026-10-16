"""Offline AC-3 probe for F2-T1 on the TSM fixture.

Independently recomputes (plain Python payout sums) the
per-expiry UNCONSTRAINED max_pain, verifies the constrained
variant + band metadata are present and correct, and
confirms an alternating +/-1e-12 noise df returns None.

Exits 0 on all-pass, 1 on any failure. No network access.
"""
import json
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from GexDashboard import (  # noqa: E402
    MIN_MATERIAL_GEX,
    compute_gamma_flip,
    compute_max_pain,
    compute_max_pain_constrained,
)

packet = json.loads(
    (REPO / "tests/fixtures/TSM_ai_packet.json")
    .read_text(encoding="utf-8")
)
SPOT = packet["market_snapshot"]["spot"]

failures = []


def oi_df(expiry_obj):
    rows = []
    for s in expiry_obj["strikes"]:
        rows.append({
            "strike": s["strike"], "type": "call",
            "openInterest": s["call_oi"],
        })
        rows.append({
            "strike": s["strike"], "type": "put",
            "openInterest": s["put_oi"],
        })
    return pd.DataFrame(rows)


def payout(df, s):
    pain = 0.0
    for _, r in df.iterrows():
        if r["type"] == "call":
            pain += max(0.0, s - r["strike"]) * r["openInterest"]
        else:
            pain += max(0.0, r["strike"] - s) * r["openInterest"]
    return pain


def indep_argmin(df, candidates):
    return min(candidates, key=lambda s: payout(df, s))


print(f"MIN_MATERIAL_GEX = {MIN_MATERIAL_GEX}")
print(f"spot = {SPOT}")

for e in packet["market_snapshot"]["expiries"]:
    df = oi_df(e)
    chain = sorted(df["strike"].unique())
    indep_uncon = indep_argmin(df, chain)
    lib_uncon = compute_max_pain(df)
    res = compute_max_pain_constrained(df, spot=SPOT)
    band = [0.7 * SPOT, 1.3 * SPOT]
    banded = [s for s in chain if band[0] <= s <= band[1]]
    indep_con = indep_argmin(df, banded) if banded else indep_uncon
    ok = (
        lib_uncon == indep_uncon
        and res["max_pain"] == indep_con
        and res["candidate_band"] == band
        and res["band_fell_back"] == (not banded)
        and isinstance(res["band_boundary_hit"], bool)
    )
    print(
        f"{e['expiry']}: uncon={lib_uncon} (indep {indep_uncon}) "
        f"constrained={res['max_pain']} (indep {indep_con}) "
        f"band={res['candidate_band']} "
        f"fell_back={res['band_fell_back']} "
        f"boundary_hit={res['band_boundary_hit']} "
        f"-> {'PASS' if ok else 'FAIL'}"
    )
    if not ok:
        failures.append(e["expiry"])

# alternating +/-1e-12 noise -> None
noise = pd.DataFrame({
    "strike": [400.0, 410.0, 420.0, 430.0, 440.0, 450.0],
    "gex": [-1e-12, 1e-12, -1e-12, 1e-12, -1e-12, 1e-12],
})
flip = compute_gamma_flip(noise, spot=SPOT)
ok = flip is None
print(f"alternating-noise flip = {flip} -> "
      f"{'PASS' if ok else 'FAIL'}")
if not ok:
    failures.append("alternating-noise")

if failures:
    print(f"FAILURES: {failures}")
    sys.exit(1)

print("ALL PASS")
sys.exit(0)

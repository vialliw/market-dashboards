"""F6-T1 fixture regeneration (OFFLINE, no network).

The committed tests/fixtures/TSM_ai_packet.json predates the
F2-F5 packet fields (no metrics_unavailable, no
max_pain_constrained, no stale_iv provenance, no meta
data_quality/metric_definitions) and carries the original
artifact gamma_flip values (370/230/250/310). It must be
replaced by a packet emitted by the CURRENT pipeline —
but tests/test_gex_metrics.py (not editable in F6-T1)
reconstructs dfs from the fixture's exported strike fields
and asserts values tied to the ORIGINAL chain.

_re prefix_run/tsm_snapshot.pkl turned out to be a DIFFERENT
fetch than the chain that produced the original fixture
(~72/105 strikes differ in OI on 2026-10-16), so replaying
it changes the exported values and breaks 2 hardcoded flip
assertions. It is therefore emitted as a SECOND fixture pair
(TSM_raw_*) instead — real rows incl. real NaN-OI rows for
SNAP-01 NaN->0 reconciliation coverage.

Fixture pairs produced:

  1) TSM_ai_packet.json + TSM_snapshot.pkl
     A raw-style chain is RECONSTRUCTED from the previous
     packet's own exported strike rows (one call row + one
     put row per listed strike carrying call_oi/put_oi,
     call_gex/put_gex, and the exported mean IV) and replayed
     through the CURRENT build_ai_packet. Every exported
     number the old tests depend on (OI, net_gex splits,
     atm_iv, walls, max_pain, material flips) is preserved
     verbatim; only the new F2-F5 fields and the corrected
     (de-artifacted) flip/flag values are added.

  2) TSM_raw_ai_packet.json + TSM_raw_snapshot.pkl
     The preserved raw chain _prefix_run/tsm_snapshot.pkl
     replayed offline through compute_gex + build_ai_packet
     with the clock frozen at 2026-10-16T14:00:00Z (the
     documented generation instant for this fixture). The
     gex-annotated df is persisted as the snapshot so
     WellnessCheck --snapshot can reconcile OI / IV / gex
     per (expiry, strike, type) incl. real NaN-OI rows
     collapsing to exported 0.
"""

import json
import pickle
import sys
from datetime import datetime, UTC
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import GexDashboard  # noqa: E402

FIXTURES = REPO / "tests" / "fixtures"
OLD_PACKET_PATH = FIXTURES / "TSM_ai_packet.json"
RAW_SNAPSHOT = REPO / "_prefix_run" / "tsm_snapshot.pkl"

RAW_GEN_UTC = datetime(2026, 10, 16, 14, 0, 0, tzinfo=UTC)


def _raw_gen_now(tz=None):
    if tz is None:
        return RAW_GEN_UTC.replace(tzinfo=None)
    return RAW_GEN_UTC.astimezone(tz)


class _FrozenDatetime(datetime):
    """datetime whose now() is pinned to RAW_GEN_UTC so
    compute_gex produces a fixed, documented T."""

    @classmethod
    def now(cls, tz=None):
        return _raw_gen_now(tz)


def _chain_from_old_packet(old):
    """Reconstruct a raw-style option-row df from a packet's
    exported per-strike aggregates (inverse of the
    groupby-sum export)."""
    spot = old["market_snapshot"]["spot"]
    rows = []
    for e in old["market_snapshot"]["expiries"]:
        exp_ts = pd.Timestamp(e["expiry"], tz="UTC")
        for s in e["strikes"]:
            base = {
                "expiry": exp_ts,
                "strike": s["strike"],
                "impliedVolatility": s["implied_volatility"],
            }
            rows.append({
                **base,
                "type": "call",
                "openInterest": s["call_oi"],
                "gex": s["call_gex"],
            })
            rows.append({
                **base,
                "type": "put",
                "openInterest": s["put_oi"],
                "gex": s["put_gex"],
            })
    return pd.DataFrame(rows), spot


def _build_packet(ticker, df, spot, expiries):
    return GexDashboard.build_ai_packet(
        ticker, df, spot, expiries
    )


def _write_packet(packet, path):
    path.write_text(
        json.dumps(packet, indent=2),
        encoding="utf-8",
    )


def main():
    # ---------- Pair 1: reconstructed chain ----------
    old = json.loads(
        OLD_PACKET_PATH.read_text(encoding="utf-8")
    )
    df1, spot1 = _chain_from_old_packet(old)
    expiries = [
        e["expiry"]
        for e in old["market_snapshot"]["expiries"]
    ]
    packet1 = _build_packet("TSM", df1, spot1, expiries)
    _write_packet(packet1, OLD_PACKET_PATH)
    with open(FIXTURES / "TSM_snapshot.pkl", "wb") as f:
        pickle.dump(df1, f)

    # ---------- Pair 2: preserved raw chain ----------
    df2 = pickle.loads(RAW_SNAPSHOT.read_bytes())
    spot2 = float(
        old["market_snapshot"]["spot"]
    )  # same TSM session spot
    orig_datetime = GexDashboard.datetime
    GexDashboard.datetime = _FrozenDatetime
    try:
        df2 = GexDashboard.compute_gex(df2.copy(), spot2)
        packet2 = _build_packet(
            "TSM", df2, spot2, expiries
        )
    finally:
        GexDashboard.datetime = orig_datetime
    _write_packet(
        packet2, FIXTURES / "TSM_raw_ai_packet.json"
    )
    with open(
        FIXTURES / "TSM_raw_snapshot.pkl", "wb"
    ) as f:
        pickle.dump(df2, f)

    for tag, pkt in (
        ("reconstructed", packet1), ("raw", packet2)
    ):
        ms = pkt["market_snapshot"]
        print(f"--- {tag} ---")
        print("generated_utc:", pkt["meta"]["generated_utc"])
        print("total_net_gex:", ms["total_net_gex"])
        print("nearest_gamma_flip:", ms["nearest_gamma_flip"])
        for e in ms["expiries"]:
            n_stale = sum(
                1
                for s in e["strikes"]
                if s.get("stale_iv")
            )
            print(
                e["expiry"],
                "strikes", len(e["strikes"]),
                "max_pain", e["max_pain"],
                "constr", e["max_pain_constrained"],
                "flip", e["gamma_flip"],
                "unavail", e["metrics_unavailable"],
                "stale", n_stale,
            )


if __name__ == "__main__":
    main()

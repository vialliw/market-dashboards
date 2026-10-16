"""Offline tests for WellnessCheck.py — the
post-generation ai_packet validator.

Everything runs OFFLINE:

- tests/fixtures/TSM_ai_packet.json + TSM_snapshot.pkl —
  packet regenerated from a chain reconstructed out of the
  original fixture's exported strikes (values preserved)
  plus its reconciling raw snapshot.
- tests/fixtures/TSM_raw_ai_packet.json +
  TSM_raw_snapshot.pkl — packet + snapshot replayed
  offline from the preserved raw chain
  (_prefix_run/tsm_snapshot.pkl); carries real NaN-OI rows
  for the NaN->0 reconciliation path.
- Hand-built mini-books whose metrics are computed by
  hand in this file.

No network access anywhere; WellnessCheck never imports
GexDashboard.
"""

import json
import math
import pickle
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

import WellnessCheck

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PACKET = FIXTURES / "TSM_ai_packet.json"
SNAPSHOT = FIXTURES / "TSM_snapshot.pkl"
RAW_PACKET = FIXTURES / "TSM_raw_ai_packet.json"
RAW_SNAPSHOT = FIXTURES / "TSM_raw_snapshot.pkl"

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "WellnessCheck.py"

FRESH_UTC = (
    datetime.now(timezone.utc) - timedelta(hours=1)
).strftime("%Y-%m-%dT%H:%M:%SZ")
STALE_UTC = (
    datetime.now(timezone.utc) - timedelta(hours=200)
).strftime("%Y-%m-%dT%H:%M:%SZ")


# =========================
# Packet/snapshot factories
# =========================

def _strike(k, co=0, po=0, cg=0.0, pg=0.0, iv=0.3,
            **extra):
    s = {
        "strike": float(k),
        "open_interest": int(co + po),
        "call_oi": int(co),
        "put_oi": int(po),
        "net_gex": cg + pg,
        "call_gex": cg,
        "put_gex": pg,
        "implied_volatility": iv,
        "is_call_wall": False,
        "is_put_wall": False,
        "is_gamma_flip": False,
        "distance_from_spot_pct": 0.0,
    }
    s.update(extra)
    return s


def _mk_expiry(name, strikes, *, metrics=None,
               unavailable_reason=None, atm_iv=0.3):
    """metrics: dict with the 7 payout fields +
    gamma_flip (hand-computed)."""
    e = {
        "expiry": name,
        "net_gex": sum(s["net_gex"] for s in strikes),
        "gamma_regime": "-Gamma",
        "metrics_unavailable": unavailable_reason
        is not None,
        "atm_iv": atm_iv,
        "call_walls": [],
        "put_walls": [],
        "strikes": strikes,
    }
    if unavailable_reason is None:
        m = metrics or {}
        e.update({
            "max_pain": m.get("max_pain"),
            "max_pain_constrained":
                m.get("max_pain_constrained"),
            "max_pain_candidate_band":
                m.get("candidate_band"),
            "max_pain_band_fell_back":
                m.get("band_fell_back", False),
            "max_pain_band_boundary_hit":
                m.get("band_boundary_hit", False),
            "max_pain_selected_lowest_candidate":
                m.get("selected_lowest_candidate", False),
            "max_pain_selected_highest_candidate":
                m.get("selected_highest_candidate", False),
            "gamma_flip": m.get("gamma_flip"),
        })
    else:
        e.update({
            "max_pain": None,
            "max_pain_constrained": None,
            "max_pain_candidate_band": None,
            "max_pain_band_fell_back": None,
            "max_pain_band_boundary_hit": None,
            "max_pain_selected_lowest_candidate": None,
            "max_pain_selected_highest_candidate": None,
            "gamma_flip": (metrics or {}).get("gamma_flip"),
            "data_quality": unavailable_reason,
        })
    return e


def _mk_packet(expiries, spot=100.0,
               generated_utc=FRESH_UTC,
               nearest_gamma_flip=None):
    return {
        "meta": {
            "ticker": "TEST",
            "generated_utc": generated_utc,
            "schema_version": "1.0",
            "source": "synthetic-test",
            "analysis_type": "gamma_exposure",
        },
        "system_prompt": "x",
        "analysis_query": "x",
        "instructions": {},
        "market_snapshot": {
            "spot": spot,
            "total_net_gex": sum(
                e["net_gex"] for e in expiries),
            "overall_gamma_regime": "-Gamma",
            "nearest_gamma_flip": nearest_gamma_flip,
            "expiries": expiries,
        },
        "expected_output": {},
    }


def _write(tmp_path, obj, name="packet.json"):
    p = tmp_path / name
    p.write_text(json.dumps(obj), encoding="utf-8")
    return p


def _run(packet_path, snapshot=None, report=None,
         max_age=72.0):
    return WellnessCheck.run(
        packet_path,
        snapshot_path=(
            str(snapshot) if snapshot else None),
        report_path=str(report) if report else None,
        max_age_hours=max_age,
    )


def _check(report, check_id):
    return next(
        c for c in report["checks"]
        if c["check_id"] == check_id)


def _raw_df(rows, with_gex=True):
    """rows: (expiry, strike, type, openInterest, gex, iv)"""
    recs = []
    for exp, k, t, oi, g, iv in rows:
        recs.append({
            "expiry": pd.Timestamp(exp, tz="UTC"),
            "strike": k,
            "type": t,
            "openInterest": oi,
            "gex": g,
            "impliedVolatility": iv,
        })
    df = pd.DataFrame(recs)
    if not with_gex:
        df = df.drop(columns=["gex"])
    return df


def _write_snapshot(tmp_path, df, name="snap.pkl"):
    p = tmp_path / name
    with open(p, "wb") as f:
        pickle.dump(df, f)
    return p


# =========================
# Hand-calculated mini-books
# =========================

def _three_strike_expiry(spot=100.0):
    """90/100/110 book; hand payout:
    pain(90)=250, pain(100)=200, pain(110)=250
    -> canonical argmin 100; band [70,130] contains all
    -> constrained 100, no flags."""
    strikes = [
        _strike(90, co=10, po=0, cg=-0.01),
        _strike(100, co=5, po=5, cg=0.0),
        _strike(110, co=0, po=10, cg=0.01),
    ]
    metrics = {
        "max_pain": 100.0,
        "max_pain_constrained": 100.0,
        "candidate_band": [0.7 * spot, 1.3 * spot],
        "band_fell_back": False,
        "band_boundary_hit": False,
        "selected_lowest_candidate": False,
        "selected_highest_candidate": False,
        "gamma_flip": 110.0,  # 100(-0.01) -> 110(+0.01)
    }
    return _mk_expiry(
        "2027-01-15", strikes, metrics=metrics)


def test_hand_max_pain_argmin(tmp_path):
    pkt = _mk_packet(
        [_three_strike_expiry()],
        nearest_gamma_flip=110.0)
    rep, code, _ = _run(_write(tmp_path, pkt))
    assert code == 0
    assert _check(rep, "MAXPAIN-01")["status"] == "PASS"
    assert _check(rep, "MAXPAIN-02")["status"] == "PASS"


def test_hand_max_pain_tampered(tmp_path):
    e = _three_strike_expiry()
    e["max_pain"] = 90.0  # wrong: true argmin is 100
    pkt = _mk_packet([e], nearest_gamma_flip=110.0)
    rep, code, _ = _run(_write(tmp_path, pkt))
    assert code == 1
    c = _check(rep, "MAXPAIN-01")
    assert c["status"] == "FAIL"
    assert c["findings"][0]["expected"].startswith(
        "independent argmin 100")


def test_constrained_literal_edge_hit(tmp_path):
    """strikes 70/130, spot 100 -> band [70,130];
    hand payout: pain(70)=60, pain(130)=6000 -> argmin
    lands exactly on band edge 70."""
    strikes = [
        _strike(70, co=100, po=0, cg=-0.01),
        _strike(130, co=0, po=1, cg=0.01),
    ]
    metrics = {
        "max_pain": 70.0,
        "max_pain_constrained": 70.0,
        "candidate_band": [70.0, 130.0],
        "band_fell_back": False,
        "band_boundary_hit": True,
        "selected_lowest_candidate": True,
        "selected_highest_candidate": False,
        "gamma_flip": 130.0,
    }
    pkt = _mk_packet(
        [_mk_expiry("2027-01-15", strikes,
                    metrics=metrics)],
        nearest_gamma_flip=130.0)
    rep, code, _ = _run(_write(tmp_path, pkt))
    assert code == 0, json.dumps(
        _check(rep, "MAXPAIN-02"), indent=1)
    assert _check(rep, "MAXPAIN-02")["status"] == "PASS"
    assert _check(rep, "MAXPAIN-01")["status"] == "PASS"


def test_constrained_between_strikes_selection(tmp_path):
    """strikes 80/120, band edges 70/130 fall BETWEEN the
    strikes; argmin on lowest eligible -> selected_lowest
    True, boundary_hit False. pain(80)=40, pain(120)=4000.
    """
    strikes = [
        _strike(80, co=100, po=0, cg=-0.01),
        _strike(120, co=0, po=1, cg=0.01),
    ]
    metrics = {
        "max_pain": 80.0,
        "max_pain_constrained": 80.0,
        "candidate_band": [70.0, 130.0],
        "band_fell_back": False,
        "band_boundary_hit": False,
        "selected_lowest_candidate": True,
        "selected_highest_candidate": False,
        "gamma_flip": 120.0,
    }
    pkt = _mk_packet(
        [_mk_expiry("2027-01-15", strikes,
                    metrics=metrics)],
        nearest_gamma_flip=120.0)
    rep, code, _ = _run(_write(tmp_path, pkt))
    assert code == 0
    assert _check(rep, "MAXPAIN-02")["status"] == "PASS"


def test_constrained_highest_candidate(tmp_path):
    """argmin on highest eligible strike. strikes
    80(c=1)/120(p=100): pain(80)=4000, pain(120)=40."""
    strikes = [
        _strike(80, co=1, po=0, cg=-0.01),
        _strike(120, co=0, po=100, cg=0.01),
    ]
    metrics = {
        "max_pain": 120.0,
        "max_pain_constrained": 120.0,
        "candidate_band": [70.0, 130.0],
        "band_fell_back": False,
        "band_boundary_hit": False,
        "selected_lowest_candidate": False,
        "selected_highest_candidate": True,
        "gamma_flip": 120.0,
    }
    pkt = _mk_packet(
        [_mk_expiry("2027-01-15", strikes,
                    metrics=metrics)],
        nearest_gamma_flip=120.0)
    rep, code, _ = _run(_write(tmp_path, pkt))
    assert code == 0
    assert _check(rep, "MAXPAIN-02")["status"] == "PASS"


def test_constrained_empty_band_fell_back(tmp_path):
    """spot=500 -> band [350,650] holds no strike ->
    fell_back True and candidates = all strikes."""
    strikes = [
        _strike(80, co=100, po=0, cg=-0.01),
        _strike(120, co=0, po=1, cg=0.01),
    ]
    metrics = {
        "max_pain": 80.0,
        "max_pain_constrained": 80.0,
        "candidate_band": [350.0, 650.0],
        "band_fell_back": True,
        "band_boundary_hit": False,
        "selected_lowest_candidate": False,
        "selected_highest_candidate": False,
        "gamma_flip": 120.0,
    }
    pkt = _mk_packet(
        [_mk_expiry("2027-01-15", strikes,
                    metrics=metrics)],
        spot=500.0, nearest_gamma_flip=120.0)
    rep, code, _ = _run(_write(tmp_path, pkt))
    assert code == 0
    assert _check(rep, "MAXPAIN-02")["status"] == "PASS"


def test_constrained_out_of_band_oi_moves_minimizer(tmp_path):
    """Out-of-band OI moves the constrained minimizer:
    spot=110 -> band [77,143] excludes strike 40; the
    canonical argmin lands on 40 (outside the band) while
    the constrained minimum over candidates {80,100,120}
    is 80.

    Hand payout (calls: 40:2000, 80:10; puts: 100:10,
    120:1):
      pain(40)  = (100-40)*10 + (120-40)*1           = 680
      pain(80)  = (80-40)*2000 + (100-80)*10
                  + (120-80)*1                        = 80240
      pain(100) = (100-40)*2000 + (100-80)*10
                  + (120-100)*1                       = 120220
      pain(120) = (120-40)*2000 + (120-80)*10         = 160400
    canonical argmin = 40; constrained argmin = 80 =
    lowest eligible candidate (no literal edge hit)."""
    strikes = [
        _strike(40, co=2000, po=0, cg=0.0),
        _strike(80, co=10, po=0, cg=-0.01),
        _strike(100, co=0, po=10, cg=0.0),
        _strike(120, co=0, po=1, cg=0.01),
    ]
    metrics = {
        "max_pain": 40.0,
        "max_pain_constrained": 80.0,
        "candidate_band": [77.0, 143.0],
        "band_fell_back": False,
        "band_boundary_hit": False,
        "selected_lowest_candidate": True,
        "selected_highest_candidate": False,
        "gamma_flip": 120.0,
    }
    pkt = _mk_packet(
        [_mk_expiry("2027-01-15", strikes,
                    metrics=metrics)],
        spot=110.0, nearest_gamma_flip=120.0)
    rep, code, _ = _run(_write(tmp_path, pkt))
    assert code == 0, json.dumps(rep["checks"], indent=1)
    assert _check(rep, "MAXPAIN-01")["status"] == "PASS"
    assert _check(rep, "MAXPAIN-02")["status"] == "PASS"


def test_hand_flip_upper_strike_no_interpolation(tmp_path):
    """Crossing between material -gex at 400 and +gex at
    420 -> flip is the upper strike 420, NOT an
    interpolated value."""
    strikes = [
        _strike(400, co=5, po=5, cg=-1.0),
        _strike(410, co=5, po=5, cg=-1.0),
        _strike(420, co=5, po=5, cg=1.0),
    ]
    e = _mk_expiry(
        "2027-01-15", strikes,
        metrics={
            "max_pain": 410.0,
            "max_pain_constrained": 410.0,
            "candidate_band": [70.0, 130.0],
            "band_fell_back": True,  # no strikes in band
            "gamma_flip": 420.0,
        })
    pkt = _mk_packet([e], spot=100.0,
                     nearest_gamma_flip=420.0)
    rep, code, _ = _run(_write(tmp_path, pkt))
    assert _check(rep, "FLIP-01")["status"] == "PASS"


def test_flip_floor_boundary(tmp_path):
    """Crossing at exactly the 1e-3 floor is material;
    just below it the whole slice is noise -> null."""
    strikes_at = [
        _strike(400, co=5, po=5, cg=-1e-3),
        _strike(420, co=5, po=5, cg=1e-3),
    ]
    # symmetric OI: pain(400)=pain(420)=100 -> argmin
    # tie -> first ascending = 400
    e = _mk_expiry(
        "2027-01-15", strikes_at,
        metrics={
            "max_pain": 400.0,
            "max_pain_constrained": 400.0,
            "candidate_band": [70.0, 130.0],
            "band_fell_back": True,
            "gamma_flip": 420.0,
        })
    pkt = _mk_packet([e], spot=100.0,
                     nearest_gamma_flip=420.0)
    rep, _, _ = _run(_write(tmp_path, pkt, "a.json"))
    assert _check(rep, "FLIP-01")["status"] == "PASS"

    strikes_below = [
        _strike(400, co=5, po=5, cg=-9e-4),
        _strike(420, co=5, po=5, cg=9e-4),
    ]
    e2 = _mk_expiry(
        "2027-01-15", strikes_below,
        metrics={
            "max_pain": 400.0,
            "max_pain_constrained": 400.0,
            "candidate_band": [70.0, 130.0],
            "band_fell_back": True,
            "gamma_flip": None,
        })
    pkt2 = _mk_packet([e2], spot=100.0,
                      nearest_gamma_flip=None)
    rep2, _, _ = _run(_write(tmp_path, pkt2, "b.json"))
    assert _check(rep2, "FLIP-01")["status"] == "PASS"


def test_flip_zero_gex_nonmaterial(tmp_path):
    """An exact-zero gex strike is never material and
    cannot break a crossing between its neighbours."""
    strikes = [
        _strike(400, co=5, po=5, cg=-1.0),
        _strike(410, co=5, po=5, cg=0.0),   # exact zero
        _strike(420, co=5, po=5, cg=1.0),
    ]
    e = _mk_expiry(
        "2027-01-15", strikes,
        metrics={
            "max_pain": 410.0,
            "max_pain_constrained": 410.0,
            "candidate_band": [70.0, 130.0],
            "band_fell_back": True,
            "gamma_flip": 420.0,
        })
    pkt = _mk_packet([e], spot=100.0,
                     nearest_gamma_flip=420.0)
    rep, code, _ = _run(_write(tmp_path, pkt))
    assert _check(rep, "FLIP-01")["status"] == "PASS"


def test_flip_multiple_crossings_nearest_spot(tmp_path):
    """material crossings at 400 and 440; spot=415 ->
    nearest is 400 (lower wins only on equidistant tie)."""
    strikes = [
        _strike(300, co=5, po=5, cg=-1.0),
        _strike(400, co=5, po=5, cg=1.0),
        _strike(430, co=5, po=5, cg=-1.0),
        _strike(440, co=5, po=5, cg=1.0),
    ]
    e = _mk_expiry(
        "2027-01-15", strikes,
        metrics={
            "max_pain": 400.0,
            "max_pain_constrained": 400.0,
            "candidate_band": [70.0, 130.0],
            "gamma_flip": 400.0,
        })
    pkt = _mk_packet([e], spot=415.0,
                     nearest_gamma_flip=400.0)
    rep, code, _ = _run(_write(tmp_path, pkt))
    assert _check(rep, "FLIP-01")["status"] == "PASS"


def test_flip_tampered(tmp_path):
    e = _three_strike_expiry()
    e["gamma_flip"] = 100.0  # wrong: true flip is 110
    pkt = _mk_packet([e], nearest_gamma_flip=110.0)
    rep, code, _ = _run(_write(tmp_path, pkt))
    assert code == 1
    assert _check(rep, "FLIP-01")["status"] == "FAIL"


# =========================
# Fixture packets
# =========================

def test_fixture_packet_pass(tmp_path):
    pkt = json.loads(PACKET.read_text(encoding="utf-8"))
    # pin freshness so the strict-PASS assertion is
    # independent of wall-clock
    pkt["meta"]["generated_utc"] = FRESH_UTC
    rep, code, _ = _run(_write(tmp_path, pkt))
    assert code == 0
    assert rep["overall_status"] == "PASS", json.dumps(
        [c for c in rep["checks"]
         if c["status"] in ("FAIL", "WARN")], indent=1)
    for cid in (
        "SCHEMA-01", "NUMERIC-01", "AVAIL-01a",
        "AVAIL-01c", "AGG-01", "MAXPAIN-01",
        "MAXPAIN-02", "FLIP-01", "IV-01",
    ):
        assert _check(rep, cid)["status"] == "PASS", cid
    assert _check(rep, "SNAP-01")["status"] == "unperformed"


def test_fixture_cli_exit_zero(tmp_path):
    rp = tmp_path / "report.json"
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(PACKET),
         "--report", str(rp)],
        capture_output=True, text=True, timeout=120,
        cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    rep = json.loads(rp.read_text(encoding="utf-8"))
    assert rep["overall_status"] in ("PASS", "WARN")
    assert rep["provenance"]["packet_hash"]
    assert rep["provenance"]["code_commit"]


def test_fixture_with_snapshot(tmp_path):
    rep, code, _ = _run(PACKET, snapshot=SNAPSHOT)
    assert code == 0
    c = _check(rep, "SNAP-01")
    assert c["status"] == "PASS", json.dumps(
        c, indent=1)[:2000]
    assert _check(rep, "AVAIL-01b")["status"] == "PASS"
    counts = rep["provenance"]["per_expiry_counts"]
    assert counts["2026-10-16"]["strikes"] == 105
    assert counts["2026-10-16"]["finite_oi_rows"] == 210
    assert counts["2026-10-16"]["stale_iv_rows"] == 23


def test_raw_pair_with_real_nan_rows(tmp_path):
    """The raw-chain replay pair: real NaN-OI rows
    collapse to exported 0 — SNAP-01 PASS with
    excluded_rows > 0."""
    pkt = json.loads(
        RAW_PACKET.read_text(encoding="utf-8"))
    pkt["meta"]["generated_utc"] = FRESH_UTC
    p = _write(tmp_path, pkt)
    rep, code, _ = _run(p, snapshot=RAW_SNAPSHOT)
    c = _check(rep, "SNAP-01")
    assert c["status"] == "PASS", json.dumps(
        c, indent=1)[:3000]
    counts = rep["provenance"]["per_expiry_counts"]
    assert any(
        v["excluded_rows"] > 0 for v in counts.values())


def test_snapshot_mismatch_fails(tmp_path):
    """The raw-chain snapshot does NOT reconcile with the
    reconstructed packet (different underlying chain)."""
    rep, code, _ = _run(PACKET, snapshot=RAW_SNAPSHOT)
    assert code == 1
    assert _check(rep, "SNAP-01")["status"] == "FAIL"


def test_snapshot_tampered_oi_fails(tmp_path):
    df = pickle.loads(SNAPSHOT.read_bytes())
    i = df.index[0]
    df.loc[i, "openInterest"] = (
        df.loc[i, "openInterest"] + 9999.0)
    snap = _write_snapshot(tmp_path, df)
    rep, code, _ = _run(PACKET, snapshot=snap)
    assert code == 1
    assert _check(rep, "SNAP-01")["status"] == "FAIL"


def test_snapshot_without_gex_partial(tmp_path):
    """A snapshot lacking the gex column still
    reconciles OI/IV; gex reconciliation is reported
    unperformed, not silently skipped."""
    df = pickle.loads(SNAPSHOT.read_bytes())
    snap = _write_snapshot(
        tmp_path, df.drop(columns=["gex"]))
    rep, code, _ = _run(PACKET, snapshot=snap)
    c = _check(rep, "SNAP-01")
    assert c["status"] == "PASS", json.dumps(
        c, indent=1)[:2000]
    assert "no gex column" in c["detail"]


def test_stale_generated_utc_warns(tmp_path):
    pkt = json.loads(PACKET.read_text(encoding="utf-8"))
    pkt["meta"]["generated_utc"] = STALE_UTC
    rep, code, _ = _run(_write(tmp_path, pkt))
    assert code == 0
    assert rep["overall_status"] == "WARN"
    assert _check(rep, "FRESH-01")["status"] == "WARN"


# =========================
# FAIL-per-check tampering
# =========================

def test_tamper_aggregation(tmp_path):
    pkt = json.loads(PACKET.read_text(encoding="utf-8"))
    e = pkt["market_snapshot"]["expiries"][0]
    e["net_gex"] = e["net_gex"] + 1e6
    rep, code, _ = _run(_write(tmp_path, pkt))
    assert code == 1
    assert _check(rep, "AGG-01")["status"] == "FAIL"


def test_tamper_iv_provenance(tmp_path):
    pkt = json.loads(PACKET.read_text(encoding="utf-8"))
    exp = pkt["market_snapshot"]["expiries"][0]
    stale = next(
        s for s in exp["strikes"] if s.get("stale_iv"))
    del stale["stale_iv"]  # unflag a stale strike
    rep, code, _ = _run(_write(tmp_path, pkt))
    assert code == 1
    assert _check(rep, "IV-01")["status"] == "FAIL"


def test_unflagged_high_iv_fails(tmp_path):
    pkt = _mk_packet([_three_strike_expiry()],
                     nearest_gamma_flip=110.0)
    pkt["market_snapshot"]["expiries"][0]["strikes"][0][
        "implied_volatility"] = 3.5  # >2.0 unflagged
    rep, code, _ = _run(_write(tmp_path, pkt))
    assert code == 1
    assert _check(rep, "IV-01")["status"] == "FAIL"


def test_flagged_stale_without_raw_fails(tmp_path):
    pkt = _mk_packet([_three_strike_expiry()],
                     nearest_gamma_flip=110.0)
    s = pkt["market_snapshot"]["expiries"][0][
        "strikes"][0]
    s["stale_iv"] = True
    s["implied_volatility"] = None
    # no raw_implied_volatility -> provenance broken
    rep, code, _ = _run(_write(tmp_path, pkt))
    assert code == 1
    assert _check(rep, "IV-01")["status"] == "FAIL"


def test_false_unavailable_on_eligible(tmp_path):
    """metrics_unavailable:true on a chain with >=2
    exported positive-OI strikes — provably eligible
    from the packet alone, no snapshot needed."""
    pkt = json.loads(PACKET.read_text(encoding="utf-8"))
    e = pkt["market_snapshot"]["expiries"][0]
    for f in WellnessCheck.PAYOUT_FIELDS:
        e[f] = None
    e["metrics_unavailable"] = True
    e["data_quality"] = "insufficient_data"
    rep, code, _ = _run(_write(tmp_path, pkt))
    assert code == 1
    b = _check(rep, "AVAIL-01b")
    assert b["status"] == "FAIL"
    assert _check(rep, "AVAIL-01a")["status"] == "PASS"


def test_unavailable_flag_consistency(tmp_path):
    pkt = _mk_packet([_three_strike_expiry()],
                     nearest_gamma_flip=110.0)
    e = pkt["market_snapshot"]["expiries"][0]
    e["metrics_unavailable"] = True
    e["data_quality"] = "insufficient_data"
    # leave payout fields non-null -> AVAIL-01a + 01c FAIL
    rep, code, _ = _run(_write(tmp_path, pkt))
    assert code == 1
    assert _check(rep, "AVAIL-01a")["status"] == "FAIL"
    assert _check(rep, "AVAIL-01c")["status"] == "FAIL"


def test_data_quality_on_available_expiry(tmp_path):
    pkt = _mk_packet([_three_strike_expiry()],
                     nearest_gamma_flip=110.0)
    e = pkt["market_snapshot"]["expiries"][0]
    e["data_quality"] = "insufficient_data"
    rep, code, _ = _run(_write(tmp_path, pkt))
    assert code == 1
    assert _check(rep, "AVAIL-01a")["status"] == "FAIL"


def test_partial_availability_flip_independent(tmp_path):
    """gamma_flip may be non-null on an unavailable
    expiry (independent metric) and null on an available
    one — both are legal, never a FAIL."""
    strikes = [
        _strike(100, co=1, po=0, cg=-0.01),
        _strike(110, co=0, po=0, cg=0.01),
    ]
    e = _mk_expiry(
        "2027-01-15", strikes,
        unavailable_reason="insufficient_data",
        metrics={"gamma_flip": 110.0})
    pkt = _mk_packet([e], nearest_gamma_flip=110.0)
    rep, code, _ = _run(_write(tmp_path, pkt))
    a = _check(rep, "AVAIL-01a")
    b = _check(rep, "AVAIL-01b")
    c = _check(rep, "AVAIL-01c")
    f = _check(rep, "FLIP-01")
    assert a["status"] == "PASS"
    # <2 exported positive-OI strikes -> ambiguous
    assert b["status"] == "unperformed", b["detail"]
    assert c["status"] == "PASS"
    assert f["status"] == "PASS"


def test_nan_token_fails(tmp_path):
    pkt = _mk_packet([_three_strike_expiry()],
                     nearest_gamma_flip=110.0)
    pkt["market_snapshot"]["expiries"][0][
        "strikes"][0]["implied_volatility"] = float("nan")
    p = _write(tmp_path, pkt)
    rep, code, _ = _run(p)
    assert code == 1
    assert _check(rep, "NUMERIC-01")["status"] == "FAIL"


def test_duplicate_strike_fails(tmp_path):
    strikes = [
        _strike(100, co=5, po=5, cg=-0.01),
        _strike(100, co=5, po=5, cg=0.01),
        _strike(110, co=5, po=5, cg=0.01),
    ]
    e = _mk_expiry("2027-01-15", strikes, metrics={
        "max_pain": 100.0,
        "max_pain_constrained": 100.0,
        "candidate_band": [70.0, 130.0],
        "gamma_flip": 110.0,
    })
    pkt = _mk_packet([e], nearest_gamma_flip=110.0)
    rep, code, _ = _run(_write(tmp_path, pkt))
    assert code == 1
    assert _check(rep, "SCHEMA-01")["status"] == "FAIL"


# =========================
# Eligibility ambiguity + snapshot resolution
# =========================

def _degenerate_packet(flag_unavailable):
    """Exported strikes are IDENTICAL whether the raw row
    at 110 was OI=0 or OI=NaN — the packet cannot
    distinguish them."""
    strikes = [
        _strike(100, co=1, po=0, cg=-0.01),
        _strike(110, co=0, po=0, cg=0.01),
    ]
    if flag_unavailable:
        e = _mk_expiry(
            "2027-01-15", strikes,
            unavailable_reason="insufficient_data",
            metrics={"gamma_flip": 110.0})
    else:
        e = _mk_expiry("2027-01-15", strikes, metrics={
            "max_pain": 100.0,
            "max_pain_constrained": 100.0,
            "candidate_band": [70.0, 130.0],
            # argmin 100 == candidates[0] of {100, 110}
            "selected_lowest_candidate": True,
            "gamma_flip": 110.0,
        })
    return _mk_packet([e], nearest_gamma_flip=110.0)


def test_degenerate_ambiguous_without_snapshot(tmp_path):
    """flag:false with <2 positive-OI strikes is
    ambiguous — could be eligible via finite-zero rows —
    so the verdict is unperformed, not assumed."""
    pkt = _degenerate_packet(flag_unavailable=False)
    rep, _, _ = _run(_write(tmp_path, pkt))
    b = _check(rep, "AVAIL-01b")
    assert b["status"] == "unperformed"
    assert "ambiguous" in b["detail"]


def test_degenerate_resolved_by_snapshot(tmp_path):
    """(100,OI=1)/(110,OI=0) -> eligible: flag:false
    verified PASS. Identical export with (110,NaN) ->
    ineligible: flag:false is a FAIL."""
    pkt = _degenerate_packet(flag_unavailable=False)

    df_zero = _raw_df([
        ("2027-01-15", 100.0, "call", 1.0, -0.01, 0.3),
        ("2027-01-15", 110.0, "call", 0.0, 0.01, 0.3),
    ])
    rep, _, _ = _run(
        _write(tmp_path, pkt, "z.json"),
        snapshot=_write_snapshot(tmp_path, df_zero, "z.pkl"))
    assert _check(rep, "AVAIL-01b")["status"] == "PASS"

    df_nan = _raw_df([
        ("2027-01-15", 100.0, "call", 1.0, -0.01, 0.3),
        ("2027-01-15", 110.0, "call", float("nan"), 0.01, 0.3),
    ])
    rep2, code2, _ = _run(
        _write(tmp_path, pkt, "n.json"),
        snapshot=_write_snapshot(tmp_path, df_nan, "n.pkl"))
    assert code2 == 1
    assert _check(rep2, "AVAIL-01b")["status"] == "FAIL"


def test_false_available_reason_mismatch(tmp_path):
    """Unavailable expiry claims 'no_finite_oi' but raw
    rows show a degenerate chain -> 'insufficient_data'
    -> reason mismatch FAIL."""
    strikes = [
        _strike(100, co=1, po=0, cg=-0.01),
        _strike(110, co=0, po=0, cg=0.01),
    ]
    e = _mk_expiry(
        "2027-01-15", strikes,
        unavailable_reason="no_finite_oi",
        metrics={"gamma_flip": 110.0})
    pkt = _mk_packet([e], nearest_gamma_flip=110.0)
    df_nan = _raw_df([
        ("2027-01-15", 100.0, "call", 1.0, -0.01, 0.3),
        ("2027-01-15", 110.0, "call", float("nan"), 0.01, 0.3),
    ])
    rep, code, _ = _run(
        _write(tmp_path, pkt),
        snapshot=_write_snapshot(tmp_path, df_nan))
    assert code == 1
    assert _check(rep, "AVAIL-01b")["status"] == "FAIL"


# =========================
# F6-T4 regressions: MAXPAIN-02 candidate-set
# ambiguity, unreadable snapshot, raw-only expiry
# =========================

def _band_nan_strikes():
    """Exported strikes identical whether raw strike 80
    was (0,0) or (NaN,NaN): band [70,130] holds ONLY the
    ambiguous strike 80; finite OI lives at 50 (call 5)
    / 200 (put 5) -> flat pain 750 on [50,200]."""
    return [
        _strike(50, co=5, po=0, cg=-0.01),
        _strike(80, co=0, po=0, cg=0.0),
        _strike(200, co=0, po=5, cg=0.01),
    ]


def _band_nan_packet(fell_back_world):
    """fell_back_world=True -> metrics as produced when
    raw 80 was all-NaN (band empty -> fell_back, mpc=50).
    False -> metrics as produced when raw 80 was a real
    0-OI candidate (mpc=80, both selected flags)."""
    if fell_back_world:
        m = {
            "max_pain": 50.0,
            "max_pain_constrained": 50.0,
            "candidate_band": [70.0, 130.0],
            "band_fell_back": True,
            "band_boundary_hit": False,
            "selected_lowest_candidate": False,
            "selected_highest_candidate": False,
        }
    else:
        m = {
            "max_pain": 50.0,
            "max_pain_constrained": 80.0,
            "candidate_band": [70.0, 130.0],
            "band_fell_back": False,
            "band_boundary_hit": False,
            "selected_lowest_candidate": True,
            "selected_highest_candidate": True,
        }
    m["gamma_flip"] = 200.0  # -0.01@50 -> +0.01@200
    e = _mk_expiry(
        "2027-01-15", _band_nan_strikes(), metrics=m)
    return _mk_packet([e], nearest_gamma_flip=200.0)


def _band_nan_snapshot(tmp_path, strike80_oi, name):
    """Raw rows matching _band_nan_strikes() exports
    (NaN->0 collapse permitted); strike80_oi may be
    float('nan')."""
    df = _raw_df([
        ("2027-01-15", 50.0, "call", 5.0, -0.01, 0.3),
        ("2027-01-15", 50.0, "put", 0.0, 0.0, 0.3),
        ("2027-01-15", 80.0, "call", strike80_oi, 0.0, 0.3),
        ("2027-01-15", 80.0, "put", strike80_oi, 0.0, 0.3),
        ("2027-01-15", 200.0, "call", 0.0, 0.01, 0.3),
        ("2027-01-15", 200.0, "put", 5.0, 0.0, 0.3),
    ])
    return _write_snapshot(tmp_path, df, name)


def test_maxpain02_ambiguous_band_no_snapshot(tmp_path):
    """F6-T4/F3 regression: an HONEST packet whose band
    eligibility hinges on an exported-0 strike that may
    have been missing-NaN must NOT be FAILed — the
    verdict is 'unperformed' without a snapshot."""
    rep, code, _ = _run(
        _write(tmp_path, _band_nan_packet(True)))
    c = _check(rep, "MAXPAIN-02")
    assert c["status"] == "unperformed", c["detail"]
    assert "missing-NaN" in c["detail"]
    assert not c["findings"]
    # the unconstrained argmin is unaffected — still PASS
    assert _check(rep, "MAXPAIN-01")["status"] == "PASS"
    assert code == 0
    # the all-true-zero interpretation of the identical
    # export is equally unprovable -> unperformed too
    rep2, code2, _ = _run(
        _write(tmp_path, _band_nan_packet(False), "z.json"))
    assert _check(
        rep2, "MAXPAIN-02")["status"] == "unperformed"
    assert code2 == 0


def test_maxpain02_ambiguity_resolved_by_snapshot(tmp_path):
    """With --snapshot the raw rows decide the candidate
    set: the honest NaN-world packet PASSes and the
    incompatible zero-world packet FAILs a true
    mismatch (and vice versa on a zero-OI raw row)."""
    pkt_nan = _band_nan_packet(True)
    pkt_zero = _band_nan_packet(False)

    snap_nan = _band_nan_snapshot(
        tmp_path, float("nan"), "nan.pkl")
    rep, code, _ = _run(
        _write(tmp_path, pkt_nan, "a.json"),
        snapshot=snap_nan)
    assert code == 0
    assert _check(rep, "MAXPAIN-02")["status"] == "PASS"

    rep2, code2, _ = _run(
        _write(tmp_path, pkt_zero, "b.json"),
        snapshot=snap_nan)
    assert code2 == 1
    assert _check(rep2, "MAXPAIN-02")["status"] == "FAIL"

    snap_zero = _band_nan_snapshot(tmp_path, 0.0, "z.pkl")
    rep3, code3, _ = _run(
        _write(tmp_path, pkt_zero, "c.json"),
        snapshot=snap_zero)
    assert code3 == 0
    assert _check(rep3, "MAXPAIN-02")["status"] == "PASS"

    rep4, code4, _ = _run(
        _write(tmp_path, pkt_nan, "d.json"),
        snapshot=snap_zero)
    assert code4 == 1
    assert _check(rep4, "MAXPAIN-02")["status"] == "FAIL"


def test_unreadable_snapshot_fails_snap01(tmp_path):
    """F6-T4/F1 regression: an unreadable/missing
    --snapshot file is a SNAP-01 FAIL (exit 1), never a
    CHECKER-ERR (exit 2)."""
    pkt = _write(tmp_path, _mk_packet(
        [_three_strike_expiry()], nearest_gamma_flip=110.0))
    rep, code, _ = _run(
        pkt, snapshot=tmp_path / "no_such.pkl")
    assert code == 1
    c = _check(rep, "SNAP-01")
    assert c["status"] == "FAIL"
    assert all(
        x["check_id"] != "CHECKER-ERR"
        for x in rep["checks"])
    # provenance records the hash failure honestly
    assert rep["provenance"]["snapshot_hash"] is None
    assert "snapshot_hash_error" in rep["provenance"]

    # a non-DataFrame pickle is likewise SNAP-01 FAIL exit 1
    bad = tmp_path / "notdf.pkl"
    with open(bad, "wb") as f:
        pickle.dump({"x": 1}, f)
    rep2, code2, _ = _run(pkt, snapshot=bad)
    assert code2 == 1
    assert _check(rep2, "SNAP-01")["status"] == "FAIL"


def test_snapshot_raw_only_expiry_fails(tmp_path):
    """F6-T4/F2 regression: a snapshot expiry absent from
    the packet is unreconciled raw data -> SNAP-01 FAIL."""
    e = _mk_expiry(
        "2027-01-15",
        [_strike(100, co=1, po=0, cg=-0.01),
         _strike(110, co=0, po=1, pg=0.01)],
        metrics={
            "max_pain": 100.0,
            "max_pain_constrained": 100.0,
            "candidate_band": [70.0, 130.0],
            "selected_lowest_candidate": True,
            "gamma_flip": 110.0,
        })
    pkt = _mk_packet([e], nearest_gamma_flip=110.0)
    df = _raw_df([
        ("2027-01-15", 100.0, "call", 1.0, -0.01, 0.3),
        ("2027-01-15", 110.0, "put", 1.0, 0.01, 0.3),
        ("2030-06-20", 100.0, "call", 9.0, -0.01, 0.3),
    ])
    rep, code, _ = _run(
        _write(tmp_path, pkt),
        snapshot=_write_snapshot(tmp_path, df))
    assert code == 1
    c = _check(rep, "SNAP-01")
    assert c["status"] == "FAIL"
    assert any(
        f["expiry"] == "2030-06-20" for f in c["findings"])
    # the packet's own expiry still reconciles cleanly
    assert _check(rep, "MAXPAIN-02")["status"] == "PASS"


# =========================
# F6-T5 regressions: MAXPAIN-01 exported-0/NaN
# candidate ambiguity (ChatGPT review concern C1)
# =========================

def _mp01_strikes():
    """ChatGPT C1 reproduction: calls at 90/100/110 with
    raw OI NaN/1/1 export IDENTICALLY to raw OI 0/1/1 —
    strike 90 is ambiguous (exported 0 conflates
    true-zero with missing-NaN)."""
    return [
        _strike(90, co=0, po=0, cg=0.0),
        _strike(100, co=1, po=0, cg=-0.01),
        _strike(110, co=1, po=0, cg=0.01),
    ]


def _mp01_packet(nan_world):
    """nan_world=True -> metrics as produced when raw 90
    was all-NaN (dropped): candidate set {100,110},
    canonical argmin 100. False -> raw 90 was a real
    0-OI candidate: flat zero payout on [90,100] ->
    ascending-first argmin 90 (2-way tie)."""
    m = {
        "max_pain": 100.0 if nan_world else 90.0,
        "max_pain_constrained": 100.0 if nan_world else 90.0,
        "candidate_band": [70.0, 130.0],
        "band_fell_back": False,
        "band_boundary_hit": False,
        # argmin is the lowest candidate in BOTH worlds
        "selected_lowest_candidate": True,
        "selected_highest_candidate": False,
        "gamma_flip": 110.0,  # -0.01@100 -> +0.01@110
    }
    e = _mk_expiry(
        "2027-01-15", _mp01_strikes(), metrics=m)
    return _mk_packet([e], nearest_gamma_flip=110.0)


def _mp01_snapshot(tmp_path, strike90_oi, name):
    """Raw rows matching _mp01_strikes() exports
    (NaN->0 collapse permitted); strike90_oi may be
    float('nan')."""
    df = _raw_df([
        ("2027-01-15", 90.0, "call", strike90_oi, 0.0, 0.3),
        ("2027-01-15", 90.0, "put", strike90_oi, 0.0, 0.3),
        ("2027-01-15", 100.0, "call", 1.0, -0.01, 0.3),
        ("2027-01-15", 100.0, "put", 0.0, 0.0, 0.3),
        ("2027-01-15", 110.0, "call", 1.0, 0.01, 0.3),
        ("2027-01-15", 110.0, "put", 0.0, 0.0, 0.3),
    ])
    return _write_snapshot(tmp_path, df, name)


def test_maxpain01_ambiguous_argmin_no_snapshot(tmp_path):
    """F6-T5/C1 regression: an HONEST packet whose
    canonical argmin hinges on an exported-0 strike that
    may have been missing-NaN must NOT be FAILed — the
    exported-only recompute yields 90 while production
    (finite-OI candidates {100,110}) emitted 100 — the
    verdict is 'unperformed' without a snapshot."""
    rep, code, _ = _run(
        _write(tmp_path, _mp01_packet(True)))
    c = _check(rep, "MAXPAIN-01")
    assert c["status"] == "unperformed", c["detail"]
    assert "missing-NaN" in c["detail"]
    assert not c["findings"]
    assert code == 0
    # the constrained check is ambiguous for the same
    # reason — never a FAIL either
    assert _check(
        rep, "MAXPAIN-02")["status"] == "unperformed"
    # the all-true-zero interpretation of the identical
    # export is equally unprovable -> unperformed too
    rep2, code2, _ = _run(
        _write(tmp_path, _mp01_packet(False), "z.json"))
    assert _check(
        rep2, "MAXPAIN-01")["status"] == "unperformed"
    assert code2 == 0


def test_maxpain01_ambiguity_resolved_by_snapshot(tmp_path):
    """With --snapshot the raw finite-OI rows decide the
    candidate set: the honest NaN-world packet PASSes
    under a NaN snapshot and FAILs a true mismatch under
    a zero-OI snapshot (and vice versa)."""
    pkt_nan = _mp01_packet(True)
    pkt_zero = _mp01_packet(False)

    snap_nan = _mp01_snapshot(
        tmp_path, float("nan"), "nan.pkl")
    rep, code, _ = _run(
        _write(tmp_path, pkt_nan, "a.json"),
        snapshot=snap_nan)
    assert code == 0
    assert _check(rep, "MAXPAIN-01")["status"] == "PASS"

    rep2, code2, _ = _run(
        _write(tmp_path, pkt_zero, "b.json"),
        snapshot=snap_nan)
    assert code2 == 1
    assert _check(rep2, "MAXPAIN-01")["status"] == "FAIL"

    snap_zero = _mp01_snapshot(tmp_path, 0.0, "z.pkl")
    rep3, code3, _ = _run(
        _write(tmp_path, pkt_zero, "c.json"),
        snapshot=snap_zero)
    assert code3 == 0
    assert _check(rep3, "MAXPAIN-01")["status"] == "PASS"

    rep4, code4, _ = _run(
        _write(tmp_path, pkt_nan, "d.json"),
        snapshot=snap_zero)
    assert code4 == 1
    assert _check(rep4, "MAXPAIN-01")["status"] == "FAIL"


def test_maxpain01_unambiguous_exported0_still_checked(tmp_path):
    """An exported-0 strike that CANNOT change the
    canonical verdict leaves the expiry evaluated: the
    zero-pain 140 candidate never ties the minimum, so
    the honest packet still PASSes and a tampered
    max_pain still FAILs — ambiguity handling only
    fires when the verdict truly depends on it."""
    strikes = [
        _strike(100, co=1, po=0, cg=-0.01),
        _strike(110, co=1, po=0, cg=0.01),
        _strike(140, co=0, po=0, cg=0.0),
    ]
    m = {
        "max_pain": 100.0,
        "max_pain_constrained": 100.0,
        "candidate_band": [70.0, 130.0],
        "band_fell_back": False,
        "band_boundary_hit": False,
        "selected_lowest_candidate": True,
        "selected_highest_candidate": False,
        "gamma_flip": 110.0,
    }
    pkt = _mk_packet([_mk_expiry(
        "2027-01-15", strikes, metrics=m)],
        nearest_gamma_flip=110.0)
    rep, _, _ = _run(_write(tmp_path, pkt))
    # 140 sits outside the band and adds only a zero-pain
    # term; argmin 100 identical under both hypotheses
    assert _check(rep, "MAXPAIN-01")["status"] == "PASS"
    assert _check(rep, "MAXPAIN-02")["status"] == "PASS"

    pkt2 = _mk_packet([_mk_expiry(
        "2027-01-15", strikes, metrics={
            **m, "max_pain": 95.0})],
        nearest_gamma_flip=110.0)
    rep2, code2, _ = _run(
        _write(tmp_path, pkt2, "t.json"))
    assert code2 == 1
    assert _check(rep2, "MAXPAIN-01")["status"] == "FAIL"


# =========================
# F6-T5/C2: regression tests killing the surviving
# F6-T2 mutants (see devin_blackboard/
# F6_MUTATION_REPORT.md — behavior-changing survivors)
# =========================

def test_agg_sub_milli_drift_fails(tmp_path):
    """Mutation-killer (agg_tol_1e-3): AGG tolerance is
    max(1e-9, 1e-9*mag) — a net_gex drift above it but
    far below the 1e-3-relative mutant bound still
    FAILs. total_net_gex is kept consistent so ONLY the
    strike->expiry comparison drifts."""
    pkt = _mk_packet([_three_strike_expiry()],
                     nearest_gamma_flip=110.0)
    e = pkt["market_snapshot"]["expiries"][0]
    # strike sums are 0 (|gex| max 0.01); drift 5e-6:
    # > real tol max(1e-9, 1e-9*0.01)=1e-9,
    # < mutant tol max(1e-9, 1e-3*0.01)=1e-5
    e["net_gex"] = e["net_gex"] + 5e-6
    pkt["market_snapshot"]["total_net_gex"] = (
        pkt["market_snapshot"]["total_net_gex"] + 5e-6)
    rep, code, _ = _run(_write(tmp_path, pkt))
    assert code == 1
    assert _check(rep, "AGG-01")["status"] == "FAIL"


def test_zero_total_oi_snapshot_insufficient(tmp_path):
    """Mutation-killer (raw_total_lt0): raw rows whose
    finite OI sums to exactly 0 make EVERY candidate an
    argmin -> 'insufficient_data', not 'eligible' —
    boundary is total <= 0, not < 0."""
    e = _mk_expiry(
        "2027-01-15",
        [_strike(100, co=0, po=0, cg=-0.01),
         _strike(110, co=0, po=0, cg=0.01)],
        unavailable_reason="insufficient_data",
        metrics={"gamma_flip": 110.0})
    pkt = _mk_packet([e], nearest_gamma_flip=110.0)
    df = _raw_df([
        ("2027-01-15", 100.0, "call", 0.0, -0.01, 0.3),
        ("2027-01-15", 100.0, "put", 0.0, 0.0, 0.3),
        ("2027-01-15", 110.0, "call", 0.0, 0.01, 0.3),
        ("2027-01-15", 110.0, "put", 0.0, 0.0, 0.3),
    ])
    rep, code, _ = _run(
        _write(tmp_path, pkt),
        snapshot=_write_snapshot(tmp_path, df))
    assert code == 0
    assert _check(rep, "AVAIL-01b")["status"] == "PASS"


def test_two_positive_strikes_prove_eligible(tmp_path):
    """Mutation-killer (pos_gt_2): exactly 2 exported
    positive-OI strikes prove eligibility — flagging the
    expiry metrics_unavailable is a FAIL (boundary is
    >= MIN_PAYOUT_STRIKES, not >)."""
    e = _mk_expiry(
        "2027-01-15",
        [_strike(100, co=1, po=0, cg=-0.01),
         _strike(110, co=0, po=1, pg=0.01)],
        unavailable_reason="insufficient_data",
        metrics={"gamma_flip": 110.0})
    pkt = _mk_packet([e], nearest_gamma_flip=110.0)
    rep, code, _ = _run(_write(tmp_path, pkt))
    assert code == 1
    b = _check(rep, "AVAIL-01b")
    assert b["status"] == "FAIL"


def test_stale_iv_requires_null_iv(tmp_path):
    """Mutation-killer (stale_iv_no_null_req): stale_iv
    with a NON-null implied_volatility is a provenance
    violation — the flag contracts iv:null +
    raw_implied_volatility>2.0."""
    pkt = _mk_packet([_three_strike_expiry()],
                     nearest_gamma_flip=110.0)
    s = pkt["market_snapshot"]["expiries"][0][
        "strikes"][0]
    s["stale_iv"] = True
    s["raw_implied_volatility"] = 3.5
    # implied_volatility deliberately left non-null
    rep, code, _ = _run(_write(tmp_path, pkt))
    assert code == 1
    assert _check(rep, "IV-01")["status"] == "FAIL"


def test_existing_report_not_overwritten(tmp_path):
    """Mutation-killer (report_w_mode): a requested
    report path that already exists is preserved —
    the report falls back to a '-N' suffixed name
    ('x'-safe, never clobbers)."""
    pkt = _mk_packet([_three_strike_expiry()],
                     nearest_gamma_flip=110.0)
    p = _write(tmp_path, pkt)
    rp = tmp_path / "report.json"
    rp.write_text('{"sentinel": true}', encoding="utf-8")
    rep, code, written = _run(p, report=rp)
    assert code == 0
    assert rp.read_text(encoding="utf-8") == \
        '{"sentinel": true}'
    assert written is not None and written != rp
    assert json.loads(
        written.read_text(encoding="utf-8"))[
            "overall_status"] in ("PASS", "WARN")


def test_unavailable_reason_whitelist(tmp_path):
    """Mutation-killer (accept_any_reason): a
    metrics_unavailable expiry must carry a documented
    data_quality reason — an arbitrary string FAILs."""
    e = _mk_expiry(
        "2027-01-15",
        [_strike(100, co=0, po=0, cg=-0.01),
         _strike(110, co=0, po=0, cg=0.01)],
        unavailable_reason="insufficient_data",
        metrics={"gamma_flip": 110.0})
    e["data_quality"] = "vendor_hiccup"  # not in the set
    pkt = _mk_packet([e], nearest_gamma_flip=110.0)
    rep, code, _ = _run(_write(tmp_path, pkt))
    assert code == 1
    assert _check(rep, "AVAIL-01a")["status"] == "FAIL"


def test_fresh_boundary_exactly_max_age(tmp_path,
                                      monkeypatch):
    """Mutation-killer (fresh_ge): a packet exactly
    max_age_hours old is still fresh — the WARN fires
    only STRICTLY above the bound."""
    fixed_now = datetime(
        2027, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    gen = (fixed_now - timedelta(hours=72)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")

    class _FixedDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr(
        WellnessCheck, "datetime", _FixedDT)
    pkt = _mk_packet([_three_strike_expiry()],
                     nearest_gamma_flip=110.0,
                     generated_utc=gen)
    rep, code, _ = _run(_write(tmp_path, pkt))
    f = _check(rep, "FRESH-01")
    assert f["status"] == "PASS", f["detail"]
    assert code == 0


def test_flag_absent_expiry_unperformed(tmp_path):
    """Mutation-killer (null_flag_means_avail): an expiry
    missing metrics_unavailable is 'cannot evaluate' —
    gated checks skip it; it is never treated as
    available."""
    e = _three_strike_expiry()
    del e["metrics_unavailable"]
    pkt = _mk_packet([e], nearest_gamma_flip=110.0)
    rep, code, _ = _run(_write(tmp_path, pkt))
    assert code == 1  # SCHEMA-01: required flag absent
    c = _check(rep, "MAXPAIN-01")
    assert c["status"] == "unperformed", c["detail"]


# =========================
# INPUT-01 / CHECKER-ERR / report handling
# =========================

def test_input_malformed_json(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    rp = tmp_path / "r.json"
    rep, code, written = _run(p, report=rp)
    assert code == 1
    assert rep["overall_status"] == "FAIL"
    assert rep["checks"][0]["check_id"] == "INPUT-01"
    assert written == rp and rp.exists()


def test_input_missing_file(tmp_path):
    rep, code, _ = _run(tmp_path / "nope.json")
    assert code == 1
    assert rep["checks"][0]["check_id"] == "INPUT-01"


def test_input_unsupported_schema(tmp_path):
    pkt = _mk_packet([_three_strike_expiry()],
                     nearest_gamma_flip=110.0)
    pkt["meta"]["schema_version"] = "9.9"
    rep, code, _ = _run(
        _write(tmp_path, pkt), report=tmp_path / "r.json")
    assert code == 1
    assert rep["checks"][0]["check_id"] == "INPUT-01"
    assert "9.9" in rep["checks"][0]["detail"]


def test_checker_err_exit_2(tmp_path, monkeypatch):
    def boom(_packet):
        raise RuntimeError("injected")
    monkeypatch.setattr(
        WellnessCheck, "check_schema", boom)
    pkt = _mk_packet([_three_strike_expiry()],
                     nearest_gamma_flip=110.0)
    rep, code, _ = _run(
        _write(tmp_path, pkt), report=tmp_path / "r.json")
    assert code == 2
    assert rep["checks"][0]["check_id"] == "CHECKER-ERR"
    assert "injected" in rep["checks"][0]["detail"]


def test_report_never_overwrites_packet(tmp_path):
    pkt = _mk_packet([_three_strike_expiry()],
                     nearest_gamma_flip=110.0)
    p = _write(tmp_path, pkt)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(p),
         "--report", str(p)],
        capture_output=True, text=True, timeout=120,
        cwd=tmp_path)
    assert proc.returncode == 2
    # packet untouched
    json.loads(p.read_text(encoding="utf-8"))


def test_default_report_path_created(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(PACKET)],
        capture_output=True, text=True, timeout=120,
        cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    reports = list(
        (tmp_path / "validation").glob(
            "TSM_ai_packet_report_*.json"))
    assert len(reports) == 1
    rep = json.loads(
        reports[0].read_text(encoding="utf-8"))
    assert rep["overall_status"] in ("PASS", "WARN")


def test_cover_warns_sparse_and_empty(tmp_path):
    e_sparse = _three_strike_expiry()  # 3 strikes < 10
    e_empty = _mk_expiry(
        "2027-02-19", [],
        unavailable_reason="no_finite_oi")
    pkt = _mk_packet(
        [e_sparse, e_empty], nearest_gamma_flip=110.0)
    rep, code, _ = _run(_write(tmp_path, pkt))
    assert code == 0
    assert rep["overall_status"] == "WARN"
    c = _check(rep, "COVER-01")
    assert c["status"] == "WARN"
    assert len(c["findings"]) == 2


def test_provenance_fields(tmp_path):
    pkt = json.loads(PACKET.read_text(encoding="utf-8"))
    p = _write(tmp_path, pkt)
    rep, code, _ = _run(p)
    prov = rep["provenance"]
    import hashlib
    assert prov["packet_hash"] == hashlib.sha256(
        p.read_bytes()).hexdigest()
    assert prov["code_commit"]  # repo is versioned
    assert prov["schema_version"] == "1.0"
    counts = prov["per_expiry_counts"]["2026-10-16"]
    assert counts["strikes"] == 105
    assert counts["finite_oi_rows"] is None  # no snapshot
    assert "counts_note" in prov

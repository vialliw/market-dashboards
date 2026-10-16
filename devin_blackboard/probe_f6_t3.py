"""F6-T3 critic probe — independent re-verification of the three
F6-T4 remediated defects + GexDashboard-independence + exit codes.

Built from scratch (no imports from tests/test_wellness.py or
probe_f6_t2.py): packets are hand-assembled dicts, snapshots are
hand-assembled DataFrames, expectations are hand-derived.

Defects under test (from F6-T2 findings / F6-T4 remediation):
  (a) MAXPAIN-02 must emit 'unperformed' — never FAIL — on an
      honest packet whose constrained verdict hinges on exported-0
      strikes that may have been raw-NaN (no --snapshot).
  (b) an unreadable/missing --snapshot file must be SNAP-01 FAIL +
      exit 1 (input error), never CHECKER-ERR exit 2.
  (c) a snapshot expiry absent from the packet is unreconciled raw
      data -> SNAP-01 FAIL.

Run: python devin_blackboard/probe_f6_t3.py
"""
import json
import pickle
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import WellnessCheck  # noqa: E402

PASS_COUNT = 0
FAILS = []


def check(name, cond, info=""):
    global PASS_COUNT
    if cond:
        PASS_COUNT += 1
        print(f"PASS {name} | {info}")
    else:
        FAILS.append(name)
        print(f"FAIL {name} | {info}")


def check_id(report, cid):
    return next(
        c for c in report["checks"] if c["check_id"] == cid)


# ---------- hand-built packet factory (schema 1.0) ----------

def mk_strike(k, co, po, cg, pg=0.0, iv=0.3):
    return {
        "strike": float(k),
        "open_interest": int(co + po),
        "call_oi": int(co),
        "put_oi": int(po),
        "net_gex": float(cg + pg),
        "call_gex": float(cg),
        "put_gex": float(pg),
        "implied_volatility": iv,
        "is_call_wall": False,
        "is_put_wall": False,
        "is_gamma_flip": False,
        "distance_from_spot_pct": 0.0,
    }


def mk_expiry(name, strikes, metrics):
    e = {
        "expiry": name,
        "net_gex": sum(s["net_gex"] for s in strikes),
        "gamma_regime": "-Gamma",
        "metrics_unavailable": False,
        "atm_iv": 0.3,
        "call_walls": [],
        "put_walls": [],
        "strikes": strikes,
    }
    e.update(metrics)
    return e


def mk_packet(expiries, spot, nearest_flip):
    return {
        "meta": {
            "ticker": "F6T3",
            "generated_utc": "2026-10-16T17:00:00Z",
            "schema_version": "1.0",
            "source": "critic-probe",
        },
        "market_snapshot": {
            "spot": spot,
            "total_net_gex": sum(
                e["net_gex"] for e in expiries),
            "overall_gamma_regime": "-Gamma",
            "nearest_gamma_flip": nearest_flip,
            "expiries": expiries,
        },
    }


def raw_df(rows):
    """rows: (expiry 'YYYY-MM-DD', strike, type, oi, gex, iv)"""
    return pd.DataFrame([{
        "expiry": pd.Timestamp(exp, tz="UTC"),
        "strike": k,
        "type": t,
        "openInterest": oi,
        "gex": g,
        "impliedVolatility": iv,
    } for exp, k, t, oi, g, iv in rows])


def write_json(d, p):
    p.write_text(json.dumps(d), encoding="utf-8")
    return p


def write_pickle(df, p):
    with open(p, "wb") as f:
        pickle.dump(df, f)
    return p


# =========================
# (a) MAXPAIN-02 ambiguous exported-0 band -> unperformed
# =========================
# spot=100 -> band [70,130]. Finite OI: call 5@50, put 5@200.
# pain flat 750 over [50,200]. Strike 80 exports OI=0 on both
# sides — ambiguous (true-zero candidate vs dropped NaN row).
#   World N (raw 80 all-NaN): band empty -> fell_back, mpc=50.
#   World Z (raw 80 OI=0):    band {80} -> mpc=80, sel lo+hi.
# The exported strikes are IDENTICAL in both worlds, so without
# --snapshot the checker must not FAIL either emission.

TMP = Path(tempfile.mkdtemp(prefix="f6t3_"))

strikes_amb = [
    mk_strike(50, 5, 0, -0.01),
    mk_strike(80, 0, 0, 0.0),   # ambiguous exported-0
    mk_strike(200, 0, 5, 0.0, 0.01),
]
metrics_nan_world = {
    "max_pain": 50.0,
    "max_pain_constrained": 50.0,
    "max_pain_candidate_band": [70.0, 130.0],
    "max_pain_band_fell_back": True,
    "max_pain_band_boundary_hit": False,
    "max_pain_selected_lowest_candidate": False,
    "max_pain_selected_highest_candidate": False,
    "gamma_flip": 200.0,
}
metrics_zero_world = {
    "max_pain": 50.0,
    "max_pain_constrained": 80.0,
    "max_pain_candidate_band": [70.0, 130.0],
    "max_pain_band_fell_back": False,
    "max_pain_band_boundary_hit": False,
    "max_pain_selected_lowest_candidate": True,
    "max_pain_selected_highest_candidate": True,
    "gamma_flip": 200.0,
}
pkt_nan = mk_packet(
    [mk_expiry("2027-01-15", strikes_amb, metrics_nan_world)],
    100.0, 200.0)
pkt_zero = mk_packet(
    [mk_expiry("2027-01-15", strikes_amb, metrics_zero_world)],
    100.0, 200.0)

rep, code, _ = WellnessCheck.run(
    write_json(pkt_nan, TMP / "amb_nan.json"))
c = check_id(rep, "MAXPAIN-02")
check("a1 nan-world honest packet MAXPAIN-02 unperformed",
      c["status"] == "unperformed" and not c["findings"],
      f"status={c['status']} detail={c['detail']!r}")
check("a2 nan-world exit 0 (no false FAIL)",
      code == 0 and rep["overall_status"] in ("PASS", "WARN"),
      f"code={code} overall={rep['overall_status']}")
check("a3 MAXPAIN-01 still PASS (canonical argmin immune)",
      check_id(rep, "MAXPAIN-01")["status"] == "PASS",
      check_id(rep, "MAXPAIN-01")["detail"])

rep2, code2, _ = WellnessCheck.run(
    write_json(pkt_zero, TMP / "amb_zero.json"))
c2 = check_id(rep2, "MAXPAIN-02")
check("a4 zero-world identical export also unperformed",
      c2["status"] == "unperformed" and code2 == 0,
      f"status={c2['status']} code={code2}")

# -- snapshot reconciliation resolves both worlds --
snap_nan = write_pickle(raw_df([
    ("2027-01-15", 50.0, "call", 5.0, -0.01, 0.3),
    ("2027-01-15", 50.0, "put", 0.0, 0.0, 0.3),
    ("2027-01-15", 80.0, "call", float("nan"), 0.0, 0.3),
    ("2027-01-15", 80.0, "put", float("nan"), 0.0, 0.3),
    ("2027-01-15", 200.0, "call", 0.0, 0.0, 0.3),
    ("2027-01-15", 200.0, "put", 5.0, 0.01, 0.3),
]), TMP / "snap_nan.pkl")
snap_zero = write_pickle(raw_df([
    ("2027-01-15", 50.0, "call", 5.0, -0.01, 0.3),
    ("2027-01-15", 50.0, "put", 0.0, 0.0, 0.3),
    ("2027-01-15", 80.0, "call", 0.0, 0.0, 0.3),
    ("2027-01-15", 80.0, "put", 0.0, 0.0, 0.3),
    ("2027-01-15", 200.0, "call", 0.0, 0.0, 0.3),
    ("2027-01-15", 200.0, "put", 5.0, 0.01, 0.3),
]), TMP / "snap_zero.pkl")

r_nn, c_nn, _ = WellnessCheck.run(
    write_json(pkt_nan, TMP / "nn.json"),
    snapshot_path=str(snap_nan))
check("a5 nan-packet + nan-snapshot -> MAXPAIN-02 PASS",
      check_id(r_nn, "MAXPAIN-02")["status"] == "PASS"
      and c_nn == 0,
      f"{check_id(r_nn, 'MAXPAIN-02')['detail']!r}")

r_zn, c_zn, _ = WellnessCheck.run(
    write_json(pkt_zero, TMP / "zn.json"),
    snapshot_path=str(snap_nan))
check("a6 zero-packet + nan-snapshot -> true mismatch FAIL",
      check_id(r_zn, "MAXPAIN-02")["status"] == "FAIL"
      and c_zn == 1,
      f"{check_id(r_zn, 'MAXPAIN-02')['detail']!r}")

r_zz, c_zz, _ = WellnessCheck.run(
    write_json(pkt_zero, TMP / "zz.json"),
    snapshot_path=str(snap_zero))
check("a7 zero-packet + zero-snapshot -> MAXPAIN-02 PASS",
      check_id(r_zz, "MAXPAIN-02")["status"] == "PASS"
      and c_zz == 0,
      f"{check_id(r_zz, 'MAXPAIN-02')['detail']!r}")

r_nz, c_nz, _ = WellnessCheck.run(
    write_json(pkt_nan, TMP / "nz.json"),
    snapshot_path=str(snap_zero))
check("a8 nan-packet + zero-snapshot -> true mismatch FAIL",
      check_id(r_nz, "MAXPAIN-02")["status"] == "FAIL"
      and c_nz == 1,
      f"{check_id(r_nz, 'MAXPAIN-02')['detail']!r}")

# =========================
# (b) missing --snapshot file -> exit 1 + SNAP-01 FAIL
# =========================
good_pkt = write_json(mk_packet(
    [mk_expiry("2027-01-15",
               [mk_strike(100, 1, 0, -0.01),
                mk_strike(110, 0, 1, 0.0, 0.01)],
               {
                   "max_pain": 100.0,
                   "max_pain_constrained": 100.0,
                   "max_pain_candidate_band": [70.0, 130.0],
                   "max_pain_band_fell_back": False,
                   "max_pain_band_boundary_hit": False,
                   "max_pain_selected_lowest_candidate": True,
                   "max_pain_selected_highest_candidate": False,
                   "gamma_flip": 110.0,
               })],
    100.0, 110.0), TMP / "good.json")

# in-process
rep_b, code_b, _ = WellnessCheck.run(
    good_pkt, snapshot_path=str(TMP / "no_such_file.pkl"))
c_b = check_id(rep_b, "SNAP-01")
check("b1 missing snapshot -> exit 1", code_b == 1,
      f"code={code_b}")
check("b2 missing snapshot -> SNAP-01 FAIL (not unperformed)",
      c_b["status"] == "FAIL", c_b["detail"])
check("b3 no CHECKER-ERR emitted",
      all(x["check_id"] != "CHECKER-ERR"
          for x in rep_b["checks"]),
      str([x["check_id"] for x in rep_b["checks"]]))
check("b4 provenance: snapshot_hash null + error field",
      rep_b["provenance"]["snapshot_hash"] is None
      and "snapshot_hash_error" in rep_b["provenance"],
      rep_b["provenance"].get("snapshot_hash_error", ""))

# CLI end-to-end
proc = subprocess.run(
    [sys.executable, str(REPO / "WellnessCheck.py"),
     str(good_pkt), "--snapshot", str(TMP / "absent.pkl"),
     "--report", str(TMP / "cli_rep.json")],
    capture_output=True, text=True, timeout=120, cwd=TMP)
check("b5 CLI missing snapshot -> exit 1",
      proc.returncode == 1,
      f"rc={proc.returncode} out={proc.stdout[-200:]!r}")
cli_rep = json.loads(
    (TMP / "cli_rep.json").read_text(encoding="utf-8"))
check("b6 CLI report: SNAP-01 FAIL, no CHECKER-ERR",
      check_id(cli_rep, "SNAP-01")["status"] == "FAIL"
      and all(x["check_id"] != "CHECKER-ERR"
              for x in cli_rep["checks"]),
      cli_rep["overall_status"])

# =========================
# (c) raw-only snapshot expiry -> SNAP-01 FAIL
# =========================
snap_extra = write_pickle(raw_df([
    ("2027-01-15", 100.0, "call", 1.0, -0.01, 0.3),
    ("2027-01-15", 110.0, "put", 1.0, 0.01, 0.3),
    ("2030-06-20", 100.0, "call", 9.0, -0.01, 0.3),
]), TMP / "snap_extra.pkl")

rep_c, code_c, _ = WellnessCheck.run(
    good_pkt, snapshot_path=str(snap_extra))
c_c = check_id(rep_c, "SNAP-01")
check("c1 raw-only expiry -> exit 1", code_c == 1,
      f"code={code_c}")
check("c2 raw-only expiry -> SNAP-01 FAIL",
      c_c["status"] == "FAIL", c_c["detail"])
check("c3 finding names the unreconciled expiry",
      any(f["expiry"] == "2030-06-20" for f in c_c["findings"]),
      str(c_c["findings"]))
check("c4 packet's own expiry still verifies (MAXPAIN-02 PASS)",
      check_id(rep_c, "MAXPAIN-02")["status"] == "PASS",
      check_id(rep_c, "MAXPAIN-02")["detail"])

# =========================
# (d) GexDashboard independence — sys.modules
# =========================
proc_d = subprocess.run(
    [sys.executable, "-c",
     "import WellnessCheck, sys; "
     "print('GexDashboard' in sys.modules)"],
    capture_output=True, text=True, timeout=60, cwd=REPO)
check("d1 importing WellnessCheck does not import "
      "GexDashboard", proc_d.stdout.strip() == "False",
      proc_d.stdout.strip() + proc_d.stderr.strip())

src = (REPO / "WellnessCheck.py").read_text(encoding="utf-8")
check("d2 no 'import GexDashboard'/'from GexDashboard' in "
      "source",
      "import GexDashboard" not in src
      and "from GexDashboard" not in src,
      "grep clean")

print(f"\n=== {PASS_COUNT + len(FAILS)} checks, "
      f"{len(FAILS)} FAIL ===")
if FAILS:
    print("FAILED:", FAILS)
    sys.exit(1)
print("ALL PASS")

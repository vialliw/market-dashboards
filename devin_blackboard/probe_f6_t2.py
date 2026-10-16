"""F6-T2 challenger probe — adversarial review of WellnessCheck.py.

All offline. Crafted tamper packets must fail at EXACTLY the right
check_id; 'unperformed' semantics verified with/without snapshot;
reference-impl independence; failure paths; fixture integrity; the
exported-OI=0 vs raw-NaN candidate-set ambiguity applied to
MAXPAIN-02; and a mutation harness that runs the REAL committed
pytest suite against mutant checker builds.
"""

import importlib.util
import io
import json
import math
import pickle
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import WellnessCheck as WC  # noqa: E402
import GexDashboard as GD  # noqa: E402

FIX = REPO / "tests" / "fixtures"
PACKET = FIX / "TSM_ai_packet.json"
SNAPSHOT = FIX / "TSM_snapshot.pkl"
RAW_PACKET = FIX / "TSM_raw_ai_packet.json"
RAW_SNAPSHOT = FIX / "TSM_raw_snapshot.pkl"
PREFIX_SNAPSHOT = REPO / "_prefix_run" / "tsm_snapshot.pkl"

TMP = Path(tempfile.mkdtemp(prefix="f6t2_"))

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name +
          (f" | {detail}" if detail else ""))


def fresh_packet():
    return json.loads(PACKET.read_text(encoding="utf-8"))


def write_pkt(obj, name="p.json"):
    p = TMP / name
    p.write_text(json.dumps(obj), encoding="utf-8")
    return p


def run(obj_or_path, snapshot=None, report=None, max_age=72.0):
    p = (obj_or_path if isinstance(obj_or_path, Path)
         else write_pkt(obj_or_path))
    return WC.run(
        p,
        snapshot_path=str(snapshot) if snapshot else None,
        report_path=str(report) if report else None,
        max_age_hours=max_age,
    )


def chk(rep, cid):
    return next(
        c for c in rep["checks"] if c["check_id"] == cid)


def failed_ids(rep):
    return {c["check_id"] for c in rep["checks"]
            if c["status"] == "FAIL"}


def warn_ids(rep):
    return {c["check_id"] for c in rep["checks"]
            if c["status"] == "WARN"}


def only_fails(rep, expected):
    """True when the FAIL set == the expected set."""
    return failed_ids(rep) == set(expected)


def mk_strike(k, co=0, po=0, cg=0.0, pg=0.0, iv=0.3, **ex):
    s = {
        "strike": float(k), "open_interest": int(co + po),
        "call_oi": int(co), "put_oi": int(po),
        "net_gex": cg + pg, "call_gex": cg, "put_gex": pg,
        "implied_volatility": iv, "is_call_wall": False,
        "is_put_wall": False, "is_gamma_flip": False,
        "distance_from_spot_pct": 0.0,
    }
    s.update(ex)
    return s


def mk_expiry(name, strikes, unavailable=None, metrics=None):
    e = {
        "expiry": name,
        "net_gex": sum(s["net_gex"] for s in strikes),
        "gamma_regime": "-Gamma",
        "metrics_unavailable": unavailable is not None,
        "atm_iv": 0.3, "call_walls": [], "put_walls": [],
        "strikes": strikes,
    }
    m = metrics or {}
    if unavailable is None:
        e.update({
            "max_pain": m.get("max_pain"),
            "max_pain_constrained": m.get("max_pain_constrained"),
            "max_pain_candidate_band": m.get("candidate_band"),
            "max_pain_band_fell_back": m.get("band_fell_back", False),
            "max_pain_band_boundary_hit": m.get("band_boundary_hit", False),
            "max_pain_selected_lowest_candidate":
                m.get("selected_lowest_candidate", False),
            "max_pain_selected_highest_candidate":
                m.get("selected_highest_candidate", False),
            "gamma_flip": m.get("gamma_flip"),
        })
    else:
        for f in WC.PAYOUT_FIELDS:
            e[f] = None
        e["gamma_flip"] = m.get("gamma_flip")
        e["data_quality"] = unavailable
    return e


def mk_packet(expiries, spot=100.0, nearest=None):
    return {
        "meta": {
            "ticker": "TEST",
            "generated_utc": (
                datetime.now(timezone.utc)
                - timedelta(hours=1)
            ).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "schema_version": "1.0",
            "source": "probe",
        },
        "market_snapshot": {
            "spot": spot,
            "total_net_gex": sum(
                e["net_gex"] for e in expiries),
            "overall_gamma_regime": "-Gamma",
            "nearest_gamma_flip": nearest,
            "expiries": expiries,
        },
    }


def raw_df(rows):
    recs = [{
        "expiry": pd.Timestamp(exp, tz="UTC"),
        "strike": k, "type": t, "openInterest": oi,
        "gex": g, "impliedVolatility": iv,
    } for exp, k, t, oi, g, iv in rows]
    return pd.DataFrame(recs)


def write_snap(df, name):
    p = TMP / name
    with open(p, "wb") as f:
        pickle.dump(df, f)
    return p


# ======================================================
# S1: import independence
# ======================================================
src = (REPO / "WellnessCheck.py").read_text(encoding="utf-8")
check("S1 no GexDashboard import in WellnessCheck source",
      "import GexDashboard" not in src
      and "from GexDashboard" not in src)
p = subprocess.run(
    [sys.executable, "-c",
     "import sys; sys.path.insert(0, r'%s');"
     "import WellnessCheck;"
     "print('GexDashboard' in sys.modules)" % REPO],
    capture_output=True, text=True, cwd=TMP)
check("S1 importing WellnessCheck does not load GexDashboard",
      p.stdout.strip() == "False", p.stdout.strip())
check("S1 no network libs imported by checker",
      all(t not in src for t in
          ("import yfinance", "import requests",
           "import urllib", "import socket",
           "http.client")))

# ======================================================
# S2: baseline fixture PASS (fresh utc to pin FRESH-01)
# ======================================================
pkt = fresh_packet()
pkt["meta"]["generated_utc"] = (
    datetime.now(timezone.utc) - timedelta(hours=1)
).strftime("%Y-%m-%dT%H:%M:%SZ")
rep, code, _ = run(write_pkt(pkt, "base.json"))
check("S2 fixture packet -> overall PASS, exit 0",
      code == 0 and rep["overall_status"] == "PASS",
      json.dumps({c["check_id"]: c["status"]
                  for c in rep["checks"]}))

rep_s, code_s, _ = run(PACKET, snapshot=SNAPSHOT)
check("S2 fixture + snapshot -> SNAP-01 PASS, "
      "AVAIL-01b PASS",
      chk(rep_s, "SNAP-01")["status"] == "PASS"
      and chk(rep_s, "AVAIL-01b")["status"] == "PASS")

# ======================================================
# S3: per-check single-tamper matrix (exactly one
# check_id must fire)
# ======================================================
def tamper(name, mutate, expected_ids, snapshot=None):
    p = fresh_packet()
    mutate(p)
    r, c, _ = run(write_pkt(p, name + ".json"),
                  snapshot=snapshot)
    ok = only_fails(r, expected_ids) and c == 1
    check(f"S3 {name} -> FAILs exactly {sorted(expected_ids)}",
          ok, f"exit={c} fails={sorted(failed_ids(r))}")


E0 = lambda p: p["market_snapshot"]["expiries"][0]

tamper("tamper_max_pain_argmin",
       lambda p: E0(p).__setitem__("max_pain", 90.0),
       {"MAXPAIN-01"})

tamper("tamper_gamma_flip",
       lambda p: E0(p).__setitem__("gamma_flip", 999.0),
       {"FLIP-01"})

# aggregation drift JUST above tolerance (tol ~1e-9*mag)
def _agg_drift(p):
    e = E0(p)
    terms = [s["net_gex"] for s in e["strikes"]]
    tol = max(1e-9, 1e-9 * max(
        abs(t) for t in terms + [e["net_gex"]]))
    e["net_gex"] = e["net_gex"] + 10 * tol
tamper("agg_drift_10x_tol", _agg_drift, {"AGG-01"})

def _agg_drift_small(p):
    e = E0(p)
    terms = [s["net_gex"] for s in e["strikes"]]
    tol = max(1e-9, 1e-9 * max(
        abs(t) for t in terms + [e["net_gex"]]))
    e["net_gex"] = e["net_gex"] + 0.4 * tol
p = fresh_packet(); _agg_drift_small(p)
r, c, _ = run(write_pkt(p, "agg_sub_tol.json"))
check("S3 agg drift 0.4x tolerance still PASSes "
      "(tolerance real, not vacuous)",
      chk(r, "AGG-01")["status"] == "PASS" and c == 0,
      f"fails={sorted(failed_ids(r))}")

def _stale_drop_raw(p):
    s = next(x for x in E0(p)["strikes"]
             if x.get("stale_iv"))
    del s["raw_implied_volatility"]
tamper("stale_iv_missing_raw", _stale_drop_raw, {"IV-01"})

def _stale_iv_nonnull(p):
    s = next(x for x in E0(p)["strikes"]
             if x.get("stale_iv"))
    s["implied_volatility"] = 0.5  # non-null while stale
tamper("stale_iv_with_nonnull_iv", _stale_iv_nonnull,
       {"IV-01"})

def _stale_low_raw(p):
    s = next(x for x in E0(p)["strikes"]
             if x.get("stale_iv"))
    s["raw_implied_volatility"] = 1.5  # <=2.0
tamper("stale_iv_raw_le_2", _stale_low_raw, {"IV-01"})

def _unflagged_high_iv(p):
    s = E0(p)["strikes"][0]
    s["implied_volatility"] = 2.5
    s.pop("stale_iv", None)
    s.pop("raw_implied_volatility", None)
tamper("unflagged_iv_2.5", _unflagged_high_iv, {"IV-01"})

def _iv_exactly_2(p):
    s = E0(p)["strikes"][0]
    s["implied_volatility"] = 2.0  # boundary: NOT stale
    s.pop("stale_iv", None)
    s.pop("raw_implied_volatility", None)
p2 = fresh_packet(); _iv_exactly_2(p2)
r, c, _ = run(write_pkt(p2, "iv_boundary.json"))
check("S3 unflagged iv==2.0 boundary does NOT fire IV-01",
      chk(r, "IV-01")["status"] == "PASS",
      f"fails={sorted(failed_ids(r))}")

def _mu_true_payout_nonnull(p):
    e = E0(p)
    e["metrics_unavailable"] = True
    e["data_quality"] = "insufficient_data"
    # leave fields non-null
tamper("flag_true_nonnull_fields", _mu_true_payout_nonnull,
       {"AVAIL-01a", "AVAIL-01c", "AVAIL-01b"})

def _mu_true_no_dq(p):
    e = E0(p)
    for f in WC.PAYOUT_FIELDS:
        e[f] = None
    e["metrics_unavailable"] = True
    e.pop("data_quality", None)
tamper("flag_true_dq_absent", _mu_true_no_dq,
       {"AVAIL-01a", "AVAIL-01b"})

def _mu_true_bogus_dq(p):
    e = E0(p)
    for f in WC.PAYOUT_FIELDS:
        e[f] = None
    e["metrics_unavailable"] = True
    e["data_quality"] = "totally_made_up_reason"
tamper("flag_true_bogus_reason", _mu_true_bogus_dq,
       {"AVAIL-01a", "AVAIL-01b"})

def _mu_false_null_field(p):
    e = E0(p)
    e["max_pain_band_fell_back"] = None
# MAXPAIN-02 co-fires: the null field contradicts the
# recomputed flag — a legitimate second verdict, not a
# wrong-id fire.
tamper("flag_false_null_payout_field",
       _mu_false_null_field,
       {"AVAIL-01a", "AVAIL-01c", "MAXPAIN-02"})

def _false_unavail(p):
    e = E0(p)
    for f in WC.PAYOUT_FIELDS:
        e[f] = None
    e["metrics_unavailable"] = True
    e["data_quality"] = "insufficient_data"
tamper("false_unavailable_on_eligible", _false_unavail,
       {"AVAIL-01b"})

# boundary: exactly-2-positive-OI chain flagged -> FAIL
strikes2 = [mk_strike(100, co=1, cg=-0.01),
            mk_strike(110, po=1, cg=0.01)]
e2 = mk_expiry("2027-01-15", strikes2,
               unavailable="insufficient_data",
               metrics={"gamma_flip": 110.0})
pkt2 = mk_packet([e2], nearest=110.0)
r, c, _ = run(write_pkt(pkt2, "pos2_boundary.json"))
check("S3 exactly-2 positive-OI strikes proves eligible "
      "-> false-unavailable FAILs AVAIL-01b",
      chk(r, "AVAIL-01b")["status"] == "FAIL" and c == 1,
      chk(r, "AVAIL-01b")["detail"][:160])

def _dup_strike(p):
    e = E0(p)
    e["strikes"].append(dict(e["strikes"][0]))
tamper("duplicate_strike", _dup_strike, {"SCHEMA-01"})

def _dup_expiry(p):
    ms = p["market_snapshot"]
    import copy
    ms["expiries"].append(copy.deepcopy(ms["expiries"][0]))
tamper("duplicate_expiry", _dup_expiry,
       {"SCHEMA-01", "AGG-01"})

def _missing_strike_field(p):
    del E0(p)["strikes"][0]["net_gex"]
tamper("missing_strike_net_gex", _missing_strike_field,
       {"SCHEMA-01"})

def _mu_string(p):
    E0(p)["metrics_unavailable"] = "yes"
tamper("metrics_unavailable_nonbool", _mu_string,
       {"SCHEMA-01"})

def _nan_token(p):
    E0(p)["strikes"][0]["implied_volatility"] = float("nan")
tamper("nan_token", _nan_token,
       {"NUMERIC-01", "IV-01"})  # strikes[0] is stale:
# null-violating iv fires IV-01 as well as NUMERIC-01

def _inf_token(p):
    E0(p)["strikes"][0]["call_gex"] = float("inf")
# inf IS a number type-wise (NUMERIC-01 owns finiteness)
tamper("inf_token", _inf_token, {"NUMERIC-01"})

def _neg_oi(p):
    E0(p)["strikes"][0]["call_oi"] = -5
tamper("negative_oi", _neg_oi, {"NUMERIC-01"})

def _spot_zero(p):
    p["market_snapshot"]["spot"] = 0
tamper("spot_zero", _spot_zero, {"NUMERIC-01", "MAXPAIN-02"})

def _bad_date(p):
    E0(p)["expiry"] = "16/10/2026"
tamper("bad_expiry_date", _bad_date, {"NUMERIC-01"})

def _bad_genutc(p):
    p["meta"]["generated_utc"] = "not-a-timestamp"
tamper("bad_generated_utc", _bad_genutc,
       {"NUMERIC-01"})

def _flip_fell_back(p):
    e = E0(p)
    e["max_pain_band_fell_back"] = (
        not e["max_pain_band_fell_back"])
tamper("tamper_fell_back_flag", _flip_fell_back,
       {"MAXPAIN-02"})

def _wrong_band(p):
    E0(p)["max_pain_candidate_band"] = [1.0, 2.0]
tamper("tamper_candidate_band", _wrong_band,
       {"MAXPAIN-02"})

def _wrong_mpc(p):
    E0(p)["max_pain_constrained"] = 90.0
tamper("tamper_constrained_argmin", _wrong_mpc,
       {"MAXPAIN-02"})

# --- SNAP-01 tampering (against the real snapshot) ---
df_snap = pickle.loads(SNAPSHOT.read_bytes())
exp0 = "2026-10-16"
sub0 = df_snap[df_snap["expiry"]
               .dt.strftime("%Y-%m-%d") == exp0]

d = df_snap.drop(index=sub0.index[0])
tamper("snap_missing_row", lambda p: None,
       {"SNAP-01"}, snapshot=write_snap(d, "snap_drop.pkl"))

extra = sub0.iloc[[0]].copy()
extra["strike"] = 9999.0
d2 = pd.concat([df_snap, extra], ignore_index=True)
tamper("snap_extra_strike", lambda p: None,
       {"SNAP-01"}, snapshot=write_snap(d2, "snap_add.pkl"))

d3 = df_snap.copy()
i = d3.index[0]
d3.loc[i, "gex"] = d3.loc[i, "gex"] + 500.0
tamper("snap_gex_drift", lambda p: None,
       {"SNAP-01"}, snapshot=write_snap(d3, "snap_gex.pkl"))

d4 = df_snap.copy()
i4 = d4.index[0]
d4.loc[i4, "impliedVolatility"] = (
    d4.loc[i4, "impliedVolatility"] + 0.5)
tamper("snap_iv_drift", lambda p: None,
       {"SNAP-01"}, snapshot=write_snap(d4, "snap_iv.pkl"))

def _oi_only(p):
    E0(p)["strikes"][0]["open_interest"] += 777
tamper("open_interest_only_tamper", _oi_only,
       {"SNAP-01"}, snapshot=SNAPSHOT)

# ======================================================
# S4: 'unperformed' semantics
# ======================================================
r, _, _ = run(write_pkt(fresh_packet(), "nosnap.json"))
check("S4 SNAP-01 unperformed without snapshot",
      chk(r, "SNAP-01")["status"] == "unperformed")
b = chk(r, "AVAIL-01b")
check("S4 AVAIL-01b notes reason-code unperformed "
      "without snapshot", "reason-code check unperformed"
      in b["detail"])

# ambiguous degenerate export, both flag directions
strikes_amb = [mk_strike(100, co=1, cg=-0.01),
               mk_strike(110, co=0, po=0, cg=0.01)]
e_amb_f = mk_expiry("2027-01-15", strikes_amb,
                    unavailable="insufficient_data",
                    metrics={"gamma_flip": 110.0})
r, _, _ = run(write_pkt(
    mk_packet([e_amb_f], nearest=110.0), "amb_t.json"))
check("S4 ambiguous export flag:true -> AVAIL-01b "
      "unperformed (not PASS/FAIL)",
      chk(r, "AVAIL-01b")["status"] == "unperformed",
      chk(r, "AVAIL-01b")["detail"][:140])

e_amb_o = mk_expiry("2027-01-15", strikes_amb, metrics={
    "max_pain": 100.0, "max_pain_constrained": 100.0,
    "candidate_band": [70.0, 130.0],
    "selected_lowest_candidate": True,
    "gamma_flip": 110.0})
r, _, _ = run(write_pkt(
    mk_packet([e_amb_o], nearest=110.0), "amb_f.json"))
check("S4 ambiguous export flag:false -> AVAIL-01b "
      "unperformed",
      chk(r, "AVAIL-01b")["status"] == "unperformed",
      chk(r, "AVAIL-01b")["detail"][:140])

# snapshot resolves the identical export both ways
df0 = raw_df([("2027-01-15", 100.0, "call", 1.0, -0.01, 0.3),
              ("2027-01-15", 110.0, "call", 0.0, 0.01, 0.3)])
s0 = write_snap(df0, "amb0.pkl")
r, _, _ = run(write_pkt(
    mk_packet([e_amb_o], nearest=110.0), "ambz.json"),
    snapshot=s0)
check("S4 snapshot (100,1)/(110,0) -> eligible, "
      "flag:false PASSes",
      chk(r, "AVAIL-01b")["status"] == "PASS",
      chk(r, "AVAIL-01b")["detail"][:140])
dfN = raw_df([("2027-01-15", 100.0, "call", 1.0, -0.01, 0.3),
              ("2027-01-15", 110.0, "call", float("nan"),
               0.01, 0.3)])
sN = write_snap(dfN, "ambN.pkl")
r, code, _ = run(write_pkt(
    mk_packet([e_amb_o], nearest=110.0), "ambn.json"),
    snapshot=sN)
check("S4 identical export + (110,NaN) -> flag:false "
      "is a FAIL at AVAIL-01b",
      chk(r, "AVAIL-01b")["status"] == "FAIL" and code == 1,
      chk(r, "AVAIL-01b")["detail"][:160])

# zero-total-OI raw -> insufficient_data (not no_finite_oi)
dfz = raw_df([("2027-01-15", 100.0, "call", 0.0, -0.01, 0.3),
              ("2027-01-15", 110.0, "call", 0.0, 0.01, 0.3)])
sz = write_snap(dfz, "zero.pkl")
e_zero = mk_expiry("2027-01-15", strikes_amb,
                   unavailable="insufficient_data",
                   metrics={"gamma_flip": 110.0})
r, _, _ = run(write_pkt(
    mk_packet([e_zero], nearest=110.0), "zero.json"),
    snapshot=sz)
check("S4 zero-total-OI raw -> 'insufficient_data' "
      "reason verified PASS",
      chk(r, "AVAIL-01b")["status"] == "PASS",
      chk(r, "AVAIL-01b")["detail"][:160])
e_zero_bad = mk_expiry("2027-01-15", strikes_amb,
                       unavailable="no_finite_oi",
                       metrics={"gamma_flip": 110.0})
r, code, _ = run(write_pkt(
    mk_packet([e_zero_bad], nearest=110.0), "zerobad.json"),
    snapshot=sz)
check("S4 wrong reason 'no_finite_oi' vs "
      "'insufficient_data' -> FAIL",
      chk(r, "AVAIL-01b")["status"] == "FAIL" and code == 1)

# expiry absent from snapshot entirely
df_other = raw_df([
    ("2030-01-01", 100.0, "call", 1.0, -0.01, 0.3)])
s_abs = write_snap(df_other, "absent.pkl")
e_abs = mk_expiry("2027-01-15", strikes_amb,
                  unavailable="no_finite_oi",
                  metrics={"gamma_flip": 110.0})
r, code, _ = run(write_pkt(
    mk_packet([e_abs], nearest=110.0), "absent.json"),
    snapshot=s_abs)
check("S4 expiry absent from snapshot -> treated as "
      "no_finite_oi; matching dq PASSes both "
      "AVAIL-01b and SNAP-01 strike-set",
      chk(r, "AVAIL-01b")["status"] == "PASS",
      f"b={chk(r, 'AVAIL-01b')['status']} "
      f"snap={chk(r, 'SNAP-01')['status']} "
      f"fails={sorted(failed_ids(r))}")

# all-flag-absent -> unperformed on AVAIL family
e_noflag = mk_expiry("2027-01-15", strikes_amb, metrics={
    "max_pain": 100.0, "max_pain_constrained": 100.0,
    "candidate_band": [70.0, 130.0],
    "selected_lowest_candidate": True,
    "gamma_flip": 110.0})
del e_noflag["metrics_unavailable"]
r, _, _ = run(write_pkt(
    mk_packet([e_noflag], nearest=110.0), "noflag.json"))
check("S4 flag absent everywhere -> AVAIL-01a/b/c "
      "unperformed (SCHEMA catches absence)",
      all(chk(r, c)["status"] == "unperformed"
          for c in ("AVAIL-01a", "AVAIL-01b", "AVAIL-01c")),
      f"schema={chk(r, 'SCHEMA-01')['status']}")

# missing expiries list -> AGG/AVAIL/MAXPAIN unperformed;
# FLIP-01 legitimately FAILs because the packet still
# claims nearest_gamma_flip=430 with no strike data.
p_noexp = fresh_packet()
del p_noexp["market_snapshot"]["expiries"]
r, _, _ = run(write_pkt(p_noexp, "noexp.json"))
check("S4 expiries absent -> downstream checks unperformed, "
      "SCHEMA-01 FAILs (FLIP-01 co-fires on the dangling "
      "nearest_gamma_flip claim — honest)",
      chk(r, "SCHEMA-01")["status"] == "FAIL"
      and all(chk(r, c)["status"] == "unperformed"
              for c in ("AGG-01", "AVAIL-01a",
                        "MAXPAIN-01", "MAXPAIN-02"))
      and chk(r, "FLIP-01")["status"] == "FAIL",
      json.dumps({c["check_id"]: c["status"]
                  for c in r["checks"]}))

# ======================================================
# S5: failure paths / exit codes / no-overwrite
# ======================================================
bad = TMP / "bad.json"
bad.write_text("{not json", encoding="utf-8")
rep_p = TMP / "bad_report.json"
r, c, rp = run(bad, report=rep_p)
check("S5 malformed JSON -> INPUT-01 FAIL exit 1 + "
      "structured report written",
      c == 1 and r["checks"][0]["check_id"] == "INPUT-01"
      and rp == rep_p and rep_p.exists())
r, c, _ = run(TMP / "missing_file.json")
check("S5 missing file -> INPUT-01 exit 1",
      c == 1 and r["checks"][0]["check_id"] == "INPUT-01")
p_sv = fresh_packet(); p_sv["meta"]["schema_version"] = "2.0"
r, c, _ = run(write_pkt(p_sv, "sv.json"))
check("S5 unsupported schema_version -> INPUT-01 exit 1",
      c == 1 and r["checks"][0]["check_id"] == "INPUT-01")
listroot = TMP / "list.json"
listroot.write_text("[1,2,3]", encoding="utf-8")
r, c, _ = run(listroot)
check("S5 non-object root -> INPUT-01 exit 1",
      c == 1 and r["checks"][0]["check_id"] == "INPUT-01")

# malformed-but-parseable structure: market_snapshot not dict
p_ms = fresh_packet()
p_ms["market_snapshot"] = [1, 2, 3]
r, c, _ = run(write_pkt(p_ms, "mslist.json"))
ms_status = {
    "exit": c,
    "ids": sorted(failed_ids(r)),
}
check("S5 market_snapshot=list -> structured report "
      "(records actual behavior)",
      c in (1, 2) and r["checks"],
      json.dumps(ms_status))

# snapshot failure modes -> SNAP-01 FAIL, exit 1
r, c, _ = run(write_pkt(fresh_packet(), "s1.json"),
              snapshot=TMP / "no_such.pkl")
# FINDING: check_snapshot itself returns SNAP-01 FAIL for
# an unreadable snapshot, but build_report then calls
# _sha256(snapshot_path) unconditionally -> the run crashes
# into CHECKER-ERR exit 2 and the SNAP-01 verdict is LOST.
check("S5 unreadable snapshot file -> SNAP-01 FAIL exit 1 "
      "(FINDING: actually CHECKER-ERR exit 2 — "
      "provenance hash crashes after SNAP-01 returned FAIL)",
      c == 1 and chk(r, "SNAP-01")["status"] == "FAIL",
      f"actual: exit={c} ids="
      f"{[(x['check_id'], x['status']) for x in r['checks']]}")
notdf = TMP / "notdf.pkl"
with open(notdf, "wb") as f:
    pickle.dump({"x": 1}, f)
r, c, _ = run(write_pkt(fresh_packet(), "s2.json"),
              snapshot=notdf)
check("S5 non-DataFrame snapshot -> SNAP-01 FAIL exit 1",
      c == 1 and chk(r, "SNAP-01")["status"] == "FAIL")
d5 = df_snap.drop(columns=["openInterest"])
r, c, _ = run(write_pkt(fresh_packet(), "s3.json"),
              snapshot=write_snap(d5, "nocol.pkl"))
check("S5 snapshot missing required column -> "
      "SNAP-01 FAIL exit 1",
      c == 1 and chk(r, "SNAP-01")["status"] == "FAIL")

# checker-internal exception -> CHECKER-ERR exit 2
orig = WC.check_numeric
WC.check_numeric = lambda p: (_ for _ in ()).throw(
    RuntimeError("injected-boom"))
try:
    r, c, rp = run(write_pkt(fresh_packet(), "boom.json"),
                   report=TMP / "boom_report.json")
finally:
    WC.check_numeric = orig
check("S5 checker exception -> CHECKER-ERR exit 2 + "
      "report written",
      c == 2 and r["checks"][0]["check_id"] == "CHECKER-ERR"
      and "injected-boom" in r["checks"][0]["detail"]
      and rp.exists())

# --report == packet -> exit 2, packet untouched (CLI)
pkt_file = write_pkt(fresh_packet(), "same.json")
before = pkt_file.read_bytes()
proc = subprocess.run(
    [sys.executable, str(REPO / "WellnessCheck.py"),
     str(pkt_file), "--report", str(pkt_file)],
    capture_output=True, text=True, cwd=TMP)
check("S5 --report==packet -> exit 2, packet bytes intact",
      proc.returncode == 2
      and pkt_file.read_bytes() == before,
      f"rc={proc.returncode}")

# 'x'-safe: two runs to same --report path -> second file
rpt = TMP / "twice.json"
for _ in range(2):
    subprocess.run(
        [sys.executable, str(REPO / "WellnessCheck.py"),
         str(PACKET), "--report", str(rpt)],
        capture_output=True, text=True, cwd=TMP)
siblings = sorted(TMP.glob("twice*.json"))
check("S5 repeated --report never overwrites "
      "(x-safe suffix fallback)",
      len(siblings) == 2 and rpt.exists(),
      [s.name for s in siblings])

# WARN path exit 0 (stale generated_utc)
p_stale = fresh_packet()
p_stale["meta"]["generated_utc"] = (
    datetime.now(timezone.utc) - timedelta(hours=200)
).strftime("%Y-%m-%dT%H:%M:%SZ")
r, c, _ = run(write_pkt(p_stale, "stale.json"))
check("S5 stale generated_utc -> FRESH-01 WARN, exit 0",
      c == 0 and chk(r, "FRESH-01")["status"] == "WARN"
      and r["overall_status"] == "WARN")
r, c, _ = run(write_pkt(p_stale, "stale2.json"), max_age=500)
check("S5 --max-age-hours honored (500h -> no WARN)",
      chk(r, "FRESH-01")["status"] == "PASS")
p_future = fresh_packet()
p_future["meta"]["generated_utc"] = (
    datetime.now(timezone.utc) + timedelta(hours=48)
).strftime("%Y-%m-%dT%H:%M:%SZ")
r, c, _ = run(write_pkt(p_future, "future.json"))
check("S5 future generated_utc -> FRESH-01 WARN exit 0",
      chk(r, "FRESH-01")["status"] == "WARN" and c == 0)

# ======================================================
# S6: THE NaN-vs-0 CANDIDATE-SET AMBIGUITY on MAXPAIN-02
# Honest packets built by the REAL build_ai_packet.
# Exported OI=0 conflates true-zero with raw-NaN; the
# checker treats every exported strike as a payout
# candidate while production only used finite-OI rows.
# ======================================================
def honest_packet(rows_by_expiry, spot, expiries,
                  gen_utc=None):
    df = pd.DataFrame(rows_by_expiry)
    pkt = GD.build_ai_packet("HON", df, spot, expiries)
    if gen_utc:
        pkt["meta"]["generated_utc"] = gen_utc
    return pkt

EXP = "2027-01-15"
fresh_utc = (
    datetime.now(timezone.utc) - timedelta(hours=1)
).strftime("%Y-%m-%dT%H:%M:%SZ")

def hon_row(k, t, oi, g=0.0, iv=0.3):
    return {"expiry": pd.Timestamp(EXP, tz="UTC"),
            "strike": float(k), "type": t,
            "openInterest": oi, "gex": g,
            "impliedVolatility": iv}

# Band [70,130] holds ONLY the OI=0/NaN strike 80;
# finite OI lives at 50 (call 5) / 200 (put 5) ->
# flat pain 750 on [50,200].
rows_nan = [
    hon_row(50, "call", 5.0, -0.01),
    hon_row(50, "put", 0.0, 0.0),
    hon_row(80, "call", float("nan"), 0.0),
    hon_row(80, "put", float("nan"), 0.0),
    hon_row(200, "call", 0.0, 0.0),
    hon_row(200, "put", 5.0, 0.01),
]
pkt_nan = honest_packet(rows_nan, 100.0, [EXP],
                        gen_utc=fresh_utc)
e_nan = pkt_nan["market_snapshot"]["expiries"][0]

rows_zero = [
    hon_row(50, "call", 5.0, -0.01),
    hon_row(50, "put", 0.0, 0.0),
    hon_row(80, "call", 0.0, 0.0),
    hon_row(80, "put", 0.0, 0.0),
    hon_row(200, "call", 0.0, 0.0),
    hon_row(200, "put", 5.0, 0.01),
]
pkt_zero = honest_packet(rows_zero, 100.0, [EXP],
                         gen_utc=fresh_utc)
e_zero2 = pkt_zero["market_snapshot"]["expiries"][0]

check("S6 honest NaN-in-band packet: production emitted "
      "fell_back=True + mpc=50",
      e_nan["max_pain_band_fell_back"] is True
      and e_nan["max_pain_constrained"] == 50.0,
      f"fell_back={e_nan['max_pain_band_fell_back']} "
      f"mpc={e_nan['max_pain_constrained']} "
      f"mp={e_nan['max_pain']}")
check("S6 honest zero-OI-in-band packet: production "
      "emitted fell_back=False + mpc=80 "
      "(identical exported strikes)",
      e_zero2["max_pain_band_fell_back"] is False
      and e_zero2["max_pain_constrained"] == 80.0,
      f"fell_back={e_zero2['max_pain_band_fell_back']} "
      f"mpc={e_zero2['max_pain_constrained']}")

r, c, _ = run(write_pkt(pkt_zero, "hon_zero.json"))
check("S6 true-zero packet -> checker PASS "
      "(recompute agrees)",
      failed_ids(r) == set(),
      f"fails={sorted(failed_ids(r))}")

r, c, _ = run(write_pkt(pkt_nan, "hon_nan.json"))
mp2 = chk(r, "MAXPAIN-02")
check("S6 NaN-exported-as-0 in band -> checker verdict "
      "on the honest packet (documented)",
      True,
      f"MAXPAIN-02={mp2['status']} "
      f"findings={json.dumps(mp2['findings'])[:300]} "
      f"exit={c} all_fails={sorted(failed_ids(r))}")

# same divergence, unconstrained: argmin must still match
check("S6 MAXPAIN-01 unaffected by extra exported-0 "
      "candidates (flat-min argmin identical)",
      chk(r, "MAXPAIN-01")["status"] == "PASS",
      chk(r, "MAXPAIN-01")["detail"][:120])

# interior NaN strike below the true argmin on the flat
# region boundary: candidates differ but argmin can't move
rows_nan2 = [
    hon_row(50, "call", 5.0, -0.01),
    hon_row(50, "put", 0.0, 0.0),
    hon_row(60, "call", float("nan"), 0.0),
    hon_row(60, "put", float("nan"), 0.0),
    hon_row(90, "call", 1.0, 0.0),   # in-band finite strike
    hon_row(90, "put", 0.0, 0.0),
    hon_row(200, "call", 0.0, 0.0),
    hon_row(200, "put", 5.0, 0.01),
]
pkt_n2 = honest_packet(rows_nan2, 100.0, [EXP],
                       gen_utc=fresh_utc)
e_n2 = pkt_n2["market_snapshot"]["expiries"][0]
r, c, _ = run(write_pkt(pkt_n2, "hon_nan2.json"))
check("S6 mixed in-band (finite 90 + NaN 60/80) -> "
      "constrained verdict documented",
      True,
      f"prod mpc={e_n2['max_pain_constrained']} "
      f"flags f/e/l/h="
      f"{e_n2['max_pain_band_fell_back']}/"
      f"{e_n2['max_pain_band_boundary_hit']}/"
      f"{e_n2['max_pain_selected_lowest_candidate']}/"
      f"{e_n2['max_pain_selected_highest_candidate']} "
      f"MAXPAIN-02={chk(r, 'MAXPAIN-02')['status']} "
      f"findings={json.dumps(chk(r,'MAXPAIN-02')['findings'])[:260]}")

# tie variant: in-band NaN-exported-0 strike BELOW a
# finite in-band candidate inside the flat-pain region
rows_nan3 = [
    hon_row(50, "call", 5.0, -0.01),
    hon_row(50, "put", 0.0, 0.0),
    hon_row(75, "call", float("nan"), 0.0),
    hon_row(75, "put", float("nan"), 0.0),
    hon_row(90, "call", 1.0, 0.0),
    hon_row(90, "put", 0.0, 0.0),
    hon_row(200, "call", 0.0, 0.0),
    hon_row(200, "put", 5.0, 0.01),
]
pkt_n3 = honest_packet(rows_nan3, 100.0, [EXP],
                       gen_utc=fresh_utc)
e_n3 = pkt_n3["market_snapshot"]["expiries"][0]
r3, c3, _ = run(write_pkt(pkt_n3, "hon_nan3.json"))
check("S6b in-band NaN strike below finite candidate on "
      "pain-tie -> mpc mismatch on honest packet "
      "(prod {90}->90, checker adds exported-0 75)",
      chk(r3, "MAXPAIN-02")["status"] == "FAIL"
      and e_n3["max_pain_constrained"] == 90.0,
      f"prod mpc={e_n3['max_pain_constrained']} "
      f"checker={json.dumps(chk(r3,'MAXPAIN-02')['findings'])[:240]}")

# ======================================================
# S7: snapshot-only expiry gap — raw expiry missing
# from the packet entirely
# ======================================================
df_extra_exp = raw_df([
    ("2027-01-15", 100.0, "call", 1.0, -0.01, 0.3),
    ("2027-01-15", 110.0, "put", 1.0, 0.01, 0.3),
    ("2030-06-20", 100.0, "call", 9.0, -0.01, 0.3),
])
s_x = write_snap(df_extra_exp, "extraexp.pkl")
e_norm = mk_expiry("2027-01-15", [
    mk_strike(100, co=1, cg=-0.01),
    mk_strike(110, po=1, pg=0.01)], metrics={
    "max_pain": 100.0, "max_pain_constrained": 100.0,
    "candidate_band": [70.0, 130.0],
    "selected_lowest_candidate": True,
    "gamma_flip": 110.0})
r, c, _ = run(write_pkt(
    mk_packet([e_norm], nearest=110.0), "xexp.json"),
    snapshot=s_x)
check("S7 raw-only expiry (2030-06-20) absent from "
      "packet -> SNAP-01 verdict documented",
      chk(r, "SNAP-01")["status"] == "PASS",
      f"SNAP-01={chk(r,'SNAP-01')['status']} "
      f"detail={chk(r,'SNAP-01')['detail'][:160]} "
      "(raw-only expiry is NOT reconciled — gap)")

# ======================================================
# S8: fixture integrity
# ======================================================
old = json.loads(subprocess.run(
    ["git", "show",
     "5519f6d^:tests/fixtures/TSM_ai_packet.json"],
    capture_output=True, text=True, cwd=REPO).stdout)
new = fresh_packet()
o_ms, n_ms = old["market_snapshot"], new["market_snapshot"]
check("S8 fixture spot + total_net_gex preserved",
      o_ms["spot"] == n_ms["spot"]
      and o_ms["total_net_gex"] == n_ms["total_net_gex"],
      f"spot={n_ms['spot']} tng={n_ms['total_net_gex']}")

o_map = {e["expiry"]: e for e in o_ms["expiries"]}
n_map = {e["expiry"]: e for e in n_ms["expiries"]}
strike_diffs = []
prov_ok = True
for exp, oe in o_map.items():
    ne = n_map.get(exp)
    if ne is None:
        strike_diffs.append((exp, "missing expiry"))
        continue
    os_ = {s["strike"]: s for s in oe["strikes"]}
    ns_ = {s["strike"]: s for s in ne["strikes"]}
    if set(os_) != set(ns_):
        strike_diffs.append((exp, "strike set differs"))
        continue
    for k, osr in os_.items():
        nsr = ns_[k]
        for f in ("open_interest", "call_oi", "put_oi",
                  "net_gex", "call_gex", "put_gex",
                  "implied_volatility"):
            ov, nv = osr.get(f), nsr.get(f)
            if ov != nv:
                # expected: ONLY the stale-IV transform —
                # iv -> null while raw_implied_volatility
                # carries the old value verbatim.
                if (
                    f == "implied_volatility"
                    and nv is None
                    and nsr.get("stale_iv") is True
                    and nsr.get("raw_implied_volatility")
                    == ov
                ):
                    continue
                strike_diffs.append((exp, k, f, ov, nv))
        if (
            nsr.get("stale_iv") is True
            and nsr.get("raw_implied_volatility")
            != osr.get("implied_volatility")
        ):
            prov_ok = False
check("S8 every exported strike field preserved "
      "verbatim except stale-IV transform "
      "(iv->null + raw_implied_volatility=old iv)",
      not strike_diffs and prov_ok,
      f"{len(strike_diffs)} unexplained diffs "
      f"{strike_diffs[:3]}")

check("S8 regenerated flips are the corrected "
      "430/432.5/442.5/460/465/440/475",
      [n_map[e]["gamma_flip"] for e in (
          "2026-10-16", "2026-10-23", "2026-10-30",
          "2026-11-06", "2026-11-13", "2026-11-20",
          "2026-11-27")] == [430.0, 432.5, 442.5, 460.0,
                             465.0, 440.0, 475.0])
check("S8 regenerated argmins preserved "
      "435/450/445/460/470/420/450",
      [n_map[e]["max_pain"] for e in (
          "2026-10-16", "2026-10-23", "2026-10-30",
          "2026-11-06", "2026-11-13", "2026-11-20",
          "2026-11-27")] == [435.0, 450.0, 445.0, 460.0,
                             470.0, 420.0, 450.0])
n_stale = sum(
    1 for s in n_map["2026-10-16"]["strikes"]
    if s.get("stale_iv"))
check("S8 23 stale_iv strikes on 2026-10-16",
      n_stale == 23, n_stale)

# _prefix_run really is a different fetch
df_pre = pickle.loads(PREFIX_SNAPSHOT.read_bytes())
df_recon = pickle.loads(SNAPSHOT.read_bytes())
pre16 = df_pre[df_pre["expiry"]
               .dt.strftime("%Y-%m-%d") == "2026-10-16"]
rec16 = df_recon[df_recon["expiry"]
                 .dt.strftime("%Y-%m-%d") == "2026-10-16"]
pre_oi = {}
for _, row in pre16.iterrows():
    pre_oi.setdefault(row["strike"], {})[
        row["type"]] = row["openInterest"]
rec_oi = {}
for _, row in rec16.iterrows():
    rec_oi.setdefault(row["strike"], {})[
        row["type"]] = row["openInterest"]
diff_n = sum(
    1 for k in pre_oi
    if pre_oi[k] != rec_oi.get(k, {}))
check("S8 _prefix_run differs from reconstructed chain "
      "(claim: ~72/105 strikes' OI differ on 10-16)",
      60 <= diff_n <= 90,
      f"{diff_n}/105 strikes differ")

# raw pair fixture really reconciles and carries NaN rows
df_raw2 = pickle.loads(RAW_SNAPSHOT.read_bytes())
check("S8 TSM_raw_snapshot has real NaN-OI rows",
      df_raw2["openInterest"].isna().sum() > 0,
      int(df_raw2["openInterest"].isna().sum()))
rp2, c2, _ = run(RAW_PACKET, snapshot=RAW_SNAPSHOT)
check("S8 raw pair -> SNAP-01 PASS via NaN->0 path",
      chk(rp2, "SNAP-01")["status"] == "PASS",
      chk(rp2, "SNAP-01")["detail"][:120])

# ======================================================
# S9: reference impl == production on fixture (all expiries)
# ======================================================
def prod_side(sub):
    return {
        "mp": GD.compute_max_pain(sub),
        "mpc": GD.compute_max_pain_constrained(
            sub, spot=422.05),
        "flip": GD.compute_gamma_flip(sub, spot=422.05),
    }

ok_all, details = True, []
for exp, e in n_map.items():
    sub = df_recon[df_recon["expiry"]
                   .dt.strftime("%Y-%m-%d") == exp]
    pr = prod_side(sub)
    # checker ref impls on the packet's exported rows
    mp, _ties = WC._ref_max_pain(e["strikes"])
    mpc = WC._ref_max_pain_constrained(
        e["strikes"], 422.05)
    pairs = [(s["strike"], s["net_gex"])
             for s in e["strikes"]]
    fl = WC._ref_gamma_flip(pairs, 422.05)
    same = (
        mp == pr["mp"] and fl == pr["flip"]
        and mpc["max_pain"] == pr["mpc"]["max_pain"]
        and mpc["band_fell_back"]
        == pr["mpc"]["band_fell_back"]
        and mpc["band_boundary_hit"]
        == pr["mpc"]["band_boundary_hit"]
        and mpc["selected_lowest_candidate"]
        == pr["mpc"]["selected_lowest_candidate"]
        and mpc["selected_highest_candidate"]
        == pr["mpc"]["selected_highest_candidate"])
    ok_all = ok_all and same
    details.append(f"{exp}:{'OK' if same else 'DIFF'}")
check("S9 independent ref impls match production on all "
      "7 fixture expiries", ok_all, " ".join(details))

# ======================================================
# S10: mutation harness — run the REAL committed suite
# against mutant checker builds. Mutant files MUST live
# inside the repo or _checker_git_commit() returns None
# and test_provenance_fields confounds every run.
# ======================================================
MUTDIR = REPO / "devin_blackboard" / "_mutant_f6t2"
MUTDIR.mkdir(exist_ok=True)

MUTANTS = [
    ("agg_tol_1e-3",
     "AGG_REL_TOL = 1e-9", "AGG_REL_TOL = 1e-3"),
    ("flip_floor_1e-6",
     "MIN_MATERIAL_GEX = 1e-3", "MIN_MATERIAL_GEX = 1e-6"),
    ("drop_maxabs_early_ret",
     "    if max_abs < MIN_MATERIAL_GEX:\n"
     "        return None\n", ""),
    ("raw_total_lt0",
     "or total <= 0:", "or total < 0:"),
    ("pos_gt_2",
     "if pos >= MIN_PAYOUT_STRIKES:",
     "if pos > MIN_PAYOUT_STRIKES:"),
    ("flip_wrong_direction",
     "if material[i - 1][1] < 0 < material[i][1]",
     "if material[i - 1][1] > 0 > material[i][1]"),
    ("stale_iv_no_null_req",
     "                if iv is not None:\n"
     "                    findings.append(_finding(",
     "                if False:\n"
     "                    findings.append(_finding("),
    ("report_w_mode",
     'open(rp, "x", encoding="utf-8")###',
     'open(rp, "w", encoding="utf-8")'),
    ("tie_last_min",
     "if best_p is None or pain < best_p:",
     "if best_p is None or pain <= best_p:"),
    ("accept_any_reason",
     "            if dq not in UNAVAILABILITY_REASONS:",
     "            if False:"),
    ("checker_err_exit_1",
     "        return report, 2, rp\n\n    rp = (",
     "        return report, 1, rp\n\n    rp = ("),
    ("band_0.6",
     'CONSTRAINED_BAND = (0.7, 1.3)',
     'CONSTRAINED_BAND = (0.6, 1.4)'),
    ("fresh_ge",
     "elif age_h > max_age_hours:",
     "elif age_h >= max_age_hours:"),
    ("no_dup_strike_check",
     "            elif k in seen_k:\n"
     "                findings.append(_finding(\n"
     "                    ename, \"strike.strike\", k,\n"
     "                    \"duplicate strike key\"))",
     "            elif False:"),
    ("null_flag_means_avail",
     "    mu = e.get(\"metrics_unavailable\")\n"
     "    return mu if isinstance(mu, bool) else None",
     "    mu = e.get(\"metrics_unavailable\")\n"
     "    return mu if isinstance(mu, bool) else False"),
]

for name, old_s, new_s in MUTANTS:
    anchor = old_s.replace("###", "")
    if src.count(anchor) == 0:
        check(f"S10 mutant {name}: anchor found", False,
              f"count={src.count(anchor)}")
        continue
    mut = MUTDIR / f"wc_{name}.py"
    mut.write_text(src.replace(anchor, new_s),
                   encoding="utf-8")
    runner = TMP / f"run_{name}.py"
    runner.write_text(
        "import sys, importlib.util, pytest\n"
        f"sys.path.insert(0, r'{REPO}')\n"
        f"spec = importlib.util.spec_from_file_location("
        f"'WellnessCheck', r'{mut}')\n"
        "m = importlib.util.module_from_spec(spec)\n"
        "sys.modules['WellnessCheck'] = m\n"
        "spec.loader.exec_module(m)\n"
        f"rc = pytest.main([r'{REPO / 'tests' / 'test_wellness.py'}',"
        "'-q', '--tb=no', '-rf'])\n"
        "sys.exit(rc)\n",
        encoding="utf-8")
    pr = subprocess.run(
        [sys.executable, str(runner)],
        capture_output=True, text=True, cwd=TMP,
        timeout=300)
    failed = [l for l in pr.stdout.splitlines()
              if l.startswith("FAILED")]
    caught = pr.returncode != 0
    check(f"S10 mutant {name}: suite "
          f"{'CAUGHT' if caught else 'ESCAPED'}",
          True,  # informational; escapes are notes
          (f"{len(failed)} test(s) fail: "
           + "; ".join(f.split(' - ')[0].split('::')[-1]
                       for f in failed[:4])
           if caught else "0 failures"))

# ======================================================
# S11: scope/diff audit + provenance spot-check
# ======================================================
files = subprocess.run(
    ["git", "diff", "--name-only", "286c36f^..14e672f"],
    capture_output=True, text=True, cwd=REPO).stdout.split()
allowed = {"WellnessCheck.py", "tests/test_wellness.py"}
bad_files = [
    f for f in files
    if f not in allowed
    and not f.startswith("tests/fixtures/")
    and not f.startswith("devin_blackboard/")]
check("S11 F6-T1 diff touches only declared files",
      not bad_files, f"files={files}")

r, c, rp = run(PACKET, snapshot=SNAPSHOT)
prov = r["provenance"]
import hashlib
check("S11 provenance: packet sha256 of file bytes + "
      "snapshot hash + commit + counts",
      prov["packet_hash"] == hashlib.sha256(
          PACKET.read_bytes()).hexdigest()
      and prov["snapshot_hash"] is not None
      and prov["code_commit"]
      and prov["per_expiry_counts"]["2026-10-16"][
          "finite_oi_rows"] == 210,
      f"commit={prov['code_commit'][:8]}")

import shutil
shutil.rmtree(MUTDIR, ignore_errors=True)

print()
n_fail = sum(1 for _, ok, _ in RESULTS if not ok)
print(f"=== {len(RESULTS)} checks, {n_fail} FAIL ===")
sys.exit(1 if n_fail else 0)

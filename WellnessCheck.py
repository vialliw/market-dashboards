"""WellnessCheck.py — post-generation AI-packet validator.

Validates a GexDashboard ai_packet JSON AFTER generation,
before it is published or fed to the AI analysis. Runs a
fixed battery of checks (SCHEMA / NUMERIC / AVAIL / AGG /
MAXPAIN / FLIP / IV / SNAP / COVER / FRESH) and emits a
structured JSON report.

The metric checks use INDEPENDENT plain-Python reference
implementations of the documented production rules —
GexDashboard.py is deliberately never imported, so a bug in
the production code reproduces here as a FAIL rather than
being duplicated.

Usage:
    python WellnessCheck.py <packet.json> [--snapshot raw.pkl]
        [--report report.json] [--max-age-hours H]

Outcomes per check: PASS / WARN / FAIL / unperformed.
Overall: FAIL if any check fails, else WARN if any warns,
else PASS. 'unperformed' is emitted honestly whenever a
check's inputs are absent (e.g. no --snapshot, or fields
missing from the packet) — never silently skipped.

Exit codes: 0 PASS / 0 WARN / 1 FAIL / 2 checker-internal
error. A structured report is written on every run —
including FAIL runs — to --report or
validation/<packet>_report_<ts>.json; the report artifact
NEVER overwrites the input packet.
"""

import argparse
import hashlib
import json
import math
import numbers
import pickle
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

# =========================
# Independent copies of the documented production rules
# (kept in sync with meta.metric_definitions / data_quality
# emitted by the packet generator — these are the RULES the
# packet claims to follow, reimplemented from scratch)
# =========================

SUPPORTED_SCHEMA_VERSIONS = {"1.0"}

# gex materiality floor: material iff |gex| >=
# max(1% of slice max |gex|, MIN_MATERIAL_GEX)
MIN_MATERIAL_GEX = 1e-3
FLIP_RELATIVE_FRACTION = 0.01

# payout eligibility: >= MIN_PAYOUT_STRIKES distinct
# strikes carrying finite OI AND positive total finite OI
MIN_PAYOUT_STRIKES = 2

STALE_IV_THRESHOLD = 2.0
CONSTRAINED_BAND = (0.7, 1.3)

# aggregation tolerance: abs_err <=
# max(AGG_ABS_FLOOR, AGG_REL_TOL * max_magnitude)
AGG_ABS_FLOOR = 1e-9
AGG_REL_TOL = 1e-9

# raw-snapshot reconciliation tolerance (1e-6 rel per the
# approved plan, with an absolute floor for near-zero gex)
SNAP_REL_TOL = 1e-6
SNAP_ABS_FLOOR = 1e-12

DEFAULT_MAX_AGE_HOURS = 72.0
SPARSE_CHAIN_STRIKES = 10
MISSING_OI_WARN_SHARE = 0.5

UNAVAILABILITY_REASONS = {
    "no_finite_oi",
    "insufficient_data",
}

# The 7 payout-metric fields that are explicit nulls iff
# metrics_unavailable is true. gamma_flip is NOT among them
# — it is computed from the gex profile independently of
# OI eligibility (per-metric availability).
PAYOUT_FIELDS = (
    "max_pain",
    "max_pain_constrained",
    "max_pain_candidate_band",
    "max_pain_band_fell_back",
    "max_pain_band_boundary_hit",
    "max_pain_selected_lowest_candidate",
    "max_pain_selected_highest_candidate",
)

CHECK_SEVERITY = {
    "SCHEMA-01": "FAIL",
    "NUMERIC-01": "FAIL",
    "AVAIL-01a": "FAIL",
    "AVAIL-01b": "FAIL",
    "AVAIL-01c": "FAIL",
    "AGG-01": "FAIL",
    "MAXPAIN-01": "FAIL",
    "MAXPAIN-02": "FAIL",
    "FLIP-01": "FAIL",
    "IV-01": "FAIL",
    "SNAP-01": "FAIL",
    "COVER-01": "WARN",
    "FRESH-01": "WARN",
}


# =========================
# Small helpers
# =========================

def _num(x):
    """True for a real number — JSON int/float or a numpy
    scalar from a snapshot row (bool excluded)."""
    return isinstance(x, numbers.Real) and not isinstance(
        x, bool
    )


def _finite(x):
    return _num(x) and math.isfinite(x)


def _finding(expiry, field, observed, expected, **extra):
    f = {
        "expiry": expiry,
        "field": field,
        "observed": observed,
        "expected": expected,
    }
    f.update(extra)
    return f


def _result(check_id, status, detail, findings=None):
    return {
        "check_id": check_id,
        "status": status,
        "severity": CHECK_SEVERITY.get(check_id, "FAIL"),
        "detail": detail,
        "findings": findings or [],
    }


def _parse_iso(ts):
    """Parse an ISO-8601 timestamp string ('...Z' or with
    offset). Returns an aware datetime or None."""
    if not isinstance(ts, str):
        return None
    try:
        dt = datetime.fromisoformat(
            ts.replace("Z", "+00:00")
        )
    except ValueError:
        try:
            dt = datetime.strptime(
                ts, "%Y-%m-%dT%H:%M:%SZ"
            ).replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_expiry_date(s):
    if not isinstance(s, str):
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def _expiries(packet):
    ms = packet.get("market_snapshot")
    if not isinstance(ms, dict):
        return None
    exps = ms.get("expiries")
    if not isinstance(exps, list):
        return None
    return exps


def _strikes(expiry_obj):
    s = expiry_obj.get("strikes")
    return s if isinstance(s, list) else []


def _packet_spot(packet):
    ms = packet.get("market_snapshot")
    if not isinstance(ms, dict):
        return None
    spot = ms.get("spot")
    return spot if _finite(spot) else None


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _checker_git_commit():
    """HEAD commit of the repo containing THIS file, or
    None when not under version control."""
    here = Path(__file__).resolve().parent
    try:
        out = subprocess.run(
            ["git", "-C", str(here), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return None
    if out.returncode != 0:
        return None
    commit = out.stdout.strip()
    return commit or None


# =========================
# Independent reference implementations
# =========================

def _finite_oi_pairs(strike_rows):
    """(strike, call_oi, put_oi) rows usable for a payout
    sum: finite strike AND finite OI on each side."""
    usable = []
    for r in strike_rows:
        k = r.get("strike")
        if not _finite(k):
            continue
        co = r.get("call_oi")
        po = r.get("put_oi")
        usable.append((
            k,
            co if _finite(co) else 0.0,
            po if _finite(po) else 0.0,
        ))
    return usable


def _ref_payout_argmin(usable, candidates):
    """Independent canonical payout argmin: the candidate
    minimizing sum over rows of
    call_OI*max(s-K,0) + put_OI*max(K-s,0).
    First minimum in ascending order wins. Returns
    (argmin, ties_count) or (None, 0) on empty input."""
    if not candidates:
        return None, 0
    best_s = None
    best_p = None
    pains = []
    for s in candidates:
        pain = 0.0
        for k, co, po in usable:
            if s > k:
                pain += co * (s - k)
            elif k > s:
                pain += po * (k - s)
        pains.append(pain)
        if best_p is None or pain < best_p:
            best_p = pain
            best_s = s
    ties = sum(1 for p in pains if p == best_p)
    return best_s, ties


def _ref_max_pain(strike_rows):
    """Canonical unconstrained max pain."""
    usable = _finite_oi_pairs(strike_rows)
    candidates = sorted({k for k, _, _ in usable})
    return _ref_payout_argmin(usable, candidates)


def _ref_max_pain_constrained(strike_rows, spot):
    """Constrained payout minimum over the spot band.

    Payouts are computed over the FULL usable chain; only
    the settlement CANDIDATES are restricted to strikes
    inside [0.7*spot, 1.3*spot]. Empty band -> fell_back
    and all strikes are candidates. Returns the flag dict
    shape the packet emits."""
    usable = _finite_oi_pairs(strike_rows)
    all_strikes = sorted({k for k, _, _ in usable})
    result = {
        "max_pain": None,
        "candidate_band": None,
        "band_fell_back": False,
        "band_boundary_hit": False,
        "selected_lowest_candidate": False,
        "selected_highest_candidate": False,
        "ties": 0,
    }
    if not all_strikes or not _finite(spot):
        return result

    band = [CONSTRAINED_BAND[0] * spot,
            CONSTRAINED_BAND[1] * spot]
    result["candidate_band"] = band
    bounded = [s for s in all_strikes if band[0] <= s <= band[1]]
    if bounded:
        candidates = bounded
    else:
        candidates = all_strikes
        result["band_fell_back"] = True

    best, ties = _ref_payout_argmin(usable, candidates)
    result["max_pain"] = best
    result["ties"] = ties

    if not result["band_fell_back"]:
        result["selected_lowest_candidate"] = (
            best == candidates[0]
        )
        result["selected_highest_candidate"] = (
            best == candidates[-1]
        )
        result["band_boundary_hit"] = (
            best == band[0] or best == band[1]
        )
    return result


def _ref_gamma_flip(strike_gex_pairs, spot=None):
    """Independent strike-profile gamma-flip recompute:
    drop nonfinite strike/gex; group-sum by strike
    ascending; material iff |gex| >=
    max(0.01*max_abs, MIN_MATERIAL_GEX); whole-slice
    max_abs < floor -> None; crossings = consecutive
    material strikes where left<0<right; returned value =
    the UPPER (positive-side) strike, no interpolation;
    with spot -> crossing nearest spot, equidistant tie ->
    first (lower) crossing."""
    grouped = {}
    for k, g in strike_gex_pairs:
        if not (_finite(k) and _finite(g)):
            continue
        grouped[k] = grouped.get(k, 0.0) + g
    pairs = sorted(grouped.items())
    if not pairs:
        return None
    max_abs = max(abs(g) for _, g in pairs)
    if max_abs < MIN_MATERIAL_GEX:
        return None
    floor = max(
        FLIP_RELATIVE_FRACTION * max_abs,
        MIN_MATERIAL_GEX,
    )
    material = [(k, g) for k, g in pairs if abs(g) >= floor]
    crossings = [
        material[i][0]
        for i in range(1, len(material))
        if material[i - 1][1] < 0 < material[i][1]
    ]
    if not crossings:
        return None
    if not _finite(spot):
        return crossings[0]
    best = crossings[0]
    best_d = abs(best - spot)
    for c in crossings[1:]:
        d = abs(c - spot)
        if d < best_d:  # strict: first (lower) crossing wins ties
            best_d = d
            best = c
    return best


def _ref_payout_ineligibility_raw(rows):
    """Eligibility verdict from RAW rows
    (iterable of (strike, openInterest)):
    None if eligible, else 'no_finite_oi' /
    'insufficient_data' — the documented production rule."""
    finite = [
        (k, oi) for k, oi in rows
        if _finite(k) and _finite(oi)
    ]
    if not finite:
        return "no_finite_oi"
    distinct = {k for k, _ in finite}
    total = sum(oi for _, oi in finite)
    if len(distinct) < MIN_PAYOUT_STRIKES or total <= 0:
        return "insufficient_data"
    return None


def _raw_payout_rows(raw_rows):
    """Checker-format payout rows aggregated from raw
    snapshot rows: one row per strike carrying >=1
    finite-strike/finite-OI raw row — exactly the
    production candidate set. All-NaN-OI strikes are
    dropped entirely while a single finite-OI row (of
    either type) keeps its strike eligible."""
    acc = {}
    for r in raw_rows:
        k, oi = r.get("strike"), r.get("openInterest")
        if not (_finite(k) and _finite(oi)):
            continue
        a = acc.setdefault(
            k, {"strike": k, "call_oi": 0.0, "put_oi": 0.0})
        if r.get("type") == "call":
            a["call_oi"] += oi
        else:
            a["put_oi"] += oi
    return list(acc.values())


def _ambiguous_exported_strike(s):
    """An exported strike with no positive OI on either
    side is ambiguous: exported 0 conflates a true-zero
    raw row (a real payout candidate) with a missing-NaN
    raw row (excluded from the candidate set entirely)."""
    co, po = s.get("call_oi"), s.get("put_oi")
    return not (
        (_num(co) and co > 0) or (_num(po) and po > 0)
    )


# =========================
# Checks
# =========================

def check_schema(packet):
    """SCHEMA-01: required fields/types; unique expiry
    keys; unique strike keys per expiry; schema_version in
    {1.0}."""
    findings = []

    meta = packet.get("meta")
    if not isinstance(meta, dict):
        findings.append(_finding(
            None, "meta", type(meta).__name__,
            "required object"))
    else:
        if not isinstance(meta.get("ticker"), str):
            findings.append(_finding(
                None, "meta.ticker", meta.get("ticker"),
                "required string"))
        if not isinstance(meta.get("generated_utc"), str):
            findings.append(_finding(
                None, "meta.generated_utc",
                meta.get("generated_utc"), "required string"))
        sv = meta.get("schema_version")
        if not isinstance(sv, str):
            findings.append(_finding(
                None, "meta.schema_version", sv,
                "required string"))
        elif sv not in SUPPORTED_SCHEMA_VERSIONS:
            findings.append(_finding(
                None, "meta.schema_version", sv,
                f"one of {sorted(SUPPORTED_SCHEMA_VERSIONS)}"))

    ms = packet.get("market_snapshot")
    if not isinstance(ms, dict):
        findings.append(_finding(
            None, "market_snapshot",
            type(ms).__name__, "required object"))
        return _result(
            "SCHEMA-01",
            "FAIL" if findings else "unperformed",
            "market_snapshot absent; "
            f"{len(findings)} finding(s)",
            findings,
        )

    if not _num(ms.get("spot")):
        findings.append(_finding(
            None, "market_snapshot.spot",
            ms.get("spot"), "required number"))
    if not _num(ms.get("total_net_gex")):
        findings.append(_finding(
            None, "market_snapshot.total_net_gex",
            ms.get("total_net_gex"), "required number"))
    if (
        "nearest_gamma_flip" in ms
        and ms["nearest_gamma_flip"] is not None
        and not _num(ms["nearest_gamma_flip"])
    ):
        findings.append(_finding(
            None, "market_snapshot.nearest_gamma_flip",
            ms["nearest_gamma_flip"], "number or null"))

    exps = ms.get("expiries")
    if not isinstance(exps, list):
        findings.append(_finding(
            None, "market_snapshot.expiries",
            type(exps).__name__, "required list"))
        return _result(
            "SCHEMA-01", "FAIL",
            f"{len(findings)} finding(s)", findings)

    seen_exp = set()
    for e in exps:
        if not isinstance(e, dict):
            findings.append(_finding(
                None, "expiry", type(e).__name__,
                "required object"))
            continue
        exp = e.get("expiry")
        ename = exp if isinstance(exp, str) else repr(exp)
        if not isinstance(exp, str):
            findings.append(_finding(
                None, "expiry.expiry", exp,
                "required ISO date string"))
        elif exp in seen_exp:
            findings.append(_finding(
                exp, "expiry.expiry", exp,
                "duplicate expiry key"))
        else:
            seen_exp.add(exp)

        if not _num(e.get("net_gex")):
            findings.append(_finding(
                ename, "net_gex", e.get("net_gex"),
                "required number"))

        mu = e.get("metrics_unavailable")
        if not isinstance(mu, bool):
            findings.append(_finding(
                ename, "metrics_unavailable", mu,
                "required bool"))

        for f in PAYOUT_FIELDS:
            if f not in e:
                findings.append(_finding(
                    ename, f, "<absent>",
                    "required field (null allowed)"))
        for f in (
            "gamma_flip", "atm_iv",
        ):
            if f not in e:
                findings.append(_finding(
                    ename, f, "<absent>",
                    "required field (null allowed)"))
            elif e[f] is not None and not _num(e[f]):
                findings.append(_finding(
                    ename, f, e[f], "number or null"))
        for f in ("max_pain", "max_pain_constrained"):
            if (
                f in e and e[f] is not None
                and not _num(e[f])
            ):
                findings.append(_finding(
                    ename, f, e[f], "number or null"))
        band = e.get("max_pain_candidate_band")
        if (
            band is not None
            and not (
                isinstance(band, list)
                and len(band) == 2
                and all(_num(b) for b in band)
            )
        ):
            findings.append(_finding(
                ename, "max_pain_candidate_band", band,
                "[lo, hi] numbers or null"))
        for f in (
            "max_pain_band_fell_back",
            "max_pain_band_boundary_hit",
            "max_pain_selected_lowest_candidate",
            "max_pain_selected_highest_candidate",
        ):
            if (
                f in e and e[f] is not None
                and not isinstance(e[f], bool)
            ):
                findings.append(_finding(
                    ename, f, e[f], "bool or null"))

        for w in ("call_walls", "put_walls"):
            walls = e.get(w)
            if not isinstance(walls, list):
                findings.append(_finding(
                    ename, w, type(walls).__name__,
                    "required list"))
            else:
                for i, item in enumerate(walls):
                    if (
                        not isinstance(item, dict)
                        or not _num(item.get("strike"))
                        or not _num(item.get("oi"))
                    ):
                        findings.append(_finding(
                            ename, f"{w}[{i}]", item,
                            "{strike, oi} numbers"))

        strikes = e.get("strikes")
        if not isinstance(strikes, list):
            findings.append(_finding(
                ename, "strikes", type(strikes).__name__,
                "required list"))
            continue
        seen_k = set()
        for s in strikes:
            if not isinstance(s, dict):
                findings.append(_finding(
                    ename, "strike", type(s).__name__,
                    "required object"))
                continue
            k = s.get("strike")
            if not _num(k):
                findings.append(_finding(
                    ename, "strike.strike", k,
                    "required number"))
            elif k in seen_k:
                findings.append(_finding(
                    ename, "strike.strike", k,
                    "duplicate strike key"))
            else:
                seen_k.add(k)
            for f in (
                "open_interest", "call_oi", "put_oi",
                "net_gex", "call_gex", "put_gex",
                "distance_from_spot_pct",
            ):
                if not _num(s.get(f)):
                    findings.append(_finding(
                        ename, f"strike[{k}].{f}",
                        s.get(f), "required number"))
            iv = s.get("implied_volatility", "<absent>")
            if iv == "<absent>":
                findings.append(_finding(
                    ename,
                    f"strike[{k}].implied_volatility",
                    iv, "required field (null allowed)"))
            elif iv is not None and not _num(iv):
                findings.append(_finding(
                    ename,
                    f"strike[{k}].implied_volatility",
                    iv, "number or null"))
            for f in (
                "is_call_wall", "is_put_wall",
                "is_gamma_flip",
            ):
                if not isinstance(s.get(f), bool):
                    findings.append(_finding(
                        ename, f"strike[{k}].{f}",
                        s.get(f), "required bool"))
            if "stale_iv" in s and s["stale_iv"] is not True:
                findings.append(_finding(
                    ename, f"strike[{k}].stale_iv",
                    s["stale_iv"], "true or absent"))
            if (
                "raw_implied_volatility" in s
                and not _num(s["raw_implied_volatility"])
            ):
                findings.append(_finding(
                    ename,
                    f"strike[{k}].raw_implied_volatility",
                    s["raw_implied_volatility"],
                    "number"))

    status = "FAIL" if findings else "PASS"
    return _result(
        "SCHEMA-01", status,
        f"{len(findings)} schema violation(s)"
        if findings else
        "required fields/types present; expiry and strike "
        "keys unique; schema_version supported",
        findings,
    )


def check_numeric(packet):
    """NUMERIC-01: no NaN/Inf tokens in emitted numerics;
    spot>0; all OI>=0; ISO expiry dates."""
    findings = []

    def scan(obj, path):
        if isinstance(obj, float) and not math.isfinite(obj):
            findings.append(_finding(
                None, path, repr(obj),
                "finite number (NaN/Inf token emitted)"))
        elif isinstance(obj, dict):
            for kk, vv in obj.items():
                scan(vv, f"{path}.{kk}")
        elif isinstance(obj, list):
            for i, vv in enumerate(obj):
                scan(vv, f"{path}[{i}]")

    scan(packet, "$")

    ms = packet.get("market_snapshot")
    if isinstance(ms, dict):
        spot = ms.get("spot")
        if _num(spot) and not spot > 0:
            findings.append(_finding(
                None, "market_snapshot.spot", spot,
                "> 0"))
        exps = ms.get("expiries")
        if isinstance(exps, list):
            for e in exps:
                if not isinstance(e, dict):
                    continue
                exp = e.get("expiry")
                if isinstance(exp, str) and (
                    _parse_expiry_date(exp) is None
                ):
                    findings.append(_finding(
                        exp, "expiry", exp,
                        "ISO %Y-%m-%d date"))
                for s in _strikes(e):
                    if not isinstance(s, dict):
                        continue
                    for f in (
                        "open_interest", "call_oi",
                        "put_oi",
                    ):
                        v = s.get(f)
                        if _num(v) and v < 0:
                            findings.append(_finding(
                                exp, f"strike[{s.get('strike')}].{f}",
                                v, ">= 0"))

    meta = packet.get("meta")
    if isinstance(meta, dict):
        g = meta.get("generated_utc")
        if isinstance(g, str) and _parse_iso(g) is None:
            findings.append(_finding(
                None, "meta.generated_utc", g,
                "ISO-8601 timestamp"))

    status = "FAIL" if findings else "PASS"
    return _result(
        "NUMERIC-01", status,
        f"{len(findings)} numeric violation(s)"
        if findings else
        "all emitted numerics finite; spot>0; OI>=0; "
        "dates ISO",
        findings,
    )


def _avail_flag(e):
    """metrics_unavailable if it is a real bool, else a
    sentinel meaning 'cannot evaluate this expiry'."""
    mu = e.get("metrics_unavailable")
    return mu if isinstance(mu, bool) else None


def check_avail_a(packet):
    """AVAIL-01a: flag/null consistency — unavailable iff
    all 7 payout fields null + valid data_quality reason;
    available iff payout fields non-null."""
    findings = []
    evaluated = 0
    skipped = 0
    for e in _expiries(packet) or []:
        if not isinstance(e, dict):
            continue
        exp = e.get("expiry")
        mu = _avail_flag(e)
        if mu is None:
            skipped += 1
            continue
        evaluated += 1
        if mu:
            for f in PAYOUT_FIELDS:
                if e.get(f) is not None:
                    findings.append(_finding(
                        exp, f, e.get(f),
                        "null while metrics_unavailable"))
            dq = e.get("data_quality")
            if dq not in UNAVAILABILITY_REASONS:
                findings.append(_finding(
                    exp, "data_quality", dq,
                    f"one of {sorted(UNAVAILABILITY_REASONS)} "
                    "while metrics_unavailable"))
        else:
            for f in PAYOUT_FIELDS:
                if e.get(f) is None:
                    findings.append(_finding(
                        exp, f, None,
                        "non-null while metrics available"))
            if "data_quality" in e:
                findings.append(_finding(
                    exp, "data_quality",
                    e.get("data_quality"),
                    "absent on an available expiry"))

    if evaluated == 0:
        return _result(
            "AVAIL-01a", "unperformed",
            "no expiry carried a bool metrics_unavailable "
            "flag")
    status = "FAIL" if findings else "PASS"
    detail = (
        f"{len(findings)} flag/null inconsistency(ies)"
        if findings else
        f"flag/null contract holds on {evaluated} "
        "expiry(ies)")
    if skipped:
        detail += f"; {skipped} expiry(ies) skipped (flag absent)"
    return _result("AVAIL-01a", status, detail, findings)


def check_avail_b(packet, raw_index):
    """AVAIL-01b: independent eligibility recompute.

    Packet-only directionality: >=2 strikes with exported
    OI>0 UNAMBIGUOUSLY proves eligible (positive OI can
    never collapse to 0 on export); the ineligible
    direction is ambiguous — exported OI=0 conflates
    true-zero with missing-NaN — so the flag verdict is
    'unperformed' whenever <2 positive-OI strikes, and the
    reason-code check is always 'unperformed' without a
    snapshot. With --snapshot the raw rows give exact
    eligibility + reason; any mismatch is a FAIL."""
    findings = []
    notes = []
    verdicts = 0
    unperformed = 0

    for e in _expiries(packet) or []:
        if not isinstance(e, dict):
            continue
        exp = e.get("expiry")
        mu = _avail_flag(e)
        if mu is None:
            notes.append(f"{exp}: flag absent — skipped")
            continue

        if raw_index is not None:
            rows = raw_index.oi_rows(exp)
            if rows is None:
                # expiry absent from the snapshot: zero
                # usable rows -> 'no_finite_oi'
                reason = "no_finite_oi"
            else:
                reason = _ref_payout_ineligibility_raw(rows)
            expected_unavail = reason is not None
            if expected_unavail != mu:
                findings.append(_finding(
                    exp, "metrics_unavailable", mu,
                    f"{expected_unavail} — raw rows give "
                    f"'{reason or 'eligible'}'"))
            elif expected_unavail:
                dq = e.get("data_quality")
                if dq != reason:
                    findings.append(_finding(
                        exp, "data_quality", dq,
                        f"'{reason}' per raw rows"))
            verdicts += 1
            continue

        pos = 0
        for s in _strikes(e):
            if not isinstance(s, dict):
                continue
            co = s.get("call_oi")
            po = s.get("put_oi")
            if (
                (_num(co) and co > 0)
                or (_num(po) and po > 0)
            ):
                pos += 1

        if pos >= MIN_PAYOUT_STRIKES:
            # >=2 exported positive-OI strikes proves the
            # chain was eligible: positive OI can never
            # collapse to 0 on export.
            if mu:
                findings.append(_finding(
                    exp, "metrics_unavailable", True,
                    "false — >=2 exported positive-OI "
                    "strikes prove eligibility"))
            verdicts += 1
        else:
            unperformed += 1
            notes.append(
                f"{exp}: verdict unperformed "
                "(ambiguous without raw rows — "
                f"only {pos} exported positive-OI "
                "strike(s)); reason-code check "
                "unperformed (no snapshot)")

    if verdicts == 0:
        status = "unperformed"
    else:
        status = "FAIL" if findings else "PASS"
    parts = []
    if findings:
        parts.append(f"{len(findings)} eligibility mismatch(es)")
    elif verdicts:
        parts.append(
            f"eligibility verdict verified on {verdicts} expiry(ies)")
    if unperformed:
        parts.append(
            f"{unperformed} expiry(ies) ambiguous without raw rows")
    if raw_index is None:
        parts.append("reason-code check unperformed (no snapshot)")
    if not parts:
        parts.append("no evaluable expiries")
    detail = "; ".join(parts)
    if notes:
        detail += " | " + " | ".join(notes)
    return _result("AVAIL-01b", status, detail, findings)


def check_avail_c(packet):
    """AVAIL-01c: separate nullability — payout metrics
    null ONLY under unavailability; gamma_flip is nullable
    on EITHER side of the flag (independent metric)."""
    findings = []
    evaluated = 0
    flip_null_avail = 0
    flip_real_unavail = 0
    for e in _expiries(packet) or []:
        if not isinstance(e, dict):
            continue
        exp = e.get("expiry")
        mu = _avail_flag(e)
        if mu is None:
            continue
        evaluated += 1
        nulls = [f for f in PAYOUT_FIELDS if e.get(f) is None]
        if mu and len(nulls) != len(PAYOUT_FIELDS):
            nonnull = [
                f for f in PAYOUT_FIELDS
                if e.get(f) is not None
            ]
            findings.append(_finding(
                exp, "payout_fields", nonnull,
                "all null while metrics_unavailable"))
        elif not mu and nulls:
            findings.append(_finding(
                exp, "payout_fields", nulls,
                "non-null while metrics available — "
                "payout metrics null only under "
                "unavailability"))
        gf = e.get("gamma_flip")
        if mu and gf is not None:
            flip_real_unavail += 1
        if not mu and gf is None:
            flip_null_avail += 1

    if evaluated == 0:
        return _result(
            "AVAIL-01c", "unperformed",
            "no expiry carried a bool metrics_unavailable flag")
    status = "FAIL" if findings else "PASS"
    detail = (
        f"{len(findings)} payout-nullability violation(s); "
        if findings else
        "payout metrics null only under unavailability on "
        f"{evaluated} expiry(ies); "
    )
    detail += (
        f"gamma_flip independence respected "
        f"({flip_null_avail} null while available, "
        f"{flip_real_unavail} real while unavailable)")
    return _result("AVAIL-01c", status, detail, findings)


def check_agg(packet):
    """AGG-01: per-expiry sum(strike net_gex) ~=
    expiry net_gex; sum(expiry net_gex) ~= total_net_gex;
    strike net_gex ~= call_gex+put_gex. Tolerance
    abs_err <= max(1e-9, 1e-9*max_magnitude)."""
    findings = []
    compared = 0

    def tol(*vals):
        m = max(abs(v) for v in vals) if vals else 0.0
        return max(AGG_ABS_FLOOR, AGG_REL_TOL * m)

    exps = _expiries(packet)
    if exps is None:
        return _result(
            "AGG-01", "unperformed",
            "market_snapshot.expiries absent")

    exp_sum_terms = []
    for e in exps:
        if not isinstance(e, dict):
            continue
        exp = e.get("expiry")
        e_net = e.get("net_gex")
        terms = [
            s.get("net_gex")
            for s in _strikes(e)
            if isinstance(s, dict)
        ]
        if all(_finite(t) for t in terms) and _finite(e_net):
            err = abs(sum(terms) - e_net)
            compared += 1
            if err > tol(*terms, e_net):
                findings.append(_finding(
                    exp, "net_gex", e_net,
                    f"sum of strike net_gex "
                    f"({sum(terms)}) within "
                    f"{tol(*terms, e_net):.3e}"))
        for s in _strikes(e):
            if not isinstance(s, dict):
                continue
            n, c, p = (
                s.get("net_gex"),
                s.get("call_gex"),
                s.get("put_gex"),
            )
            if _finite(n) and _finite(c) and _finite(p):
                err = abs(c + p - n)
                if err > tol(n, c, p):
                    findings.append(_finding(
                        exp, f"strike[{s.get('strike')}].net_gex",
                        n, f"call_gex+put_gex ({c + p}) "
                        f"within {tol(n, c, p):.3e}"))
        if _finite(e_net):
            exp_sum_terms.append(e_net)

    total = (
        packet.get("market_snapshot", {}) or {}
    ).get("total_net_gex")
    if _finite(total) and exp_sum_terms:
        err = abs(sum(exp_sum_terms) - total)
        compared += 1
        if err > tol(*exp_sum_terms, total):
            findings.append(_finding(
                None, "total_net_gex", total,
                f"sum of expiry net_gex "
                f"({sum(exp_sum_terms)}) within "
                f"{tol(*exp_sum_terms, total):.3e}"))

    if compared == 0:
        return _result(
            "AGG-01", "unperformed",
            "no finite aggregation terms to compare")
    status = "FAIL" if findings else "PASS"
    return _result(
        "AGG-01", status,
        f"{len(findings)} aggregation mismatch(es)"
        if findings else
        f"strike->expiry->book gex sums reconcile "
        f"({compared} sums checked)",
        findings,
    )


def check_max_pain(packet, raw_index=None):
    """MAXPAIN-01: independent canonical recompute —
    packet max_pain must equal the unconstrained payout
    argmin on the exported rows (exact ==; ties counted).

    Candidate-set ambiguity (same doctrine as
    MAXPAIN-02): exported OI=0 conflates a true-zero raw
    row (a real payout candidate) with a missing-NaN raw
    row (excluded from the candidate set entirely — see
    AVAIL-01b). An exported-0 strike that ties the
    minimum payout BELOW the finite-OI minimizer wins the
    ascending-first argmin — flipping the expected value
    (e.g. calls at 90/100/110 with raw OI NaN/1/1 export
    as 0/1/1: production argmin is 100, but the exported
    strike set alone yields 90). Without --snapshot an
    expiry whose argmin differs between the extreme
    hypotheses (all exported-0 strikes real vs all
    dropped) is honestly 'unperformed' — never a FAIL on
    a possibly-honest packet. Identical endpoint argmins
    are ambiguity-proof: ambiguous strikes carry zero OI,
    so they add only zero-pain terms and an argmin below
    the unambiguous minimizer under ANY ambiguous-subset
    removal would already win under the all-included
    hypothesis (the reported tie count is annotation
    only). With --snapshot the reconciled raw finite-OI
    rows are exactly the production candidate set and a
    true mismatch is a FAIL."""
    findings = []
    evaluated = 0
    skipped = 0
    ambiguous = []
    tie_notes = []
    for e in _expiries(packet) or []:
        if not isinstance(e, dict):
            continue
        exp = e.get("expiry")
        mu = _avail_flag(e)
        if mu is not False:
            skipped += 1
            continue

        if (
            raw_index is not None
            and raw_index.oi_rows(exp) is not None
        ):
            # reconciled: raw finite-OI rows are exactly
            # production's usable/candidate rows
            expected, ties = _ref_max_pain(
                _raw_payout_rows(raw_index.rows(exp)))
        else:
            strikes = _strikes(e)
            expected, ties = _ref_max_pain(strikes)
            unambig = [
                s for s in strikes
                if isinstance(s, dict)
                and not _ambiguous_exported_strike(s)
            ]
            if len(unambig) != len(strikes):
                expected0, _ = _ref_max_pain(unambig)
                if expected != expected0:
                    # verdict hinges on exported-0 strikes
                    # that may have been missing-NaN —
                    # cannot honestly PASS or FAIL
                    ambiguous.append(exp)
                    continue

        evaluated += 1
        got = e.get("max_pain")
        if ties > 1:
            tie_notes.append(f"{exp}: {ties}-way argmin tie")
        if expected is None or got != expected:
            findings.append(_finding(
                exp, "max_pain", got,
                f"independent argmin {expected}",
                ties=ties))

    if evaluated == 0 and not ambiguous:
        return _result(
            "MAXPAIN-01", "unperformed",
            "no available expiry to recompute")
    if findings:
        status = "FAIL"
    elif ambiguous:
        status = "unperformed"
    else:
        status = "PASS"
    detail = (
        f"{len(findings)} max_pain mismatch(es)"
        if findings else
        f"canonical argmin matches on {evaluated} "
        "expiry(ies)")
    if ambiguous:
        detail += (
            f"; {len(ambiguous)} expiry(ies) "
            "unverifiable — verdict depends on exported-0 "
            "strikes that may have been missing-NaN "
            f"({', '.join(str(x) for x in ambiguous)}; "
            "reconcile via --snapshot)")
    if skipped:
        detail += f"; {skipped} unavailable expiry(ies) skipped"
    if tie_notes:
        detail += " | " + " | ".join(tie_notes)
    return _result("MAXPAIN-01", status, detail, findings)


def check_max_pain_constrained(packet, raw_index=None):
    """MAXPAIN-02: constrained recompute — payouts over
    the full usable chain, candidates restricted to
    [0.7*spot, 1.3*spot]; all band flags must match.

    Candidate-set ambiguity: exported OI=0 conflates a
    true-zero raw row (a real production candidate) with
    a missing-NaN raw row (excluded from the candidate
    set entirely — see AVAIL-01b). Without --snapshot the
    packet cannot distinguish the two, so an expiry whose
    verdict DIFFERS between the extreme hypotheses (all
    exported-0 strikes real vs all dropped) is honestly
    'unperformed' — never a FAIL on a possibly-honest
    packet. If the verdict is identical either way it is
    ambiguity-proof and verified normally. With
    --snapshot the raw finite-OI rows decide exactly
    which strikes were candidates and any true mismatch
    is a FAIL."""
    findings = []
    evaluated = 0
    skipped = 0
    ambiguous = []
    spot = _packet_spot(packet)
    flag_map = (
        ("max_pain_band_fell_back", "band_fell_back"),
        ("max_pain_band_boundary_hit", "band_boundary_hit"),
        ("max_pain_selected_lowest_candidate",
         "selected_lowest_candidate"),
        ("max_pain_selected_highest_candidate",
         "selected_highest_candidate"),
    )
    verdict_keys = (
        "max_pain", "band_fell_back", "band_boundary_hit",
        "selected_lowest_candidate",
        "selected_highest_candidate",
    )
    for e in _expiries(packet) or []:
        if not isinstance(e, dict):
            continue
        exp = e.get("expiry")
        mu = _avail_flag(e)
        if mu is not False or spot is None:
            skipped += 1
            continue

        if (
            raw_index is not None
            and raw_index.oi_rows(exp) is not None
        ):
            # reconciled: raw finite-OI rows are exactly
            # production's usable/candidate rows
            ref = _ref_max_pain_constrained(
                _raw_payout_rows(raw_index.rows(exp)), spot)
        else:
            strikes = _strikes(e)
            ref = _ref_max_pain_constrained(strikes, spot)
            unambig = [
                s for s in strikes
                if isinstance(s, dict)
                and not _ambiguous_exported_strike(s)
            ]
            if len(unambig) != len(strikes):
                ref0 = _ref_max_pain_constrained(unambig, spot)
                if any(
                    ref[k] != ref0[k] for k in verdict_keys
                ):
                    # verdict hinges on exported-0 strikes
                    # that may have been missing-NaN —
                    # cannot honestly PASS or FAIL
                    ambiguous.append(exp)
                    continue

        evaluated += 1
        if e.get("max_pain_constrained") != ref["max_pain"]:
            findings.append(_finding(
                exp, "max_pain_constrained",
                e.get("max_pain_constrained"),
                ref["max_pain"]))
        if e.get("max_pain_candidate_band") != (
            ref["candidate_band"]
        ):
            findings.append(_finding(
                exp, "max_pain_candidate_band",
                e.get("max_pain_candidate_band"),
                ref["candidate_band"]))
        for pf, rf in flag_map:
            if e.get(pf) != ref[rf]:
                findings.append(_finding(
                    exp, pf, e.get(pf), ref[rf]))

    if evaluated == 0 and not ambiguous:
        return _result(
            "MAXPAIN-02", "unperformed",
            "no available expiry with finite spot")
    if findings:
        status = "FAIL"
    elif ambiguous:
        status = "unperformed"
    else:
        status = "PASS"
    detail = (
        f"{len(findings)} constrained-field mismatch(es)"
        if findings else
        f"constrained argmin + band flags match on "
        f"{evaluated} expiry(ies)")
    if ambiguous:
        detail += (
            f"; {len(ambiguous)} expiry(ies) "
            "unverifiable — verdict depends on exported-0 "
            "strikes that may have been missing-NaN "
            f"({', '.join(str(x) for x in ambiguous)}; "
            "reconcile via --snapshot)")
    if skipped:
        detail += f"; {skipped} expiry(ies) skipped"
    return _result("MAXPAIN-02", status, detail, findings)


def check_flip(packet):
    """FLIP-01: independent recompute of the material
    neg->pos crossing rule (upper strike returned, no
    interpolation; nearest-to-spot, lower wins ties);
    per-expiry gamma_flip and whole-book
    nearest_gamma_flip must match exactly or both be null."""
    findings = []
    evaluated = 0
    skipped = 0
    spot = _packet_spot(packet)

    book_pairs = []
    for e in _expiries(packet) or []:
        if not isinstance(e, dict):
            continue
        exp = e.get("expiry")
        pairs = [
            (s.get("strike"), s.get("net_gex"))
            for s in _strikes(e)
            if isinstance(s, dict)
        ]
        book_pairs.extend(pairs)
        if "gamma_flip" not in e:
            skipped += 1
            continue
        evaluated += 1
        expected = _ref_gamma_flip(pairs, spot)
        got = e.get("gamma_flip")
        if got != expected:
            findings.append(_finding(
                exp, "gamma_flip", got,
                f"independent recompute {expected}"))

    ms = packet.get("market_snapshot")
    if isinstance(ms, dict) and "nearest_gamma_flip" in ms:
        expected = _ref_gamma_flip(book_pairs, spot)
        got = ms["nearest_gamma_flip"]
        evaluated += 1
        if got != expected:
            findings.append(_finding(
                None, "nearest_gamma_flip", got,
                f"independent recompute {expected}"))

    if evaluated == 0:
        return _result(
            "FLIP-01", "unperformed",
            "no gamma_flip fields present")
    status = "FAIL" if findings else "PASS"
    return _result(
        "FLIP-01", status,
        f"{len(findings)} flip mismatch(es)"
        if findings else
        f"material crossings match on {evaluated} "
        f"field(s){'; ' + str(skipped) + ' skipped' if skipped else ''}",
        findings,
    )


def check_iv(packet):
    """IV-01: stale_iv:true iff raw_implied_volatility>2.0
    AND implied_volatility:null; no strike carries an
    unflagged IV>2.0."""
    findings = []
    evaluated = 0
    flagged = 0
    for e in _expiries(packet) or []:
        if not isinstance(e, dict):
            continue
        exp = e.get("expiry")
        for s in _strikes(e):
            if not isinstance(s, dict):
                continue
            evaluated += 1
            k = s.get("strike")
            iv = s.get("implied_volatility")
            raw = s.get("raw_implied_volatility")
            stale = s.get("stale_iv") is True
            if stale:
                flagged += 1
                if iv is not None:
                    findings.append(_finding(
                        exp,
                        f"strike[{k}].implied_volatility",
                        iv, "null while stale_iv"))
                if not (_num(raw) and raw > STALE_IV_THRESHOLD):
                    findings.append(_finding(
                        exp,
                        f"strike[{k}].raw_implied_volatility",
                        raw, f"> {STALE_IV_THRESHOLD} "
                        "while stale_iv"))
            else:
                if _num(iv) and iv > STALE_IV_THRESHOLD:
                    findings.append(_finding(
                        exp,
                        f"strike[{k}].implied_volatility",
                        iv, f"unflagged IV > "
                        f"{STALE_IV_THRESHOLD}"))
                if "raw_implied_volatility" in s:
                    findings.append(_finding(
                        exp,
                        f"strike[{k}].raw_implied_volatility",
                        raw, "absent without stale_iv"))

    if evaluated == 0:
        return _result(
            "IV-01", "unperformed", "no strike rows")
    status = "FAIL" if findings else "PASS"
    return _result(
        "IV-01", status,
        f"{len(findings)} IV-provenance violation(s)"
        if findings else
        f"stale-IV contract holds on {evaluated} "
        f"strike(s) ({flagged} flagged)",
        findings,
    )


# =========================
# Snapshot handling (SNAP-01)
# =========================

class RawSnapshotIndex:
    """Indexed raw option rows for reconciliation.

    rows: list of dicts with expiry(date), strike, type,
    openInterest, optional gex, optional
    impliedVolatility."""

    def __init__(self, df):
        self.has_gex = "gex" in df.columns
        self.has_iv = "impliedVolatility" in df.columns
        self.by_expiry = {}
        self.other_type_rows = 0
        self.bad_rows = 0
        for _, r in df.iterrows():
            exp = r.get("expiry")
            try:
                exp_date = pd.Timestamp(exp).date()
            except Exception:
                self.bad_rows += 1
                continue
            t = r.get("type")
            rec = {
                "strike": r.get("strike"),
                "type": t,
                "openInterest": r.get("openInterest"),
                "gex": r.get("gex") if self.has_gex else None,
                "iv": (
                    r.get("impliedVolatility")
                    if self.has_iv else None
                ),
            }
            if t not in ("call", "put"):
                self.other_type_rows += 1
            self.by_expiry.setdefault(
                exp_date.isoformat(), []).append(rec)

    def rows(self, expiry_str):
        return self.by_expiry.get(expiry_str, [])

    def oi_rows(self, expiry_str):
        """(strike, openInterest) pairs for eligibility —
        None when the expiry is absent entirely."""
        rs = self.by_expiry.get(expiry_str)
        if rs is None:
            return None
        return [
            (r["strike"], r["openInterest"]) for r in rs
        ]


def _load_snapshot(path):
    """Load a raw snapshot pickle -> DataFrame or raise.
    Pickles are DataFrames saved at packet-generation
    time."""
    with open(path, "rb") as f:
        obj = pickle.load(f)
    if not isinstance(obj, pd.DataFrame):
        raise TypeError(
            f"snapshot is {type(obj).__name__}, "
            "not a DataFrame")
    required = {"expiry", "strike", "type", "openInterest"}
    missing = required - set(obj.columns)
    if missing:
        raise ValueError(
            f"snapshot missing columns {sorted(missing)}")
    return obj


def check_snapshot(packet, snapshot_path):
    """SNAP-01: reconcile raw snapshot rows to exported
    strikes by (expiry, strike, type). Permitted export
    transforms: NaN->0 collapse of OI, NaN->null IV; any
    other OI/gex mismatch beyond 1e-6 rel fails. Without
    --snapshot: unperformed (never silently skipped).
    Returns (result, counts, raw_index)."""
    counts = {}
    if snapshot_path is None:
        for e in _expiries(packet) or []:
            if not isinstance(e, dict):
                continue
            counts[e.get("expiry")] = {
                "strikes": len(_strikes(e)),
                "finite_oi_rows": None,
                "excluded_rows": None,
                "stale_iv_rows": sum(
                    1 for s in _strikes(e)
                    if isinstance(s, dict)
                    and s.get("stale_iv") is True
                ),
                "metrics_unavailable": (
                    e.get("metrics_unavailable")
                    if isinstance(
                        e.get("metrics_unavailable"), bool)
                    else None
                ),
            }
        return _result(
            "SNAP-01", "unperformed",
            "no snapshot: raw-row reconciliation and "
            "OI-missingness provenance not performed"
        ), counts, None

    try:
        df = _load_snapshot(snapshot_path)
    except Exception as exc:
        return _result(
            "SNAP-01", "FAIL",
            f"snapshot cannot be reconciled: {exc!r}",
            [_finding(None, "snapshot",
                      repr(exc), "loadable raw rows")],
        ), counts, None

    idx = RawSnapshotIndex(df)
    findings = []
    if idx.other_type_rows:
        findings.append(_finding(
            None, "type",
            f"{idx.other_type_rows} row(s)",
            "only 'call'/'put' rows are reconcilable"))
    if idx.bad_rows:
        findings.append(_finding(
            None, "expiry",
            f"{idx.bad_rows} row(s)",
            "parseable expiry on every raw row"))

    # snapshot expiries absent from the packet are
    # unreconciled raw rows — never silently ignored
    pkt_exp_names = {
        e.get("expiry") for e in _expiries(packet) or []
        if isinstance(e, dict)
    }
    for raw_exp in sorted(set(idx.by_expiry) - pkt_exp_names):
        findings.append(_finding(
            raw_exp, "expiry",
            f"{len(idx.by_expiry[raw_exp])} raw row(s) "
            "in snapshot only",
            "expiry present in packet"))

    for e in _expiries(packet) or []:
        if not isinstance(e, dict):
            continue
        exp = e.get("expiry")
        raw_rows = idx.rows(exp)
        finite_oi = sum(
            1 for r in raw_rows
            if _finite(r["strike"])
            and _finite(r["openInterest"])
        )
        counts[exp] = {
            "strikes": len(_strikes(e)),
            "finite_oi_rows": finite_oi,
            "excluded_rows": len(raw_rows) - finite_oi,
            "stale_iv_rows": sum(
                1 for s in _strikes(e)
                if isinstance(s, dict)
                and s.get("stale_iv") is True
            ),
            "metrics_unavailable": (
                e.get("metrics_unavailable")
                if isinstance(
                    e.get("metrics_unavailable"), bool)
                else None
            ),
        }

        # strike-set reconciliation
        raw_strikes = {
            r["strike"] for r in raw_rows if _finite(r["strike"])
        }
        pkt_strikes = {
            s.get("strike") for s in _strikes(e)
            if isinstance(s, dict) and _finite(s.get("strike"))
        }
        for k in sorted(pkt_strikes - raw_strikes):
            findings.append(_finding(
                exp, f"strike[{k}]", "in packet only",
                "reconcilable to a raw row"))
        for k in sorted(raw_strikes - pkt_strikes):
            findings.append(_finding(
                exp, f"strike[{k}]", "in snapshot only",
                "exported strike"))

        # per-(strike,type) sums
        agg = {}
        for r in raw_rows:
            key = (r["strike"], r["type"])
            a = agg.setdefault(key, {
                "oi": 0.0, "gex": 0.0, "ivs": [],
            })
            oi = r["openInterest"]
            if _finite(oi):
                a["oi"] += oi  # NaN collapses to 0
            g = r["gex"]
            if _finite(g):
                a["gex"] += g
            iv = r["iv"]
            if _finite(iv):
                a["ivs"].append(iv)

        for s in _strikes(e):
            if not isinstance(s, dict):
                continue
            k = s.get("strike")
            call = agg.get((k, "call"))
            put = agg.get((k, "put"))
            if call is None and put is None:
                continue  # strike-set finding already emitted

            def tol(a, b):
                return max(
                    SNAP_ABS_FLOOR,
                    SNAP_REL_TOL * max(abs(a), abs(b)),
                )

            if call is not None and _finite(s.get("call_oi")):
                if abs(call["oi"] - s["call_oi"]) > tol(
                    call["oi"], s["call_oi"]
                ):
                    findings.append(_finding(
                        exp, f"strike[{k}].call_oi",
                        s["call_oi"],
                        f"raw sum {call['oi']} "
                        "(NaN->0 permitted)"))
            if put is not None and _finite(s.get("put_oi")):
                if abs(put["oi"] - s["put_oi"]) > tol(
                    put["oi"], s["put_oi"]
                ):
                    findings.append(_finding(
                        exp, f"strike[{k}].put_oi",
                        s["put_oi"],
                        f"raw sum {put['oi']} "
                        "(NaN->0 permitted)"))
            if (
                _finite(s.get("open_interest"))
                and abs(
                    (call["oi"] if call else 0.0)
                    + (put["oi"] if put else 0.0)
                    - s["open_interest"]
                ) > tol(
                    s["open_interest"],
                    (call["oi"] if call else 0.0)
                    + (put["oi"] if put else 0.0),
                )
            ):
                findings.append(_finding(
                    exp, f"strike[{k}].open_interest",
                    s["open_interest"], "raw OI sum"))

            if idx.has_gex:
                if call is not None and _finite(
                    s.get("call_gex")
                ):
                    if abs(call["gex"] - s["call_gex"]) > tol(
                        call["gex"], s["call_gex"]
                    ):
                        findings.append(_finding(
                            exp, f"strike[{k}].call_gex",
                            s["call_gex"],
                            f"raw sum {call['gex']}"))
                if put is not None and _finite(
                    s.get("put_gex")
                ):
                    if abs(put["gex"] - s["put_gex"]) > tol(
                        put["gex"], s["put_gex"]
                    ):
                        findings.append(_finding(
                            exp, f"strike[{k}].put_gex",
                            s["put_gex"],
                            f"raw sum {put['gex']}"))

            # IV mean (across types) vs exported iv or
            # raw_implied_volatility when flagged stale;
            # NaN->null is a permitted transform.
            if idx.has_iv:
                ivs = (
                    (call["ivs"] if call else [])
                    + (put["ivs"] if put else [])
                )
                mean_iv = (
                    sum(ivs) / len(ivs) if ivs
                    else float("nan")
                )
                exported = s.get("implied_volatility")
                stale = s.get("stale_iv") is True
                target = (
                    s.get("raw_implied_volatility")
                    if stale else exported
                )
                if math.isnan(mean_iv):
                    # raw side has no finite IV — exported
                    # null is the permitted transform; an
                    # emitted NaN token is NUMERIC-01's
                    # finding, not ours.
                    if exported is not None and not (
                        isinstance(exported, float)
                        and math.isnan(exported)
                    ):
                        findings.append(_finding(
                            exp,
                            f"strike[{k}].implied_volatility",
                            exported,
                            "null (raw IV all missing)"))
                elif _finite(target):
                    if abs(mean_iv - target) > tol(
                        mean_iv, target
                    ):
                        findings.append(_finding(
                            exp,
                            f"strike[{k}]."
                            + (
                                "raw_implied_volatility"
                                if stale
                                else "implied_volatility"
                            ),
                            target,
                            f"raw mean IV {mean_iv}"))

    status = "FAIL" if findings else "PASS"
    detail = (
        f"{len(findings)} reconciliation mismatch(es)"
        if findings else
        "raw rows reconcile to exported strikes "
        "(OI NaN->0 / IV NaN->null transforms honored)")
    if not idx.has_gex:
        detail += "; gex reconciliation unperformed " \
            "(snapshot has no gex column)"
    return _result("SNAP-01", status, detail, findings), counts, idx


def check_cover(packet, raw_index):
    """COVER-01 (WARN): expiries with 0 strikes; sparse
    chains (<10 strikes); >50% missing-OI share when a
    snapshot was given."""
    findings = []
    for e in _expiries(packet) or []:
        if not isinstance(e, dict):
            continue
        exp = e.get("expiry")
        n = len(_strikes(e))
        if n == 0:
            findings.append(_finding(
                exp, "strikes", 0,
                ">= 1 exported strike"))
        elif n < SPARSE_CHAIN_STRIKES:
            findings.append(_finding(
                exp, "strikes", n,
                f">= {SPARSE_CHAIN_STRIKES} "
                "(sparse chain)"))
        if raw_index is not None:
            rows = raw_index.rows(exp)
            if rows:
                missing = sum(
                    1 for r in rows
                    if not _finite(r["openInterest"])
                )
                share = missing / len(rows)
                if share > MISSING_OI_WARN_SHARE:
                    findings.append(_finding(
                        exp, "openInterest",
                        f"{missing}/{len(rows)} rows missing",
                        f"<= {MISSING_OI_WARN_SHARE:.0%} "
                        "missing-OI share"))

    status = "WARN" if findings else "PASS"
    detail = (
        f"{len(findings)} coverage warning(s)"
        if findings else "coverage adequate")
    if raw_index is None:
        detail += "; missing-OI share unperformed (no snapshot)"
    return _result("COVER-01", status, detail, findings)


def check_fresh(packet, max_age_hours):
    """FRESH-01 (WARN): generated_utc older than
    --max-age-hours (default 72h)."""
    meta = packet.get("meta")
    ts = (
        _parse_iso(meta.get("generated_utc"))
        if isinstance(meta, dict) else None
    )
    if ts is None:
        return _result(
            "FRESH-01", "unperformed",
            "meta.generated_utc absent or unparseable")
    now = datetime.now(timezone.utc)
    age_h = (now - ts).total_seconds() / 3600.0
    findings = []
    if age_h < -1.0:
        findings.append(_finding(
            None, "meta.generated_utc",
            meta.get("generated_utc"),
            "not in the future"))
    elif age_h > max_age_hours:
        findings.append(_finding(
            None, "meta.generated_utc",
            meta.get("generated_utc"),
            f"<= {max_age_hours}h old (age {age_h:.1f}h)"))
    status = "WARN" if findings else "PASS"
    return _result(
        "FRESH-01", status,
        f"packet age {age_h:.1f}h"
        + ("" if not findings else " — stale"),
        findings,
    )


# =========================
# Driver
# =========================

def validate_packet(packet, snapshot_path=None,
                    max_age_hours=DEFAULT_MAX_AGE_HOURS):
    """Run the full check battery. Returns
    (checks_list, counts, raw_index)."""
    checks = []
    checks.append(check_schema(packet))
    checks.append(check_numeric(packet))

    snap_res, counts, raw_index = check_snapshot(
        packet, snapshot_path)
    checks.append(snap_res)

    checks.append(check_avail_a(packet))
    checks.append(check_avail_b(packet, raw_index))
    checks.append(check_avail_c(packet))
    checks.append(check_agg(packet))
    checks.append(check_max_pain(packet, raw_index))
    checks.append(check_max_pain_constrained(packet, raw_index))
    checks.append(check_flip(packet))
    checks.append(check_iv(packet))
    checks.append(check_cover(packet, raw_index))
    checks.append(check_fresh(packet, max_age_hours))
    return checks, counts, raw_index


def build_report(packet, packet_path, packet_bytes,
                 snapshot_path, checks, counts,
                 max_age_hours):
    overall = "PASS"
    if any(c["status"] == "FAIL" for c in checks):
        overall = "FAIL"
    elif any(c["status"] == "WARN" for c in checks):
        overall = "WARN"

    meta = packet.get("meta") if isinstance(packet, dict) else {}
    commit = _checker_git_commit()
    snapshot_hash = None
    snapshot_hash_error = None
    if snapshot_path:
        try:
            snapshot_hash = _sha256(snapshot_path)
        except Exception as exc:
            # an unreadable snapshot is already a SNAP-01
            # FAIL finding — hashing must not turn it into
            # a checker-internal error
            snapshot_hash_error = repr(exc)
    provenance = {
        "packet_hash": hashlib.sha256(packet_bytes).hexdigest(),
        "packet_path": str(packet_path),
        "packet_generated_utc": (
            meta.get("generated_utc")
            if isinstance(meta, dict) else None
        ),
        "snapshot_hash": snapshot_hash,
        "snapshot_path": (
            str(snapshot_path) if snapshot_path else None
        ),
        "code_commit": commit,
        "code_commit_note": (
            None if commit else
            "WellnessCheck.py source is not under "
            "version control"
        ),
        "schema_version": (
            meta.get("schema_version")
            if isinstance(meta, dict) else None
        ),
        "per_expiry_counts": counts,
    }
    if snapshot_hash_error is not None:
        provenance["snapshot_hash_error"] = snapshot_hash_error
    if snapshot_path is None:
        provenance["counts_note"] = (
            "finite_oi_rows / excluded_rows require "
            "--snapshot (OI-missingness provenance "
            "unperformed)")

    summary = {"PASS": 0, "WARN": 0, "FAIL": 0,
               "unperformed": 0}
    for c in checks:
        summary[c["status"]] = (
            summary.get(c["status"], 0) + 1)

    ms = packet.get("market_snapshot") if isinstance(
        packet, dict) else {}
    return {
        "overall_status": overall,
        "report_generated_utc": datetime.now(
            timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "checker": Path(__file__).name,
        "max_age_hours": max_age_hours,
        "packet": {
            "path": str(packet_path),
            "sha256": provenance["packet_hash"],
            "ticker": (
                meta.get("ticker")
                if isinstance(meta, dict) else None
            ),
            "generated_utc": provenance[
                "packet_generated_utc"],
            "schema_version": provenance["schema_version"],
            "expiries": (
                len(ms.get("expiries", []))
                if isinstance(ms, dict) else None
            ),
        },
        "snapshot": provenance["snapshot_path"],
        "provenance": provenance,
        "checks": checks,
        "summary": summary,
    }


def _default_report_path(packet_path):
    stem = Path(packet_path).stem
    ts = datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ")
    return Path("validation") / f"{stem}_report_{ts}.json"


def _write_report(report, report_path, packet_path):
    """Write the structured report without EVER
    overwriting the input packet ('x'-safe mode)."""
    rp = Path(report_path)
    if rp.resolve() == Path(packet_path).resolve():
        raise ValueError(
            "report path must not overwrite the input packet")
    rp.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(rp, "x", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
    except FileExistsError:
        base = rp
        for i in range(1, 100):
            rp = base.with_name(
                f"{base.stem}-{i}{base.suffix}")
            try:
                with open(rp, "x", encoding="utf-8") as f:
                    json.dump(report, f, indent=2)
                break
            except FileExistsError:
                continue
        else:
            raise
    return rp


def run(packet_path, snapshot_path=None, report_path=None,
        max_age_hours=DEFAULT_MAX_AGE_HOURS):
    """Full validation run. Returns
    (report_dict_or_None, exit_code, written_report_path)."""
    packet_path = Path(packet_path)

    # ---- INPUT-01: unreadable / malformed / unsupported
    try:
        packet_bytes = packet_path.read_bytes()
    except Exception as exc:
        report = {
            "overall_status": "FAIL",
            "report_generated_utc": datetime.now(
                timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "checker": Path(__file__).name,
            "checks": [_result(
                "INPUT-01", "FAIL",
                f"packet unreadable: {exc!r}")],
            "summary": {"FAIL": 1},
        }
        rp = (
            _write_report(report, report_path, packet_path)
            if report_path else None
        )
        return report, 1, rp

    try:
        packet = json.loads(packet_bytes.decode("utf-8"))
    except Exception as exc:
        report = {
            "overall_status": "FAIL",
            "report_generated_utc": datetime.now(
                timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "checker": Path(__file__).name,
            "checks": [_result(
                "INPUT-01", "FAIL",
                f"malformed JSON: {exc!r}")],
            "summary": {"FAIL": 1},
        }
        rp = (
            _write_report(report, report_path, packet_path)
            if report_path else None
        )
        return report, 1, rp

    if not isinstance(packet, dict):
        report = {
            "overall_status": "FAIL",
            "report_generated_utc": datetime.now(
                timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "checker": Path(__file__).name,
            "checks": [_result(
                "INPUT-01", "FAIL",
                "packet root is not a JSON object")],
            "summary": {"FAIL": 1},
        }
        rp = (
            _write_report(report, report_path, packet_path)
            if report_path else None
        )
        return report, 1, rp

    meta = packet.get("meta")
    sv = (
        meta.get("schema_version")
        if isinstance(meta, dict) else None
    )
    if (
        isinstance(sv, str)
        and sv not in SUPPORTED_SCHEMA_VERSIONS
    ):
        report = {
            "overall_status": "FAIL",
            "report_generated_utc": datetime.now(
                timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "checker": Path(__file__).name,
            "checks": [_result(
                "INPUT-01", "FAIL",
                f"unsupported schema_version {sv!r} — "
                f"supported: "
                f"{sorted(SUPPORTED_SCHEMA_VERSIONS)}")],
            "summary": {"FAIL": 1},
        }
        rp = (
            _write_report(report, report_path, packet_path)
            if report_path else None
        )
        return report, 1, rp

    # ---- check battery (CHECKER-ERR guard)
    try:
        checks, counts, raw_index = validate_packet(
            packet, snapshot_path, max_age_hours)
        report = build_report(
            packet, packet_path, packet_bytes,
            snapshot_path, checks, counts, max_age_hours)
    except Exception as exc:
        report = {
            "overall_status": "FAIL",
            "report_generated_utc": datetime.now(
                timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "checker": Path(__file__).name,
            "checks": [_result(
                "CHECKER-ERR", "FAIL",
                f"checker-internal error: {exc!r}")],
            "summary": {"FAIL": 1},
        }
        rp = (
            _write_report(report, report_path, packet_path)
            if report_path else None
        )
        return report, 2, rp

    rp = (
        _write_report(report, report_path, packet_path)
        if report_path else None
    )

    code = {"PASS": 0, "WARN": 0, "FAIL": 1}[
        report["overall_status"]]
    return report, code, rp


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Post-generation AI-packet wellness "
                    "check (independent reference "
                    "implementations; GexDashboard is "
                    "never imported).")
    ap.add_argument("packet", help="ai_packet JSON path")
    ap.add_argument(
        "--snapshot",
        help="raw chain snapshot pickle for SNAP-01 "
             "reconciliation")
    ap.add_argument(
        "--report",
        help="report output path (default "
             "validation/<packet>_report_<ts>.json)")
    ap.add_argument(
        "--max-age-hours", type=float,
        default=DEFAULT_MAX_AGE_HOURS,
        help="FRESH-01 staleness threshold (default 72)")
    args = ap.parse_args(argv)

    try:
        report, code, rp = run(
            args.packet,
            snapshot_path=args.snapshot,
            report_path=(
                args.report
                or _default_report_path(args.packet)
            ),
            max_age_hours=args.max_age_hours,
        )
    except Exception as exc:
        # checker-internal error (incl. report-path
        # misuse such as pointing --report at the
        # input packet)
        print(
            f"overall_status: FAIL\n"
            f"  CHECKER-ERR  FAIL  {exc!r}")
        return 2

    print(f"overall_status: {report['overall_status']}")
    for c in report.get("checks", []):
        print(f"  {c['check_id']:<12} {c['status']}")
    if rp:
        print(f"report: {rp}")
    return code


if __name__ == "__main__":
    sys.exit(main())

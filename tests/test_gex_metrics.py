"""Offline tests for the issue-#1 fixes in GexDashboard.py:

- compute_gamma_flip: combined materiality filter
  (max(1% of slice max, MIN_MATERIAL_GEX=1e-3)),
  nonfinite-strike drop, nearest-to-spot crossing,
  and honest None when no material crossing exists.
- compute_max_pain: canonical UNCONSTRAINED payout
  argmin; compute_max_pain_constrained: the
  +/-30% spot-band variant with fell_back and
  boundary_hit metadata.
- build_ai_packet: stale-IV (>200%) flagging with
  raw_implied_volatility provenance, both max_pain
  fields, and meta.metric_definitions.

All tests run OFFLINE against tests/fixtures/TSM_ai_packet.json
(a real ai_packet produced by this pipeline; spot = 422.05).
No network access is used anywhere in this file.
"""

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import GexDashboard
from GexDashboard import (
    MIN_MATERIAL_GEX,
    build_ai_packet,
    compute_gamma_flip,
    compute_max_pain,
    compute_max_pain_constrained,
)

FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "TSM_ai_packet.json"
)

SPOT = 422.05  # fixture market_snapshot.spot
BAND_LO = 0.7 * SPOT   # 295.435
BAND_HI = 1.3 * SPOT   # 548.665


def _load_packet():
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


def _gex_df(expiry_obj):
    """Reconstruct a strike/gex df from one fixture expiry.

    net_gex is already the per-strike sum, so a df with
    columns strike + gex is a valid compute_gamma_flip input.
    """
    return pd.DataFrame({
        "strike": [
            s["strike"] for s in expiry_obj["strikes"]
        ],
        "gex": [
            s["net_gex"] for s in expiry_obj["strikes"]
        ],
    })


def _oi_df(expiry_obj):
    """Reconstruct call/put OI rows for compute_max_pain."""
    rows = []

    for s in expiry_obj["strikes"]:

        rows.append({
            "strike": s["strike"],
            "type": "call",
            "openInterest": s["call_oi"],
        })

        rows.append({
            "strike": s["strike"],
            "type": "put",
            "openInterest": s["put_oi"],
        })

    return pd.DataFrame(rows)


def _material_crossings(df):
    """Independent recomputation of material neg->pos
    crossings (plain Python, separate implementation
    of the combined relative+absolute rule)."""
    finite = df[
        df["strike"].map(math.isfinite)
        & df["gex"].map(math.isfinite)
    ]

    grouped = (
        finite.groupby("strike")["gex"].sum().sort_index()
    )

    pairs = list(zip(grouped.index, grouped.values))

    if not pairs:
        return []

    max_abs = max(abs(g) for _, g in pairs)

    if max_abs < MIN_MATERIAL_GEX:
        return []

    floor = max(0.01 * max_abs, MIN_MATERIAL_GEX)

    material = [
        (k, g) for k, g in pairs if abs(g) >= floor
    ]

    return [
        material[i][0]
        for i in range(1, len(material))
        if material[i - 1][1] < 0 < material[i][1]
    ]


def _independent_max_pain(df):
    """Independent plain-Python max-pain argmin
    over the finite rows only — a separate
    implementation used to verify the lib."""
    frame = df[
        df["strike"].map(math.isfinite)
        & df["openInterest"].map(math.isfinite)
    ]

    best_s = None
    best_p = None

    for s in sorted(frame["strike"].unique()):
        pain = 0.0
        for _, r in frame.iterrows():
            if r["type"] == "call":
                pain += (
                    max(0.0, s - r["strike"])
                    * r["openInterest"]
                )
            else:
                pain += (
                    max(0.0, r["strike"] - s)
                    * r["openInterest"]
                )
        if best_p is None or pain < best_p:
            best_s, best_p = s, pain

    return best_s


# =========================
# Gamma Flip
# =========================
def test_gamma_flip_expiry_2026_10_16_near_430():
    packet = _load_packet()

    expiry = next(
        e
        for e in packet["market_snapshot"]["expiries"]
        if e["expiry"] == "2026-10-16"
    )

    flip = compute_gamma_flip(_gex_df(expiry), spot=SPOT)

    assert flip is not None
    assert 425.0 <= flip <= 435.0


def test_gamma_flip_per_expiry_material_crossings_only():
    """Every expiry: None if no material crossing exists,
    else the material crossing nearest to spot — never an
    artifact strike like 370/230/250/310."""
    packet = _load_packet()

    artifacts = {370.0, 230.0, 250.0, 310.0}

    for e in packet["market_snapshot"]["expiries"]:

        df = _gex_df(e)
        expected = _material_crossings(df)
        flip = compute_gamma_flip(df, spot=SPOT)

        if not expected:
            assert flip is None, (
                f"{e['expiry']}: no material crossing "
                f"but got {flip}"
            )
        else:
            want = min(
                expected,
                key=lambda s: abs(s - SPOT)
            )
            assert flip == want, (
                f"{e['expiry']}: expected {want}, "
                f"got {flip}"
            )
            assert flip not in artifacts


def test_gamma_flip_aggregate_book_near_430():
    """Whole-book (all expiries concatenated) flip must
    match market_snapshot.nearest_gamma_flip == 430."""
    packet = _load_packet()

    book = pd.concat(
        [
            _gex_df(e)
            for e in packet["market_snapshot"]["expiries"]
        ],
        ignore_index=True,
    )

    flip = compute_gamma_flip(book, spot=SPOT)

    assert flip is not None
    assert 425.0 <= flip <= 435.0


def test_gamma_flip_edge_cases_return_none():
    empty = pd.DataFrame({"strike": [], "gex": []})
    assert compute_gamma_flip(empty, spot=SPOT) is None
    assert compute_gamma_flip(empty) is None

    all_neg = pd.DataFrame({
        "strike": [100.0, 110.0, 120.0],
        "gex": [-1e6, -2e6, -3e6],
    })
    assert compute_gamma_flip(all_neg, spot=SPOT) is None
    assert compute_gamma_flip(all_neg) is None

    all_pos = pd.DataFrame({
        "strike": [100.0, 110.0, 120.0],
        "gex": [1e6, 2e6, 3e6],
    })
    assert compute_gamma_flip(all_pos, spot=SPOT) is None
    assert compute_gamma_flip(all_pos) is None


def test_gamma_flip_noise_crossing_returns_none():
    """A neg->pos crossing only among ~1e-12 noise gex
    must NOT produce an artifact flip (old code returned
    170.0 here)."""
    df = pd.DataFrame({
        "strike": [165.0, 170.0, 400.0, 405.0],
        "gex": [-1.2e-12, 3.4e-12, -5e6, -2e6],
    })

    assert compute_gamma_flip(df, spot=SPOT) is None
    assert compute_gamma_flip(df) is None


def test_gamma_flip_nearest_spot_vs_first_crossing():
    """With multiple material crossings, spot selects the
    nearest; spot=None keeps ascending-scan behavior."""
    df = pd.DataFrame({
        "strike": [250.0, 300.0, 400.0, 430.0],
        "gex": [-5e6, 2e6, -3e6, 4e6],
    })
    # material crossings at 300 and 430;
    # 430 is nearer to spot 422.05
    assert compute_gamma_flip(df, spot=SPOT) == 430.0
    assert compute_gamma_flip(df) == 300.0


def test_gamma_flip_alternating_noise_returns_none():
    """Alternating +/-1e-12 sign noise contains
    real neg->pos crossings but the whole slice is
    below MIN_MATERIAL_GEX — must return None."""
    df = pd.DataFrame({
        "strike": [
            400.0, 410.0, 420.0,
            430.0, 440.0, 450.0,
        ],
        "gex": [
            -1e-12, 1e-12, -1e-12,
            1e-12, -1e-12, 1e-12,
        ],
    })

    assert compute_gamma_flip(df, spot=SPOT) is None
    assert compute_gamma_flip(df) is None


def test_gamma_flip_all_zero_book_returns_none():
    """All-zero gex: no material strikes at all,
    so zero-gex strikes can never anchor a
    crossing — honest None."""
    df = pd.DataFrame({
        "strike": [400.0, 420.0, 440.0],
        "gex": [0.0, 0.0, 0.0],
    })

    assert compute_gamma_flip(df, spot=SPOT) is None
    assert compute_gamma_flip(df) is None


def test_gamma_flip_absolute_floor_boundary():
    """max_abs just below / at / above the 1e-3
    floor decides whether the slice counts as
    noise or signal."""
    # max_abs = 9e-4 < 1e-3 -> whole slice noise
    below = pd.DataFrame({
        "strike": [400.0, 420.0],
        "gex": [-9e-4, 9e-4],
    })
    assert compute_gamma_flip(below, spot=SPOT) is None

    # crossing exactly AT the floor is material
    at = pd.DataFrame({
        "strike": [400.0, 420.0],
        "gex": [-1e-3, 1e-3],
    })
    assert (
        compute_gamma_flip(at, spot=SPOT) == 420.0
    )

    # just above the floor is material too
    above = pd.DataFrame({
        "strike": [400.0, 420.0],
        "gex": [-1.1e-3, 1.1e-3],
    })
    assert (
        compute_gamma_flip(above, spot=SPOT) == 420.0
    )


def test_gamma_flip_floor_blocks_relative_only_crossing():
    """A strike above 1%-of-max but below the
    absolute 1e-3 floor is NOT material — the
    pure-relative filter would have flipped here."""
    df = pd.DataFrame({
        "strike": [400.0, 420.0, 440.0],
        "gex": [-5e-2, 5e-4, -1e-2],
    })
    # 1% of max_abs(0.05) = 5e-4 -> old rule made
    # the 420 strike material; the 1e-3 floor
    # rejects it, leaving no neg->pos crossing
    assert compute_gamma_flip(df, spot=SPOT) is None


def test_gamma_flip_multiple_crossings_nearest_spot():
    """Material crossings both below and above
    spot: nearest wins."""
    df = pd.DataFrame({
        "strike": [400.0, 410.0, 430.0, 445.0],
        "gex": [-2e6, 1e6, -3e6, 4e6],
    })
    # neg->pos crossings at 410 and 445;
    # 410 is nearer to spot 422.05
    assert compute_gamma_flip(df, spot=SPOT) == 410.0


def test_gamma_flip_nonfinite_strikes_dropped():
    """NaN/inf strikes and nonfinite gex rows are
    dropped before grouping — they cannot anchor
    crossings or poison the materiality max."""
    df = pd.DataFrame({
        "strike": [
            -np.inf, 400.0, 410.0,
            420.0, 430.0, np.nan,
        ],
        "gex": [
            -9e9, -5e6, -3e6,
            4e6, np.inf, 1e6,
        ],
    })

    # finite profile flips at 420; the -inf strike
    # (|gex| 9e9) and inf/nan rows are excluded
    assert compute_gamma_flip(df, spot=SPOT) == 420.0


def test_gamma_flip_all_nan_gex_returns_none():
    """All-NaN gex slice: every row is dropped by
    the finite filter, so there is no profile at
    all — the max_abs guard must see an empty
    slice and return None, not a crash or an
    artifact strike."""
    df = pd.DataFrame({
        "strike": [400.0, 420.0, 440.0],
        "gex": [np.nan, np.nan, np.nan],
    })

    assert compute_gamma_flip(df, spot=SPOT) is None
    assert compute_gamma_flip(df) is None


# =========================
# Max Pain
# =========================
def test_max_pain_constrained_expiries_within_spot_band():
    packet = _load_packet()

    for e in packet["market_snapshot"]["expiries"]:

        res = compute_max_pain_constrained(
            _oi_df(e), spot=SPOT
        )

        mp = res["max_pain"]

        assert BAND_LO <= mp <= BAND_HI, (
            f"{e['expiry']}: constrained max_pain "
            f"{mp} outside [{BAND_LO}, {BAND_HI}]"
        )

        chain = {s["strike"] for s in e["strikes"]}
        assert mp in chain

        assert res["candidate_band"] == [
            BAND_LO, BAND_HI
        ]
        assert res["band_fell_back"] is False


def test_max_pain_unconstrained_is_canonical():
    """compute_max_pain returns the canonical
    UNCONSTRAINED argmin — even when that lands
    outside the tradable band (honest label, not
    silently constrained)."""
    df = pd.DataFrame({
        "strike": [
            200.0, 420.0, 430.0,
            200.0, 420.0, 430.0,
        ],
        "type": [
            "call", "call", "call",
            "put", "put", "put",
        ],
        "openInterest": [
            100000, 100, 50,
            10, 10, 10,
        ],
    })

    # canonical unconstrained is dragged to the
    # 200 strike by the massive deep-OTM call OI
    assert compute_max_pain(df) == 200.0
    # `spot` no longer constrains the canonical fn
    assert compute_max_pain(df, spot=SPOT) == 200.0

    res = compute_max_pain_constrained(df, spot=SPOT)

    # constrained variant stays inside the band
    assert res["max_pain"] == 420.0
    assert BAND_LO <= res["max_pain"] <= BAND_HI
    assert res["candidate_band"] == [BAND_LO, BAND_HI]
    assert res["band_fell_back"] is False
    assert res["band_boundary_hit"] is False


def test_max_pain_constrained_empty_band_falls_back():
    """If the spot band contains no strikes, fall
    back to the full chain — flagged as fell_back,
    distinct from a boundary hit."""
    df = pd.DataFrame({
        "strike": [
            100.0, 200.0, 300.0,
            100.0, 200.0, 300.0,
        ],
        "type": [
            "call", "call", "call",
            "put", "put", "put",
        ],
        "openInterest": [
            10, 50, 10,
            10, 5, 90,
        ],
    })

    # band [700, 1300] contains no strikes -> fallback
    res = compute_max_pain_constrained(df, spot=1000.0)

    assert res["candidate_band"] == [700.0, 1300.0]
    assert res["band_fell_back"] is True
    assert res["band_boundary_hit"] is False
    assert res["max_pain"] == compute_max_pain(df)
    assert res["max_pain"] in {100.0, 200.0, 300.0}


def test_max_pain_constrained_boundary_hit():
    """Band active and the payout argmin lands
    exactly on a band edge -> boundary_hit=True,
    NOT fell_back. 0.7*25 == 17.5 and
    1.3*25 == 32.5 exactly in binary fp, so the
    band edges coincide with real strikes."""
    df = pd.DataFrame({
        "strike": [
            17.5, 20.0, 30.0, 32.5,
            17.5, 20.0, 30.0, 32.5,
        ],
        "type": [
            "call", "call", "call", "call",
            "put", "put", "put", "put",
        ],
        "openInterest": [
            10000, 10, 10, 10,
            5, 5, 5, 5,
        ],
    })

    res = compute_max_pain_constrained(df, spot=25.0)

    # heavy call OI at 17.5 pins the payout
    # minimum to the lower band edge
    assert res["max_pain"] == 17.5
    assert res["candidate_band"] == [17.5, 32.5]
    assert res["band_fell_back"] is False
    assert res["band_boundary_hit"] is True
    # a literal edge hit is also the lowest
    # ELIGIBLE candidate
    assert res["selected_lowest_candidate"] is True
    assert res["selected_highest_candidate"] is False


def test_max_pain_constrained_between_strikes_edge():
    """Band endpoints fall BETWEEN listed strikes:
    no strike literally equals a band edge, so
    band_boundary_hit must stay False — but the
    constraint can still drive the argmin to the
    lowest/highest ELIGIBLE candidate, which the
    selected_*_candidate flags must report."""
    df = pd.DataFrame({
        "strike": [
            100.0, 150.0, 200.0,
            100.0, 150.0, 200.0,
        ],
        "type": [
            "call", "call", "call",
            "put", "put", "put",
        ],
        "openInterest": [
            10, 10000, 10,
            10, 10, 10,
        ],
    })

    # spot=210 -> band [147, 273]; eligible
    # candidates are 150 and 200, NEITHER of
    # which is a literal band edge
    res = compute_max_pain_constrained(df, spot=210.0)

    assert res["candidate_band"] == [147.0, 273.0]
    # heavy call OI at 150 pins the minimum to
    # the LOWEST eligible candidate
    assert res["max_pain"] == 150.0
    assert res["band_fell_back"] is False
    assert res["band_boundary_hit"] is False
    assert res["selected_lowest_candidate"] is True
    assert res["selected_highest_candidate"] is False

    df2 = df.copy()
    df2["openInterest"] = [
        10, 10, 10,
        10, 10, 90000,
    ]

    res2 = compute_max_pain_constrained(
        df2, spot=210.0
    )

    # heavy put OI at 200 drags the minimum to
    # the HIGHEST eligible candidate — still
    # not a literal band edge
    assert res2["max_pain"] == 200.0
    assert res2["band_fell_back"] is False
    assert res2["band_boundary_hit"] is False
    assert res2["selected_lowest_candidate"] is False
    assert res2["selected_highest_candidate"] is True


def test_max_pain_nan_open_interest_regression():
    """REGRESSION — original artifact mechanism:
    the data vendor emits NaN openInterest on dead
    strikes; a NaN-OI row turns EVERY pain sum
    NaN and np.argmin silently returns the FIRST
    NaN index = the lowest strike. This is how
    the original packet produced max_pain
    artifacts 225/230/235 on the 10-23 / 10-30 /
    11-27 expiries. Rows with missing OI (and
    nonfinite strikes) must be excluded so the
    argmin reflects only real open interest."""
    df = pd.DataFrame({
        "strike": [
            np.nan, 100.0, 400.0, 420.0, 440.0,
            100.0, 400.0, 420.0, 440.0,
        ],
        "type": [
            "call", "call", "call", "call", "call",
            "put", "put", "put", "put",
        ],
        "openInterest": [
            99999, np.nan, 500, 800, 600,
            np.nan, 700, 300, 900,
        ],
    })

    expected = _independent_max_pain(df)

    # independent finite-only argmin is the
    # interior 420 strike; the OLD code would
    # have returned the lowest strike (100)
    # because all-NaN pains -> argmin index 0
    assert expected == 420.0

    mp = compute_max_pain(df)

    assert mp == expected
    assert mp != 100.0

    # constrained variant agrees on the same
    # NaN-poisoned input
    res = compute_max_pain_constrained(
        df, spot=420.0
    )
    assert res["max_pain"] == expected

    # a fully missing-OI slice has no honest
    # answer — it fails loudly (ValueError from
    # the empty candidate set), never an
    # artifact strike
    with pytest.raises(ValueError):
        compute_max_pain(
            df[df["openInterest"].isna()]
        )


def test_max_pain_backward_compatible_no_spot():
    packet = _load_packet()
    e = packet["market_snapshot"]["expiries"][0]

    mp = compute_max_pain(_oi_df(e))

    chain = {s["strike"] for s in e["strikes"]}
    assert mp in chain


def test_ai_packet_emits_both_max_pain_variants():
    """Packet must emit unconstrained 'max_pain'
    AND the separately labeled constrained variant
    with band metadata — even when the canonical
    value sits outside the band."""
    exp = "2026-10-16"
    exp_dt = pd.Timestamp(exp, tz="UTC")

    df = pd.DataFrame({
        "strike": [
            200.0, 420.0, 430.0,
            200.0, 420.0, 430.0,
        ],
        "type": [
            "call", "call", "call",
            "put", "put", "put",
        ],
        "openInterest": [
            100000, 100, 50,
            10, 10, 10,
        ],
        "gex": [
            1e5, 2e5, 1e5,
            -1e5, -2e5, -1e5,
        ],
        "impliedVolatility": [0.30] * 6,
        "expiry": [exp_dt] * 6,
    })

    packet = build_ai_packet("TEST", df, 422.05, [exp])
    e = packet["market_snapshot"]["expiries"][0]

    # canonical unconstrained value — honestly
    # outside the band
    assert e["max_pain"] == 200.0

    # constrained variant, separately labeled
    assert e["max_pain_constrained"] == 420.0
    assert e["max_pain_candidate_band"] == [
        0.7 * 422.05, 1.3 * 422.05
    ]
    assert e["max_pain_band_fell_back"] is False
    assert e["max_pain_band_boundary_hit"] is False
    # 420 is the lowest ELIGIBLE candidate in
    # the band — reported by the new flag even
    # though it is not a literal band edge
    assert (
        e["max_pain_selected_lowest_candidate"]
        is True
    )
    assert (
        e["max_pain_selected_highest_candidate"]
        is False
    )


# =========================
# Stale IV flagging
# =========================
def test_stale_iv_flagged_in_ai_packet():
    exp = "2026-10-16"
    exp_dt = pd.Timestamp(exp, tz="UTC")

    df = pd.DataFrame({
        "strike": [100.0, 100.0, 110.0, 110.0],
        "type": ["call", "put", "call", "put"],
        "openInterest": [10, 20, 30, 40],
        "gex": [1e5, -2e5, 3e5, -1e5],
        "impliedVolatility": [0.30, 0.30, 3.5, 3.5],
        "expiry": [exp_dt] * 4,
    })

    packet = build_ai_packet("TEST", df, 105.0, [exp])

    strikes = (
        packet["market_snapshot"]["expiries"][0]["strikes"]
    )
    by_strike = {s["strike"]: s for s in strikes}

    stale = by_strike[110.0]
    assert stale["implied_volatility"] is None
    assert stale["stale_iv"] is True
    # flagged quote kept for provenance
    assert stale["raw_implied_volatility"] == 3.5
    # OI / GEX fields must be preserved
    assert stale["open_interest"] == 70
    assert stale["call_oi"] == 30
    assert stale["put_oi"] == 40
    assert stale["net_gex"] == 200000.0

    normal = by_strike[100.0]
    assert normal["implied_volatility"] == 0.30
    assert normal.get("stale_iv") is not True
    # provenance key only exists on stale strikes
    assert "raw_implied_volatility" not in normal

    # flag is documented in the packet
    assert "stale_iv" in packet["meta"]["data_quality"]
    assert (
        "raw_implied_volatility"
        in packet["meta"]["data_quality"]
    )


def test_meta_metric_definitions_documented():
    """meta.metric_definitions must honestly label
    gamma_flip as a strike-profile proxy (not a
    repriced-chain flip) and max_pain_constrained
    as a constrained payout minimum."""
    exp = "2026-10-16"
    exp_dt = pd.Timestamp(exp, tz="UTC")

    df = pd.DataFrame({
        "strike": [100.0, 100.0],
        "type": ["call", "put"],
        "openInterest": [10, 20],
        "gex": [1e5, -2e5],
        "impliedVolatility": [0.30, 0.30],
        "expiry": [exp_dt] * 2,
    })

    packet = build_ai_packet("TEST", df, 105.0, [exp])

    md = packet["meta"]["metric_definitions"]

    assert "gamma_flip" in md
    assert (
        "strike-profile" in md["gamma_flip"].lower()
    )
    assert "max_pain" in md
    assert "max_pain_constrained" in md
    assert (
        "constrained payout minimum"
        in md["max_pain_constrained"].lower()
    )
    assert "materiality_floor" in md


# =========================
# Unusable-expiry tolerance
# =========================
def test_ai_packet_all_nan_oi_expiry_null_metrics():
    """Live SPY 2026-10-16 failure: expiry
    2026-10-29 has rows with ALL-NaN
    openInterest, so the raw metric functions
    raise ValueError. The packet call site must
    tolerate that ONE unusable expiry —
    explicit nulls + metrics_unavailable flag —
    while other expiries and whole-book fields
    still compute."""
    e_ok = "2026-10-16"
    e_dead = "2026-10-29"
    e_absent = "2027-01-01"  # in list, no rows

    d_ok = pd.Timestamp(e_ok, tz="UTC")
    d_dead = pd.Timestamp(e_dead, tz="UTC")

    rows = []

    # healthy expiry — grouped net gex is
    # -1e5 @ 100 and +2e5 @ 110, so the
    # material neg->pos crossing is 110
    for st, t, oi, g in [
        (100.0, "call", 500, -2e5),
        (100.0, "put", 200, 1e5),
        (110.0, "call", 300, 1e5),
        (110.0, "put", 50, 1e5),
    ]:
        rows.append({
            "strike": st,
            "type": t,
            "openInterest": oi,
            "gex": g,
            "impliedVolatility": 0.30,
            "expiry": d_ok,
        })

    # dead expiry — every OI value missing
    for st, t in [(100.0, "call"), (110.0, "put")]:
        rows.append({
            "strike": st,
            "type": t,
            "openInterest": np.nan,
            "gex": np.nan,
            "impliedVolatility": 0.30,
            "expiry": d_dead,
        })

    df = pd.DataFrame(rows)

    packet = build_ai_packet(
        "SPY", df, 105.0,
        [e_ok, e_dead, e_absent],
    )

    exs = {
        e["expiry"]: e
        for e in packet["market_snapshot"]["expiries"]
    }

    # --- healthy expiry is untouched ---
    ok = exs[e_ok]
    assert ok["metrics_unavailable"] is False
    assert "data_quality" not in ok
    dead_sub = df[df["expiry"] == d_dead]
    assert ok["max_pain"] == _independent_max_pain(
        df[df["expiry"] == d_ok]
    )
    assert ok["max_pain"] in {100.0, 110.0}
    assert ok["max_pain_constrained"] in {
        100.0, 110.0
    }
    assert ok["max_pain_candidate_band"] == [
        0.7 * 105.0, 1.3 * 105.0
    ]
    assert ok["max_pain_band_fell_back"] is False
    assert ok["gamma_flip"] == 110.0
    assert ok["atm_iv"] == 0.30

    # --- dead expiry: explicit nulls + flag,
    #     never artifact strikes ---
    dead = exs[e_dead]
    assert dead["metrics_unavailable"] is True
    assert dead["data_quality"] == "no_finite_oi"
    for field in (
        "max_pain",
        "max_pain_constrained",
        "max_pain_candidate_band",
        "max_pain_band_fell_back",
        "max_pain_band_boundary_hit",
        "max_pain_selected_lowest_candidate",
        "max_pain_selected_highest_candidate",
        "gamma_flip",
    ):
        assert dead[field] is None, field
    # the expiry's strikes are still exported
    # (OI collapsed to 0 — the documented
    # conflation), only the metrics are nulled
    assert len(dead["strikes"]) == 2
    assert all(
        s["open_interest"] == 0
        for s in dead["strikes"]
    )
    assert dead["atm_iv"] == 0.30

    # --- expiry listed but absent from the
    #     chain: same honest nulls ---
    absent = exs[e_absent]
    assert absent["metrics_unavailable"] is True
    assert absent["data_quality"] == "no_finite_oi"
    assert absent["max_pain"] is None
    assert absent["gamma_flip"] is None
    assert absent["strikes"] == []
    assert absent["atm_iv"] is None

    # --- whole-book fields still compute ---
    snap = packet["market_snapshot"]
    assert snap["spot"] == 105.0
    assert snap["total_net_gex"] == 100000.0
    assert snap["nearest_gamma_flip"] == 110.0

    # --- the raw functions still fail loudly
    #     on direct misuse ---
    with pytest.raises(ValueError):
        compute_max_pain(dead_sub)
    with pytest.raises(ValueError):
        compute_max_pain_constrained(
            dead_sub, spot=105.0
        )


def test_metrics_unavailable_differs_from_no_flip():
    """metrics_unavailable flags a payout-data
    failure, NOT merely a missing crossing: an
    expiry with finite OI but all-NaN gex has
    usable metrics (max_pain real) AND a null
    gamma_flip — both honest, distinct states."""
    exp = "2026-10-16"
    exp_dt = pd.Timestamp(exp, tz="UTC")

    df = pd.DataFrame({
        "strike": [100.0, 110.0, 100.0, 110.0],
        "type": ["call", "call", "put", "put"],
        "openInterest": [500, 300, 200, 50],
        "gex": [np.nan] * 4,
        "impliedVolatility": [0.30] * 4,
        "expiry": [exp_dt] * 4,
    })

    packet = build_ai_packet("TEST", df, 105.0, [exp])
    e = packet["market_snapshot"]["expiries"][0]

    # payout metrics computable -> available
    assert e["metrics_unavailable"] is False
    assert "data_quality" not in e
    assert e["max_pain"] == _independent_max_pain(df)
    # but no material crossing -> null flip,
    # which is a DIFFERENT state from
    # metrics_unavailable
    assert e["gamma_flip"] is None


def test_meta_data_quality_notes_oi_conflation():
    """meta.data_quality must document that the
    exported OI fields may conflate true-zero
    open interest with missing data (source NaN
    collapsed to 0 by aggregation) and that
    missing-OI rows are excluded from max-pain
    payout sums."""
    exp = "2026-10-16"
    exp_dt = pd.Timestamp(exp, tz="UTC")

    df = pd.DataFrame({
        "strike": [100.0, 100.0],
        "type": ["call", "put"],
        "openInterest": [10, 20],
        "gex": [1e5, -2e5],
        "impliedVolatility": [0.30, 0.30],
        "expiry": [exp_dt] * 2,
    })

    packet = build_ai_packet("TEST", df, 105.0, [exp])

    dq = packet["meta"]["data_quality"].lower()

    assert "open_interest" in dq
    assert "conflate" in dq
    assert "missing" in dq


# =========================
# Degenerate-chain eligibility
# =========================
def test_degenerate_single_strike_expiry():
    """Live INTC 2026-10-19 case: the expiry chain
    has ONE strike (call OI 2, put OI 0). The
    payout argmin is mathematically valid (190) but
    economically meaningless — payout is 0 at the
    only candidate and there is no second strike
    to discriminate against. The expiry must emit
    metrics_unavailable:true + data_quality:
    'insufficient_data' with explicit null payout
    fields, NOT the vacuous argmin."""
    exp = "2026-10-19"
    exp_dt = pd.Timestamp(exp, tz="UTC")

    df = pd.DataFrame({
        "strike": [190.0, 190.0],
        "type": ["call", "put"],
        "openInterest": [2, 0],
        "gex": [5e4, -1e4],
        "impliedVolatility": [0.30, 0.30],
        "expiry": [exp_dt] * 2,
    })

    packet = build_ai_packet("TEST", df, 185.0, [exp])
    e = packet["market_snapshot"]["expiries"][0]

    assert e["metrics_unavailable"] is True
    assert e["data_quality"] == "insufficient_data"

    for field in (
        "max_pain",
        "max_pain_constrained",
        "max_pain_candidate_band",
        "max_pain_band_fell_back",
        "max_pain_band_boundary_hit",
        "max_pain_selected_lowest_candidate",
        "max_pain_selected_highest_candidate",
    ):
        assert e[field] is None, field

    # a single strike can never host a sign
    # crossing, so gamma_flip is an honest null
    # here too (computed, not forced)
    assert e["gamma_flip"] is None

    # the strike row itself is still exported
    assert len(e["strikes"]) == 1
    assert e["strikes"][0]["strike"] == 190.0
    assert e["strikes"][0]["call_oi"] == 2

    # the raw functions are NOT 'fixed' — the
    # vacuous 190.0 argmin is still what they
    # return on direct misuse; the eligibility
    # gate lives at the packet/plot layer
    assert compute_max_pain(df) == 190.0


def test_payout_eligibility_boundary():
    """Boundary of the <2 distinct finite-OI
    strikes rule: a chain with 2 distinct strikes
    carrying finite OI is eligible (real metrics),
    while a 1-strike chain — even with BOTH the
    call and put row finite — is degenerate, as is
    a 2-strike chain where only one strike has
    finite OI."""
    exp = "2026-10-16"
    exp_dt = pd.Timestamp(exp, tz="UTC")

    # eligible: two distinct strikes, finite OI
    two = pd.DataFrame({
        "strike": [100.0, 110.0, 100.0, 110.0],
        "type": ["call", "call", "put", "put"],
        "openInterest": [500, 300, 200, 50],
        "gex": [-2e5, 1e5, 1e5, 1e5],
        "impliedVolatility": [0.30] * 4,
        "expiry": [exp_dt] * 4,
    })

    packet = build_ai_packet("TEST", two, 105.0, [exp])
    e = packet["market_snapshot"]["expiries"][0]

    assert e["metrics_unavailable"] is False
    assert "data_quality" not in e
    assert e["max_pain"] == _independent_max_pain(two)

    # ineligible: one distinct strike even though
    # BOTH rows carry finite OI — the argmin over
    # a single candidate is vacuous
    one = pd.DataFrame({
        "strike": [100.0, 100.0],
        "type": ["call", "put"],
        "openInterest": [10, 20],
        "gex": [1e5, -2e5],
        "impliedVolatility": [0.30, 0.30],
        "expiry": [exp_dt] * 2,
    })

    packet = build_ai_packet("TEST", one, 105.0, [exp])
    e = packet["market_snapshot"]["expiries"][0]

    assert e["metrics_unavailable"] is True
    assert e["data_quality"] == "insufficient_data"
    assert e["max_pain"] is None

    # ineligible: two strikes but only one carries
    # finite OI — the NaN-OI row at 110 drops out
    one_finite = pd.DataFrame({
        "strike": [100.0, 110.0, 100.0, 110.0],
        "type": ["call", "call", "put", "put"],
        "openInterest": [500, np.nan, 200, np.nan],
        "gex": [-2e5, np.nan, 1e5, np.nan],
        "impliedVolatility": [0.30] * 4,
        "expiry": [exp_dt] * 4,
    })

    packet = build_ai_packet(
        "TEST", one_finite, 105.0, [exp]
    )
    e = packet["market_snapshot"]["expiries"][0]

    assert e["metrics_unavailable"] is True
    assert e["data_quality"] == "insufficient_data"
    assert e["max_pain"] is None


def test_zero_total_oi_chain_insufficient_data():
    """A chain with >=2 distinct finite-OI strikes
    but ZERO total OI produces an all-zero payout
    curve — every candidate is an argmin, so the
    'max pain' strike is meaningless. It must be
    flagged insufficient_data, not emit the
    lowest-strike artifact."""
    exp = "2026-10-16"
    exp_dt = pd.Timestamp(exp, tz="UTC")

    df = pd.DataFrame({
        "strike": [100.0, 110.0, 100.0, 110.0],
        "type": ["call", "call", "put", "put"],
        "openInterest": [0, 0, 0, 0],
        "gex": [-2e5, 1e5, 1e5, 1e5],
        "impliedVolatility": [0.30] * 4,
        "expiry": [exp_dt] * 4,
    })

    packet = build_ai_packet("TEST", df, 105.0, [exp])
    e = packet["market_snapshot"]["expiries"][0]

    assert e["metrics_unavailable"] is True
    assert e["data_quality"] == "insufficient_data"
    assert e["max_pain"] is None
    # gamma_flip is OI-independent and this gex
    # profile has a material crossing at 110 —
    # partial availability: it is still emitted
    assert e["gamma_flip"] == 110.0


def test_partial_availability_gamma_flip_survives():
    """Per-metric availability: an expiry whose
    payout inputs are degenerate but whose gex
    profile has a material crossing must emit
    metrics_unavailable:true (payout fields null)
    while STILL reporting the real gamma_flip —
    'no usable payout data' and 'no crossing' are
    distinct states."""
    exp = "2026-10-16"
    exp_dt = pd.Timestamp(exp, tz="UTC")

    # payout-degenerate: only strike 100 has
    # finite OI. The gex profile crosses
    # neg->pos at 110 (the NaN-OI row still
    # carries finite gex in this synthetic df;
    # on the real data path NaN OI implies
    # NaN gex via compute_gex, but the metrics
    # must not presume that coupling).
    df = pd.DataFrame({
        "strike": [100.0, 110.0],
        "type": ["call", "call"],
        "openInterest": [2, np.nan],
        "gex": [-1e5, 2e5],
        "impliedVolatility": [0.30, 0.30],
        "expiry": [exp_dt] * 2,
    })

    packet = build_ai_packet("TEST", df, 105.0, [exp])
    e = packet["market_snapshot"]["expiries"][0]

    assert e["metrics_unavailable"] is True
    assert e["data_quality"] == "insufficient_data"

    for field in (
        "max_pain",
        "max_pain_constrained",
        "max_pain_candidate_band",
        "max_pain_band_fell_back",
        "max_pain_band_boundary_hit",
        "max_pain_selected_lowest_candidate",
        "max_pain_selected_highest_candidate",
    ):
        assert e[field] is None, field

    # the flip is real and is still reported
    assert e["gamma_flip"] == 110.0


# =========================
# Scoped failure absorption
# =========================
def _healthy_book(exp_dt):
    """A plain eligible expiry used by the
    injection tests below."""
    return pd.DataFrame({
        "strike": [100.0, 110.0, 100.0, 110.0],
        "type": ["call", "call", "put", "put"],
        "openInterest": [500, 300, 200, 50],
        "gex": [-2e5, 1e5, 1e5, 1e5],
        "impliedVolatility": [0.30] * 4,
        "expiry": [exp_dt] * 4,
    })


def test_unrelated_valueerror_still_propagates(
    monkeypatch,
):
    """The scoped absorption must NOT mask an
    unrelated ValueError: injecting a ValueError
    whose message is NOT numpy's empty-argmin
    error into the payout path must propagate
    and kill the packet build loudly."""
    exp = "2026-10-16"
    exp_dt = pd.Timestamp(exp, tz="UTC")
    df = _healthy_book(exp_dt)

    def boom(frame, spot=None):
        raise ValueError("unrelated bug state")

    monkeypatch.setattr(
        GexDashboard, "compute_max_pain", boom
    )

    with pytest.raises(
        ValueError, match="unrelated bug"
    ):
        build_ai_packet("TEST", df, 105.0, [exp])


def test_known_empty_argmin_valueerror_absorbed(
    monkeypatch,
):
    """Belt-and-suspenders: if the payout path
    raises numpy's 'attempt to get argmin of an
    empty sequence' DESPITE the eligibility
    pre-check, it is still the recognized
    missing-data failure mode and degrades to
    metrics_unavailable + insufficient_data."""
    exp = "2026-10-16"
    exp_dt = pd.Timestamp(exp, tz="UTC")
    df = _healthy_book(exp_dt)

    def boom(frame, spot=None):
        raise ValueError(
            "attempt to get argmin of an "
            "empty sequence"
        )

    monkeypatch.setattr(
        GexDashboard, "compute_max_pain", boom
    )

    packet = build_ai_packet("TEST", df, 105.0, [exp])
    e = packet["market_snapshot"]["expiries"][0]

    assert e["metrics_unavailable"] is True
    assert e["data_quality"] == "insufficient_data"
    assert e["max_pain"] is None
    # gamma_flip is unaffected by the payout
    # failure — still computed and real
    assert e["gamma_flip"] == 110.0


def test_non_valueerror_failures_propagate(
    monkeypatch,
):
    """Regression-guards the F4 review gap: only
    the scoped ValueError is tolerated at the
    packet boundary — a non-ValueError failure
    inside the metric path must propagate."""
    exp = "2026-10-16"
    exp_dt = pd.Timestamp(exp, tz="UTC")
    df = _healthy_book(exp_dt)

    def key_boom(frame, spot=None):
        raise KeyError("openInterest")

    monkeypatch.setattr(
        GexDashboard, "compute_max_pain", key_boom
    )

    with pytest.raises(KeyError):
        build_ai_packet("TEST", df, 105.0, [exp])

    def type_boom(frame, spot=None):
        raise TypeError("object dtype")

    monkeypatch.setattr(
        GexDashboard, "compute_max_pain", type_boom
    )

    with pytest.raises(TypeError):
        build_ai_packet("TEST", df, 105.0, [exp])

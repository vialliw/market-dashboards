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

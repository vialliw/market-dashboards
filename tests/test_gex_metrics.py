"""Offline tests for the issue-#1 fixes in GexDashboard.py:

- compute_gamma_flip: materiality filter + nearest-to-spot
  crossing + honest None when no material crossing exists.
- compute_max_pain: +/-30% spot band with all-strikes fallback.
- build_ai_packet: stale-IV (>200%) flagging on strike objects.

All tests run OFFLINE against tests/fixtures/TSM_ai_packet.json
(a real ai_packet produced by this pipeline; spot = 422.05).
No network access is used anywhere in this file.
"""

import json
from pathlib import Path

import pandas as pd

from GexDashboard import (
    build_ai_packet,
    compute_gamma_flip,
    compute_max_pain,
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
    crossings (plain Python, separate implementation)."""
    grouped = (
        df.groupby("strike")["gex"].sum().sort_index()
    )

    pairs = list(zip(grouped.index, grouped.values))

    if not pairs:
        return []

    floor = 0.01 * max(abs(g) for _, g in pairs)

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


# =========================
# Max Pain
# =========================
def test_max_pain_all_expiries_within_spot_band():
    packet = _load_packet()

    for e in packet["market_snapshot"]["expiries"]:

        mp = compute_max_pain(_oi_df(e), spot=SPOT)

        assert BAND_LO <= mp <= BAND_HI, (
            f"{e['expiry']}: max_pain {mp} outside "
            f"[{BAND_LO}, {BAND_HI}]"
        )

        chain = {s["strike"] for s in e["strikes"]}
        assert mp in chain


def test_max_pain_band_excludes_far_otm_drag():
    """Massive deep-OTM call OI must not drag max pain
    far below spot once the band is applied."""
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

    unbounded = compute_max_pain(df)
    banded = compute_max_pain(df, spot=SPOT)

    # unbounded is dragged to the 200 artifact strike
    assert unbounded == 200.0
    # banded result stays inside the spot band
    assert banded == 420.0
    assert BAND_LO <= banded <= BAND_HI


def test_max_pain_band_fallback_when_empty():
    """If the spot band contains no strikes, fall back
    to the full chain (same as spot=None)."""
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
    banded = compute_max_pain(df, spot=1000.0)
    unbounded = compute_max_pain(df)

    assert banded == unbounded
    assert banded in {100.0, 200.0, 300.0}


def test_max_pain_backward_compatible_no_spot():
    packet = _load_packet()
    e = packet["market_snapshot"]["expiries"][0]

    mp = compute_max_pain(_oi_df(e))

    chain = {s["strike"] for s in e["strikes"]}
    assert mp in chain


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
    # OI / GEX fields must be preserved
    assert stale["open_interest"] == 70
    assert stale["call_oi"] == 30
    assert stale["put_oi"] == 40
    assert stale["net_gex"] == 200000.0

    normal = by_strike[100.0]
    assert normal["implied_volatility"] == 0.30
    assert normal.get("stale_iv") is not True

    # flag is documented in the packet
    assert "stale_iv" in packet["meta"]["data_quality"]

# v04d: Cater for US option market trading hour
import os
import json
import argparse
import numpy as np
import pandas as pd
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta, time, UTC
import pytz


def is_market_open():
    market_open = time(9, 30)  # 9:30 AM
    market_close = time(16, 0)  # 4:00 PM

    eastern_tz = pytz.timezone('US/Eastern')
    now = datetime.now(eastern_tz)
    if now.isoweekday() >= 6:  # Saturday or Sunday
        return False
    now_time = now.time()
    return market_open <= now_time <= market_close

# =========================
# JSON Safe Serializer
# =========================
def json_safe(obj):

    if isinstance(obj, np.integer):
        return int(obj)

    if isinstance(obj, np.floating):
        return float(obj)

    if isinstance(obj, np.bool_):
        return bool(obj)

    if pd.isna(obj):
        return None

    raise TypeError(
        f"Type not serializable: {type(obj)}"
    )


# =========================
# Black-Scholes Gamma
# =========================
def black_scholes_gamma(S, K, T, r, sigma):

    from scipy.stats import norm

    if T <= 0 or sigma <= 0:
        return 0.0

    # FIXED d1 FORMULA
    d1 = (
        np.log(S / K)
        + (r + 0.5 * sigma**2) * T
    ) / (sigma * np.sqrt(T))

    return norm.pdf(d1) / (S * sigma * np.sqrt(T))


# =========================
# Compute GEX
# =========================
def compute_gex(df, spot, r=0.01):

    today = datetime.now(UTC)

    gex_vals = []

    for _, row in df.iterrows():

        iv = row.get('impliedVolatility', 0)

        if pd.isna(iv) or iv <= 0:
            gex_vals.append(0.0)
            continue

        T = max(
            (
                row['expiry'].timestamp()
                - today.timestamp()
            ) / (365 * 24 * 3600),
            1e-6
        )

        gamma = black_scholes_gamma(
            spot,
            row['strike'],
            T,
            r,
            iv
        )

        sign = 1 if row['type'] == 'call' else -1

        gex = (
            gamma
            * row['openInterest']
            * 100
            * spot
            * sign
        )

        gex_vals.append(gex)

    df['gex'] = gex_vals

    return df


# =========================
# Max Pain
# =========================
def _payout_pain(df, candidate_strikes):

    pains = []

    # justified: max pain requires evaluating
    # total pain at every candidate strike over
    # all option rows; kept as the existing
    # O(strikes x rows) loop for readability.
    for s in candidate_strikes:

        pain = 0

        for _, row in df.iterrows():

            if row['type'] == 'call':

                pain += (
                    max(0, s - row['strike'])
                    * row['openInterest']
                )

            else:

                pain += (
                    max(0, row['strike'] - s)
                    * row['openInterest']
                )

        pains.append(pain)

    return pains


def compute_max_pain(df, spot=None):
    """Canonical max pain: the UNCONSTRAINED
    strike that minimizes total option payout
    across all strikes in the chain.

    `spot` is accepted for backward
    compatibility but does NOT restrict the
    candidate set — use
    compute_max_pain_constrained() for the
    banded variant and its metadata."""

    strikes = sorted(df['strike'].unique())

    pains = _payout_pain(df, strikes)

    return strikes[int(np.argmin(pains))]


def compute_max_pain_constrained(df, spot=None):
    """Constrained max-pain variant: the payout
    minimum restricted to strikes inside
    [0.7*spot, 1.3*spot]. This is a constrained
    payout minimum, NOT a 'nearest-to-spot'
    metric.

    Returns a dict with the constrained argmin
    plus honest band metadata:

      max_pain          - constrained payout
                          minimum strike
      candidate_band    - [0.7*spot, 1.3*spot],
                          or None when spot=None
      band_fell_back    - True when the band
                          emptied the candidate
                          set and ALL strikes
                          were used instead
      band_boundary_hit - True when the band
                          was active and the
                          argmin landed exactly
                          on a band edge
                          (distinct from an
                          empty-band fallback)"""

    strikes = sorted(df['strike'].unique())

    result = {
        "candidate_band": None,
        "band_fell_back": False,
        "band_boundary_hit": False,
    }

    candidates = strikes

    if spot is not None:

        band = [0.7 * spot, 1.3 * spot]

        result["candidate_band"] = band

        bounded = [
            s
            for s in strikes
            if band[0] <= s <= band[1]
        ]

        if bounded:

            candidates = bounded

        else:

            # Fall back to all strikes if the
            # spot band empties the candidate
            # set.
            result["band_fell_back"] = True

    pains = _payout_pain(df, candidates)

    best = candidates[int(np.argmin(pains))]

    if (
        result["candidate_band"] is not None
        and not result["band_fell_back"]
        and best in result["candidate_band"]
    ):
        result["band_boundary_hit"] = True

    result["max_pain"] = best

    return result


# =========================
# Gamma Flip
# =========================

# Absolute materiality floor. gex units are
# dollar-gamma in $M per 1% move
# (gamma * OI * 100 * spot^2 / 1e8), so 1e-3
# is roughly $1k of notional gamma — a slice
# whose entire |gex| profile is below this is
# numerical noise, not signal.
MIN_MATERIAL_GEX = 1e-3


def compute_gamma_flip(df, spot=None):

    # Drop rows with nonfinite strike or gex so
    # NaN/inf artifacts cannot anchor a crossing
    # or poison the grouped profile.
    df = df[
        np.isfinite(df['strike'])
        & np.isfinite(df['gex'])
    ]

    grouped = (
        df.groupby('strike')['gex']
        .sum()
        .sort_index()
    )

    strikes = grouped.index.values
    gex_vals = grouped.values

    if len(gex_vals) == 0:
        return None

    max_abs = np.abs(gex_vals).max()

    # The whole slice is numerical noise — there
    # is no honest flip here.
    if max_abs < MIN_MATERIAL_GEX:
        return None

    # Materiality filter: a strike is material
    # iff its |gex| clears BOTH 1% of the slice
    # max AND the absolute floor (~$1k notional
    # gamma). Zero-gex strikes can never be
    # material, so they never anchor a crossing.
    materiality = max(
        0.01 * max_abs,
        MIN_MATERIAL_GEX
    )

    material = (
        np.abs(gex_vals) >= materiality
    )

    m_strikes = strikes[material]
    m_gex = gex_vals[material]

    # Negative -> positive crossings between
    # consecutive material strikes.
    crossing_idx = (
        np.where(
            (m_gex[:-1] < 0)
            & (m_gex[1:] > 0)
        )[0] + 1
    )

    if len(crossing_idx) == 0:
        return None

    crossings = m_strikes[crossing_idx]

    if spot is None:
        return crossings[0]

    return crossings[
        np.argmin(np.abs(crossings - spot))
    ]


# =========================
# Top Walls
# =========================
def compute_top_walls(df, top_n=3):

    calls = df[df['type'] == 'call']
    puts = df[df['type'] == 'put']

    call_walls = (
        calls.groupby('strike')['openInterest']
        .sum()
        .sort_values(ascending=False)
        .head(top_n)
    )

    put_walls = (
        puts.groupby('strike')['openInterest']
        .sum()
        .sort_values(ascending=False)
        .head(top_n)
    )

    return call_walls, put_walls


# =========================
# Fetch Options
# =========================
def fetch_options(ticker, months):

    tk = yf.Ticker(ticker)

    spot = (
        tk.history(period="1d")['Close']
        .iloc[-1]
    )

    expiries = tk.options

    cutoff = (
        datetime.now(UTC)
        + timedelta(days=30 * months)
    )

    selected = []

    for e in expiries:

        exp_dt = (
            datetime.strptime(e, "%Y-%m-%d")
            .replace(tzinfo=UTC)
        )

        if exp_dt <= cutoff:
            selected.append(e)

    all_data = []

    for exp in selected:

        chain = tk.option_chain(exp)

        calls = chain.calls.copy()
        calls['type'] = 'call'

        puts = chain.puts.copy()
        puts['type'] = 'put'

        df = pd.concat([calls, puts])

        df['expiry'] = (
            datetime.strptime(exp, "%Y-%m-%d")
            .replace(tzinfo=UTC)
        )

        all_data.append(df)

    return pd.concat(all_data), spot, selected


# =========================
# Navigation HTML
# =========================
def create_navigation_html(
    current_ticker: str,
    tickers_list: list,
    link_suffix: str
) -> str:

    ticker_links = []

    for ticker in tickers_list:

        if ticker == current_ticker:

            ticker_links.append(
                f'<span style="color: #00FFAA; '
                f'font-weight: bold; '
                f'margin: 0 10px;">'
                f'{ticker}</span>'
            )

        else:

            filename = f"{ticker}{link_suffix}"

            ticker_links.append(
                f'<a href="./{filename}" '
                f'style="color: #CCCCCC; '
                f'text-decoration: none; '
                f'margin: 0 10px;">'
                f'{ticker}</a>'
            )

    nav_html = f"""
    <div style="
        padding: 12px 20px;
        background-color: #1f1f1f;
        border-bottom: 1px solid #444;
        font-family: Arial, sans-serif;
        text-align: center;
        font-size: 15px;
    ">
        <strong style="
            color: #4A90E2;
            margin-right: 15px;
        ">
            GEX Dashboard:
        </strong>

        {' '.join(ticker_links)}

        <a href="./index.html" style="
            color: #CCCCCC;
            text-decoration: none;
            margin-left: 15px;
        ">← All dashboards</a>
        <a href="https://github.com/vialliw/market-dashboards" target="_blank" rel="noopener" style="
            color: #CCCCCC;
            text-decoration: none;
            opacity: 0.55;
            font-size: 12px;
            margin-left: 14px;
        ">by Vialli Wong</a>
    </div>
    """

    return nav_html


# =========================
# Theme Selector HTML
# =========================
def create_theme_selector_html() -> str:

    selector_html = """
    <div id="gex-theme-selector" style="
        position: fixed;
        top: 10px;
        right: 16px;
        z-index: 10000;
        font-family: Arial, sans-serif;
        font-size: 13px;
        background-color: #ffffff;
        border: 1px solid #ccc;
        border-radius: 4px;
        padding: 4px 8px;
    ">
        <label
            for="gex-theme-select"
            style="margin-right: 6px;"
        >Theme</label>
        <select
            id="gex-theme-select"
            onchange="gexApplyTheme(this.value)"
        >
            <option value="light" selected>Light</option>
            <option value="dark">Dark</option>
        </select>
    </div>
    """

    return selector_html


# =========================
# Theme Script
# =========================
def create_theme_script() -> str:

    theme_script = """
    <script>
    function gexApplyTheme(theme) {
        try { localStorage.setItem('vp_theme', theme); } catch (e) {}
        var isDark = (theme === 'dark');
        var bg = isDark ? '#1e1e1e' : '#ffffff';
        var fontColor = isDark ? '#e8e8e8' : '#1a1a1a';
        var grid = isDark ? '#444444' : '#d9d9d9';
        var spotColor = isDark ? '#ffffff' : '#000000';
        document.body.style.backgroundColor =
            isDark ? '#121212' : '#ffffff';
        var gd =
            document.getElementsByClassName(
                'plotly-graph-div'
            )[0];
        if (!gd || !gd.layout) return;
        var update = {
            'paper_bgcolor': bg,
            'plot_bgcolor': bg,
            'font.color': fontColor
        };
        Object.keys(gd.layout).forEach(function (k) {
            if (/^[xy]axis[0-9]*$/.test(k)) {
                update[k + '.gridcolor'] = grid;
                update[k + '.zerolinecolor'] = grid;
            }
        });
        var shapes = gd.layout.shapes || [];
        for (var i = 0; i < shapes.length; i++) {
            var lc = shapes[i].line && shapes[i].line.color;
            var norm = String(lc).replace(/\\s/g, '');
            if (
                isDark &&
                (norm === 'black' ||
                 norm === 'rgb(0,0,0)' ||
                 norm === '#000' ||
                 norm === '#000000')
            ) {
                update['shapes[' + i + '].line.color'] =
                    '#ffffff';
            } else if (
                !isDark &&
                (norm === 'white' ||
                 norm === 'rgb(255,255,255)' ||
                 norm === '#fff' ||
                 norm === '#ffffff')
            ) {
                update['shapes[' + i + '].line.color'] =
                    'black';
            }
        }
        Plotly.relayout(gd, update);
        var spotIdx = -1;
        (gd.data || []).forEach(function (t, i) {
            if (t.name === 'Spot') spotIdx = i;
        });
        if (spotIdx >= 0) {
            Plotly.restyle(
                gd,
                {'line.color': spotColor},
                [spotIdx]
            );
        }
    }
    window.addEventListener('load', function () {
        var saved = 'light';
        try {
            saved =
                localStorage.getItem('vp_theme') || 'light';
        } catch (e) {}
        var sel =
            document.getElementById('gex-theme-select');
        if (sel) sel.value = saved;
        gexApplyTheme(saved);
    });
    </script>
    """

    return theme_script


# =========================
# Build AI Packet
# =========================
def build_ai_packet(
    ticker,
    df,
    spot,
    expiries
):

    generated_utc = (
        datetime.now(UTC)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )

    total_net_gex = float(df['gex'].sum())

    overall_gamma_regime = (
        "+Gamma"
        if total_net_gex > 0
        else "-Gamma"
    )

    overall_gamma_flip = compute_gamma_flip(
        df,
        spot=spot
    )

    expiry_objects = []

    for exp in expiries:

        exp_dt = (
            datetime.strptime(exp, "%Y-%m-%d")
            .replace(tzinfo=UTC)
        )

        sub = df[df['expiry'] == exp_dt].copy()

        grouped = (
            sub.groupby('strike')
            .agg({
                'openInterest': 'sum',
                'gex': 'sum',
                'impliedVolatility': 'mean'
            })
            .reset_index()
        )

        call_oi = (
            sub[sub['type'] == 'call']
            .groupby('strike')['openInterest']
            .sum()
        )

        put_oi = (
            sub[sub['type'] == 'put']
            .groupby('strike')['openInterest']
            .sum()
        )

        call_gex = (
            sub[sub['type'] == 'call']
            .groupby('strike')['gex']
            .sum()
        )

        put_gex = (
            sub[sub['type'] == 'put']
            .groupby('strike')['gex']
            .sum()
        )

        net_gex = float(sub['gex'].sum())

        gamma_regime = (
            "+Gamma"
            if net_gex > 0
            else "-Gamma"
        )

        max_pain = compute_max_pain(sub)

        max_pain_constrained = (
            compute_max_pain_constrained(
                sub,
                spot=spot
            )
        )

        gamma_flip = compute_gamma_flip(
            sub,
            spot=spot
        )

        call_walls, put_walls = compute_top_walls(sub)

        atm_strike = min(
            grouped['strike'],
            key=lambda x: abs(x - spot)
        )

        atm_iv = grouped[
            grouped['strike'] == atm_strike
        ]['impliedVolatility'].values[0]

        strike_objects = []

        for _, row in grouped.iterrows():

            strike = float(row['strike'])

            iv = float(row['impliedVolatility'])

            # IV above 200% is a stale quote
            # artifact, not a real vol reading.
            stale_iv = iv > 2.0

            strike_obj = {

                "strike": strike,

                "open_interest": int(
                    row['openInterest']
                ),

                "call_oi": int(
                    call_oi.get(strike, 0)
                ),

                "put_oi": int(
                    put_oi.get(strike, 0)
                ),

                "net_gex": float(
                    row['gex']
                ),

                "call_gex": float(
                    call_gex.get(strike, 0)
                ),

                "put_gex": float(
                    put_gex.get(strike, 0)
                ),

                "implied_volatility": (
                    None if stale_iv else iv
                ),

                "is_call_wall": bool(
                    strike in call_walls.index
                ),

                "is_put_wall": bool(
                    strike in put_walls.index
                ),

                "is_gamma_flip": bool(
                    strike == gamma_flip
                ),

                "distance_from_spot_pct": float(
                    ((strike - spot) / spot) * 100
                )
            }

            if stale_iv:
                strike_obj["stale_iv"] = True
                # Keep the flagged quote for
                # provenance — consumers may
                # audit or exclude these rows.
                strike_obj["raw_implied_volatility"] = iv

            strike_objects.append(strike_obj)

        expiry_objects.append({

            "expiry": exp,

            "net_gex": net_gex,

            "gamma_regime": gamma_regime,

            "max_pain": float(max_pain),

            "max_pain_constrained": float(
                max_pain_constrained["max_pain"]
            ),

            "max_pain_candidate_band": [
                float(b)
                for b in (
                    max_pain_constrained[
                        "candidate_band"
                    ]
                )
            ],

            "max_pain_band_fell_back": bool(
                max_pain_constrained[
                    "band_fell_back"
                ]
            ),

            "max_pain_band_boundary_hit": bool(
                max_pain_constrained[
                    "band_boundary_hit"
                ]
            ),

            "gamma_flip": (
                float(gamma_flip)
                if gamma_flip
                else None
            ),

            "atm_iv": float(atm_iv),

            "call_walls": [
                {
                    "strike": float(k),
                    "oi": int(v)
                }
                for k, v in call_walls.items()
            ],

            "put_walls": [
                {
                    "strike": float(k),
                    "oi": int(v)
                }
                for k, v in put_walls.items()
            ],

            "strikes": strike_objects
        })

    ai_packet = {

        "meta": {

            "ticker": ticker,

            "generated_utc": generated_utc,

            "schema_version": "1.0",

            "source": "yfinance",

            "analysis_type": "gamma_exposure",

            "data_quality": (
                "Strikes flagged 'stale_iv': true "
                "had stale implied_volatility "
                "(>200%) replaced with null; the "
                "original quote is preserved in "
                "'raw_implied_volatility' for "
                "provenance. Their OI and GEX "
                "fields are retained-with-flag — "
                "IV-derived gamma for these "
                "strikes is suspect and "
                "consumers may exclude them. "
                "'gamma_flip' is null when "
                "no material sign crossing exists "
                "for that expiry."
            ),

            "metric_definitions": {

                "gamma_flip": (
                    "Strike-profile proxy at the "
                    "current spot: the material "
                    "negative->positive net-gex "
                    "sign crossing nearest spot "
                    "under a modeled "
                    "call-long/put-short dealer "
                    "convention. It is NOT a "
                    "repriced-chain zero-gamma "
                    "flip."
                ),

                "materiality_floor": (
                    "A strike's gex is material "
                    "iff |gex| >= max(1% of the "
                    "slice's max |gex|, 1e-3 "
                    "$M-per-1% ~= $1k notional "
                    "gamma). Sub-floor slices "
                    "return gamma_flip = null."
                ),

                "max_pain": (
                    "Canonical UNCONSTRAINED max "
                    "pain: the strike minimizing "
                    "total option payout across "
                    "all strikes in the chain."
                ),

                "max_pain_constrained": (
                    "Constrained payout minimum: "
                    "the payout argmin restricted "
                    "to 'max_pain_candidate_band' "
                    "([0.7*spot, 1.3*spot]) — NOT "
                    "a 'nearest-to-spot' metric. "
                    "'max_pain_band_fell_back' "
                    "marks the empty-band "
                    "fallback to all strikes; "
                    "'max_pain_band_boundary_hit' "
                    "marks a minimum landing "
                    "exactly on a band edge."
                )
            }
        },

        "system_prompt": (
            "You are an institutional options "
            "market strategist specializing in "
            "dealer positioning, gamma exposure, "
            "volatility regime analysis, and "
            "market microstructure."
        ),

        "analysis_query": (
            "Analyze the gamma exposure, "
            "opened interest data to predict "
            "market maker plan to their interest, "
            "show gamma flip strikes, "
            "call/put wall with OI, "
            f"stock, {ticker}"
        ),

        "instructions": {

            "focus_priority": [

                "gamma_flip",

                "call_wall",

                "put_wall",

                "net_gex",

                "expiry_positioning",

                "spot_vs_flip"
            ],

            "interpretation_rules": {

                "positive_gamma": (
                    "Expect lower volatility "
                    "and mean reversion"
                ),

                "negative_gamma": (
                    "Expect higher volatility "
                    "and trend acceleration"
                ),

                "spot_below_gamma_flip": (
                    "Elevated downside "
                    "instability risk"
                ),

                "spot_above_gamma_flip": (
                    "Dealer hedging likely "
                    "stabilizing"
                )
            },

            "response_style": (
                "institutional technical concise"
            )
        },

        "market_snapshot": {

            "spot": float(spot),

            "total_net_gex": total_net_gex,

            "overall_gamma_regime": (
                overall_gamma_regime
            ),

            "nearest_gamma_flip": (
                float(overall_gamma_flip)
                if overall_gamma_flip
                else None
            ),

            "expiries": expiry_objects
        },

        "expected_output": {

            "format": "markdown",

            "sections": [

                "Market Structure",

                "Dealer Positioning",

                "Gamma Flip Analysis",

                "Call and Put Walls",

                "Volatility Outlook",

                "Directional Bias",

                "Risk Scenario",

                "Key Price Levels"
            ]
        }
    }

    return ai_packet


# =========================
# Export AI Packet
# =========================
def export_ai_packet(
    ai_packet,
    ticker,
    output_dir
):

    os.makedirs(output_dir, exist_ok=True)

    output_path = os.path.join(
        output_dir,
        f"{ticker}_ai_packet.json"
    )

    with open(
        output_path,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            ai_packet,
            f,
            indent=2,
            ensure_ascii=False,
            default=json_safe
        )

    return output_path


# =========================
# Main Plot Function
# =========================
def plot_gex_dashboard(
    ticker,
    months,
    output_dir,
    link_suffix="_gex_dashboard.html"
):

    os.makedirs(output_dir, exist_ok=True)

    df, spot, expiries = fetch_options(
        ticker,
        months
    )

    df = compute_gex(df, spot)

    # =========================
    # AI PACKET EXPORT
    # =========================
    ai_packet = build_ai_packet(
        ticker,
        df,
        spot,
        expiries
    )

    ai_packet_path = export_ai_packet(
        ai_packet,
        ticker,
        output_dir
    )

    print(
        f"AI Packet Saved: "
        f"{ai_packet_path}"
    )

    strike_min = df['strike'].min()
    strike_max = df['strike'].max()
    oi_max = df['openInterest'].max()

    # =========================
    # Subplot Titles
    # =========================
    subplot_titles = []

    for exp in expiries:

        exp_dt = (
            datetime.strptime(exp, "%Y-%m-%d")
            .replace(tzinfo=UTC)
        )

        sub = df[df['expiry'] == exp_dt]

        net_gex = sub['gex'].sum()

        regime = (
            "+Gamma"
            if net_gex > 0
            else "-Gamma"
        )

        color = (
            "green"
            if net_gex > 0
            else "red"
        )

        net_gex_b = net_gex / 1e9

        title = (
            f"{exp} | "
            f"<span style='color:{color}'>"
            f"{regime} "
            f"({net_gex_b:.2f}B)"
            f"</span>"
        )

        subplot_titles.append(title)

    fig = make_subplots(
        rows=len(expiries),
        cols=1,
        shared_xaxes=True,
        subplot_titles=subplot_titles,
        specs=[
            [{"secondary_y": True}]
            for _ in expiries
        ]
    )

    legend_flags = {
        "oi": True,
        "gex": True,
        "call_wall": True,
        "put_wall": True,
        "gamma_flip": True,
        "max_pain": True,
        "max_pain_constrained": True,
        "spot": True
    }

    # =========================
    # Per Expiry Plot
    # =========================
    for i, exp in enumerate(expiries, start=1):

        exp_dt = (
            datetime.strptime(exp, "%Y-%m-%d")
            .replace(tzinfo=UTC)
        )

        sub = df[df['expiry'] == exp_dt]

        grouped = (
            sub.groupby('strike')
            .agg({
                'openInterest': 'sum',
                'gex': 'sum',
                'impliedVolatility': 'mean'
            })
            .reset_index()
        )

        max_pain = compute_max_pain(sub)

        max_pain_constrained = (
            compute_max_pain_constrained(
                sub,
                spot=spot
            )["max_pain"]
        )

        gamma_flip = compute_gamma_flip(
            sub,
            spot=spot
        )

        call_walls, put_walls = (
            compute_top_walls(sub)
        )

        atm_strike = min(
            grouped['strike'],
            key=lambda x: abs(x - spot)
        )

        atm_iv = grouped[
            grouped['strike'] == atm_strike
        ]['impliedVolatility'].values[0]

        # =========================
        # Open Interest
        # =========================
        fig.add_trace(
            go.Bar(
                x=grouped['strike'],
                y=grouped['openInterest'],
                marker_color="yellow",
                opacity=0.95,
                name="Open Interest",
                showlegend=legend_flags["oi"]
            ),
            row=i,
            col=1,
            secondary_y=False
        )

        legend_flags["oi"] = False

        # =========================
        # GEX
        # =========================
        fig.add_trace(
            go.Scatter(
                x=grouped['strike'],
                y=grouped['gex'],
                mode='lines',
                line=dict(color="purple"),
                name="Gamma Exposure",
                showlegend=legend_flags["gex"]
            ),
            row=i,
            col=1,
            secondary_y=True
        )

        legend_flags["gex"] = False

        # =========================
        # Call Walls
        # =========================
        for strike, oi in call_walls.items():

            fig.add_vline(
                x=strike,
                line_color="green",
                row=i,
                col=1
            )

            fig.add_trace(
                go.Scatter(
                    x=[strike],
                    y=[0],
                    mode="markers",
                    marker=dict(
                        color="green",
                        size=8
                    ),
                    name="Call Wall",
                    hovertemplate=(
                        f"Call Wall<br>"
                        f"Strike: {strike}<br>"
                        f"OI: {oi}"
                        f"<extra></extra>"
                    ),
                    showlegend=legend_flags["call_wall"]
                ),
                row=i,
                col=1
            )

            legend_flags["call_wall"] = False

        # =========================
        # Put Walls
        # =========================
        for strike, oi in put_walls.items():

            fig.add_vline(
                x=strike,
                line_color="red",
                row=i,
                col=1
            )

            fig.add_trace(
                go.Scatter(
                    x=[strike],
                    y=[0],
                    mode="markers",
                    marker=dict(
                        color="red",
                        size=8
                    ),
                    name="Put Wall",
                    hovertemplate=(
                        f"Put Wall<br>"
                        f"Strike: {strike}<br>"
                        f"OI: {oi}"
                        f"<extra></extra>"
                    ),
                    showlegend=legend_flags["put_wall"]
                ),
                row=i,
                col=1
            )

            legend_flags["put_wall"] = False

        # =========================
        # Max Pain
        # =========================
        # Canonical unconstrained max pain.
        fig.add_vline(
            x=max_pain,
            line_color="cyan",
            line_dash="dot",
            row=i,
            col=1
        )

        # Separately labeled constrained variant
        # (payout minimum inside the
        # 0.7x-1.3x spot band).
        fig.add_vline(
            x=max_pain_constrained,
            line_color="teal",
            line_dash="dashdot",
            row=i,
            col=1
        )

        if legend_flags["max_pain"]:

            fig.add_trace(
                go.Scatter(
                    x=[None],
                    y=[None],
                    mode="lines",
                    line=dict(
                        color="cyan",
                        dash="dot"
                    ),
                    name="Max Pain"
                ),
                row=i,
                col=1
            )

            legend_flags["max_pain"] = False

        if legend_flags["max_pain_constrained"]:

            fig.add_trace(
                go.Scatter(
                    x=[None],
                    y=[None],
                    mode="lines",
                    line=dict(
                        color="teal",
                        dash="dashdot"
                    ),
                    name=(
                        "Max Pain "
                        "(0.7x-1.3x spot band)"
                    )
                ),
                row=i,
                col=1
            )

            legend_flags["max_pain_constrained"] = False

        # =========================
        # Gamma Flip
        # =========================
        if gamma_flip:

            fig.add_vline(
                x=gamma_flip,
                line_color="orange",
                line_dash="dash",
                row=i,
                col=1
            )

            if legend_flags["gamma_flip"]:

                fig.add_trace(
                    go.Scatter(
                        x=[None],
                        y=[None],
                        mode="lines",
                        line=dict(
                            color="orange",
                            dash="dash"
                        ),
                        name="Gamma Flip"
                    ),
                    row=i,
                    col=1
                )

                legend_flags["gamma_flip"] = False

        # =========================
        # Spot
        # =========================
        fig.add_vline(
            x=spot,
            line_color="black",
            row=i,
            col=1
        )

        if legend_flags["spot"]:

            fig.add_trace(
                go.Scatter(
                    x=[None],
                    y=[None],
                    mode="lines",
                    line=dict(color="black"),
                    name="Spot"
                ),
                row=i,
                col=1
            )

            legend_flags["spot"] = False

        # =========================
        # ATM IV Annotation
        # =========================
        fig.add_annotation(
            x=spot,
            y=oi_max * 0.9,
            text=f"ATM IV: {atm_iv:.2%}",
            showarrow=False,
            row=i,
            col=1
        )

        fig.update_xaxes(
            range=[strike_min, strike_max],
            row=i,
            col=1
        )

    london_tz = pytz.timezone('Europe/London')

    now_london = datetime.now(london_tz)

    london_tz_name = (
        "GMT Daylight Time"
        if now_london.dst()
        else "GMT Standard Time"
    )

    generated_time = (
        now_london.strftime("%Y/%m/%d %H:%M")
        + " "
        + london_tz_name
    )

    # =========================
    # Layout
    # =========================
    fig.update_layout(

        height=320 * len(expiries),

        title=(
            f"{ticker} Options Positioning Dashboard "
            f"as of {generated_time}"
        ),

        plot_bgcolor="white",

        paper_bgcolor="white",

        legend=dict(
            orientation="h",
            y=1.05,
            x=0,
        ),
    )

    fig.update_xaxes(
        showgrid=True,
        gridcolor="lightgrey"
    )

    fig.update_yaxes(
        showgrid=True,
        gridcolor="lightgrey"
    )

    # =========================
    # Save HTML + Navigation
    # =========================
    output_filename = f"{ticker}{link_suffix}"

    output_path = os.path.join(
        output_dir,
        output_filename
    )

    html_str = fig.to_html(
        full_html=True,
        include_plotlyjs='cdn'
    )

    nav_html = create_navigation_html(
        ticker,
        GAMMA_TICKERS_LIST,
        link_suffix
    )

    theme_html = create_theme_selector_html()

    html_str = html_str.replace(
        '<body>',
        f'<body>\n{nav_html}{theme_html}',
        1
    )

    theme_script = create_theme_script()

    html_str = html_str.replace(
        '</body>',
        f'{theme_script}</body>',
        1
    )

    with open(
        output_path,
        'w',
        encoding='utf-8'
    ) as f:

        f.write(html_str)

    print(f"Dashboard Saved: {output_path}")
    return output_path


# =========================
# Main Block
# =========================
def main() -> None:
    global GAMMA_TICKERS_LIST

    parser = argparse.ArgumentParser(
        description="Generate interactive gamma exposure (GEX) dashboards as standalone HTML files."
    )
    parser.add_argument(
        "tickers", nargs="?", default="SPY,AAPL,NVDA,TSM,GOOGL",
        help='Comma-separated tickers, e.g. "AAPL,NVDA,TSLA" (default: %(default)s)'
    )
    parser.add_argument("-m", "--months", type=int, default=2,
                        help="Option expiry look-ahead in months (default: %(default)s)")
    parser.add_argument("-o", "--output",
                        default=os.environ.get("VP_OUTPUT_DIR", "charts"),
                        help="Output folder (default: %(default)s; env var VP_OUTPUT_DIR)")
    parser.add_argument("--market-hours", action="store_true",
                        help="Skip generation unless the US market is open (Mon-Fri 09:30-16:00 ET)")
    args = parser.parse_args()

    if args.market_hours and not is_market_open():
        print("The US option market is closed — skipping.")
        return

    GAMMA_TICKERS_LIST = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]

    output_dir = args.output
    try:
        os.makedirs(output_dir, exist_ok=True)
        probe = os.path.join(output_dir, ".write_probe")
        with open(probe, "w"):
            pass
        os.remove(probe)
    except OSError:
        output_dir = "charts"
        os.makedirs(output_dir, exist_ok=True)
        print(f"{args.output} not writable, falling back to {output_dir}")

    print(f"Tickers: {args.tickers}")
    for ticker in GAMMA_TICKERS_LIST:
        try:
            plot_gex_dashboard(
                ticker=ticker,
                months=args.months,
                output_dir=output_dir,
            )

        except Exception as e:
            print(f"ERROR processing {ticker}: {e}")

    print(
        "\n✅ All GEX dashboards "
        "generated successfully "
        "with full ticker navigation "
        "and AI packet export!"
    )


if __name__ == "__main__":
    main()

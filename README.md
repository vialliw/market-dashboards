# Market Dashboards

Interactive market dashboards in Python — Plotly candlesticks overlaid with horizontal volume-by-price profiles, plus options gamma-exposure (GEX) dashboards per expiry. Generated as standalone HTML pages for multiple tickers with cross-navigation and light/dark themes. Powered by yfinance.

![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)

**Live charts:** https://vialliw.github.io/market-dashboards/ — updated hourly during US market hours (Mon–Fri 09:30–16:00 ET) via GitHub Actions.

## Features

- Fetches OHLCV data for multiple tickers in a **single yfinance call**
- Horizontal **volume-by-price profile** rendered behind the candlesticks
- **GEX dashboards**: gamma exposure by strike per expiry, dealer positioning, max pain, gamma-flip level, call/put walls — plus a structured `AI packet` JSON export per ticker
- **Standalone HTML** output — no server required, open in any browser
- Cross-linked **navigation bar** across all generated ticker pages; volume pages link to their GEX dashboard and back via `index.html`
- **Light/dark theme switcher** on every page, persisted via `localStorage` across both dashboard types
- Unified hover tooltips with `DD/MM/YYYY` dates, dual crosshairs, and timestamped chart titles
- `index.html` landing page listing volume profiles and GEX dashboards — ready for GitHub Pages
- Optional **market-hours gate** (`--market-hours`) for scheduled runs

## Requirements

- Python 3.9+

```bash
pip install -r requirements.txt
```

## Usage

**Volume profile charts:**

```bash
python VolumeProfileVisualizer.py                        # defaults: SPY,AAPL,NVDA,TSM,GOOGL → charts/
python VolumeProfileVisualizer.py "AAPL,NVDA,TSLA"       # custom tickers (comma-separated)
python VolumeProfileVisualizer.py "SPY" -m 12 -o docs    # 12 months, output to docs/
python VolumeProfileVisualizer.py --market-hours -o docs # skip if US market is closed
```

**GEX dashboards:**

```bash
python GexDashboard.py                        # defaults: SPY,AAPL,NVDA,TSM,GOOGL → charts/
python GexDashboard.py "AAPL,NVDA,TSLA" -m 2  # custom tickers, 2 months of expiries
```

| Option | Volume | GEX | Description |
|---|---|---|---|
| `tickers` (positional) | `SPY,AAPL,NVDA,TSM,GOOGL` | same | Comma-separated ticker list |
| `-m`, `--months` | `6` | `2` | Price lookback / option expiry horizon |
| `-b`, `--bins` | `20` | — | Number of price bins in the profile |
| `-i`, `--interval` | `1d` | — | yfinance data interval |
| `-o`, `--output` | `charts` | `charts` | Output folder (or set `VP_OUTPUT_DIR`) |
| `--market-hours` | off | off | Only run while the US market is open (Mon–Fri 09:30–16:00 ET) |

## AI Packet (LLM-ready analysis)

Each `GexDashboard.py` run also writes `<TICKER>_ai_packet.json` — a self-contained prompt payload for LLM-based analysis of the options positioning:

- `system_prompt` — institutional options strategist persona
- `analysis_query` + `instructions` — what to analyze (gamma flip, call/put walls, net GEX, expiry positioning) and how to interpret it
- `market_snapshot` + `expiries` — spot, per-expiry net GEX/regime/max pain/flip/ATM IV, walls, and per-strike OI & GEX
- `expected_output` — the markdown sections the response should produce

**Manual use:** paste `system_prompt` as the system message in ChatGPT/Claude/etc., then paste the rest of the JSON as the user message.

**Programmatic use:**

```python
import json

pkt = json.load(open("docs/SPY_ai_packet.json"))
messages = [
    {"role": "system", "content": pkt["system_prompt"]},
    {"role": "user", "content": json.dumps({
        "query": pkt["analysis_query"],
        "instructions": pkt["instructions"],
        "market_snapshot": pkt["market_snapshot"],
        "expiries": pkt["expiries"],
        "expected_output": pkt["expected_output"],
    })},
]
# send `messages` to your LLM API of choice
```

Packets are published alongside the dashboards, e.g. https://vialliw.github.io/market-dashboards/SPY_ai_packet.json

## Automation

A GitHub Actions workflow (`.github/workflows/update-charts.yml`) regenerates both dashboard types hourly at :30, Monday–Friday, covering the US trading session year-round (the `--market-hours` flag skips off-hours runs), then commits the results to `docs/`. With GitHub Pages enabled on `main` → `/docs`, the charts are live at:

```
https://vialliw.github.io/market-dashboards/
https://vialliw.github.io/market-dashboards/SPY_VolumeProfile_6M.html
https://vialliw.github.io/market-dashboards/SPY_gex_dashboard.html
```

Market holidays and early-close days still run (harmless — yfinance returns the latest available bars).

## Output

- `VolumeProfileVisualizer.py` → `<TICKER>_VolumeProfile_<months>M.html` per ticker
- `GexDashboard.py` → `<TICKER>_gex_dashboard.html` + `<TICKER>_ai_packet.json` per ticker
- `index.html` landing page links to both sets, and each chart page cross-links to the others.

## License

[MIT](LICENSE)

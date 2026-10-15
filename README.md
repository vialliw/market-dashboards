# Volume Profile Visualizer

Interactive stock volume-profile dashboards in Python — Plotly candlesticks overlaid with horizontal volume-by-price profiles, generated as standalone HTML pages for multiple tickers with cross-navigation and light/dark themes. Powered by yfinance.

![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)

**Live charts:** https://vialliw.github.io/market-dashboards/ — updated hourly during US market hours (Mon–Fri 09:30–16:00 ET) via GitHub Actions.

## Features

- Fetches OHLCV data for multiple tickers in a **single yfinance call**
- Horizontal **volume-by-price profile** rendered behind the candlesticks
- **Standalone HTML** output — no server required, open in any browser
- Cross-linked **navigation bar** across all generated ticker pages, plus a link to a companion GEX dashboard
- **Light/dark theme switcher** on the page, persisted via `localStorage` across pages
- Unified hover tooltips with `DD/MM/YYYY` dates, dual crosshairs, and timestamped chart titles
- `index.html` landing page linking to all generated charts — ready for GitHub Pages
- Optional **market-hours gate** (`--market-hours`) for scheduled runs

## Requirements

- Python 3.9+

```bash
pip install -r requirements.txt
```

## Usage

```bash
python VolumeProfileVisualizer.py                        # defaults: SPY,AAPL,NVDA,TSM,GOOGL → charts/
python VolumeProfileVisualizer.py "AAPL,NVDA,TSLA"       # custom tickers (comma-separated)
python VolumeProfileVisualizer.py "SPY" -m 12 -o docs    # 12 months, output to docs/
python VolumeProfileVisualizer.py --market-hours -o docs # skip if US market is closed
```

| Option | Default | Description |
|---|---|---|
| `tickers` (positional) | `SPY,AAPL,NVDA,TSM,GOOGL` | Comma-separated ticker list |
| `-m`, `--months` | `6` | Lookback period in months |
| `-b`, `--bins` | `20` | Number of price bins in the profile |
| `-i`, `--interval` | `1d` | yfinance data interval |
| `-o`, `--output` | `charts` | Output folder (or set `VP_OUTPUT_DIR`) |
| `--market-hours` | off | Only run while the US market is open (Mon–Fri 09:30–16:00 ET) |

## Automation

A GitHub Actions workflow (`.github/workflows/update-charts.yml`) regenerates the charts hourly at :30, Monday–Friday, covering the US trading session year-round (the `--market-hours` flag skips off-hours runs), then commits the results to `docs/`. With GitHub Pages enabled on `main` → `/docs`, the charts are live at:

```
https://vialliw.github.io/market-dashboards/
https://vialliw.github.io/market-dashboards/SPY_VolumeProfile_6M.html
```

Market holidays and early-close days still run (harmless — yfinance returns the latest available bars).

## Output

One file per ticker: `<TICKER>_VolumeProfile_<months>M.html`, plus `index.html`. Each page links to every other generated page, so you can jump between tickers without reopening the script.

## License

[MIT](LICENSE)

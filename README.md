# Volume Profile Visualizer

Interactive stock volume-profile dashboards in Python — Plotly candlesticks overlaid with horizontal volume-by-price profiles, generated as standalone HTML pages for multiple tickers with cross-navigation and light/dark themes. Powered by yfinance.

![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)

## Features

- Fetches OHLCV data for multiple tickers in a **single yfinance call**
- Horizontal **volume-by-price profile** rendered behind the candlesticks
- **Standalone HTML** output — no server required, open in any browser
- Cross-linked **navigation bar** across all generated ticker pages, plus a link to a companion GEX dashboard
- **Light/dark theme switcher** on the page, persisted via `localStorage` across pages
- Unified hover tooltips with `DD/MM/YYYY` dates, dual crosshairs, and timestamped chart titles

## Requirements

- Python 3.9+
- `yfinance`, `plotly`, `pandas`, `numpy`

```bash
pip install yfinance plotly pandas numpy
```

## Usage

```bash
python VolumeProfileVisualizer.py "AAPL,NVDA,TSLA,SPY"
```

Or edit the defaults in the `__main__` block:

| Setting | Default | Notes |
|---|---|---|
| `tickers` | preset watchlist | overridden by the first CLI argument |
| `no_of_months` | `6` | lookback period |
| `num_bins` | `20` | number of price bins in the profile |
| `output_folder` | `R:/spiderhive0` | falls back to `c:/intel/parquet/tech_analysis` if not writable — change to suit your machine |

## Output

One file per ticker: `<TICKER>_VolumeProfile_<months>M.html`. Each page links to every other generated page, so you can jump between tickers without reopening the script.

## License

[MIT](LICENSE)

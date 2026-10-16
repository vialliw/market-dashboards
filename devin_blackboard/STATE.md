# Project State — Canonical Source
Last Updated: 2026-10-16
Current Branch: main

## Repo

market-dashboards (public, github.com/vialliw/market-dashboards).
Standalone-HTML market dashboards: VolumeProfileVisualizer.py +
GexDashboard.py, hourly GitHub Actions → docs/ → GitHub Pages.

## Current cycle

F1: fix compute_gamma_flip/compute_max_pain artifacts (GitHub issue #1),
flagged by both sample LLM analyses. CONSTRAINT: market CLOSED during this
cycle — NO live yfinance calls; tests must use tests/fixtures/TSM_ai_packet.json.

## Active Blocker

None.

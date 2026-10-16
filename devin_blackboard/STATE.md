# Project State — Canonical Source
Last Updated: 2026-10-16 (F1-T1 done by Agent B)
Current Branch: main

## Repo

market-dashboards (public, github.com/vialliw/market-dashboards).
Standalone-HTML market dashboards: VolumeProfileVisualizer.py +
GexDashboard.py, hourly GitHub Actions → docs/ → GitHub Pages.

## Current cycle

F1: fix compute_gamma_flip/compute_max_pain artifacts (GitHub issue #1),
flagged by both sample LLM analyses. CONSTRAINT: market CLOSED during this
cycle — NO live yfinance calls; tests must use tests/fixtures/TSM_ai_packet.json.

## What's Done

- F1-T1 (B, done): compute_gamma_flip now takes spot=None, applies a 1%-of-
  max-|gex| materiality filter, returns material neg->pos crossing nearest
  to spot (ascending scan when spot=None), None when no material crossing.
  compute_max_pain takes spot=None, restricts candidates to [0.7x,1.3x] spot
  with all-strikes fallback. build_ai_packet flags stale IV (>200%) strikes
  with implied_volatility=None + stale_iv:true (OI/GEX kept); data_quality
  note in meta. tests/test_gex_metrics.py + tests/conftest.py added —
  11 offline tests on TSM fixture, all 5 ACs PASS. Fixture: 10/16 flip
  430.0 (was 370), aggregate 430.0, all max_pain inside [295.4, 548.7].

## Active Blocker

None.

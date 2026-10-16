# Project State — Canonical Source
Last Updated: 2026-10-16 (F1-T3 critic gate done by Agent D — PASS; cycle F1 complete)
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
- F1-T2 (C, done): adversarial review APPROVED. Mutation probe (pre-fix
  fns vs TSM fixture) confirms tests non-vacuous; nearest-spot correct
  across multi-crossings; materiality filter cannot empty; band
  fallback/ties OK; stale_iv preserves OI/GEX; scope audit clean; no
  network in tests. Minor notes only (np.int64 return type on int-strike
  df — wrapped by float() in packet path; flip-at-0 truthiness edge;
  empty-df max_pain ValueError pre-existing). F1-T3 (D) ready.
- F1-T3 (D, done): critic gate PASS. All 3 ACs re-verified independently
  (11/11 pytest, import exit 0, no yfinance in tests). Independent probe
  (temp script, real GexDashboard fns on fixture-rebuilt dfs): 10-16
  flip=430.0 in [425,435], max_pain=435.0 in [295.435,548.665]; all 7
  expiries flip 430-475 near spot (artifacts 370/230/250/310 gone); all
  max_pain 420-475 in-band; far expiry 11-27 flip=475.0 sane. Diff audit
  fe19b3b..ccd364e clean: only output_files + blackboard touched, zero
  network calls added, git tree clean. Cycle F1 CLOSED — issue #1 fix
  verified end-to-end.

None.

# Project State — Canonical Source
Last Updated: 2026-10-16 (F2-T2 done by Agent C — APPROVED; F2-T3 ready)
Current Branch: main

## Repo

market-dashboards (public, github.com/vialliw/market-dashboards).
Standalone-HTML market dashboards: VolumeProfileVisualizer.py +
GexDashboard.py, hourly GitHub Actions → docs/ → GitHub Pages.

## Current cycle

F2: external ChatGPT review of the issue-#1 fix (cycle F1) returned
NEEDS_REVISION with 3 blocking concerns — now implemented in F2-T1.
CONSTRAINT: market CLOSED during this cycle — NO live yfinance calls;
tests must use tests/fixtures/TSM_ai_packet.json.

## What's Done

- F1-T1 (B, done): compute_gamma_flip spot-aware materiality filter;
  compute_max_pain spot band; stale-IV flagging; 11 offline tests.
- F1-T2 (C, done): adversarial review APPROVED (mutation probes,
  non-vacuous tests, scope audit clean).
- F1-T3 (D, done): critic gate PASS. Cycle F1 CLOSED.
- F2-T1 (B, done): review amendments implemented —
  (1) compute_gamma_flip: MIN_MATERIAL_GEX=1e-3 absolute floor combined
      with 1%-of-max relative filter; max_abs < 1e-3 (whole-noise
      slice) -> None; nonfinite strike/gex rows dropped pre-grouping;
      zero-gex strikes can never anchor a crossing (floor > 0).
  (2) compute_max_pain = canonical UNCONSTRAINED argmin (spot param
      kept for backward compat, no longer restricts); NEW
      compute_max_pain_constrained returns {max_pain, candidate_band,
      band_fell_back, band_boundary_hit} — boundary-hit distinguished
      from empty-band fallback.
  (3) build_ai_packet per-expiry emits max_pain + max_pain_constrained
      + max_pain_candidate_band + max_pain_band_fell_back +
      max_pain_band_boundary_hit; stale strikes emit
      implied_volatility:null + raw_implied_volatility:<original> +
      stale_iv:true; meta.data_quality documents retained-with-flag
      GEX + provenance; meta.metric_definitions documents gamma_flip
      as strike-profile sign-crossing proxy (call-long/put-short
      convention, NOT repriced-chain flip), constrained payout
      minimum, and materiality floor.
  (4) Dashboard plot draws both lines: "Max Pain" (cyan dot) and
      "Max Pain (0.7x-1.3x spot band)" (teal dashdot).
  Tests: tests/test_gex_metrics.py extended 11 -> 20 offline tests
  (alternating ±1e-12 noise -> None, all-zero -> None, 1e-3 floor
  boundary below/at/above, relative-only sub-floor block, multi-
  crossing nearest-spot, nonfinite dropped, unconstrained-outside-
  band packet fields, boundary-hit vs fell_back, IV provenance,
  metric_definitions). pytest: 20 passed. Probe
  devin_blackboard/probe_f2_t1.py: independent payout argmin matches
  lib on all 7 TSM expiries (unconstrained 435/450/445/460/470/420/450
  = constrained, all in-band, no fell_back/boundary_hit); alternating
  noise -> None. ACs: 3/3 PASS. Commit 11c1d52.
- F2-T2 (C, done): adversarial review APPROVED. Probe
  devin_blackboard/probe_f2_t2.py: 33 hostile-input checks all PASS
  (uniform 1e-12/1e-6/9.999e-4 noise -> None, alternating +/-1e-12 ->
  None, mixed noise+real books correct, nonfinite dropped, spot=None
  first-ascending, canonical ignores spot, empty-band fell_back vs
  edge boundary_hit distinct end-to-end in packet, iv=2.0 not stale /
  2.0000001 stale + raw IV provenance + GEX retained, meta docs
  accurate). 10/10 meaningful mutants caught (relative-only floor,
  strict >, no nonfinite drop, packet field swap, boundary/fell_back
  suppression, re-banded canonical, first-crossing revert, provenance
  drop, metric-key rename). pytest 20/20; zero network; diff scope
  clean (GexDashboard.py + tests/test_gex_metrics.py + blackboard).
  Non-blocking notes below.

## Ready

- F2-T3 (D): critic gate for F2 (depends on F2-T2 — now unblocked).

## Notes for reviewers

- Semantics change: compute_max_pain(df, spot=X) now IGNORES spot —
  the banded variant is exclusively compute_max_pain_constrained().
  Packet field "max_pain" is again the canonical unconstrained value
  (can land outside the tradable band — honest label).
- Old fixture packet meta lacks data_quality/metric_definitions
  (pre-dates the fields); new build_ai_packet output includes them —
  asserted by tests.
- Empty-df input to either max_pain fn still raises ValueError
  (pre-existing, unchanged).
- F2-T2 non-blocking notes: (a) the `max_abs < MIN_MATERIAL_GEX`
  early return at GexDashboard.py:283 is functionally redundant —
  the combined floor already makes every sub-floor strike
  immaterial (removal alone is uncatchable; harmless fast-path);
  (b) spot=NaN into compute_gamma_flip silently degenerates to the
  first ascending crossing (argmin over NaN distances) and into
  compute_max_pain_constrained degrades to fell_back — spec only
  mandates spot=None; (c) band_boundary_hit is literal
  "argmin landed on an edge" — fires even when the unconstrained
  argmin is the same edge strike, i.e. it does NOT prove the band
  was binding (docstring/meta describe it exactly this way);
  (d) NaN impliedVolatility passes through unflagged as NaN
  (NaN>2.0 is False) — pre-existing, unchanged; (e) flip at
  strike 0.0 would emit null via truthiness check — pre-existing.

# Project State — Canonical Source
Last Updated: 2026-10-16 (F3-T1 done by Agent B — 3/3 AC PASS; awaiting F3-T2 challenger)
Current Branch: main

## Repo

market-dashboards (public, github.com/vialliw/market-dashboards).
Standalone-HTML market dashboards: VolumeProfileVisualizer.py +
GexDashboard.py, hourly GitHub Actions → docs/ → GitHub Pages.

## Current cycle

F3: external review round 3 — band removal alone could not explain
max_pain 225-235 -> 435 since the original was already the canonical
argmin. Orchestrator forensics verified the TRUE root cause: NaN
openInterest rows make every pain sum NaN and np.argmin returns the
first NaN index = lowest strike. Fixed in F3-T1.
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
- F2-T3 (D, done): critic gate PASS. pytest 20/20 (5.73s); zero
  network in tests (only fixture meta.source string). Independent
  plain-Python payout recompute over ALL 105 strikes of TSM
  2026-10-16: argmin=435.0 (unique, payout=1,269,250.00) — the
  reported 435 is the TRUE unconstrained argmin, not a band
  artifact (it merely also lies inside [295.435, 548.665]);
  compute_max_pain ignores spot as designed; constrained variant
  agrees, no fell_back/boundary_hit. All 7 expiries independently
  verified: 435/450/445/460/470/420/450, all unique argmins.
  gamma_flip: alternating +/-1e-12 -> None, all-zero -> None,
  9e-4 & 9.999e-4 -> None, +/-1e-3 and +/-1.1e-3 -> flip at 420
  (floor inclusive at 1e-3). Fresh build_ai_packet on real fixture
  expiry: max_pain + max_pain_constrained + candidate_band +
  fell_back + boundary_hit all emitted; 23 stale strikes carry
  implied_volatility:null + raw_implied_volatility>2.0 +
  stale_iv:true; meta.metric_definitions + data_quality accurate.
  Diff audit ccd364e..HEAD: only GexDashboard.py +
  tests/test_gex_metrics.py + devin_blackboard/*; tree clean.
  Probe: devin_blackboard/probe_f2_t3.py (20/20 checks).
  Cycle F2 CLOSED.
- F3-T1 (B, done): TRUE root cause fixed —
  (1) NEW _finite_oi_rows() drops nonfinite-strike/nonfinite-OI rows
      in _payout_pain AND both compute_max_pain fns before the payout
      loop / candidate-strike extraction (docstrings document the
      225/230/235 np.argmin-first-NaN mechanism).
  (2) NEW band-edge flags selected_lowest_candidate /
      selected_highest_candidate on compute_max_pain_constrained
      distinguish constraint-driven eligible-endpoint selection from
      literal band_boundary_hit; both emitted per-expiry in the
      packet + documented in meta.metric_definitions.
  (3) meta.data_quality now notes exported open_interest/call_oi/
      put_oi may conflate true-zero OI with missing data (source NaN
      collapsed to 0 by aggregation); missing-OI rows excluded from
      payout sums.
  Tests: 20 -> 24 offline tests (NaN-OI regression w/ independent
  argmin 420 vs old lowest-strike artifact + all-NaN slice ->
  ValueError; between-strikes band-edge flags lowest+highest;
  all-NaN gex -> None; packet flag assertions; OI-conflation note).
  pytest: 24 passed. Probe devin_blackboard/probe_f3_t1.py: naive
  original algorithm reproduces EXACTLY 225/230/235 on zero-as-NaN
  fixture dfs; fixed returns independent argmin 450/445/450; clean
  canonical max_pain unchanged (435/450/445/460/470/420/450);
  edge flags correct. ACs: 3/3 PASS. Commit 9a8f573 (+bde2c93 wrap).

## Ready

- F3-T2 (C, todo): adversarial review of F3-T1 — NaN-OI exclusion
  must not alter clean-data results; argmin tie behavior; band-edge
  flag semantics; provenance accuracy; mutation-check the regression
  test; scope/diff audit; zero network.

## Notes for reviewers

- Semantics change: compute_max_pain(df, spot=X) now IGNORES spot —
  the banded variant is exclusively compute_max_pain_constrained().
  Packet field "max_pain" is again the canonical unconstrained value
  (can land outside the tradable band — honest label).
- Old fixture packet meta lacks data_quality/metric_definitions
  (pre-dates the fields); new build_ai_packet output includes them —
  asserted by tests.
- Empty-df input to either max_pain fn still raises ValueError
  (pre-existing, unchanged); an all-NaN-OI df now lands on the same
  ValueError after filtering — loud failure, never an artifact.
- New flags: band_boundary_hit (literal strike==edge) implies the
  matching selected_*_candidate flag, not vice versa — selected_*
  fires whenever the argmin is the lowest/highest ELIGIBLE strike,
  including the common case where the unconstrained argmin itself
  sits at an interior edge of the eligible set.
- np.argmin tie behavior unchanged: first lowest index wins among
  equal pains (documented implicitly; challenger may probe).
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

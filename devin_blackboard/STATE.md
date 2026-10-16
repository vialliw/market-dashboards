# Project State — Canonical Source
Last Updated: 2026-10-16 (F4-T3 critic gate PASS by Agent D — Cycle F4 CLOSED)
Current Branch: main

## Repo

market-dashboards (public, github.com/vialliw/market-dashboards).
Standalone-HTML market dashboards: VolumeProfileVisualizer.py +
GexDashboard.py, hourly GitHub Actions → docs/ → GitHub Pages.

## Current cycle

F4: live SPY run (market open, 2026-10-16) failed — expiry 2026-10-29
has all-NaN openInterest; compute_max_pain's loud ValueError propagated
and killed the whole ticker. F4-T1 makes per-expiry metric computation
in build_ai_packet + the plot loop tolerate unusable expiries (explicit
nulls + metrics_unavailable flag) while keeping loud failure on the
raw functions for direct misuse.
CONSTRAINT: NO live yfinance calls; tests must stay offline
(tests/fixtures/TSM_ai_packet.json + synthetic dfs).

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
- F3-T2 (C, done): adversarial review APPROVED. Probe
  devin_blackboard/probe_f3_t2.py: 40 checks all PASS —
  (1) clean-data A/B vs pre-F3 committed module (git show
      9a8f573^): identical max_pain + constrained results on all
      7 TSM expiries (435/450/445/460/470/420/450); dropping
      all-NaN-OI strikes never moved any fixture argmin.
  (2) One-sided NaN: only the NaN row drops — the strike stays a
      candidate via the finite side and matches an independent
      finite-only argmin; NaN-strike and inf-OI rows excluded;
      all-NaN-OI / empty / all-NaN-strike -> loud ValueError on
      both fns; pain tie -> lowest strike (np.argmin first-min);
      int-dtype df works.
  (3) Band-edge flags: constraint-driven lowest+highest fire
      between strikes with boundary_hit correctly False; literal
      edge -> boundary_hit AND selected_lowest; single-eligible
      band -> both flags (1-elem set is both endpoints); empty-band
      fell_back / spot=None / spot=NaN -> flags suppressed by the
      `not band_fell_back` guard; coincidental endpoint fires
      (argmin IS lowest eligible even when band non-binding —
      literal semantics, honestly documented, consumers can detect
      binding via max_pain != max_pain_constrained).
  (4) gamma_flip path protected by its own strike/gex finite
      filter (NaN-OI rows are irrelevant — OI unused); all-NaN
      gex -> None via len==0 guard.
  (5) Mutations: reverting _finite_oi_rows -> artifact 225
      returns AND the real regression test fails (non-vacuous);
      simulating filter-only-in-payout -> NaN-strike candidate
      hijacks argmin (outer filter load-bearing); flag-swap ->
      between-strikes test fails; removing fell_back guard ->
      flags misfire (guard load-bearing).
  (6) Packet: selected_* emitted as bools; NaN-OI strike exports
      OI=0 (conflation demonstrated, matching the data_quality
      note); missing-OI rows excluded from payout sums;
      metric_definitions documents flags + exclusion.
  (7) pytest 24/24; diff audit 5a230fc..b01a26d: only
      GexDashboard.py + tests/test_gex_metrics.py +
      devin_blackboard/*; zero network (only fixture meta.source
      string "yfinance").
  Non-blocking notes below.

- F3-T3 (D, done): critic gate PASS. Probe
  devin_blackboard/probe_f3_t3.py: 32/32 checks.
  pytest 24/24 (5.01s); zero network in tests (only
  fixture meta.source string "yfinance").
  (A) Clean zeros-as-is dfs: canonical compute_max_pain ==
  independent plain-Python argmin ==
  435/450/445/460/470/420/450 on all 7 TSM expiries —
  unchanged from F2.
  (B) Zeros-as-NaN dfs: current functions == independent
  finite-only argmin on all 7 expiries (never the lowest
  listed strike); simulated PRE-F3 naive algorithm (no
  finite filter) reproduces the exact reported artifacts
  225/230/235 on 10-23/10-30/11-27 (lowest listed
  strike = np.argmin first-NaN). Expiry 10-23: pre-F3 ->
  225 (artifact), current -> 450 (true argmin);
  constrained variant agrees (450).
  (C) Band-edge flags: band [147,273] between listed
  strikes -> argmin on lowest eligible (150) fires
  selected_lowest only, argmin on highest eligible (250)
  fires selected_highest only, boundary_hit False in
  both; literal edge equality (argmin=140=band edge)
  fires band_boundary_hit + selected_lowest.
  (D) Fresh build_ai_packet: max_pain +
  max_pain_constrained + candidate_band + fell_back +
  boundary_hit + selected_lowest/highest all emitted as
  bools; stale-IV strike carries iv:null +
  raw_implied_volatility:3.5 + stale_iv:true with OI/GEX
  preserved; NaN-OI strike exports OI=0 (conflation
  demonstrated, matching data_quality note);
  meta.data_quality + metric_definitions document the
  flags and the exclusion.
  (E) Diff audit 5a230fc..HEAD: only GexDashboard.py +
  tests/test_gex_metrics.py + devin_blackboard/*; tree
  clean. Cycle F3 CLOSED.
- F4-T1 (B, done): unusable-expiry tolerance —
  (1) NEW _per_expiry_metrics(sub, spot) bundles
      compute_max_pain / compute_max_pain_constrained /
      compute_gamma_flip; returns None on ValueError
      (insufficient data — all-NaN OI, empty slice);
      all OTHER exceptions still propagate.
  (2) build_ai_packet: unusable expiry emits explicit
      nulls for max_pain / max_pain_constrained /
      candidate_band / fell_back / boundary_hit /
      selected_lowest / selected_highest / gamma_flip
      PLUS metrics_unavailable:true and per-expiry
      data_quality reason ("no_finite_oi" /
      "insufficient_data"); empty-slice atm_iv -> null;
      meta.data_quality + meta.metric_definitions
      document the flag; strikes still exported (OI
      collapses to 0 — documented conflation);
      whole-book fields unaffected.
  (3) plot_gex_dashboard: same bundle; max_pain +
      constrained vlines/legend omitted with a
      "unavailable (no finite OI)" annotation; ATM IV
      annotation skipped when None/nonfinite.
  (4) Raw compute_max_pain / _constrained UNCHANGED —
      still raise ValueError on all-NaN slices.
  Tests 24 -> 26: mixed book (healthy + all-NaN-OI +
  absent expiry) -> nulls+flag for unusable, real
  metrics elsewhere, raw-ValueError asserts;
  metrics_unavailable-vs-no-crossing distinction test.
  pytest: 26 passed. AC 2/2 PASS. Commit 5bc7a42.
- F4-T2 (C, done): adversarial review APPROVED. Probe
  devin_blackboard/probe_f4_t2.py — 37 PASS checks:
  (A) packet honesty: all-NaN-OI expiry emits 8 explicit
      null metric fields + metrics_unavailable:true +
      data_quality:"no_finite_oi"; absent expiry same
      (strikes []); strikes still exported (OI->0
      conflation); healthy expiry untouched (no
      data_quality key); whole-book fields unaffected;
      "insufficient_data" reason branch verified via
      injected ValueError on a finite-OI slice.
  (B) finite-OI/all-NaN-gex expiry -> metrics available
      + gamma_flip:null — 'no crossing' and 'could not
      compute' are distinguishable states.
  (C) scoped tolerance: IndexError/KeyError/TypeError/
      RuntimeError/ZeroDivisionError/AssertionError
      injected into the bundle ALL propagate (kill
      ticker) — only ValueError is absorbed; natural
      KeyError (missing gex col) + TypeError (object
      dtype) also propagate.
  (D) A/B vs pre-F4 module (git show 9deb260): all 7
      TSM expiries byte-identical per-expiry dicts
      (new keys stripped) + identical whole-book
      snapshot; no false flags on clean book.
  (E) raw compute_max_pain/_constrained still raise
      ValueError on all-NaN-OI + real empty slices.
  (F) dashboard path offline (monkeypatched
      fetch_options + captured fig): dead subplot has
      'unavailable (no finite OI)' annotation + zero
      cyan/teal vlines; NaN/None atm_iv -> annotation
      skipped; absent-expiry subplot no crash.
  (G) mutations vs REAL tests (mutant module injected
      via sys.modules, actual pytest run): 5/6 caught —
      artifact-instead-of-null, flag removed, wrong
      data_quality, 0-instead-of-null, finite-filter
      revert; 'except Exception' broadening ESCAPES
      (see notes). pytest 26/26; zero network; diff
      audit 9deb260..HEAD: only GexDashboard.py +
      tests/test_gex_metrics.py + devin_blackboard/*.
  Non-blocking notes below.

- F4-T3 (D, done): critic gate PASS. Probe
  devin_blackboard/probe_f4_t3.py — 22/22 checks.
  pytest 26/26 (5.29s); zero network in tests (only
  fixture meta.source string "yfinance").
  (A) Synthetic mixed book (spot=100; healthy
      2026-11-20 strikes 90/100/110; dead 2026-10-29
      strikes 95/105/115 ALL-NaN OI; absent
      2027-01-15): healthy expiry ->
      metrics_unavailable:false, max_pain=100.0 ==
      independent plain-Python finite-only payout
      argmin (payout 7000.0), constrained=100.0,
      band [70,130], gamma_flip=110.0, atm_iv=0.25,
      no data_quality key. Dead expiry ->
      metrics_unavailable:true, data_quality:
      "no_finite_oi", all 8 metric fields explicit
      null (max_pain/constrained/candidate_band/
      fell_back/boundary_hit/selected_lowest/
      selected_highest/gamma_flip), atm_iv:0.30.
      Absent expiry -> same flag, strikes [],
      atm_iv null. Whole-book unaffected
      (spot 100, total_net_gex 0.0, flip 110.0).
  (B) Raw compute_max_pain +
      compute_max_pain_constrained still raise
      ValueError ("attempt to get argmin of an
      empty sequence") on the all-NaN-OI slice.
  (C) Non-ValueError NOT swallowed: missing
      'openInterest' -> KeyError out of
      build_ai_packet; missing 'strike' ->
      KeyError out of _per_expiry_metrics;
      injected RuntimeError propagates; injected
      ValueError -> None (boundary absorbs only
      data failures, as designed).
  (D) Dead expiry strikes still exported (3
      strike objects 95/105/115, OI->0
      conflation per data_quality note);
      max_pain is null — never the lowest-strike
      (95) artifact.
  (E) Diff audit: declared F4 range
      5bc7a42..d97a193 touched only
      devin_blackboard/*; full F4 cycle range
      9deb260..d97a193 touched only
      GexDashboard.py + tests/test_gex_metrics.py
      + devin_blackboard/* — all declared files.
      No import/network additions in the code
      diff. Tree clean (probe file is an allowed
      output). Cycle F4 CLOSED.

## Ready

- (none — Cycle F4 closed; awaiting orchestrator)

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
- F4-T1 semantics: metrics_unavailable:true means the payout metrics
  could not be computed at all (all metric fields explicit nulls +
  per-expiry data_quality reason) — DISTINCT from gamma_flip:null
  with metrics_unavailable:false (data fine, no material crossing).
  data_quality reason key only appears on unavailable expiries
  (stale_iv-style conditional key). _per_expiry_metrics catches ONLY
  ValueError; TypeError/KeyError/etc. still propagate loudly.
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
- F4-T2 non-blocking notes: (a) TEST-RIGOR GAP — mutating
  `except ValueError` to `except Exception` in
  _per_expiry_metrics produces an observable behavior change
  (probe: injected KeyError is swallowed -> a HEALTHY expiry
  degrades to metrics_unavailable) yet the suite still passes
  2/2 — no test injects a non-ValueError failure, so the
  "only ValueError is tolerated" invariant is verified by
  probe section C (6 injected exception types + 2 natural
  failure modes all propagate) but is NOT regression-guarded
  by any committed test; a 3-line
  pytest.raises((KeyError, TypeError)) test would close it;
  (b) an unusable expiry still exports net_gex:0.0 /
  gamma_regime:"-Gamma" (sum of all-NaN collapses to 0) and
  call/put walls with oi:0 — aggregate fields sit OUTSIDE the
  nulled metric set; metrics_unavailable:true is the
  consumer signal; (c) an expiry with all-NaN IV exports
  atm_iv as a bare `NaN` JSON token (json.dump allow_nan
  default; strict parsers would reject) — the plot got an
  np.isfinite guard but the packet only checks `is not None`;
  pre-existing wart, F4 touched that exact line; (d)
  _per_expiry_metrics is all-or-nothing: a ValueError from
  either payout fn nulls a potentially computable
  gamma_flip — unreachable on the real path since NaN OI
  implies NaN gex via compute_gex; (e) a columns-only
  object-dtype df raises TypeError (not ValueError) inside
  _finite_oi_rows — loud, propagates, consistent with F3
  note (c); real slices keep float64 so empty -> ValueError
  -> tolerated as designed.
- F3-T2 non-blocking notes: (a) selected_*_candidate flags are
  LITERAL "argmin is the lowest/highest eligible strike" — they
  fire even when the band is not binding (probe: unconstrained
  argmin == lowest eligible -> flag True with max_pain ==
  max_pain_constrained); same caveat class as band_boundary_hit,
  docstrings/metric_definitions state literal semantics, and
  binding is detectable via max_pain != max_pain_constrained;
  (b) a single-eligible-candidate band sets BOTH flags (the one
  element is both lowest and highest — correct but worth
  knowing); (c) _finite_oi_rows assumes numeric dtypes —
  object-dtype OI (None/pd.NA) raises TypeError, a loud failure
  (yfinance emits float64, so unreachable on the real path);
  (d) one-sided missing OI silently contributes nothing — the
  strike remains a candidate via its finite side, documented in
  metric_definitions; (e) zero-OI-kept vs NaN-dropped candidate
  sets differ in principle (a 0-OI strike is a valid settlement
  candidate, a missing-OI strike is not) — intended semantics,
  and empirically identical argmins on all 7 TSM expiries.

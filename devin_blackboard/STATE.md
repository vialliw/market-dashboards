# Project State — Canonical Source
Last Updated: 2026-10-16 (F6-T1 done by Agent B — WellnessCheck.py implemented, 3/3 AC PASS)
Current Branch: main

## Repo

market-dashboards (public, github.com/vialliw/market-dashboards).
Standalone-HTML market dashboards: VolumeProfileVisualizer.py +
GexDashboard.py, hourly GitHub Actions → docs/ → GitHub Pages.

## Current cycle

F6: post-generation AI-packet validator (WellnessCheck.py) per the
ChatGPT-APPROVED implementation plan v4 — a separate script with
independent plain-Python reference implementations (NEVER imports
GexDashboard) re-verifying every documented rule after generation:
schema/numerics, availability flag/null/reason consistency incl.
the exported-OI ambiguity directionality, gex aggregation, payout
argmins + band flags, material gamma-flip crossings, stale-IV
provenance, raw-snapshot reconciliation, coverage/freshness warns.
PASS/WARN/FAIL/unperformed outcomes, structured reports,
provenance, exit codes 0/0/1/2, reports never overwrite input.
CONSTRAINT: NO live yfinance calls; tests stay offline
(tests/fixtures/* only).

## What's Done

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
- F5-T1 (B, done): degenerate-chain eligibility +
  scoped ValueError absorption —
  (1) NEW MIN_PAYOUT_STRIKES=2 +
      _payout_ineligibility(sub): 'no_finite_oi'
      when zero finite-OI rows;
      'insufficient_data' when the usable chain
      is degenerate — fewer than 2 DISTINCT
      finite-OI strikes (single-candidate argmin
      is vacuous: live INTC 2026-10-19, one
      strike, call_oi=2/put_oi=0 -> 'correct'
      but meaningless 190) or total finite OI
      <= 0 (every candidate is an argmin);
      None when eligible.
  (2) _per_expiry_metrics now ALWAYS returns a
      dict {max_pain, max_pain_constrained,
      gamma_flip, unavailable}: the eligibility
      pre-check runs BEFORE the payout fns so
      they are never called on unusable slices;
      gamma_flip is computed independently of
      OI eligibility (per-metric availability —
      a real crossing is still emitted on a
      flagged expiry); the belt-and-suspenders
      except absorbs ONLY the ValueError
      matching 'attempt to get argmin of an
      empty sequence' — every other ValueError
      and all non-ValueErrors propagate.
  (3) build_ai_packet: unavailable expiry emits
      all 7 payout fields as explicit nulls +
      metrics_unavailable:true +
      data_quality=reason + the computed
      gamma_flip (real value or null);
      meta.data_quality + metric_definitions
      document 'insufficient_data' and a new
      'payout_eligibility' entry.
  (4) plot loop: max-pain vlines/legend skipped
      with a 'max_pain unavailable (<reason>)'
      annotation; gamma_flip line may still
      draw.
  Tests 26 -> 33 (+7): INTC-style single-strike
  -> insufficient_data with payout nulls and
  strikes still exported (raw compute_max_pain
  still returns the vacuous 190.0 — loud-
  artifact behavior on direct misuse is
  unchanged); eligibility boundaries (2-strike
  eligible, 1-distinct-strike-both-rows
  ineligible, 2-strike-only-1-finite-OI
  ineligible); zero-total-OI ->
  insufficient_data; partial availability
  (degenerate payout + real gamma_flip 110.0
  emitted under metrics_unavailable:true);
  injected unrelated ValueError propagates;
  injected empty-argmin-message ValueError
  absorbed -> insufficient_data; injected
  KeyError/TypeError propagate (closes the
  F4-T2 non-blocking test-rigor gap).
  pytest: 33 passed. ACs: 2/2 PASS.
  Commit bb9f7b0.
- F5-T2 (C, done): adversarial review APPROVED.
  Probe devin_blackboard/probe_f5_t2.py — 67 PASS
  checks, 8 non-blocking notes:
  (1) Eligibility rule: 2/3-strike tiny-OI chains,
      one-sided books, and a zero-OI second strike
      all stay ELIGIBLE and match an independent
      plain-Python argmin — the rule cannot flag a
      usable chain; every degenerate shape
      (1 distinct strike both-rows-finite, 2
      strikes/1 finite-OI, zero-total-OI,
      negative-total-OI, inf-OI-dropped, all-NaN,
      absent expiry) lands on the right reason.
      <2 is the minimal non-vacuous cut (a
      1-candidate argmin carries zero
      information, a 2-candidate argmin
      discriminates) — defensible vs <3 which
      would flag honest thin books.
  (2) Scoped absorption verified: 5 unrelated
      ValueError shapes propagate (incl. case-
      mangled magic message), exact + substring
      magic message absorbed -> insufficient_
      data, constrained-fn-only raise still
      yields packet nulls, 8 non-ValueError
      types propagate, gamma_flip-side and
      eligibility-fn failures propagate
      (outside try). This numpy emits the magic
      message.
  (3) Partial availability: degenerate payout +
      real flip emits metrics_unavailable:true
      + nulls + gamma_flip:110.0 + per-strike
      is_gamma_flip; 4-state distinguishability
      matrix verified; consumers gate on the
      flag.
  (4) A/B vs bb9f7b0^ module on all 7 TSM
      fixture expiries: per-expiry dicts BYTE-
      identical (json.dumps sort_keys),
      whole-book identical, meta diff = doc
      strings only.
  (5) INTC live case: 1-strike call_oi=2/put_oi=0
      -> insufficient_data + nulls; raw
      compute_max_pain still returns vacuous
      190 (documented); healthy expiry in the
      same book unaffected.
  (6) Mutations vs REAL pytest: 8/8 meaningful
      mutants caught — threshold 2->1, absorb-
      all-ValueError, null-flip-on-unavailable,
      nunique->len, <=-><, pre-check removal
      (via all-NaN reason degradation), wrong
      reason string, inverted flag.
  (7) pytest 33/33; diff audit bb9f7b0^..084bebf:
      only GexDashboard.py + tests/test_gex_
      metrics.py + devin_blackboard/*; zero
      network in tests.
  Non-blocking notes below.
- F5-T3 (D, done): critic gate PASS. Probe
  devin_blackboard/probe_f5_t3.py — 26/26
  checks. pytest 33/33 (7.66s); zero network
  in tests (only fixture meta.source string
  "yfinance").
  (A) INTC live case reproduced inside a
      two-expiry packet (spot=100): dead
      2026-10-19 (1 strike 190, call_oi=2,
      put_oi=0) emits metrics_unavailable:true
      + data_quality:'insufficient_data' + all
      7 payout fields explicit null +
      gamma_flip:null (honest — a single
      strike can host no crossing); strike row
      still exported (190/2/0); the vacuous
      190 argmin is never emitted. Healthy
      2026-11-20 in the SAME packet computes
      normally: metrics_unavailable:false, no
      data_quality key, max_pain=100.0 ==
      independent plain-Python payout argmin
      (payout=1500.0), constrained=100.0,
      band [70,130], gamma_flip=110.0, bool
      flags real. Raw compute_max_pain still
      returns vacuous 190.0 on direct misuse
      (documented).
  (B) Scoped absorption: injected
      ValueError('totally unrelated message')
      propagates out of build_ai_packet AND
      out of _per_expiry_metrics via the
      constrained fn; injected numpy
      'attempt to get argmin of an empty
      sequence' absorbed -> metrics_
      unavailable:true + insufficient_data +
      gamma_flip still computed (110.0);
      injected KeyError propagates;
      case-mangled magic message propagates
      (not a blanket ValueError catch);
      gamma_flip-side RuntimeError propagates
      (outside the payout try).
  (C) Partial availability: degenerate
      payout expiry (1 distinct finite-OI
      strike) with a material gex crossing
      emits metrics_unavailable:true +
      insufficient_data + 7 null payout
      fields WHILE reporting gamma_flip:
      110.0 == independent plain-Python
      material-crossing recompute;
      is_gamma_flip flag set on strike 110.
  (D) all-NaN-OI expiry -> 'no_finite_oi'
      (distinguishable from
      'insufficient_data'); meta documents
      metrics_unavailable + insufficient_data
      + payout_eligibility.
  (E) Diff audit bb9f7b0^..HEAD: only
      GexDashboard.py +
      tests/test_gex_metrics.py +
      devin_blackboard/*; tree clean.
      Cycle F5 CLOSED.

- F6-T1 (B, done): WellnessCheck.py — post-generation
  ai_packet validator, separate script, NEVER imports
  GexDashboard (independent plain-Python reference impls
  of the documented production rules). Checks per the
  approved plan table: SCHEMA-01 (required fields/types,
  unique expiry+strike keys, schema_version {1.0}),
  NUMERIC-01 (no NaN/Inf tokens, spot>0, OI>=0, ISO
  dates), AVAIL-01a (flag/null/reason consistency),
  AVAIL-01b (eligibility directionality: >=2 exported
  positive-OI strikes proves eligible -> FAIL on
  false-unavailable; <2 is ambiguous without raw rows ->
  verdict unperformed; reason-code always unperformed
  without snapshot; with --snapshot exact eligibility +
  reason from raw rows, FAIL on mismatch), AVAIL-01c
  (gamma_flip nullable independent of payout
  availability), AGG-01 (strike->expiry->book net_gex
  sums, tol max(1e-9,1e-9*mag)), MAXPAIN-01 (canonical
  argmin exact match + tie count), MAXPAIN-02 (band
  candidates over full usable chain; fell_back/
  boundary_hit/selected_* flags), FLIP-01 (material
  neg->pos crossing, upper strike, no interpolation,
  nearest-spot + whole-book nearest_gamma_flip), IV-01
  (stale_iv iff iv:null AND raw>2.0; no unflagged >2.0),
  SNAP-01 ((expiry,strike,type) reconciliation; permitted
  transforms NaN->0 OI + NaN->null IV; >1e-6 rel gex/OI
  mismatch FAIL; 'unperformed' w/o snapshot), COVER-01
  (0-strike/sparse<10/>50% missing-OI warns), FRESH-01
  (>72h or --max-age-hours warn). INPUT-01 (unreadable/
  malformed/unsupported schema) + CHECKER-ERR (exit 2);
  structured report on every run incl. FAIL; provenance:
  packet+snapshot SHA-256, generated_utc, checker git
  HEAD (null+note when unversioned), schema_version,
  per-expiry counts {strikes, finite_oi_rows,
  excluded_rows, stale_iv_rows, metrics_unavailable};
  report 'x'-safe, never overwrites input. Exit 0/0/1/2.
  Fixtures regenerated OFFLINE: TSM_ai_packet.json rebuilt
  via current build_ai_packet from a chain RECONSTRUCTED
  out of the old fixture's exported strikes (every
  value the 33 legacy tests assert preserved: total_net_
  gex -52,574,553.7344639, flips 430/432.5/442.5/460/465/
  440/475, argmins 435/450/445/460/470/420/450, 23 stale
  strikes) now carrying all F2-F5 fields + corrected
  de-artifacted flips; TSM_snapshot.pkl = the
  reconstructed reconciling chain; TSM_raw_* pair =
  offline replay of the preserved raw chain
  (_prefix_run/tsm_snapshot.pkl, frozen clock
  2026-10-16T14:00Z) carrying real NaN-OI rows for the
  NaN->0 path. NOTE: _prefix_run snapshot is a DIFFERENT
  fetch (~72/105 strikes' OI differ on 10-16) — replaying
  it as the shared fixture would break 2 hardcoded legacy
  flip asserts, hence the two-pair layout (regen script:
  devin_blackboard/regen_f6_fixture.py). TASKS.json AC-3
  had a JSON-escaping bug (formfeed \f in the path) —
  command corrected to forward slashes, same criterion.
  tests/test_wellness.py: 41 offline tests — hand-calc
  mini-books (3-strike argmin, boundary-hit vs fell_back
  vs between-strikes, out-of-band OI shifting the
  constrained minimizer, flip floor/zero-gex/multi-
  crossing/nearest-spot), fixture PASS (+fresh override),
  stale-utc WARN, tamper FAILs (max_pain, flip, agg, IV
  provenance x2, false-unavailable-on-eligible,
  flag/null consistency, dq-on-available, NaN token,
  duplicate strike), ambiguity-without-snapshot +
  (100,1)/(110,0) vs (100,1)/(110,NaN) identical-export
  resolution, reason-mismatch FAIL, INPUT-01 x3,
  CHECKER-ERR exit 2, no-overwrite, default report path,
  cover warns, provenance fields. pytest 74/74 (33+41);
  findstr yfinance tests: no match; CLI exit 0 PASS.
  ACs: 3/3 PASS. Commits 5519f6d, 1342d64, 8593526.

## Ready

- F6-T2 (C): adversarial review — mutation probes
  corrupting each checked invariant should be caught by
  the matching check_id.

## Notes for reviewers

- F5-T2 non-blocking notes: (a) a one-sided
  2-strike chain (calls-only / puts-only) is
  eligible and its argmin lands mechanically on
  an extreme strike — the true argmin of that
  book, honestly computed; whether one-sided
  thin chains carry signal is a policy call,
  out of scope; (b) an expiry with a real
  gamma_flip + flagged payout is UNREACHABLE on
  the real pipeline — material gex requires
  nonzero finite OI at >=2 strikes, which makes
  the slice eligible; per-metric availability
  is defense-in-depth for cross-fed/synthetic
  frames (the new tests exercise it via
  synthetic gex, honestly documented);
  (c) the magic-message check is a SUBSTRING
  match — any ValueError containing numpy's
  'attempt to get argmin of an empty sequence'
  is absorbed, but only inside the try around
  the two payout calls (narrow blast radius);
  if a future numpy rewords the message, a real
  empty-argmin would propagate loudly — fail-
  loud direction; (d) after a constrained-fn-
  only failure the internal metrics dict can
  hold a computed max_pain alongside
  unavailable=reason — both consumers gate on
  'unavailable' so external state is always
  consistent; (e) inf-OI expiries crash the
  packet EXPORTER (int(inf) in strike export,
  OverflowError) — verified identical on
  bb9f7b0^, pre-existing and outside the
  metrics path; (f) mixed-sign OI summing
  positive passes the gate (negative OI is
  invalid input, garbage-in class).
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
- F5-T1 semantics EVOLVE that flag: metrics_unavailable:true now means
  the PAYOUT metrics (max_pain*/band flags) are unavailable — those 7
  fields are still explicit nulls + data_quality reason, but
  gamma_flip is an independent metric (gex-based, not OI-based) and
  MAY be non-null on a flagged expiry. _per_expiry_metrics always
  returns a dict now (never None); the "unavailable" key carries
  None / 'no_finite_oi' / 'insufficient_data'. New data_quality reason
  'insufficient_data' covers degenerate chains: <2 distinct finite-OI
  strikes (vacuous single-candidate argmin — live INTC 2026-10-19) or
  total finite OI <= 0 (every candidate is an argmin). Payout fns are
  never called on ineligible slices; the remaining except only
  tolerates numpy's 'attempt to get argmin of an empty sequence' —
  unrelated ValueErrors propagate.
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

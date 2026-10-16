# F6 Mutation Report — WellnessCheck.py

Enumeration of the 10 mutants that SURVIVED the committed
test suite at the F6-T2 adversarial review (mutation
harness: `devin_blackboard/probe_f6_t2.py` section S10 —
note: the task-brief path `devin_blackboard/f6_t2_mutants.py`
does not exist as a separate file; the authoritative mutant
list is the `MUTANTS` table embedded in probe_f6_t2.py).

Re-verified at the current revision (working tree on commit
ac4bedc + F6-T5 changes) by
`devin_blackboard/probe_f6_t5_mutants.py`, which builds each
mutant checker from the CURRENT WellnessCheck.py source and
runs the real committed `tests/test_wellness.py` suite
against it (mutant files must live inside the repo or
`_checker_git_commit()` returns None and confounds
`test_provenance_fields`).

Result at HEAD: **9 of the 10 former survivors are now
CAUGHT** by the suite (F6-T4 tests + the 8 new F6-T5
regression tests). The single remaining escape is a provably
equivalent mutation — uncatchable by design.

Classification legend:
- **equivalent-mutation** — no input exists on which the
  mutant's output differs from the real implementation.
- **verified-correct-narrow-edge** — the mutant diverges
  only on a measure-zero/degenerate edge where the real
  implementation's behavior was independently verified
  correct (probe-verified at F6-T2).
- **behavior-changing defect** — the mutant produces wrong
  verdicts/behavior on reachable inputs; a regression test
  was added and now kills it.

---

## 1. `agg_tol_1e-3` — AGG_REL_TOL 1e-9 → 1e-3

- **Mutation**: `check_agg` tolerance relaxed 1,000,000x.
- **Classification**: behavior-changing defect.
- **Probe input**: `_three_strike_expiry()` packet with the
  expiry `net_gex` drifted +5e-6 vs the strike-sum of 0
  (strike |gex| magnitudes 0.01); `total_net_gex` kept
  consistent so only the strike->expiry comparison drifts.
- **Expected vs actual**: real checker — AGG-01 **FAIL**
  (err 5e-6 > tol max(1e-9, 1e-9*0.01) = 1e-9); mutant —
  **PASS** (tol max(1e-9, 1e-3*0.01) = 1e-5 >= err).
- **Regression test**:
  `test_agg_sub_milli_drift_fails` (the drift must sit
  strictly inside (1e-9*mag, 1e-3*mag) — an earlier 1e-5
  probe hit the mutant boundary exactly and did not kill).
- **Status at HEAD**: CAUGHT (1 failing test under mutant).

## 2. `drop_maxabs_early_ret` — remove the
   `max_abs < MIN_MATERIAL_GEX -> None` early return in
   `_ref_gamma_flip`

- **Mutation**: deletes the whole-slice-noise early return.
- **Classification**: equivalent-mutation.
- **Proof of equivalence**: when `max_abs < 1e-3` the
  materiality floor is `max(0.01*max_abs, 1e-3) = 1e-3 >
  max_abs >= every |gex|`, so `material` is empty ->
  `crossings` is empty -> the function returns None by the
  later guard anyway. No distinguishing input exists
  (mirrors the identical redundant guard in production,
  noted at F2-T2).
- **Status at HEAD**: still ESCAPED — uncatchable by
  design; not a defect.

## 3. `raw_total_lt0` — `total <= 0` -> `total < 0` in
   `_ref_payout_ineligibility_raw`

- **Mutation**: a raw chain whose finite OI sums to exactly
  0 becomes "eligible" instead of `insufficient_data`.
- **Classification**: behavior-changing defect (zero-total
  chains are reachable on dead expiries).
- **Probe input**: snapshot rows with all-finite OI=0.0 at
  strikes 100/110 + a packet flagged
  `metrics_unavailable:true, data_quality:insufficient_data`.
- **Expected vs actual**: real — every candidate is an
  argmin -> reason `insufficient_data` -> AVAIL-01b **PASS**;
  mutant — reason None (eligible) -> flag mismatch
  **FAIL** verdict on the honest packet.
- **Regression test**:
  `test_zero_total_oi_snapshot_insufficient`.
- **Status at HEAD**: CAUGHT.

## 4. `pos_gt_2` — `pos >= MIN_PAYOUT_STRIKES` -> `pos > `
   in `check_avail_b`

- **Mutation**: exactly-2 exported positive-OI strikes no
  longer prove eligibility.
- **Classification**: behavior-changing defect (boundary
  shift defeats false-unavailable detection at the minimal
  eligible chain size).
- **Probe input**: expiry with exactly 2 positive-OI
  strikes (100 call_oi=1, 110 put_oi=1) flagged
  `metrics_unavailable:true`.
- **Expected vs actual**: real — AVAIL-01b **FAIL**
  (false-unavailable on provably-eligible chain); mutant —
  falls to the ambiguous branch -> **unperformed**, no FAIL.
- **Regression test**:
  `test_two_positive_strikes_prove_eligible`.
- **Status at HEAD**: CAUGHT.

## 5. `stale_iv_no_null_req` — `if iv is not None:` ->
   `if False:` in `check_iv`

- **Mutation**: drops the requirement that a `stale_iv:true`
  strike must emit `implied_volatility:null`.
- **Classification**: behavior-changing defect (provenance
  contract hole).
- **Probe input**: strike with `stale_iv:true`,
  `raw_implied_volatility:3.5`, `implied_volatility:0.3`
  left non-null.
- **Expected vs actual**: real — IV-01 **FAIL**; mutant —
  **PASS** (raw>2.0 satisfied, null requirement skipped).
- **Regression test**: `test_stale_iv_requires_null_iv`.
- **Status at HEAD**: CAUGHT.

## 6. `report_w_mode` — `open(rp, "x")` -> `open(rp, "w")`
   in `_write_report`

- **Mutation**: report writes clobber an existing file
  instead of falling back to a `-N` suffixed name.
- **Classification**: behavior-changing defect (silent
  data loss on a reachable path — the committed test only
  covered `--report == packet`).
- **Probe input**: pre-existing `report.json` containing
  a sentinel payload, passed as `--report`.
- **Expected vs actual**: real — sentinel file preserved,
  report written to `report-1.json`; mutant — sentinel
  **overwritten**, report returned at the requested path.
- **Regression test**:
  `test_existing_report_not_overwritten`.
- **Status at HEAD**: CAUGHT.

## 7. `tie_last_min` — `pain < best_p` -> `pain <= best_p`
   in `_ref_payout_argmin`

- **Mutation**: argmin tie-breaking flips from
  first-lowest (ascending, matching production's
  np.argmin first-min) to last-highest strike.
- **Classification**: verified-correct-narrow-edge —
  divergence only on exact payout ties; the real
  ascending-first behavior was probe-verified correct at
  F6-T2 and matches production semantics.
- **Probe input**: any flat-minimum book, e.g. the F6-T4
  `_band_nan` packet (pain 750 flat on [50,200]) or the
  F6-T5 `_mp01` zero-payout tie at strikes 90/100.
- **Expected vs actual**: real — argmin = lowest tying
  strike; mutant — argmin = highest tying strike.
- **Status at HEAD**: CAUGHT since F6-T4 (and now doubly
  covered by the new C1 tie tests — 5 tests fail under the
  mutant: the ambiguous-band/-argmin and raw-only-expiry
  cases all contain flat minima).

## 8. `accept_any_reason` —
   `if dq not in UNAVAILABILITY_REASONS:` -> `if False:` in
   `check_avail_a`

- **Mutation**: any string is accepted as `data_quality` on
  an unavailable expiry.
- **Classification**: behavior-changing defect (reason
  whitelist hole — undocumented reasons pass silently).
- **Probe input**: `metrics_unavailable:true` expiry with
  `data_quality:"vendor_hiccup"`.
- **Expected vs actual**: real — AVAIL-01a **FAIL**;
  mutant — **PASS**.
- **Regression test**: `test_unavailable_reason_whitelist`.
- **Status at HEAD**: CAUGHT.

## 9. `fresh_ge` — `elif age_h > max_age_hours:` -> `>=`
   in `check_fresh`

- **Mutation**: a packet exactly `max_age_hours` old is
  flagged stale (WARN) instead of fresh.
- **Classification**: verified-correct-narrow-edge —
  divergence only at exact boundary equality; spec wording
  is ">72h old" so strict `>` is correct.
- **Probe input**: packet `generated_utc` exactly 72h
  before a frozen `datetime.now` (monkeypatched clock).
- **Expected vs actual**: real — FRESH-01 **PASS**;
  mutant — FRESH-01 **WARN**.
- **Regression test** (boundary pinned anyway):
  `test_fresh_boundary_exactly_max_age`.
- **Status at HEAD**: CAUGHT.

## 10. `null_flag_means_avail` — `_avail_flag` returns
    `False` instead of `None` for a non-bool/absent
    `metrics_unavailable`

- **Mutation**: flag-absent/malformed expiries are treated
  as AVAILABLE — every flag-gated check (AVAIL-01a/b/c,
  MAXPAIN-01/02) evaluates them instead of skipping.
- **Classification**: behavior-changing defect (inverts the
  flag-absent -> skip/unperformed doctrine verified at
  F6-T2/F6-T3).
- **Probe input**: expiry with `metrics_unavailable` key
  absent entirely (pre-F5-style packet).
- **Expected vs actual**: real — MAXPAIN-01 **unperformed**
  ("no available expiry to recompute"; SCHEMA-01 also FAILs
  the missing required field); mutant — expiry evaluated as
  available -> MAXPAIN-01 **PASS**.
- **Regression test**: `test_flag_absent_expiry_unperformed`.
- **Status at HEAD**: CAUGHT.

---

## Non-survivors (context)

The 5 mutants already caught at F6-T2 remain caught at HEAD:
`flip_floor_1e-6` (test_flip_floor_boundary),
`flip_wrong_direction` (22 tests), `checker_err_exit_1`
(test_checker_err_exit_2), `band_0.6` (16 tests),
`no_dup_strike_check` (collection error under mutant).

## Score

| revision | caught | escaped |
|---|---|---|
| F6-T2 review | 5/15 | 10 |
| HEAD (F6-T5) | 14/15 | 1 (`drop_maxabs_early_ret`,
equivalent-mutation — uncatchable by design) |

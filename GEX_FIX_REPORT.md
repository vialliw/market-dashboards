# GEX Dashboard Fix — What Went Wrong and What We Changed

*Plain-language summary. For the technical version with commit hashes and test
evidence, see the review artifact (`gex_prefix_vs_current_v2.md`) and
`devin_blackboard/`.*

## The bug, in one sentence

Two of the numbers on the options dashboard — the "gamma flip" level and
"max pain" — were quietly picking meaningless strikes instead of the real
ones, because leftover dust in the data (missing values and near-zero noise)
was being treated as signal.

## What was going wrong

**Max pain** is supposed to find the strike where option payouts would hurt
most. The old code asked the computer for "the cheapest total" — but when a
strike's open-interest figure was missing (blank/NaN from the data vendor),
the total came back as "not a number," and the search blindly picked the
lowest strike on the list. That's why the packet reported max pain of
225–235 for TSM while the stock traded at ~$412 — the equivalent of saying
the market cares most about a strike nearly half the price away. It wasn't
analysis; it was a spreadsheet accident.

**Gamma flip** is supposed to mark where dealer hedging flips sign. The old
code scanned strikes from the bottom up and reported the *first* place the
number crossed zero — including deep out-of-the-money strikes whose gamma
was a rounding-error-sized fraction of the book (real values like +44 or
+360 on a book whose biggest strike carries ~1.4 million). Those crossings
exist, but they're economically meaningless — like weighing a truck by its
dust. That's how the dashboard reported a "flip" at 210 for a $412 stock.

**Stale implied volatility**: dead option contracts sometimes carry absurd
quotes (300–450% IV). These flowed into the AI packet unmarked, so an LLM
reading it couldn't tell live data from junk.

## What the new code does differently

1. **Ignores the dust.** Strikes now count only if their gamma is material —
   at least 1% of the biggest strike's gamma *and* above a small absolute
   floor. Noise-level crossings can no longer produce fake flips; a slice
   that is all noise returns "no flip" instead of a random strike.

2. **Doesn't let missing data pick answers.** Rows with missing/invalid
   open interest are excluded *before* the payout search, so the "lowest
   strike wins by accident" bug is gone. The fix was proven by reproducing
   the exact 225/230/235 artifacts on real data.

3. **Reports two honest max-pain numbers.** `max_pain` is the textbook,
   unrestricted answer; `max_pain_constrained` is the within-±30%-of-spot
   variant, clearly labeled with flags showing whether the band was empty
   or its edge was hit.

4. **Flags suspicious data instead of hiding or trusting it.** Wild IVs
   (>200%) are nulled in the packet but the original value is preserved as
   `raw_implied_volatility` with `stale_iv: true`, so downstream analysis
   can decide for itself.

5. **One bad expiry can't sink the whole ticker.** A totally empty or
   degenerate expiry (like SPY's all-missing 10/29 chain or INTC's
   single-strike 10/19 chain) now emits `metrics_unavailable` with a reason
   — `no_finite_oi` or `insufficient_data` — instead of crashing the run or
   emitting a meaningless number.

## What the live run caught

After the initial fix, a real market-hours run over 22 tickers exposed two
edge cases no test had anticipated — both fixed on the spot:

- **SPY, expiry 2026-10-29**: the vendor served a chain where *every*
  open-interest value was missing. The corrected code now correctly refused
  to compute on it — but the error killed the whole SPY run. One rotten
  expiry sank 15 good ones. Fix: an unusable expiry now emits
  `metrics_unavailable: true` + `data_quality: "no_finite_oi"` + explicit
  `null`s, while its siblings compute normally.

- **INTC, expiry 2026-10-19**: subtler. The vendor served a chain with
  exactly *one* strike (190, total open interest = 2). The math ran "fine"
  and returned that only strike as max pain — mathematically true,
  economically meaningless: the payout is zero at every price ≤190, so
  nothing actually minimizes anything. Fix: an expiry now needs ≥2 distinct
  strikes with real open interest before payout metrics are emitted;
  otherwise it's flagged `insufficient_data` with nulls instead of a
  confident-looking fake answer.

Both cases now show honest flags in the regenerated packets — verified
live.

## Why the review took so many rounds

The external reviewer refused to accept "looks right" as proof — and she
was right to:

- **Round 1**: she caught that a percentage-only filter can never catch a
  book that's *all* dust, that nulling bad IVs while keeping numbers
  computed *from* them was inconsistent, and that our "gamma flip" wasn't
  quite the textbook definition.
- **Round 2**: she rejected a written plan as evidence — specs aren't
  implementations. So the changes were actually built and tested.
- **Round 3**: her sharpest catch — she noticed the original max-pain
  *formula* was already correct, so fixing the *formula* couldn't explain
  the wrong answers. The *input* had to be poisoned. That forced the
  forensics that uncovered the missing-OI root cause.
- **Rounds 4–5**: she called out overconfidence — since the export had
  already erased the blanks-vs-zeros distinction, the mechanism was
  *consistent with* the artifacts, not *proven* to be their cause.
- **The live run** then surfaced the SPY and INTC edge cases above —
  she caused those fixes too, by flagging both the vacuous INTC number and
  the too-broad error catching.

Each round peeled a layer: surface artifact → wrong semantics → deeper
input bug → intellectual honesty about what can and can't be proven. The
fix is meaningfully better for it — final verdict **APPROVED** after six
rounds on the core fix, plus approval on the live-run verification and the
controlled old-vs-new comparison.

## Proof it works

Same raw TSM options data (one snapshot, replayed through old and new code
— no confounds): the old code produced flip **210** and max pain **225–235**;
the new code produces flip **425** and max pain **435–455** near spot
(~$413). Every artifact value was verified against independent
recomputation, and 33 offline tests now guard the behavior — including one
that deliberately reintroduces the bug to prove the tests catch it.

All changes were reviewed by an independent adversarial agent, a separate
critic with fresh eyes, and an external ChatGPT review loop that ended
**APPROVED** after six rounds.

## What the numbers still aren't

`max_pain` and `gamma_flip` are modeled quantities from open interest and
textbook assumptions — useful for framing, not forecasts of where price
goes. And during thin or off-hours data the flags (`stale_iv`,
`metrics_unavailable`) are the signal telling you how much to trust each
expiry.

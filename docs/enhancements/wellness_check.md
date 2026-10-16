Yes—add a **post-generation data-quality check before the JSON is published or passed to the AI analysis**. This is a useful next step after F1–F5. It can catch many defects, although no checker can detect every possible issue.

I would give it three outcomes:

- **PASS:** required checks passed.
- **WARN:** usable output, with specific limitations carried into the AI analysis.
- **FAIL:** a structural or calculation inconsistency makes the output unsuitable for publication.

For your GEX pipeline, I suggest these checks:

| Check | What it should detect | Response |
|---|---|---|
| JSON structure | Missing required fields, wrong types, duplicate expiries or strike keys, unsupported schema version | FAIL |
| Numeric validity | NaN/Infinity, nonpositive spot, negative OI, invalid dates | FAIL for affected data; distinguish missing values from invalid ones |
| Availability consistency | Null metrics without explanations; flags claiming availability when required inputs are unusable | FAIL |
| Aggregation consistency | Per-strike, per-expiry and aggregate GEX totals disagree beyond a defined rounding tolerance | FAIL |
| Max-pain consistency | Reported canonical value does not minimize payout on eligible inputs; constrained value or flags contradict the candidate band | FAIL |
| Gamma-flip consistency | Reported crossing violates the documented direction, materiality threshold or nearest-spot selection rule | FAIL |
| IV provenance | Flagged stale IV lacks its raw value; sanitization rules and exported values disagree | FAIL |
| Coverage and freshness | Missing expected expiries, stale timestamps, substantial missing OI, unusually sparse chains | WARN or FAIL according to explicit coverage requirements |
| Unusual results | Large metric moves, extreme distance from spot, endpoint selection or concentrated GEX | WARN and investigate |

**Do not make “near spot” a correctness requirement.** A distant max-pain value can be mathematically valid. Likewise, `gamma_flip:null` can be the correct result when no qualifying crossing exists. The check should enforce the defined calculation rules rather than prefer plausible-looking numbers.

There is one important limitation in your current workflow: **JSON alone cannot recover information discarded during export.** If missing OI becomes `0`, a downstream check cannot tell it apart from genuine zero OI. Keep the post-generation check, but let it access the preserved raw snapshot—or export explicit missingness and eligibility metadata.

For each run, retain:

- Raw snapshot hash, observation timestamps, code commit and schema version.
- Counts of missing or excluded OI rows and eligible strikes per expiry.
- A validation report containing status, check ID, affected expiry/field, observed value and expected rule.

The calculation checks should use a small, independent reference implementation. Calling the same production function again would reproduce its bugs and provide false reassurance.

My suggested first implementation is **schema/numeric checks, flag consistency, aggregate reconciliation, and independent max-pain/gamma-flip checks**. Test it against the preserved TSM snapshot, the NaN-OI regression, and the degenerate-chain cases already used for F1–F5. Add cross-run anomaly warnings afterward.

On failure, preserve the candidate packet and report for diagnosis, and keep the last validated published packet with its original timestamp clearly visible. This gives you a practical quality gate without silently concealing fresh-data failures.
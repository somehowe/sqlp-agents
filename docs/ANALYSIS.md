# Summarize a paired run

`tools.analyze_runs` reads one completed saved report. It recomputes descriptive
metrics from individual attempts; it does not call a model or rerun queries.

From the repository root:

```bash
python3 -m tools.analyze_runs build/pilot-report.json > build/analysis.json
python3 -m tools.analyze_runs build/pilot-report.json --markdown > build/analysis.md
```

It accepts the [prepared runner](QUERY_COMPARISON.md)'s `manual` reports and the
[pilot](MODEL_PILOT.md)'s completed `live`/`replay` reports. Dry plans, setup errors,
partial pilots, missing/duplicate conditions, and inconsistent status/usage records
are rejected. Each case must contain exactly one SQL and one SQLP attempt. Case
and condition order do not affect pairing. Cached report summaries are ignored.
Exit `0` means analysis succeeded, including when queries were wrong; exit `2`
means the report could not be analyzed. The CLI rejects duplicate JSON fields,
nonfinite constants, and files larger than 64 MiB.

The JSON result preserves `origin` and `scope`, and contains:

- **Paired correctness:** both right, SQL only right, SQLP only right, or neither
  right. Conditions agreeing does not establish correctness; this uses the saved
  oracle labels.
- **Per-condition correctness:** correct/attempts, execution successes, and error
  counts by generation, compilation, or execution stage. Failed attempts remain
  in the denominator.
- **Token usage:** input/output/total counts and mean/median total tokens per
  attempt. These use normalized provider counters, including failed attempts.
  Cached input and reasoning output are already included in the main counters;
  their nested fields are not added again.
- **Tokens per correct answer:** all tokens spent divided by the number correct,
  including tokens spent on failures. It is undefined (`null`) when no answers
  are correct or any usage is unknown.
- **Paired token differences:** `SQLP - SQL` for each case with both usages known,
  plus sum/mean/median over those known pairs. The result always gives known and
  missing pair counts and explicitly labels this coverage `known_pairs_only`.
  A negative difference means fewer recorded SQLP tokens on that observed pair.

A condition's total, mean, median, and tokens per correct remain `null` if even
one attempt has missing usage. The known/missing attempt counts explain why.
A known provider count of zero is retained as zero. Partial usage is unknown;
there is no invented estimate. Known-pair differences must not be described as
an overall token difference when other pairs are missing. Markdown displays
unknown values as `unknown`.

Pilot analysis verifies each request hash and condition's binding, generation
status/query/model, and normalized usage against its saved raw provider response.
The complete generation settings must match the manifest in both conditions,
including whether optional temperature/reasoning settings are absent. Requests
must remain synchronous, stateless, and tool-free.
The returned model must agree across conditions. This detects inconsistent
records; it is not a signature proving the report's origin. Manual reports cannot
contain provider generation or usage. Replay figures refer to recorded responses,
not newly billed calls; synthetic replay remains development evidence.

The analysis trusts the saved correctness labels and does not independently
review gold answers, prove SQL equivalence, or verify the dataset against its
hash. Preserve the original report, journal, and frozen artifacts. A `held_out`
scope label is preserved if supplied; the analyzer does not certify that tasks
were actually held out or reviewed. Reports are never automatically combined
across models, data versions, seeds, or question sets.

These are descriptive results for one batch. They do not establish statistical
significance, performance on other databases, API spending, database-storage
savings, or human productivity. Review individual failures and task coverage
before deciding what the paper can claim.

Offline checks: `python3 tests/analysis_test.py`.

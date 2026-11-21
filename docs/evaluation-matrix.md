# Compare evaluation reports offline

`evaluate-matrix` compares existing driver reports. It does not invoke a
model or driver, load credentials, fetch prices, or run publisher queries.
Its JSON and CSV exports keep execution, automatic checks, reviewed quality,
token usage and estimated cost separate.

[Inspect the deterministic driver demonstration](evaluation-example.html).
That example has no model-quality scores or supplied prices.

## Create a comparison bundle

```bash
doe-mcp evaluate-matrix \
  --report focused-skills-on.json \
  --report focused-skills-off.json \
  --grades reviewed-answers.json --prices supplied-prices.json \
  --output ./comparison
```

Repeat `--report` for every input. Grades and prices are optional. The output
must be a new directory. It contains `report.json`, `matrix.csv`, `runs.csv`,
`grades-template.json` and a checksum manifest. Unknown values are JSON null
and the word `unknown` in CSV. Existing bundles are never overwritten.

| Count | Meaning |
|---|---|
| Scheduled | Every task and repetition listed in the input, including failures and runs never started. |
| Attempted | Runs with at least one driver invocation. A timeout or invalid driver response still counts. |
| Completed | The driver returned a final-answer field. An answer that fails automatic checks remains in this count. |
| Automatic checked / passed | Final answers checked for answer and citation-reference structure, and those that passed. This is not semantic grading. |
| Reviewed | Completed answers with a supplied review bound to that exact report, task, rubric and answer. |
| Unreviewed or unrun | Scheduled runs without a matching review. Their quality remains unknown. |

`reviewed_quality_mean` describes only reviewed answers and has that explicit
denominator. `overall_quality_mean` remains unknown unless every scheduled
run is reviewed. Human and model reviews have separate counts and means.
A cell containing both reviewer kinds has no pooled quality mean.

## Review exact answers

New `evaluate` reports use format version 2 and record report/run identifiers,
task and rubric hashes, an explicit rubric version, and the final answer's
hash. The matrix recomputes those bindings and the automatic checks.
Changed answers, tasks or rubrics invalidate an old review.

First compare reports without `--grades`, then copy and fill
`grades-template.json`. Each review has this shape:

```json
{
  "format_version": "1",
  "grades": [{
    "report_id": "COPY_FROM_TEMPLATE",
    "report_sha256": "COPY_FROM_TEMPLATE",
    "run_id": "COPY_FROM_TEMPLATE",
    "task_sha256": "COPY_FROM_TEMPLATE",
    "answer_sha256": "COPY_FROM_TEMPLATE",
    "rubric_version": "COPY_FROM_TEMPLATE",
    "rubric_sha256": "COPY_FROM_TEMPLATE",
    "score": 0.75,
    "reviewer": {"kind": "human", "id": "YOUR_REVIEWER_ID"},
    "reviewed_at": "2026-11-21T12:00:00+00:00",
    "notes": "Explain the rubric judgments."
  }]
}
```

The number above illustrates the file format, not a measured model score.
Score on the supplied rubric's normalized zero-to-one scale. Read the full
answer, task, rubric and tool evidence in the source report. Keep only
completed reviews in the grade file; null template fields are refused.
Reviewer kind is `human` or `model`, and the reviewer identifier should name
the person or exact grading model. Only one review per run is accepted.
These are supplied reviews; the command cannot authenticate a reviewer.

Legacy format-1 reports can contribute execution and token counts. They
lack the explicit rubric-version binding required for reviewed quality.
Their quality remains unknown; they do not get inferred grades.

## Supply dated prices

The command does not know current provider prices. Supply rates for the
exact declared model and reported model version. This is an illustrative
price file, not a quote for any real model:

```json
{
  "format_version": "1",
  "rates": [{
    "model": "example-model",
    "model_version": "example-version",
    "effective_date": "2026-01-01",
    "currency": "USD",
    "input_per_million": "3.00",
    "cached_input_per_million": "0.30",
    "output_per_million": "12.00",
    "source_note": "Replace with the dated rates you chose for this comparison."
  }]
}
```

The latest matching rate dated on or before the report's UTC start date is
used. Dates, model versions and prices stay in the export. Multiple rates
with the same model, version and date are refused. Only USD is supported;
there is no currency conversion.

Input tokens include cached input. The estimate charges total input minus
cached input at the standard input rate, then charges cached input once at
its supplied rate. Output tokens include reasoning output, which is retained
as a subset and never added a second time.

Drivers may report `cached_input_tokens` and `reasoning_output_tokens`
beside `input_tokens` and `output_tokens`. Each subset must fit within its
parent total. A missing cached-input breakdown, incomplete usage, missing
model version or missing applicable price makes cost unknown. A cached
price is required when cached usage is positive. An unattempted run has no
driver calls and zero estimated token cost.

A cell with any unknown run cost has unknown total cost. Known run estimates
remain visible individually. These calculations cover the supplied token
rates only; they are estimates, never provider invoices or billed totals.
Driver token counts and sampling settings are declarations, not independently
verified usage.

## Keep comparison conditions visible

The default comparison requires equal task definitions, rubrics, repeat
schedules, skill-file hashes, registry revisions and fixture hashes. Model,
tool exposure, skill enablement, sampling settings and budgets are retained
as separate configuration fields. Per-task visible tools, profiles and tool
schema hashes remain available in the JSON.

Use `--allow-multiple-cohorts` to export unequal cohorts as separate groups.
They are never pooled. Missing model/version/settings metadata is explicit;
reports with unknown configuration fields are not pooled together. Duplicate
reports, executions, run ids, grades and conflicting task repeats are
refused. Usage totals must agree with the per-call data and report totals.

The source files and their content hashes identify the supplied evidence.
They are not signatures proving that an untrusted driver made a provider
call or that a report was never edited.

## Prepare future runs explicitly

Existing budgeted runs remain available through `evaluate`:

```bash
doe-mcp evaluate --prepare --output ./evaluation-tasks.json
doe-mcp evaluate --driver ./YOUR_DRIVER --model YOUR_MODEL \
  --task alias --max-calls 6 --max-tokens 10000 --repeats 3 \
  --exposure focused --output ./focused-skills-on.json
```

The second command invokes the executable you supply and may incur provider
charges through that executable. Choose its provider, key and budgets yourself.
Use `--no-skills` and `--exposure all` for the other configurations. No paid
driver is configured or run by the comparison command. `--simulation` labels
an explicitly supplied test driver; it does not turn an arbitrary driver
into an offline or safe executable.

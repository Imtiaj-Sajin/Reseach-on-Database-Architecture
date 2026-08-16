# How Often Does the PostgreSQL Planner Choose the Wrong Index?

Experimental study of access-path selection in PostgreSQL under controlled data
skew, predicate correlation, and physical clustering.

This repository contains everything needed to reproduce every number and figure
in the paper. No result is transcribed by hand; every table and figure is
generated from `results/raw/measurements_*.jsonl` by `analyze.py`.

## Research questions

1. How often does the PostgreSQL planner select a slower access path than one
   that was available to it, and how much time does that cost?
2. Which data properties drive misselection: value skew, correlation between
   predicate columns, or physical clustering of the indexed column?
3. Does cardinality misestimation actually cause misselection, or does the
   planner recover from bad estimates?
4. Do extended statistics (`CREATE STATISTICS`) fix index misselection under
   correlated predicates? Practitioner guidance says yes; the claim does not
   appear to have been evaluated systematically.

## Definitions

**q-error.** `max(estimate, actual) / min(estimate, actual)`, the standard
symmetric measure of cardinality misestimation. A value of 1 is perfect. The
ratio form is used because a factor-of-ten underestimate and a factor-of-ten
overestimate are equally damaging to plan choice.

**Access-path regret.** `time(plan the planner chose) / time(fastest plan
measured)`. A regret of 1.0 means the planner made the best available choice.
The denominator is the fastest arm we actually measured rather than a
theoretical optimum, so reported regret is a **lower bound** on the true
penalty.

**Misselection.** Regret above 1.2. The threshold keeps run-to-run variation
from being reported as a planner error. It is a judgement call and is stated as
such.

## Design

Each dataset varies one factor around a uniform, independent baseline so that
every effect is attributable.

| Factor | Levels | What it controls |
|---|---|---|
| `zipf_s` | 0.0, 0.5, 1.0, 1.5 | Value skew of the indexed column |
| `dep_strength` | 0.0, 0.25, 0.5, 0.75, 1.0 | Probability that `b` is a deterministic function of `a` |
| `physical_corr` | 0.0, 0.5, 0.95, 1.0 | Correlation between `ts` value order and physical row order, which BRIN depends on |

Four query families are run against each dataset: equality and range predicates
on the skewed column, a range predicate on the physically clustered column, and
conjunctive predicates on the correlated pair in both a dependent and an
independent variant.

**Index arms.** PostgreSQL offers no way to hide one index from the planner
while leaving others visible, so each candidate access path is measured by
building that index alone and running the full query set. A final `all` arm
builds every index at once, which reproduces a realistic deployment and is what
the planner's choice is read from. Regret is the ratio between the `all` arm and
the best single-index arm.

## Measurement protocol

Every timed execution goes through
`EXPLAIN (ANALYZE, BUFFERS, TIMING OFF, FORMAT JSON)`, and the reported figure
is the top-level `Execution Time`.

- `ANALYZE` executes server side and discards the result set, so client
  transfer never enters the measurement.
- `TIMING OFF` disables per-node instrumentation. With timing on, PostgreSQL
  reads the clock twice per emitted row, which adds a **plan-dependent**
  overhead that would bias the comparison towards plans emitting fewer rows.
  That is exactly the comparison being made, so the bias would be fatal.
- Using one instrument across all arms means residual overhead is common mode
  and cancels in the ratio.

Each query runs 7 times. The first is discarded as cache warming and the median
of the remaining 6 is reported.

**Session settings.** `max_parallel_workers_per_gather = 0` so that parallelism
does not confound the serial access-path decision, and `jit = off` because JIT
fires on cost thresholds and would therefore engage inconsistently across arms.

**Cache state.** Measurements are warm. Reliably evicting the OS page cache is
not portable across platforms, so cold-cache behaviour is out of scope and is
declared as such rather than approximated.

## Validity checks

`tests/test_generator.py` verifies the generator against ground truth before any
measurement is trusted:

- Zipfian skew concentrates the top ten values from 0.3 percent of rows at
  `s = 0` to 76.8 percent at `s = 1.5`.
- Realised dependency strength lands within 0.005 of the requested value at
  every level.
- Realised physical rank correlation is 0.005, 0.497, 0.949, 1.000 for the four
  settings, and is independently confirmed against PostgreSQL's own `pg_stats`
  correlation estimate at run time.
- Every predicate's true row count matches a direct recount of the generated
  arrays exactly.
- Identical seeds produce byte-identical data.

## Layout

```
src/config.py     parameter sweep and session settings
src/datagen.py    skew, dependency and clustering generation
src/queries.py    query construction with exact ground-truth cardinality
src/db.py         schema, bulk load, index arms, timing instrument
src/metrics.py    q-error and regret
run_experiment.py driver, resumable, appends JSONL
analyze.py        figures and tables
tests/            generator validation
```

## Reproducing

```bash
pip install -r requirements.txt

# One-off: place experiment objects on a dedicated tablespace.
psql -U postgres -c "CREATE TABLESPACE ts_dtms LOCATION '/path/with/space';"

python tests/test_generator.py       # validate the generator
python run_experiment.py --pilot     # smoke test the pipeline
python run_experiment.py --rows 1000000
python analyze.py
```

Runs are resumable. Anything already present in the measurement file is
skipped, so an interrupted sweep can be restarted without losing work. Each
table is dropped once its measurements are recorded, so peak disk use is one
table rather than all of them.

## Environment

| | |
|---|---|
| DBMS | PostgreSQL 17.1, x86_64-windows, MSVC 19.41 |
| CPU | Intel Core i5-12400, 6 cores / 12 threads |
| RAM | 23.8 GB |
| Storage | SSD, dedicated tablespace on a separate physical device from the OS |
| `shared_buffers` | 128 MB |
| `work_mem` | 4 MB |

Exact server settings are captured per run in
`results/raw/run_metadata_*.json`.

## Scope and limitations

- **Warm cache only.** See the protocol note above.
- **Single-table access paths.** Join order and join method selection are a
  separate and much-studied problem; this study deliberately isolates the
  access-path decision.
- **Regret is a lower bound**, since the denominator is the best measured arm
  rather than a proven optimum.
- **Buffer pool residency.** At one million rows the heap is roughly 112 MB
  against a 128 MB `shared_buffers`, so the working set is fully resident and
  no physical I/O occurs. Findings at that scale describe the cached regime.
  A larger scale is run separately to test whether they survive once the heap
  exceeds the buffer pool.

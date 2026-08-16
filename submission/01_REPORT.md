# Do Common PostgreSQL Index Tuning Practices Actually Help?

**An experimental study of index access-path selection in PostgreSQL 17**

Imtiaj Sajin
Department of Computer Science
Database Technology, Management and Security, Summer 2025-26
Project Type 4, Experimental / Benchmarking Project

---

## Abstract

Choosing the wrong index can make a query orders of magnitude slower, and a
substantial body of practitioner guidance exists to help database
administrators avoid that outcome. This report asks whether that guidance
survives measurement.

We built a controlled benchmark that varies value skew, predicate correlation,
and physical clustering independently, and measured 11,136 query executions
against PostgreSQL 17.1 across two table scales, 11 dataset configurations, 10
index configurations, and 7 cost-model settings. Ground-truth cardinality for
every predicate is computed from the generated data rather than taken from the
database, so estimation error can be measured directly.

Our first result is reassuring: with a conventional index set, PostgreSQL
selects a near-optimal access path. Only 2.3 percent of queries ran materially
slower than the best plan available, at a median penalty of 1.3 percent.

Our remaining results are not. We evaluate three widely recommended practices
and find all three backfire under identifiable conditions. Extended statistics,
the documented remedy for correlated predicates, improve cardinality estimates
by up to 108 times and make the affected queries up to 2.7 times slower.
Lowering `random_page_cost` below its default on SSD storage, advice repeated
across the PostgreSQL community, makes our workload 2.3 times slower rather
than faster. And adding a BRIN index alongside a B-tree on the same column
raises misselection from 2.3 percent to 73.3 percent, with individual queries
degrading by up to 194 times, though only while the table is resident in the
buffer pool.

In each case we trace the behaviour to a specific mechanism visible in the
query plan. The common thread is that all three practices are justified by
reasoning about one quantity, such as estimate accuracy or storage latency,
while plan quality depends on several.

---

## 1. Introduction

### 1.1 Motivation

A query optimiser's access-path decision, whether to scan a table sequentially
or to use one of several available indexes, is among the most consequential
choices it makes. The same query can differ by two orders of magnitude in
runtime depending on that single decision.

Because the decision matters and is opaque, a large body of tuning folklore has
grown around it. Database administrators are advised to create extended
statistics when columns are correlated, to lower `random_page_cost` when
running on solid-state storage, and to add BRIN indexes on naturally ordered
columns because they are small and cheap. This advice appears in official
documentation, vendor blogs, and community forums. It is rarely accompanied by
measurement.

### 1.2 Research questions

**RQ1.** How often does the PostgreSQL planner select a slower access path than
one available to it, and what does that cost?

**RQ2.** Which data properties drive misselection: value skew, correlation
between predicate columns, or physical clustering?

**RQ3.** Is cardinality misestimation the cause, as the literature on query
optimisation would suggest?

**RQ4.** Do the three tuning practices named above actually improve access-path
selection?

### 1.3 Contributions

1. A controlled benchmark in which skew, predicate correlation, and physical
   clustering are varied independently, with exact ground-truth cardinality for
   every predicate.
2. A quantification of PostgreSQL 17's access-path selection quality: near
   optimal under a conventional index set.
3. Three measured cases where recommended tuning practice degrades performance,
   each traced to a mechanism visible in the query plan.
4. A demonstration that one of those effects is conditional on buffer-pool
   residency, establishing a boundary rather than a blanket claim.
5. A fully reproducible artifact: public repository, seeded data generation,
   one-command re-run.

### 1.4 Related work

Leis et al. (VLDB 2015) established that cardinality misestimation is the
dominant source of bad plans, but studied join ordering rather than
single-table access paths. Moerkotte et al. (VLDB 2009) introduced the q-error
measure we adopt. Recent work has evaluated *learned* cost models on access-path
selection tasks, leaving the behaviour of the shipped classical planner
comparatively unexamined. Our study addresses that gap and, unlike the
cardinality-focused literature, finds misestimation is *not* the primary cause
of the misselection we observe.

---

## 2. Methodology

### 2.1 Definitions

**q-error.** The standard symmetric measure of cardinality misestimation,
`max(estimate, actual) / min(estimate, actual)`. A value of 1.0 is a perfect
estimate. The ratio form is used because a factor-of-ten underestimate and a
factor-of-ten overestimate are equally damaging to plan choice.

**Access-path regret.** `time(plan the planner chose) / time(fastest plan
measured)`. A regret of 1.0 means the planner made the best available choice; a
regret of 4.0 means the query took four times longer than necessary. The
denominator is the fastest configuration we actually measured, not a proven
optimum, so **reported regret is a lower bound** on the true penalty.

**Misselection.** Regret above 1.2. The threshold prevents run-to-run variation
from being counted as a planner error. It is a judgement call and is stated as
such.

### 2.2 Data generation

Real datasets do not permit varying one property while holding others fixed, so
all data is generated. Three factors are controlled independently:

| Factor | Levels | Controls |
|---|---|---|
| `zipf_s` | 0.0, 0.5, 1.0, 1.5 | Value skew of the indexed column |
| `dep_strength` | 0.0, 0.25, 0.5, 0.75, 1.0 | Probability that column `b` is a deterministic function of column `a` |
| `physical_corr` | 0.0, 0.5, 0.95, 1.0 | Correlation between value order and physical row order, which BRIN depends on |

Each dataset varies one factor around a uniform, independent baseline, so every
effect is attributable. Eleven configurations result. All randomness is seeded,
so the corpus is byte-for-byte reproducible.

Domain sizes are load-bearing. With one million rows and 100 distinct values in
each of `a` and `b`, an independent conjunction `a = va AND b = vb` returns
about 100 rows, which is exactly what PostgreSQL's independence assumption
predicts. Under a full functional dependency the same predicate returns all
10,000 rows sharing `a = va` while the estimate stays at 100. That is a q-error
of 100 at one percent selectivity, which is precisely the region where index
and sequential access compete.

### 2.3 Query families

Four families, each with exactly known true cardinality computed by counting
the generated arrays directly:

- **Equality** on the skewed column. Candidates: hash, B-tree, BRIN, sequential.
- **Range** on the skewed column. Hash cannot serve this, testing fallback.
- **Range on the physically clustered column.** BRIN's home ground.
- **Conjunctive** on the correlated pair, in dependent and independent variants.

### 2.4 Measurement protocol

Every timed execution used
`EXPLAIN (ANALYZE, BUFFERS, TIMING OFF, FORMAT JSON)`, reading the top-level
`Execution Time`. Three reasons, each of which affects validity:

1. `ANALYZE` executes server-side and discards the result set, so client
   transfer never enters the measurement.
2. `TIMING OFF` disables per-node instrumentation. With timing on, PostgreSQL
   reads the clock twice per emitted row, adding an overhead that is
   **plan-dependent**. That would systematically bias comparison toward plans
   emitting fewer rows, which is the exact comparison being made.
3. One instrument across all configurations means residual overhead is common
   mode and cancels in the ratio defining regret.

Each query ran 7 times; the first was discarded as cache warming and the median
of the remaining 6 reported. Parallelism was disabled
(`max_parallel_workers_per_gather = 0`) so it could not confound the serial
access-path decision, and JIT was disabled because it engages on cost
thresholds and would fire inconsistently across configurations.

Because PostgreSQL offers no way to hide one index from the planner while
leaving others visible, each candidate access path was measured by building
that index alone and running the full query set. A final configuration builds
every index at once, reproducing a realistic deployment, and is what the
planner's choice is read from.

### 2.5 Validity checks

Nineteen automated checks run before any measurement is trusted:

- Zipfian skew concentrates the top ten values from 0.3 percent of rows at
  `s = 0` to 76.8 percent at `s = 1.5`.
- Realised dependency strength lands within 0.005 of the requested value at
  every level.
- Realised physical rank correlation is 0.005, 0.497, 0.949, 1.000 for the four
  settings, **independently confirmed against PostgreSQL's own `pg_stats`
  correlation estimate** at run time.
- Every predicate's row count matches a direct recount of the generated arrays.
- Identical seeds produce identical data.

One methodology defect was found and corrected during the study. The range
predicate builder originally anchored a fixed-width window at the median row
position; on a Zipfian column the median row falls inside a single very
frequent value, so the bounds collapsed and the predicate returned that value's
entire frequency regardless of the requested target. On the most skewed dataset
every target below 13.6 percent produced an identical query. The affected
measurements were discarded and re-collected after the fix. Recorded row counts
were always exact, so no reported metric was wrong, and the headline results
were unchanged by the correction (73.9 to 73.3 percent).

### 2.6 Environment

| | |
|---|---|
| DBMS | PostgreSQL 17.1, x86_64-windows, MSVC 19.41 |
| CPU | Intel Core i5-12400, 6 cores / 12 threads |
| RAM | 23.8 GB |
| Storage | SSD, dedicated tablespace on a separate physical device from the OS |
| `shared_buffers` | 128 MB |
| `work_mem` | 4 MB |
| `random_page_cost` | 4.0 (default), swept 4.0 to 1.0 |

Scale: 1,000,000 rows (heap 112 MB, buffer-pool resident) and 10,000,000 rows
(heap 1.1 GB, not resident). Total: **11,136 measured executions**.

---

## 3. Results

### 3.1 RQ1: the planner is good

With a conventional index set, that is B-tree and hash indexes but no BRIN:

| scale | queries | misselection | median regret | p90 | max |
|---|---|---|---|---|---|
| 1M | 130 | **2.3%** | 1.013 | 1.13 | 1.39 |
| 10M | 155 | 32.3% | 1.043 | 1.52 | 3.70 |

At one million rows the planner chose the best available access path in 97.7
percent of cases, and where it erred the median penalty was 1.3 percent. This
is a positive result and it frames everything that follows: PostgreSQL's
access-path selection is not broken, so the failures reported below are
specific rather than general.

### 3.2 RQ3: misestimation is not the cause

Of the 113 misselected queries at one million rows, the **median q-error was
1.017**, and **98.2 percent had a q-error below 1.5**. The planner's row
estimates were essentially correct and it chose badly anyway.

This is the study's most important negative result. It rules out the
explanation the optimisation literature would predict and redirects attention
to the cost model and to path generation.

### 3.3 Practice 1: extended statistics correct the estimate and slow the query

`CREATE STATISTICS (ndistinct, dependencies, mcv)` is PostgreSQL's documented
remedy for correlated predicates. It works, on the estimate:

| dependency | q-error without | with | improvement | est. rows without | with | true | ms without | with | slowdown |
|---|---|---|---|---|---|---|---|---|---|
| 0.25 | 25.23 | 1.11 | 22.8x | 105 | 2,767 | 2,556 | 0.93 | 2.54 | **2.73x** |
| 0.50 | 49.52 | 1.04 | 47.6x | 103 | 5,200 | 5,053 | 2.12 | 3.53 | 1.66x |
| 0.75 | 75.89 | 1.02 | 74.7x | 99 | 7,633 | 7,513 | 2.80 | 4.34 | 1.55x |
| 1.00 | 113.19 | 1.05 | **108x** | 89 | 9,700 | 9,995 | 3.95 | 5.06 | 1.28x |

Estimates go from wrong by two orders of magnitude to essentially exact, and
every affected query gets slower.

**Mechanism.** The plans show it directly. Without extended statistics the
planner uses the composite index on `(a, b)` in a single index scan. With them
it switches to a `BitmapAnd` of two single-column indexes. Both fetch the
identical 7,317 heap blocks, but the `BitmapAnd` performs two index scans plus
a bitmap intersection instead of one scan.

The reason it makes that switch is visible in the node estimates:

```
Bitmap Heap Scan  ... rows=10367     <- extended statistics applied
  -> BitmapAnd    ... rows=107       <- independence assumption, NOT corrected
```

The correction reaches the top-level estimate but not the `BitmapAnd` node,
which still derives 107 rows by multiplying the two per-column selectivities
under independence. The planner therefore costs the `BitmapAnd` at 635 using
the *uncorrected* figure and the composite path at 13,558 using the *corrected*
one, a 21-fold gap, and picks the plan that is actually slower. **The
correction is applied inconsistently across path types.**

### 3.4 Practice 2: lowering `random_page_cost` on SSD

The default `random_page_cost` of 4.0 assumes a random page fetch costs four
times a sequential one, which is a spinning-disk assumption. Community guidance
recommends lowering it to between 1.0 and 1.5 on SSD storage. Measured across
the whole query set:

| `random_page_cost` | total time, conventional index set | versus best |
|---|---|---|
| 1.0 | 2,306 ms | **2.3x worse** |
| 1.1 | 1,954 ms | 1.9x worse |
| 1.2 | 1,942 ms | 1.9x worse |
| **1.5** | **1,006 ms** | best |
| 2.0 | 1,053 ms | 1.0x |
| 3.0 | 1,053 ms | 1.0x |
| **4.0 (default)** | **1,068 ms** | 1.06x |

The default is within 6 percent of optimal. Lowering it to 1.0 costs a factor
of 2.3. The access-path counts explain why: at `rpc = 1.0` the planner abandons
bitmap scans entirely in favour of plain index scans (136 index scans, zero
bitmap scans), which is the wrong choice for higher-selectivity predicates.

A single-query measurement early in this study suggested the default cost 24
times. That observation was real but unrepresentative, and the workload-level
sweep supersedes it. We report this because it illustrates how anecdotal tuning
evidence misleads.

### 3.5 Practice 3: adding a BRIN index alongside a B-tree

BRIN indexes are small and cheap to build, so adding one is often treated as
harmless. It is not, while the table is buffer-pool resident:

| scale | with BRIN | without BRIN | median regret | max regret |
|---|---|---|---|---|
| **1M** | **73.3%** | 2.3% | 2.06 vs 1.01 | **18.78** |
| 10M | 30.1% | 32.3% | 1.04 vs 1.04 | 2.63 |

At one million rows, adding a BRIN index alongside an existing B-tree raises
misselection from 2.3 percent to 73.3 percent. In controlled diagnostics with
statistics held byte-identical, individual queries degraded by up to **194
times**, from 0.34 ms to 65.95 ms.

**Mechanism.** With both indexes present and a bitmap plan forced, the planner
selected BRIN in **16 of 16 cases**, and in 10 of those the B-tree bitmap path
was cheaper *by the planner's own cost model*, once by a factor of 9. A
cost-based optimiser does not knowingly select a path it rates as nine times
more expensive, which indicates the B-tree bitmap path is discarded during path
generation rather than rejected on cost. We note that this is an inference from
cost arithmetic and has not been confirmed against PostgreSQL's source.

The cost of the chosen plan is visible in its execution:
`Heap Blocks: lossy=14304` and `Rows Removed by Index Recheck: 990000`. BRIN
reads essentially the whole table and discards 99 percent of it.

**Boundary.** The effect does not survive scale. At ten million rows the two
configurations are indistinguishable. The `phys` datasets, which vary the
physical correlation BRIN depends on, confirm this directly: `phys05` moves
from 90.0 percent versus 0.0 percent at 1M to 50.0 versus 57.1 at 10M, with the
BRIN configuration marginally better.

The two scales are genuinely different regimes, confirmed independently by the
buffer statistics: blocks read from outside the buffer pool are **0 at one
million rows and 89,081 at ten million**.

**Why the effect disappears is not established.** BRIN is selected about equally
often at both scales, roughly 4.5 percent of range queries, so it is not that
the planner stops choosing it. We record this as an open question rather than
offer an unverified explanation.

### 3.6 Incidental finding: hash index build cost under skew

Hash index build time at ten million rows, by skew:

| dataset | `zipf_s` | hash build | B-tree build |
|---|---|---|---|
| baseline | 0.0 | 11.6 s | 3.2 s |
| skew05 | 0.5 | 16.7 s | 2.3 s |
| skew10 | 1.0 | 187.7 s | 2.5 s |
| **skew15** | **1.5** | **2,152.9 s** | 2.3 s |

A **185-fold blowup** from uniform to heavily skewed data, while a B-tree on the
same column is unaffected. Zipfian values collide into few hash buckets,
producing long overflow chains, and PostgreSQL does not parallelise hash builds
as it does B-tree builds. Practitioners should not assume hash index creation
is cheap on skewed columns.

---

## 4. Discussion

### 4.1 A common thread

The three practices fail for the same underlying reason. Each is justified by
reasoning about a single quantity while plan quality depends on several.

Extended statistics optimise **estimate accuracy**, and a more accurate
estimate improves the plan only if it is applied consistently everywhere the
plan is costed. It is not.

Lowering `random_page_cost` optimises for **storage latency**, and it is true
that random access on SSD is cheaper than the default assumes. But the setting
also governs the choice between bitmap and plain index scans, and pushing it
too low wins on one axis and loses on the other.

Adding a BRIN index optimises for **index size and build cost**, both of which
genuinely favour BRIN. But index availability also changes which paths the
planner generates, and a cheap index that captures the bitmap path can exclude
a better one.

In each case the advice is correct about the quantity it names and wrong about
the outcome, because the outcome depends on more than that quantity.

### 4.2 Practical guidance

1. **Do not add extended statistics reflexively.** Verify the plan changed for
   the better, not merely that the estimate improved. The two are separable.
2. **Do not lower `random_page_cost` below about 1.5 without measuring.** The
   default was within 6 percent of optimal in our environment.
3. **Treat a BRIN index alongside a B-tree on the same column as a risk**, and
   measure before deploying, particularly where the table fits in the buffer
   pool.
4. **Do not assume hash index creation is cheap** on skewed columns.

### 4.3 Threats to validity

**Internal.** Regret is a lower bound because the denominator is the fastest
measured configuration rather than a proven optimum. Regret values slightly
below 1.0 occur because the planner can combine indexes in ways no single
forced configuration can. The 1.2 misselection threshold is a judgement call.

**Construct.** The path-suppression mechanism in Section 3.5 is inferred from
cost arithmetic, not confirmed against source. The extended statistics result
requires a redundant index set, a composite index plus both single-column
indexes, which is common but not universal.

**External.** One DBMS, one version, one hardware configuration. All
measurements are warm-cache; reliably evicting the OS page cache is not
portable across platforms, so cold-storage behaviour is out of scope rather
than approximated. With 23.8 GB of RAM the OS page cache retains the data even
at ten million rows, so the larger scale tests leaving the *buffer pool*, not
leaving memory. Single-table access paths only; join ordering is deliberately
excluded.

---

## 5. Conclusions

PostgreSQL 17's access-path selection is close to optimal under a conventional
index set, choosing the best available plan for 97.7 percent of our queries.
Where it fails, cardinality misestimation is not the cause: 98.2 percent of
misselected queries had accurate row estimates.

The failures instead trace to the cost model and to path generation, and are
triggered by three practices intended to help. Extended statistics improve
estimates by up to 108 times and slow queries by up to 2.7 times, because the
correction is not applied to the `BitmapAnd` node. Lowering `random_page_cost`
on SSD, contrary to common advice, costs a factor of 2.3 in our workload, the
default being within 6 percent of optimal. A co-located BRIN index raises
misselection from 2.3 to 73.3 percent and can slow individual queries by 194
times, but only while the table is buffer-pool resident.

That last boundary is as much a part of the result as the effect itself. A
tuning recommendation is not right or wrong in general; it is right or wrong
under conditions, and those conditions are measurable.

## 6. Future work

1. Confirm the path-suppression mechanism against PostgreSQL's source.
2. Locate the scale at which the BRIN effect breaks down, which would convert
   an open question into a measured boundary tied to buffer pool size.
3. Repeat on a second DBMS to test whether the findings are PostgreSQL-specific.
4. Extend to cold-cache conditions on hardware where page cache eviction is
   controllable.

---

## References

1. V. Leis, A. Gubichev, A. Mirchev, P. Boncz, A. Kemper, T. Neumann. How Good
   Are Query Optimizers, Really? PVLDB 9(3), 2015.
2. G. Moerkotte, T. Neumann, G. Steidl. Preventing Bad Plans by Bounding the
   Impact of Cardinality Estimation Errors. PVLDB 2(1), 2009.
3. PostgreSQL Global Development Group. PostgreSQL 17 Documentation, Chapter
   14.2, Statistics Used by the Planner, and `CREATE STATISTICS`.
4. PostgreSQL Global Development Group. PostgreSQL 17 Documentation, Chapter
   63.6, Index Cost Estimation Functions.

---

## Artifact

All code, raw measurements, figures, and the executable analysis notebook:
<https://github.com/Imtiaj-Sajin/Reseach-on-Database-Architecture>

Every number in this report is generated from the raw measurement files by
`analyze.py`. None is transcribed by hand.

# Research Status

**Project.** Do common PostgreSQL index tuning practices actually help?
**Student.** Md. Imtiaj Alam Sajin
**Supervisor.** Dr. Ashraf Uddin
**Updated.** 18 August 2026, 18:45

---

## What the research asks

When you run a query, the database must decide **how** to find the rows: read
the whole table, or use an index, and if so which one. The same query on the
same data can be a hundred times slower depending on that one decision.

Because the decision is invisible to users, a large body of tuning advice has
grown around helping the database choose better. It appears in the official
documentation, vendor blogs and community forums. **Almost none of it has been
measured.** This project measures it.

---

## How we measure it

We generate the data ourselves rather than using a fixed benchmark, for one
reason: it lets us vary one property at a time while holding the others fixed,
and it means **we know the true answer for every query**. That is what allows
us to say precisely when the database guessed wrong, rather than guessing along
with it.

Three properties are varied independently:

| Property | What it means | Levels |
|---|---|---|
| Value skew | Are some values far more common than others? | 4 |
| Predicate correlation | Do two columns move together? | 5 |
| Physical clustering | Are rows stored in value order? | 4 |

That produces **11 dataset configurations**. Each is run against **10 index
configurations** (every index alone, then all together), with four query
families, and every query executed 7 times with the first discarded.

Full parameter detail is in the reference section at the end of this document.

---

## Experiment matrix

Each cell is one full pass: 11 datasets, all index configurations, all queries.

### PostgreSQL 17.1 â€” complete

| Table size | Heap | Fits in 128 MB buffer pool? | Datasets | Measurements | Status |
|---|---|---|---|---|---|
| 1,000,000 | 112 MB | yes | 11 | 2,398 | done |
| 1,250,000 | 140 MB | no | 3 | 654 | done |
| 1,500,000 | 168 MB | no | 3 | 654 | done |
| 2,000,000 | 223 MB | no | 3 | 654 | done |
| 3,000,000 | 335 MB | no | 3 | 654 | done |
| 5,000,000 | 558 MB | no | 3 | 654 | done |
| 10,000,000 | 1,116 MB | no | 11 | 2,398 | done |
| cost-model sweep | 7 settings | | 5 | 6,230 | done |

**PostgreSQL total: 14,296 measurements.**

### MariaDB 10.4.28 â€” in progress

| Table size | Datasets | Measurements | Status |
|---|---|---|---|
| 1,000,000 | 11 | 1,232 | done |
| 1,250,000 | 3 | 336 | done |
| 1,500,000 | 3 | 336 | done |
| 2,000,000 | 3 | 336 | done |
| 3,000,000 | 3 | 336 | done |
| 5,000,000 | 3 | 336 | done |
| 10,000,000 | 11 | 144 so far | **running now, ~3 hours left** |

**MariaDB so far: 3,058 measurements.**

The second database exists to separate findings that are general properties of
databases from findings that are quirks of one product. Note it is measured at
fewer sizes than PostgreSQL at present; closing that gap is exactly what the
current run is doing.

---

## Findings so far

Every number below is measured, not estimated. "Regret" means how many times
slower the chosen plan was than the best plan that was available. A regret of
1.0 means the database made the best choice it could.

### 1. PostgreSQL is good at this, MariaDB much less so

Identical data, identical queries, identical measurement.

| Database | Queries | Chose badly | Median regret | Worst case |
|---|---|---|---|---|
| PostgreSQL 17.1 | 130 | **2.3%** | 1.01 | 1.39 |
| MariaDB 10.4.28 | 153 | **66.0%** | 1.34 | 7.91 |

PostgreSQL picks the best available plan 97.7% of the time. So the failures
below are specific problems, not a generally poor optimiser.

MariaDB errs far more often but far more mildly. The reason is visible in the
plans: PostgreSQL uses "bitmap scans", which sort matching rows into disk order
before fetching them. MariaDB has no equivalent, so it either does a direct
index lookup or gives up and scans the whole table. It scanned the whole table
for 66 queries where PostgreSQL never did.

### 2. The textbook explanation does not apply

The accepted cause of bad query plans is the database misjudging how many rows
will come back. We found the opposite.

| Measure | Value |
|---|---|
| Bad choices examined | 113 |
| Median estimation error among them | **1.017** (a perfect estimate is 1.0) |
| Share whose estimate was essentially correct | **98.2%** |

The database knew the right numbers and chose badly anyway. This contradicts
what the query optimisation literature predicts.

### 3. Three recommended practices make things worse

**3a. Extended statistics for correlated columns**

The official remedy. It fixes the estimate and damages the plan.

| Column correlation | Estimation error without | with | Improvement | Time without | with | Result |
|---|---|---|---|---|---|---|
| 0.25 | 25.2 | 1.11 | 23x better | 0.93 ms | 2.54 ms | **2.7x slower** |
| 0.50 | 49.5 | 1.04 | 48x better | 2.12 ms | 3.53 ms | 1.7x slower |
| 0.75 | 75.9 | 1.02 | 75x better | 2.80 ms | 4.34 ms | 1.6x slower |
| 1.00 | 113.2 | 1.05 | **108x better** | 3.95 ms | 5.06 ms | 1.3x slower |

**3b. Lowering `random_page_cost` on SSD**

Widely recommended because SSDs handle random reads well. Measured over the
whole workload, the default is nearly optimal and lowering it is harmful.

| Setting | Total workload time | Versus best |
|---|---|---|
| 1.0 | 2,306 ms | **2.3x worse** |
| 1.1 | 1,954 ms | 1.9x worse |
| 1.2 | 1,942 ms | 1.9x worse |
| 1.5 | 1,006 ms | best |
| 2.0 | 1,053 ms | equal |
| 3.0 | 1,053 ms | equal |
| **4.0 (default)** | **1,068 ms** | **within 6% of best** |

**3c. Adding a BRIN index because it is small and cheap**

| Table size | With BRIN | Without BRIN | Worst case |
|---|---|---|---|
| 1,000,000 rows | **73.3%** chose badly | 2.3% | **18.8x slower** |
| 10,000,000 rows | 30.1% | 32.3% | 2.6x |

In controlled tests holding everything else fixed, individual queries went from
0.34 ms to 65.95 ms purely because a BRIN index existed alongside the B-tree.
That is **194x**.

### 4. We identified the exact cause of the third

Reading PostgreSQL's source code (`choose_bitmap_and` in `indxpath.c`): when two
indexes can answer the same query, it discards one, keeping whichever is
"cheaper to scan". But cheapness is judged by **the cost of reading the index
alone**, ignoring the work the query then does on the table itself.

A BRIN index is tiny by design, so it wins that comparison essentially always.

| | Index-reading cost | Actual query time |
|---|---|---|
| BRIN | **12.13** (wins) | 65.95 ms |
| B-tree | 213.35 (discarded) | **0.34 ms** |

The index it throws away is the one that would have been 194x faster.

### 5. The problem has a boundary, and it is counterintuitive

The BRIN problem occurs only while the table fits in the 128 MB buffer pool.
But the **worst** cases are not at the smallest size.

| Table size | Heap | Fits in pool? | Chose badly | Worst case |
|---|---|---|---|---|
| 1,000,000 | 112 MB | yes | **71.4%** | 18.8x |
| 1,250,000 | 140 MB | no | 37.9% | **38.8x** |
| 1,500,000 | 168 MB | no | 31.0% | **40.3x** |
| 2,000,000 | 223 MB | no | 25.8% | **38.3x** |
| 3,000,000 | 335 MB | no | 30.3% | **41.5x** |
| 5,000,000 | 558 MB | no | 36.4% | 7.0x |
| 10,000,000 | 1,116 MB | no | 38.2% | 1.9x |

Two separate things happen at two separate points. How **often** it goes wrong
collapses right at the memory boundary. How **badly** it goes wrong in the worst
case actually doubles there, stays high to 3 million rows, and only subsides
much later.

Had we tested only the smallest and largest sizes, we would have concluded the
problem simply disappears as tables grow. It does not, and the most severe
damage happens to databases sized two to three times their memory.

### 6. The second database separates general from specific

**The correlated-predicate failure is general.** MariaDB estimates these
predicates perfectly until it switches to its equivalent of PostgreSQL's
combined-index plan, at which point it fails the same way.

| Column correlation | PostgreSQL error | MariaDB error | MariaDB plan used |
|---|---|---|---|
| 0.25 | 26.4 | **1.00** | single combined index |
| 0.50 | 49.0 | **1.00** | single combined index |
| 0.75 | 81.8 | **1.00** | single combined index |
| 1.00 | 98.7 | **55.8** | **merges two indexes** |

Both databases are accurate while one index answers the query, and both fail
once two indexes are merged. Two independently written databases failing the
same way means this is a **design problem, not a bug in either product**.

**Good plan selection is PostgreSQL-specific**, as the table in Finding 1 shows.

### 7. The memory boundary itself is implementation-specific (preliminary)

MariaDB across the same table sizes shows a **different** pattern from
PostgreSQL. Its worst case keeps growing rather than subsiding.

| Table size | Heap | Chose badly | Worst case |
|---|---|---|---|
| 1,000,000 | 110 MB | 52.9% | 3.3x |
| 1,250,000 | 137 MB | 74.3% | 6.5x |
| 1,500,000 | 164 MB | 77.8% | 10.9x |
| 2,000,000 | 218 MB | 75.7% | 15.2x |
| 3,000,000 | 327 MB | 36.1% | 18.8x |
| 5,000,000 | 545 MB | 37.9% | **27.8x** |

Where PostgreSQL's worst case collapses beyond 3 million rows, MariaDB's is
still climbing at 5 million. This is marked preliminary because the 10 million
row measurements are still running.

---

## Quality control

Nineteen automated checks run before any measurement is trusted, including
verifying our generated data against PostgreSQL's own internal statistics, so
the generator is validated against something we do not control.

Three of our own measurement errors were found and corrected during the study,
and the paper reports them rather than omitting them:

1. A query builder defect that silently collapsed the selectivity range on
   skewed data. Affected measurements were discarded and recollected.
2. Row counts meaning different things in the two databases, which would have
   made every cross-database comparison meaningless.
3. An estimate that looked too accurate to be real, resolved by re-running
   through a command that does not execute the query. It turned out genuine.

---

## Outputs

| Item | State |
|---|---|
| Course report (Type 4) | Complete, 27 pages, LaTeX, submitted format |
| Journal paper draft | Complete, 30 pages, Elsevier format |
| Code and raw data | Public, one-command reproduction |
| Analysis notebook | Executes end to end, no errors |

**Repository:** <https://github.com/Imtiaj-Sajin/Reseach-on-Database-Architecture>

**Target journal:** Information Systems (Elsevier, Q2, impact factor 3.4).
Fallback: Data and Knowledge Engineering (Elsevier, Q2, impact factor 3.9).
Neither charges a publication fee under the standard subscription route.

---

## What remains

1. MariaDB sizes finishing tonight, which removes the asymmetry between the two
   databases.
2. A cost-model sweep on MariaDB, to match the one already done on PostgreSQL.
3. Expand the paper from 5,300 to roughly 8,000 words, mostly related work.
4. **Supervisor review and approval before any submission.**

---
---

## Reference: every parameter

This section exists so the design can be defended in detail without reading the
code. All values are the ones actually used.

## The three knobs

Everything is generated rather than taken from a fixed benchmark, for one
reason: it allows one property to be changed while the others are held still.
With a real dataset you cannot separate cause from coincidence.

### Knob 1: value skew (`zipf_s`)

Are some values far more common than others?

Real example: a `city` column where 40% of rows say Dhaka and one row says
Bandarban. Drawn from a Zipfian distribution over a fixed domain, so the number
of distinct values stays constant and only the shape changes.

| Setting | Meaning | Measured effect |
|---|---|---|
| 0.0 | every value equally common | top 10 values hold 0.3% of rows |
| 0.5 | mildly lopsided | |
| 1.0 | strongly lopsided | |
| 1.5 | extremely lopsided | top 10 values hold **76.8%** of rows |

**Why it matters.** An index is excellent for a rare value and useless for a
value covering 40% of the table. Skew is what makes the same index good for one
query and bad for the next.

### Knob 2: predicate correlation (`dep_strength`)

Do two columns move together?

Real example: `city` and `postcode`. Knowing one nearly determines the other.
Implemented as: column `b` is a fixed function of column `a` with this
probability, and independent otherwise.

| Setting | Meaning |
|---|---|
| 0.0 | fully independent (the control) |
| 0.25 | weakly related |
| 0.50 | half the rows follow the relationship |
| 0.75 | strongly related |
| 1.00 | `b` is completely determined by `a` |

**Why it matters.** Databases estimate `WHERE a = x AND b = y` by multiplying
the two probabilities, which assumes independence. When the columns are
correlated that assumption is wrong, and this knob controls exactly how wrong.

### Knob 3: physical clustering (`physical_corr`)

Are rows stored on disk in the same order as their values?

Real example: a log table where rows are appended by timestamp, so the physical
order matches the time order. Implemented by sorting the column, then shuffling
a controlled fraction of positions.

| Setting | Meaning | Verified |
|---|---|---|
| 0.0 | rows in random order | realised 0.005 |
| 0.5 | half sorted | realised 0.497 |
| 0.95 | nearly sorted | realised 0.949 |
| 1.0 | perfectly sorted | realised 1.000 |

**Why it matters, and why `phys` datasets get special attention.** A BRIN index
does not record where each row is. For each block of the table it records only
the smallest and largest value inside it. So if rows are well sorted, each block
covers a narrow range and BRIN can skip nearly all of them. If rows are
shuffled, every block contains a wide mix of values, nothing can be skipped, and
the whole table is read anyway plus the wasted checking.

That is why BRIN is dangerous. It looks nearly free, and whether it helps or
destroys performance depends on a property most people never check.

## The 11 datasets

Only one knob moves per dataset, everything else stays at baseline.

| Dataset | skew | dep | phys | Purpose |
|---|---|---|---|---|
| `baseline` | 0.0 | 0.0 | 0.0 | control |
| `skew05` | **0.5** | 0.0 | 0.0 | mild skew |
| `skew10` | **1.0** | 0.0 | 0.0 | strong skew |
| `skew15` | **1.5** | 0.0 | 0.0 | extreme skew |
| `dep025` | 0.0 | **0.25** | 0.0 | weak correlation |
| `dep05` | 0.0 | **0.50** | 0.0 | medium correlation |
| `dep075` | 0.0 | **0.75** | 0.0 | strong correlation |
| `dep10` | 0.0 | **1.00** | 0.0 | total correlation |
| `phys05` | 0.0 | 0.0 | **0.5** | half-sorted, BRIN's hard case |
| `phys095` | 0.0 | 0.0 | **0.95** | nearly sorted |
| `phys10` | 0.0 | 0.0 | **1.0** | perfectly sorted, BRIN's best case |

The intermediate table sizes run only the three `phys` datasets, because the
boundary question is about BRIN and BRIN depends only on physical clustering.
Running `skew` and `dep` there would add hours and answer a question we did not
ask. The 1M and 10M runs use all eleven.

## Table structure

| Column | Type | Role |
|---|---|---|
| `id` | bigint | row identifier |
| `k_uniform` | int | uniform control column, 10,000 distinct values |
| `k_skew` | int | the skewed column, 10,000 distinct values |
| `a` | int | first correlated column, 100 distinct values |
| `b` | int | second correlated column, 100 distinct values |
| `ts` | bigint | the physically clustered column |
| `payload` | text | 48 characters, so rows are realistically wide |

**Why 100 distinct values for `a` and `b`.** At one million rows this makes an
independent `a = x AND b = y` return about 100 rows, which is exactly what the
independence assumption predicts, giving a clean control. Under full correlation
the same query returns all 10,000 rows sharing `a = x` while the estimate stays
at 100. That is an estimation error of 100x at 1% selectivity, which is the
exact region where index and full-scan costs cross. Larger domains push the
control down to about one row, which measures nothing.

## The 10 index configurations

Each candidate is measured **alone**, because PostgreSQL gives no way to hide
one index from the planner while leaving others visible.

| Configuration | Contains | Serves |
|---|---|---|
| `none` | no indexes | full scan baseline |
| `btree_skew` | B-tree on `k_skew` | equality, range |
| `hash_skew` | hash on `k_skew` | equality only |
| `brin_skew` | BRIN on `k_skew` | equality, range |
| `btree_ts` | B-tree on `ts` | timestamp range |
| `brin_ts` | BRIN on `ts` | timestamp range |
| `btree_a_b_separate` | two B-trees, on `a` and `b` | conjunctions |
| `btree_ab_composite` | one B-tree on `(a, b)` | conjunctions |
| **`all`** | everything above | **planner chooses freely** |
| **`all_no_brin`** | everything except BRIN | **planner chooses freely** |

The last two are the ones under study. Everything else exists to establish what
the best achievable plan was, so we can measure the gap.

MariaDB runs six of these. InnoDB has neither BRIN nor hash indexes, so those
configurations have no counterpart and are omitted rather than faked.

## The four query families

| Family | Shape | Tests |
|---|---|---|
| `eq` | `WHERE k_skew = ?` | equality, all index types compete |
| `range` | `WHERE k_skew BETWEEN ? AND ?` | hash cannot serve this, tests fallback |
| `ts_range` | `WHERE ts BETWEEN ? AND ?` | BRIN's home ground |
| `conj` | `WHERE a = ? AND b = ?` | correlated predicates, in dependent and independent variants |

Target selectivities swept: 0.001%, 0.01%, 0.1%, 1%, 5%, 10%, 20%, 50%.
Predicate constants are chosen by scanning the generated data so each target is
hit as closely as the data allows, and the **achieved** value is always reported
rather than the target.

## Measurement protocol

| Setting | Value | Reason |
|---|---|---|
| Executions per query | 7 | first discarded as cache warming, median of remaining 6 |
| Instrument (PostgreSQL) | `EXPLAIN (ANALYZE, BUFFERS, TIMING OFF, FORMAT JSON)` | runs server side, discards results, so client transfer is never measured |
| Instrument (MariaDB) | `ANALYZE FORMAT=JSON` | the equivalent |
| Parallelism | disabled | otherwise it confounds the access-path decision |
| JIT compilation | disabled | it triggers on cost thresholds, so it would fire inconsistently between configurations |
| Cache state | warm | evicting the OS cache is not portable across platforms, so this is declared rather than approximated |

**Why `TIMING OFF` matters.** With per-row timing enabled, the database reads
the clock twice for every row produced. A full scan producing a million rows
pays that a million times; an index scan producing ten thousand pays it ten
thousand times. That overhead depends on the plan, which would bias the
comparison towards the plans we are trying to evaluate. Turning it off removes
the bias.

## Environment

| | PostgreSQL | MariaDB |
|---|---|---|
| Version | 17.1 | 10.4.28 |
| Buffer pool | `shared_buffers` = 128 MB | `innodb_buffer_pool_size` = 128 MB |
| Working memory | `work_mem` = 4 MB | default |
| Port | 5432 | 3307 (separate instance, XAMPP untouched) |
| Storage | dedicated tablespace on a drive separate from the OS | dedicated data directory, same drive |

Both buffer pools are set to 128 MB deliberately. That number is what the
boundary finding turns on, so the two systems must agree on it or the comparison
means nothing.

Hardware: Intel Core i5-12400, 6 cores, 23.8 GB RAM, SSD, Windows 11.

**One scope limit worth stating.** With 23.8 GB of RAM, the operating system
keeps the data cached even at ten million rows. So the large sizes test leaving
the **database's own buffer pool**, not leaving memory entirely. Those are
different things and this hardware cannot separate them. The paper says so.

## Reproducing any of this

```bash
python tests/test_generator.py          # 19 validity checks
python run_experiment.py --rows 1000000 # PostgreSQL
python run_experiment_mysql.py --rows 1000000
python analyze.py                       # regenerates every figure
```

All data is generated from a fixed seed, so the corpus is identical on any
machine and nothing needs to be downloaded.


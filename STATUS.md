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


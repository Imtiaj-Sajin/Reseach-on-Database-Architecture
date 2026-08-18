# Research Status

**Project.** Do common PostgreSQL index tuning practices actually help?
**Student.** Md. Imtiaj Alam Sajin
**Supervisor.** Dr. Ashraf Uddin
**Updated.** 18 August 2026, 16:12

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

### PostgreSQL 17.1 — complete

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

### MariaDB 10.4.28 — in progress

| Table size | Datasets | Measurements | Status |
|---|---|---|---|
| 1,000,000 | 11 | 1,232 | done |
| 1,250,000 | 3 | 336 | done |
| 1,500,000 | 3 | 336 | done |
| 2,000,000 | 3 | 336 | done |
| 3,000,000 | 3 | 336 | done |
| 5,000,000 | 3 | 204 | **running now** |
| 10,000,000 | 11 | — | queued, ~4 hours |

**MariaDB so far: 2,780 measurements.**

The second database exists to separate findings that are general properties of
databases from findings that are quirks of one product. Note it is measured at
fewer sizes than PostgreSQL at present; closing that gap is exactly what the
current run is doing.

---

## Findings so far

### 1. PostgreSQL is good at this

It selects the best available plan for **97.7%** of queries. So the failures
below are specific problems, not a generally poor optimiser.

### 2. The textbook explanation does not apply

The accepted cause of bad query plans is the database misjudging how many rows
will come back. We found the opposite: **98.2%** of the bad choices had
**accurate** row estimates. The database knew the right numbers and chose badly
anyway.

### 3. Three recommended practices make things worse

| Practice | What it achieves | What it costs |
|---|---|---|
| Create extended statistics for correlated columns | Row estimate improves **108x** | Query becomes **2.7x slower** |
| Lower `random_page_cost` on SSD | (the stated goal) | Workload becomes **2.3x slower**; the default is within 6% of optimal |
| Add a BRIN index, since it is small and cheap | Index really is tiny | Queries up to **194x slower** |

### 4. We identified the exact cause of the third

Reading PostgreSQL's source code: when two indexes can answer the same query,
it keeps only the "cheaper" one, but judges cheapness by **the cost of reading
the index alone**, ignoring the work the query then does. A BRIN index is tiny
by design, so it wins that comparison essentially always, including when it is
the badly wrong choice.

### 5. The problem has a measurable boundary, and it is counterintuitive

The BRIN problem occurs only while the table fits in the buffer pool. But the
**worst** cases are not at the smallest size; they occur at two to three times
the memory limit, where individual queries degraded by up to 40x.

Had we tested only the smallest and largest sizes, we would have concluded the
problem simply disappears as tables grow. It does not. This is why the
intermediate sizes in the matrix above exist.

### 6. The second database separates general from specific

| Finding | Verdict |
|---|---|
| Correlated predicates handled badly | **General.** Occurs in MariaDB too, so it is architectural rather than a PostgreSQL bug |
| Near-optimal plan selection | **PostgreSQL-specific.** MariaDB chooses wrongly 66% of the time against PostgreSQL's 2.3% |

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

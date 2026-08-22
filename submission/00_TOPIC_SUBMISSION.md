# Topic Submission

Paste the block below into the MS Teams inbox. The Project and Report Guide asks
for exactly four items, in this order.

---

**1. Group No:** _(fill in if a group number has been assigned)_

**2. Full names and Student IDs of all group members:**

- Imtiaj Sajin, _(student ID)_

**3. Selected project type:**

Type 4, Experimental / Benchmarking Project

**4. Selected or proposed topic title:**

Measuring the Impact of Index Types and Tuning Settings on Query Execution Plans in PostgreSQL and MariaDB

---

## Short description, if the instructor asks for one

An empirical study of index access-path selection in PostgreSQL 17 under
controlled data skew, predicate correlation, and physical clustering.

The study measures how often the query planner selects a slower access path
than one available to it, and then evaluates three widely recommended tuning
practices to see whether they help. All three are measured to backfire under
identifiable conditions: extended statistics, lowering `random_page_cost` on
SSD storage, and adding a BRIN index alongside a B-tree on the same column.

The work sits alongside example topic 2 under Type 4, "Measuring the Impact of
Index Types (B-Tree, Hash, GIN, BRIN) on Query Execution Plans in PostgreSQL",
and covers the same ground with a sharper research question.

11,026 measurements across two table scales, 11 dataset configurations, 10
index arms, and 7 cost-model settings. Fully reproducible, with a public
repository and a one-command re-run.

---

## Note on the title

The guide allows either selecting a listed example topic or proposing an
original one. This title is a narrowing of example topic 2 rather than a new
topic, so it should not require separate approval. If the instructor prefers
the listed wording exactly, submit:

> Measuring the Impact of Index Types (B-Tree, Hash, GIN, BRIN) on Query
> Execution Plans in PostgreSQL

and keep the research question unchanged. The work satisfies both readings.


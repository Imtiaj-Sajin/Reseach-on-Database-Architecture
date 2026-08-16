# Appendix A. Query Execution Plans and Profiling Output

Every plan below is unmodified `EXPLAIN` output captured during the study. The
script that produced each one is named, so any of these can be regenerated.

---

## A.1 The BRIN effect, with statistics held constant

**Source:** `research/diagnose_brin.py`, log at `research/results/raw/diagnose_brin.log`

Dataset: 1,000,000 rows, `physical_corr = 0.5`. Both a B-tree and a BRIN index
exist on `ts`. `ANALYZE` was run once and never again, so both plans below were
produced from byte-identical statistics.

Generator realised rank correlation: 0.4984. PostgreSQL's own `pg_stats`
estimate: 0.49863365. The two agree, confirming the generator produced the
intended physical clustering.

Query: `SELECT * FROM t_brindiag WHERE ts BETWEEN 1655753994 AND 1657336369`
True rows: 10,000

### Plan the planner chose, both indexes available

```
Bitmap Heap Scan on t_brindiag  (cost=14.56..14858.18 rows=9705 width=81) (actual rows=10000 loops=1)
  Recheck Cond: ((ts >= 1655753994) AND (ts <= 1657336369))
  Rows Removed by Index Recheck: 990000
  Heap Blocks: lossy=14304
  Buffers: shared hit=2696 read=11616
  I/O Timings: shared read=60.146
  ->  Bitmap Index Scan on ix_brindiag_brin_ts  (cost=0.00..12.13 rows=35975 width=0) (actual rows=143040 loops=1)
        Index Cond: ((ts >= 1655753994) AND (ts <= 1657336369))
        Buffers: shared hit=8
Planning Time: 0.563 ms
Execution Time: 132.691 ms
```

Note `Heap Blocks: lossy=14304` and `Rows Removed by Index Recheck: 990000`.
The BRIN index returns block ranges rather than rows, so the plan reads
essentially the entire table and discards 99 percent of what it reads.

### Same query, same statistics, BRIN index removed

```
Bitmap Heap Scan on t_brindiag  (cost=215.87..14045.79 rows=10092 width=81) (actual rows=10000 loops=1)
  Recheck Cond: ((ts >= 1655753994) AND (ts <= 1657336369))
  Heap Blocks: exact=4293
  Buffers: shared hit=4293 read=30
  ->  Bitmap Index Scan on ix_brindiag_btree_ts  (cost=0.00..213.35 rows=10092 width=0) (actual rows=10000 loops=1)
        Index Cond: ((ts >= 1655753994) AND (ts <= 1657336369))
Planning Time: 0.129 ms
Execution Time: 3.416 ms
```

`Heap Blocks: exact=4293` against `lossy=14304`, and **3.416 ms against 132.691
ms**. The planner costed the chosen plan at 14,858 and the unchosen one at
14,046, so the cheaper plan by its own model is the one it did not use.

---

## A.2 Path suppression across the correlation range

**Source:** `research/diagnose_suppression.py`

Bitmap plans forced (`enable_seqscan`, `enable_indexscan`, `enable_indexonlyscan`
all off) so the planner must choose between the two bitmap paths. Statistics
held constant within each row.

| corr | query | both indexes | cost | ms | B-tree only | cost | ms |
|---|---|---|---|---|---|---|---|
| 0.0 | 0.1% range | BRIN | 29,320 | 65.95 | B-tree | 3,166 | **0.34** |
| 0.0 | 1% range | BRIN | 29,322 | 66.73 | B-tree | 13,857 | 5.14 |
| 0.0 | 5% range | BRIN | 29,332 | 69.50 | B-tree | 16,111 | 12.87 |
| 0.5 | 0.1% range | BRIN | 15,338 | 66.33 | B-tree | 3,174 | **0.21** |
| 0.5 | 1% range | BRIN | 14,852 | 63.90 | B-tree | 13,848 | 2.76 |
| 0.95 | 0.1% range | BRIN | 13,511 | 62.07 | B-tree | 3,294 | **0.09** |
| 0.95 | 1% range | BRIN | 15,365 | 66.45 | B-tree | 14,201 | 0.76 |
| 1.0 | 0.1% range | BRIN | 13,286 | 0.55 | B-tree | 2,903 | 0.07 |

BRIN was selected in **16 of 16 cases**. In 10 of those the B-tree bitmap path
was cheaper by the planner's own cost model. The largest observed penalty is
the 0.95 correlation, 0.1 percent range case: 62.07 ms against 0.09 ms, a
factor of **690**.

---

## A.3 Extended statistics change the plan for the worse

**Source:** `research/diagnose_extstats.py`

Dataset: 1,000,000 rows, `dep_strength = 1.0`. Query:
`SELECT * FROM t_ext WHERE a = 57 AND b = 84`. True rows: 10,216.
Identical table, data, indexes and session. Only the extended statistics object
is added.

### Without extended statistics

```
Bitmap Heap Scan on t_ext  (cost=5.38..356.28 rows=93 width=81) (actual rows=10216 loops=1)
  Recheck Cond: ((a = 57) AND (b = 84))
  Heap Blocks: exact=7317
  Buffers: shared hit=7329
  ->  Bitmap Index Scan on ix_ext_ab  (cost=0.00..5.35 rows=93 width=0) (actual rows=10216 loops=1)
        Index Cond: ((a = 57) AND (b = 84))
Execution Time: 4.427 ms
>> 15 runs: median=4.059 ms  mean=4.152  min=3.534  max=5.263  stdev=0.473
```

### With extended statistics

```
Bitmap Heap Scan on t_ext  (cost=219.84..559.77 rows=9467 width=81) (actual rows=10216 loops=1)
  Recheck Cond: ((b = 84) AND (a = 57))
  Heap Blocks: exact=7317
  Buffers: shared hit=7341
  ->  BitmapAnd  (cost=219.84..219.84 rows=90 width=0) (actual rows=0 loops=1)
        ->  Bitmap Index Scan on ix_ext_b  (cost=0.00..107.43 rows=9467 width=0) (actual rows=10216 loops=1)
              Index Cond: (b = 84)
        ->  Bitmap Index Scan on ix_ext_a  (cost=0.00..107.43 rows=9467 width=0) (actual rows=10216 loops=1)
              Index Cond: (a = 57)
Execution Time: 5.958 ms
>> 15 runs: median=5.171 ms  mean=5.207  min=4.864  max=5.660  stdev=0.261
```

The estimate improves from 93 to 9,467 against a true 10,216, and the query
slows from a median of 4.059 ms to 5.171 ms. The distributions do not overlap
(4.059 ± 0.473 against 5.171 ± 0.261), so this is not measurement noise.

Both plans fetch the identical 7,317 heap blocks. The difference is that the
first uses the composite `(a, b)` index in one scan while the second performs
two index scans plus a bitmap intersection.

### The inconsistency that causes it

Note the two row estimates in the second plan:

```
Bitmap Heap Scan  ... rows=9467     <- extended statistics applied
  -> BitmapAnd    ... rows=90       <- independence assumption, NOT corrected
```

9,467 × 9,467 / 1,000,000 ≈ 90. The `BitmapAnd` node derives its own estimate
by multiplying the two per-column selectivities, exactly the independence
assumption that extended statistics exist to replace.

---

## A.4 Confirming the planner acts rationally on its own model

**Source:** `research/diagnose_composite.py`

Same query, `dep_strength = 1.0`, extended statistics present, varying which
indexes exist:

| indexes available | plan chosen | total cost | median ms |
|---|---|---|---|
| all three | `BitmapAnd` of `a` and `b` | **635.63** | 4.794 |
| composite only | composite `(a, b)` | **13,558.73** | **4.030** |
| separate only | `BitmapAnd` | 643.90 | 5.357 |

With correct estimates the planner costs the composite path at 13,558 and the
`BitmapAnd` at 635, a 21-fold gap, while the composite path is in fact the
faster of the two. The planner is choosing rationally on a cost model that
misprices the composite index once the estimate is corrected.

For contrast, without extended statistics the same composite path is costed at
392.80 and runs in 4.155 ms. The *correct* estimate makes the plan look 34
times more expensive while running at the same speed.

---

## A.5 Cost model sensitivity

**Source:** `research/diagnose.py`

Same query, same data, identical row estimate (10,662) in every row. Only
`random_page_cost` changes:

| `random_page_cost` | plan chosen | total cost | median ms |
|---|---|---|---|
| 4.0 (default) | sequential scan | 29,304.0 | 49.14 |
| 2.0 | B-tree index scan | 15,769.6 | **2.08** |
| 1.5 | B-tree index scan | 11,880.6 | 2.33 |
| 1.1 | B-tree index scan | 8,769.4 | 2.49 |
| 1.0 | B-tree index scan | 7,991.6 | 2.25 |

This single query motivated the full sweep in Section 3.4 of the report. Taken
alone it suggests the default is catastrophic; measured across the entire query
set it is within 6 percent of optimal. The contrast is retained here
deliberately, as a caution against tuning from individual queries.

---

## A.6 Buffer pool residency

Median buffer counters for range queries on the physically clustered column,
full index set:

| scale | `shared_hit` | `shared_read` |
|---|---|---|
| 1,000,000 rows | 13,194 | **0** |
| 10,000,000 rows | 11,314 | **89,081** |

`shared_read` counts blocks fetched from outside the buffer pool. Zero at one
million rows confirms full residency; 89,081 at ten million confirms the
working set has left the pool. This is the independent evidence that the two
scales are genuinely different regimes, which the scale comparison in Section
3.5 depends on.

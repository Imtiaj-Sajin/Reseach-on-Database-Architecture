# Appendix B. Reproducibility

Everything in this study can be regenerated from scratch. No measurement is
transcribed by hand, and no dataset is shipped: all data is generated from a
fixed seed, so the corpus is byte-for-byte identical on any machine.

Repository: <https://github.com/Imtiaj-Sajin/Reseach-on-Database-Architecture>

---

## B.1 Requirements

| | |
|---|---|
| PostgreSQL | 17.x |
| Python | 3.11+ |
| Disk | ~5 GB free for the 10M row scale, on the tablespace device |
| RAM | 8 GB minimum |

Python packages, in `research/requirements.txt`:

```
psycopg2-binary>=2.9   numpy>=1.24   pandas>=2.0
matplotlib>=3.7        scipy>=1.10   notebook>=7.0
```

---

## B.2 Setup

```bash
pip install -r research/requirements.txt
```

Create a tablespace with room for the generated tables. This matters: the 10M
row scale writes over a gigabyte per dataset, and the default PostgreSQL data
directory is often on a system drive without that much free space.

```sql
CREATE TABLESPACE ts_dtms LOCATION '/path/with/space';
```

On Windows the PostgreSQL service account needs write access to that directory:

```powershell
icacls "G:\pgdata_dtms" /grant "NETWORK SERVICE:(OI)(CI)F"
```

Connection settings are read from standard PostgreSQL environment variables:

```bash
export PGHOST=127.0.0.1 PGPORT=5432 PGUSER=postgres PGPASSWORD=... PGDATABASE=accesspath
```

The experiment database is created automatically on first run.

---

## B.3 Running

```bash
# 1. Validate the generator before trusting any measurement
python research/tests/test_generator.py
python research/tests/test_range_targeting.py

# 2. Smoke test the whole pipeline at small scale
python research/run_experiment.py --pilot

# 3. Main sweeps
python research/run_experiment.py --rows 1000000        # ~25 min
python research/run_experiment.py --rows 10000000       # ~3 h
python research/run_rpc_sweep.py                        # ~45 min

# 4. Figures, tables, summary statistics
python research/analyze.py

# 5. Executable analysis notebook
jupyter nbconvert --to notebook --execute --inplace research/notebooks/analysis.ipynb
```

Diagnostics for the individual mechanisms in Appendix A:

```bash
python research/diagnose.py              # cost model sensitivity
python research/diagnose_brin.py         # BRIN plan comparison
python research/diagnose_tie.py          # BRIN choice with statistics held fixed
python research/diagnose_suppression.py  # path suppression across correlation
python research/diagnose_extstats.py     # extended statistics plan change
python research/diagnose_composite.py    # composite index costing
```

---

## B.4 Interruption and resume

Runs are resumable at the granularity of a single measurement. Every result is
appended to `results/raw/measurements_*.jsonl` the moment it is taken, and the
resume key is `(dataset, index arm, query, extended-statistics state)`. An
interrupted run restarts from the exact query it stopped on.

This was exercised twice during the study, once by a stopped process and once by
a power failure. Both times the loss was a single in-flight query.

Completed datasets are recorded in `measurements_*_completed.json` so a resume
skips them without regenerating and reloading the table, which at ten million
rows costs about four minutes each. If that marker file is missing, for example
after a run killed before it could be written, reconstruct it:

```bash
python research/mark_complete.py
```

Each table is dropped once its measurements are recorded, so peak disk use is
one table rather than all eleven.

---

## B.5 Layout

```
research/
  src/config.py        parameter sweep, session settings, index arms
  src/datagen.py       skew, dependency and clustering generation
  src/queries.py       query construction with exact ground-truth cardinality
  src/db.py            schema, bulk load, index arms, timing instrument
  src/metrics.py       q-error and regret
  run_experiment.py    main driver, resumable
  run_rpc_sweep.py     random_page_cost sweep
  analyze.py           figures and tables
  invalidate.py        removes measurements superseded by a methodology fix
  mark_complete.py     reconstructs completion markers
  diagnose*.py         per-mechanism diagnostics
  tests/               generator and query-builder validation
  notebooks/           executable analysis
  results/raw/         measurements, JSONL, one object per execution
  results/figures/     generated figures
  results/tables/      generated tables
```

---

## B.6 Environment captured per run

`results/raw/run_metadata_*.json` records, automatically, at run time:

- PostgreSQL version string
- `shared_buffers`, `work_mem`, `effective_cache_size`
- `random_page_cost`, `seq_page_cost`, `cpu_tuple_cost`, `cpu_index_tuple_cost`, `cpu_operator_cost`
- `max_parallel_workers_per_gather`, `jit`, `block_size`, `default_statistics_target`
- Python version, platform, processor
- Measurement protocol: repeat count, warm-up count, instrument, statistic, cache state

The environment used for the reported results:

| | |
|---|---|
| DBMS | PostgreSQL 17.1, x86_64-windows, MSVC 19.41 |
| CPU | Intel Core i5-12400, 6 cores / 12 threads |
| RAM | 23.8 GB |
| Storage | SSD, dedicated tablespace on a device separate from the OS |
| `shared_buffers` | 128 MB |
| `work_mem` | 4 MB |
| `maintenance_work_mem` | 1 GB (raised from 64 MB; affects index build time only, never query execution) |

---

## B.7 Record schema

One JSON object per measured execution:

| Field | Meaning |
|---|---|
| `dataset`, `dataset_spec` | Configuration and its factor levels |
| `arm`, `arm_index_bytes`, `arm_build_seconds` | Index configuration, size, build time |
| `ext_stats` | Whether extended statistics were present |
| `qid`, `family`, `sql`, `params` | Query identity and text |
| `true_rows`, `n_rows`, `target_selectivity` | Ground truth, computed from the generated arrays |
| `est_rows`, `actual_rows` | Planner estimate and observed rows |
| `access_path`, `indexes_used`, `node_types`, `total_cost` | Plan shape and cost |
| `shared_hit`, `shared_read` | Buffer statistics |
| `exec_ms_median`, `_mean`, `_min`, `_max`, `_stdev`, `runs` | Timing, all repeats retained |
| `planning_time_ms` | Planning time |

Retaining every individual run, not just the median, means the variance claims
in this report can be checked independently.

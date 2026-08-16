"""
Experiment configuration for the access-path selection study.

Every tunable lives here so that a reviewer can see the full parameter space
in one file and reproduce the sweep exactly.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from pathlib import Path

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = ROOT / "results"
RAW_DIR = RESULTS_DIR / "raw"
FIGURES_DIR = RESULTS_DIR / "figures"
TABLES_DIR = RESULTS_DIR / "tables"

for _d in (RESULTS_DIR, RAW_DIR, FIGURES_DIR, TABLES_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------------------------------
# Database connection
# --------------------------------------------------------------------------
DB = {
    "host": os.environ.get("PGHOST", "127.0.0.1"),
    "port": int(os.environ.get("PGPORT", 5432)),
    "user": os.environ.get("PGUSER", "postgres"),
    "password": os.environ.get("PGPASSWORD", ""),
    "dbname": os.environ.get("PGDATABASE", "accesspath"),
}

# Database we connect to in order to CREATE the experiment database.
MAINTENANCE_DB = "postgres"

# --------------------------------------------------------------------------
# Session settings applied to every measurement connection.
#
# Rationale (this goes into the paper's methodology section):
#   max_parallel_workers_per_gather = 0
#       Parallelism changes the cost of a sequential scan dramatically and
#       would confound the access-path comparison. We isolate the serial
#       access-path decision first; a parallel arm can be added separately.
#   jit = off
#       JIT compilation adds tens of milliseconds of variance to short
#       queries and is triggered by cost thresholds, which means it would
#       fire inconsistently across arms.
#   track_io_timing = on
#       Lets us attribute time to I/O vs CPU in the BUFFERS output.
# --------------------------------------------------------------------------
SESSION_SETTINGS = {
    "max_parallel_workers_per_gather": "0",
    "jit": "off",
    "track_io_timing": "on",
    "statement_timeout": "600000",  # 10 min safety net
}


# --------------------------------------------------------------------------
# Dataset definitions
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class DatasetSpec:
    """One generated table.

    zipf_s              Zipfian exponent for the skewed column. 0.0 gives a
                        uniform distribution; higher values concentrate mass
                        on a few values.
    dep_strength        Probability that column b is a deterministic function
                        of column a. 0.0 is independence, 1.0 is a hard
                        functional dependency. This is the axis PostgreSQL's
                        extended statistics 'dependencies' is meant to model.
    physical_corr       Correlation between the ts column and physical row
                        order, which is what BRIN depends on.
    n_rows              Table cardinality.
    domain_a/b          Number of distinct values in the correlated pair.
    domain_skew         Number of distinct values in the skewed column.
    """

    name: str
    n_rows: int
    zipf_s: float
    dep_strength: float
    physical_corr: float
    # Domain sizing is load bearing, so the reasoning is recorded here.
    #
    # With n = 1e6 and domain_a = domain_b = 100, each `a` value covers about
    # 10,000 rows (1 percent). A conjunction a = va AND b = vb then returns:
    #
    #   independent (dep_strength = 0): about 100 rows, and PostgreSQL's
    #       independence assumption estimates 1e6 * (1/100) * (1/100) = 100.
    #       Estimate and truth agree, which is the control.
    #
    #   dependent (dep_strength = 1): all 10,000 rows with a = va also satisfy
    #       b = g(va), so the truth is 10,000 while the estimate stays at 100.
    #       That is a q-error of 100, and 1 percent selectivity sits exactly in
    #       the region where index and sequential access compete.
    #
    # Larger domains (1000 x 1000) push the independent control down to about
    # one row, which is a degenerate query that measures nothing.
    domain_skew: int = 10_000
    domain_a: int = 100
    domain_b: int = 100
    seed: int = 42

    @property
    def table(self) -> str:
        return f"t_{self.name}"

    def to_dict(self) -> dict:
        return asdict(self)


def build_dataset_grid(n_rows: int = 1_000_000) -> list[DatasetSpec]:
    """The dataset sweep.

    We vary one factor at a time around a uniform/independent baseline so that
    each effect is attributable. A full cross product would be 4 x 4 x 3 = 48
    tables, which is unnecessary for the questions we ask.
    """
    specs: list[DatasetSpec] = []

    # Baseline: uniform, independent, uncorrelated physical order.
    specs.append(
        DatasetSpec(
            name="baseline",
            n_rows=n_rows,
            zipf_s=0.0,
            dep_strength=0.0,
            physical_corr=0.0,
        )
    )

    # Skew arm: vary the Zipfian exponent.
    for s in (0.5, 1.0, 1.5):
        specs.append(
            DatasetSpec(
                name=f"skew{str(s).replace('.', '')}",
                n_rows=n_rows,
                zipf_s=s,
                dep_strength=0.0,
                physical_corr=0.0,
            )
        )

    # Correlation arm: vary functional dependency strength between a and b.
    for d in (0.25, 0.5, 0.75, 1.0):
        specs.append(
            DatasetSpec(
                name=f"dep{str(d).replace('.', '')}",
                n_rows=n_rows,
                zipf_s=0.0,
                dep_strength=d,
                physical_corr=0.0,
            )
        )

    # Physical correlation arm: the axis BRIN lives or dies on.
    for p in (0.5, 0.95, 1.0):
        specs.append(
            DatasetSpec(
                name=f"phys{str(p).replace('.', '')}",
                n_rows=n_rows,
                zipf_s=0.0,
                dep_strength=0.0,
                physical_corr=p,
            )
        )

    return specs


# --------------------------------------------------------------------------
# Index arms
#
# Each arm is a set of indexes created in isolation. Measuring one index type
# at a time is the only way to observe what each access path would have cost,
# because PostgreSQL has no way to make a specific index invisible to the
# planner while leaving others visible.
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class IndexArm:
    name: str
    ddl: tuple[str, ...]
    # Which query families this arm can actually serve.
    supports: tuple[str, ...]


INDEX_ARMS: list[IndexArm] = [
    IndexArm(name="none", ddl=(), supports=("eq", "range", "conj", "ts_range")),
    IndexArm(
        name="btree_skew",
        ddl=("CREATE INDEX {ix} ON {t} USING btree (k_skew)",),
        supports=("eq", "range"),
    ),
    IndexArm(
        name="hash_skew",
        ddl=("CREATE INDEX {ix} ON {t} USING hash (k_skew)",),
        supports=("eq",),
    ),
    IndexArm(
        name="brin_skew",
        ddl=("CREATE INDEX {ix} ON {t} USING brin (k_skew)",),
        supports=("eq", "range"),
    ),
    IndexArm(
        name="btree_a_b_separate",
        ddl=(
            "CREATE INDEX {ix}_a ON {t} USING btree (a)",
            "CREATE INDEX {ix}_b ON {t} USING btree (b)",
        ),
        supports=("conj",),
    ),
    IndexArm(
        name="btree_ab_composite",
        ddl=("CREATE INDEX {ix} ON {t} USING btree (a, b)",),
        supports=("conj",),
    ),
    IndexArm(
        name="btree_ts",
        ddl=("CREATE INDEX {ix} ON {t} USING btree (ts)",),
        supports=("ts_range",),
    ),
    IndexArm(
        name="brin_ts",
        ddl=("CREATE INDEX {ix} ON {t} USING brin (ts)",),
        supports=("ts_range",),
    ),
]

# The arm that reproduces a realistic deployment: every candidate index exists
# and the planner is free to choose. This is what we compare against the best
# single-index arm to compute regret.
ALL_INDEXES_ARM = IndexArm(
    name="all",
    ddl=(
        "CREATE INDEX {ix}_btree_skew ON {t} USING btree (k_skew)",
        "CREATE INDEX {ix}_hash_skew ON {t} USING hash (k_skew)",
        "CREATE INDEX {ix}_brin_skew ON {t} USING brin (k_skew)",
        "CREATE INDEX {ix}_a ON {t} USING btree (a)",
        "CREATE INDEX {ix}_b ON {t} USING btree (b)",
        "CREATE INDEX {ix}_ab ON {t} USING btree (a, b)",
        "CREATE INDEX {ix}_btree_ts ON {t} USING btree (ts)",
        "CREATE INDEX {ix}_brin_ts ON {t} USING brin (ts)",
    ),
    supports=("eq", "range", "conj", "ts_range"),
)

# --------------------------------------------------------------------------
# Query sweep
# --------------------------------------------------------------------------
# Target selectivities, expressed as the fraction of table rows the predicate
# should return. Chosen to straddle the region where the planner is known to
# switch between index and sequential access (roughly 1 to 10 percent).
TARGET_SELECTIVITIES = (
    0.00001,
    0.0001,
    0.001,
    0.01,
    0.05,
    0.10,
    0.20,
    0.50,
)

# Number of times each query is executed. The first run is discarded as a
# cache-warming run and the median of the rest is reported.
REPEATS = 6
WARMUP_RUNS = 1

# Extended statistics arm: run the conjunctive queries with and without
# CREATE STATISTICS to test whether it changes the planner's choice.
EXTENDED_STATS_DDL = (
    "CREATE STATISTICS {st} (ndistinct, dependencies, mcv) ON a, b FROM {t}"
)

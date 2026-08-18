# Journal Paper Draft

**Title.** When Index Tuning Advice Backfires: An Experimental Study of
Access-Path Selection in PostgreSQL

**Target venues.** Data and Knowledge Engineering (Elsevier, Q2) or Information
Systems (Elsevier, Q1/Q2). Both are subscription journals with **no article
processing charge**. Formatted with `elsarticle`, the Elsevier class both use.

**Status.** Complete first draft. Every number is from measured data. Not yet
submittable; see the gap list below.

---

## Building

Requires a TeX distribution with `elsarticle` and `bibtex`.

```bash
pdflatex paper.tex
bibtex paper
pdflatex paper.tex
pdflatex paper.tex
```

Current build: 25 pages, no undefined references or citations, no errors.

---

## Current state

| | |
|---|---|
| Body words | 4,343 |
| Abstract words | 287 |
| Figures | 5 |
| Tables | 10 |
| References | 33, all cited |
| Measurements behind it | 14,296 |

---

## What still has to happen before submission

Listed in order of how much each affects acceptance odds.

**1. Second DBMS.** The single most important gap. The paper currently
establishes that *PostgreSQL* behaves this way. A reviewer will immediately ask
whether these are PostgreSQL implementation choices or general properties of
cost-based access-path selection, and the paper cannot answer. Running the same
harness against MySQL or MariaDB would answer it. The measurement
infrastructure exists; the database layer needs rewriting because MySQL has no
BRIN, different `EXPLAIN` output, and different cost parameters. This is
acknowledged as the most significant limitation in Section 8.

**2. Expand to 8,000 to 10,000 words.** At 4,343 the draft is roughly half the
typical length for these venues. The expansion is not padding; specific
sections are genuinely thin:

- Related work needs deeper engagement, particularly with the learned cost
  model literature, which is the closest active area.
- Section 5.2 (misestimation does not explain misselection) is the paper's most
  surprising claim and currently gets the least space. It deserves a fuller
  treatment including the distribution of q-error among correctly selected
  queries as a contrast.
- The discussion should engage with why access-path selection differs from join
  ordering rather than only asserting that it does.

**3. Source-level confirmation of path elimination.** Section 6.5 infers from
cost arithmetic that the B-tree bitmap path is eliminated during path
generation rather than rejected on cost. Reading PostgreSQL's
`create_index_paths` and `choose_bitmap_and` would convert the paper's
strongest inference into a verified claim. This is the point a reviewer is most
likely to press.

**4. Abstract trim.** 287 words is long for Elsevier; 200 to 250 is more usual.

---

## What is already strong

- **A negative result that contradicts the dominant explanation.** Cardinality
  misestimation is the accepted cause of bad plans; we show it explains almost
  none of the misselection observed here (98.2 percent of misselected queries
  had accurate estimates).
- **Mechanisms, not just effects.** Each of the three findings is traced to
  something visible in plan output, including the `BitmapAnd` node retaining an
  uncorrected independence estimate.
- **Boundaries are measured, not assumed.** Section 6.6 shows the two-point
  scale comparison would have produced the wrong conclusion, and locates two
  separate transitions at two separate system boundaries. Few benchmark papers
  establish where their own findings stop holding.
- **A reproducible artifact** with seeded generation and resumable measurement.
- **A self-correction reported rather than hidden.** The single-query
  `random_page_cost` observation that turned out to be unrepresentative is
  retained in the paper as a documented instance of how anecdotal tuning
  evidence misleads.

---

## Honest assessment

As it stands: a solid short empirical paper. Q3 comfortably; Q2 possible but
not likely without item 1.

With the second DBMS and the expansion: Q2 is a realistic target. Not Q1 in the
database venues (VLDB, SIGMOD, TODS), and that was never the plan.

The review cycle at these journals runs three to nine months. Posting a
preprint to arXiv on submission is worth doing for the timestamp and
citability.

# Submission Package

**Course.** Database Technology, Management and Security
**Project type.** Type 4, Experimental / Benchmarking Project
**Title.** Do Common PostgreSQL Index Tuning Practices Actually Help?
**Instructor.** Dr. Ashraf Uddin, Assistant Professor, Department of Computer
Science, American International University-Bangladesh

---

## What to submit

| File | What it is |
|---|---|
| **[DB_Report.pdf](DB_Report.pdf)** | **The report. This is the main deliverable.** 27 pages, A4, built from the course LaTeX template. |
| [00_TOPIC_SUBMISSION.md](00_TOPIC_SUBMISSION.md) | Text to paste into the MS Teams inbox to reserve the topic. Send this first. |
| [db-report-overleaf.zip](db-report-overleaf.zip) | The LaTeX project, ready to upload to Overleaf if the instructor wants the source. |
| [data/](data/) | Result tables as CSV. |
| [report/](report/) | LaTeX source: `main.tex`, `references.bib`, `figures/`. |

Code and raw measurements:
<https://github.com/Imtiaj-Sajin/Reseach-on-Database-Architecture>

---

## Before you submit: two placeholders to fill

Both are in `report/main.tex` near the top, and in `00_TOPIC_SUBMISSION.md`:

1. `\GroupNumber` is currently `Group XX`
2. The student ID in the title-page table is currently `XX-XXXXX-X`

Edit those, then rebuild (see below) so the PDF matches.

---

## Rebuilding the PDF

MiKTeX or TeX Live, with `biber`. From `report/`:

```bash
pdflatex main.tex
biber main
pdflatex main.tex
pdflatex main.tex
```

Two `pdflatex` passes after `biber` are needed so the table of contents, list of
figures, list of tables, and cross-references all resolve.

On Overleaf, upload `db-report-overleaf.zip` and set the compiler to pdfLaTeX;
Overleaf runs biber automatically.

---

## How the report meets the requirements

**Deliverables** required for Type 4:

| Required | Where |
|---|---|
| Written benchmarking report, 3,000 to 5,000 words | `DB_Report.pdf`, main body |
| Reproducible experimental setup, scripts and configuration | Appendix B, plus the repository |
| Raw and processed data tables with visualisations | Sections 4, `data/`, 7 figures |
| Query execution plans or profiling output | Appendix A, unmodified `EXPLAIN` output |
| Performance analysis, root cause discussion, limitations | Sections 4.7, 5.1, 5.4 |

**Rubric criteria** and where each is addressed:

| Criterion | Marks | Where |
|---|---|---|
| Experimental Methodology | 5 | Section 3, especially 3.5 measurement protocol |
| Data Collection and Measurement Rigor | 4 | Section 3.5, 3.6 validity checks; all repeats retained |
| Result Visualization and Reporting | 4 | Section 4, figures 1 to 6, tables 3 to 9 |
| Analysis and Root Cause Explanation | 5 | Sections 4.3 to 4.7, mechanisms traced in plan output |
| Conclusions and Limitations | 2 | Sections 5.4 and 6 |

Template requirements from `README.txt` that are satisfied: IEEE referencing via
biblatex, every figure and table referenced and interpreted in the text,
variance and trial counts reported, and long artefacts placed in appendices.

---

## Study at a glance

| | |
|---|---|
| Measured executions | 11,136 |
| Table scales | 1,000,000 and 10,000,000 rows |
| Dataset configurations | 11 per scale |
| Index configurations | 10 |
| Cost-model levels swept | 7 |
| Automated validity checks | 19, all passing |
| Repeats per query | 7, first discarded, median of 6 reported |

**Headline findings.** PostgreSQL's access-path selection is near optimal with a
conventional index set (2.3 percent misselection). Three widely recommended
tuning practices were each measured to backfire: extended statistics improve
estimates up to 108x while slowing queries up to 2.7x; lowering
`random_page_cost` on SSD costs a factor of 2.3; and a co-located BRIN index
raises misselection to 73.3 percent, but only while the table is buffer-pool
resident.

---

## A note on word count

The main body is approximately 3,000 words by a strict count that excludes the
abstract, captions, tables, listings and appendices. Overleaf's own word count
includes captions and table text and will report a higher figure. Both sit
inside the required 3,000 to 5,000 range. If you want more margin, Section 2
(Background and Literature Review) is the natural place to expand.

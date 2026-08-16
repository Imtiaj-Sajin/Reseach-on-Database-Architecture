# Submission Package

Database Technology, Management and Security. Project Type 4, Experimental / Benchmarking Project.

**Title.** Do Common PostgreSQL Index Tuning Practices Actually Help?

**Group.** Imtiaj Sajin

---

## Files, in the order you need them

| # | File | Purpose | When |
|---|---|---|---|
| 00 | [00_TOPIC_SUBMISSION.md](00_TOPIC_SUBMISSION.md) | Text to paste into the MS Teams inbox to reserve the topic | Send first |
| 01 | [01_REPORT.md](01_REPORT.md) | The benchmarking report, 3,000 to 5,000 words | Main deliverable |
| 02 | [02_figures/](02_figures/) | All figures referenced by the report | With the report |
| 03 | [03_tables/](03_tables/) | Result tables, CSV | With the report |
| 04 | [04_APPENDIX_execution_plans.md](04_APPENDIX_execution_plans.md) | Query execution plans and profiling output | Appendix |
| 05 | [05_REPRODUCIBILITY.md](05_REPRODUCIBILITY.md) | Setup, commands, environment, how to re-run everything | Appendix |

---

## How this maps to the rubric

The Type 4 deliverables in the Project and Report Guide, and where each is satisfied:

| Required deliverable | Where |
|---|---|
| Written benchmarking report, 3,000 to 5,000 words | `01_REPORT.md` |
| Reproducible experimental setup, scripts and configuration files | `05_REPRODUCIBILITY.md`, and the `research/` directory |
| Raw and processed data tables with performance visualisations | `03_tables/`, `02_figures/` |
| Query execution plans or profiling outputs | `04_APPENDIX_execution_plans.md` |
| Performance analysis, root cause discussion, and limitations | `01_REPORT.md` sections 4, 5 and 6 |

The rubric's five scored criteria, and where each is addressed:

| Criterion | Marks | Where |
|---|---|---|
| Experimental Methodology | 5 | Report section 2, plus `05_REPRODUCIBILITY.md` |
| Data Collection and Measurement Rigor | 4 | Report section 2.4, validity checks in section 2.5 |
| Result Visualization and Reporting | 4 | Report section 3, `02_figures/` |
| Analysis and Root Cause Explanation | 5 | Report section 4 |
| Conclusions and Limitations | 2 | Report sections 5 and 6 |

---

## Scale of the study

| | |
|---|---|
| Total measurements | 11,026 |
| Scales | 1,000,000 and 10,000,000 rows |
| Datasets | 11 factor levels per scale |
| Index arms | 10 |
| Cost model levels swept | 7 |
| Automated validity checks | 19, all passing |

Code and data: <https://github.com/Imtiaj-Sajin/Reseach-on-Database-Architecture>

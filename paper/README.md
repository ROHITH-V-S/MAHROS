# MAHROS paper (IEEE journal style)

*Checkable Refusals in Decentralised Inter-Hospital Transfer: A Multi-Agent
Contract-Net Protocol with Ledger-Backed Argumentation, Evaluated on Real
Hospital Networks*

Compiled output: `main.pdf` — **25 pages**, verified with pdfLaTeX + BibTeX
(TeX Live 2026, IEEEtran 1.8b class, IEEEtran.bst 1.14).

## Layout

```text
paper/
├── main.tex                  preamble, title, abstract, keywords,
│                             open-access reviewer list, \input of sections
├── references.bib            54 entries, BibTeX (IEEEtran.bst)
├── sections/
│   ├── 01_introduction.tex   I    Introduction, RQ1–RQ6, contributions
│   ├── 02_related.tex        II   Background and related work
│   ├── 03_gap.tex            III  Research gap (Table I), formal problem
│   ├── 04_method.tex         IV   Methodology (Fig. 1–3, Algorithm 1, Eq. 5–16)
│   ├── 05_system.tex         V    Implementation (Fig. 4 screenshot)
│   ├── 06_setup.tex          VI   Experimental setup and statistics
│   ├── 07_results.tex        VII  Results (Fig. 5–11, Tables III–XIV)
│   ├── 08_discussion.tex     VIII Discussion
│   ├── 09_limitations.tex    IX   Limitations and threats to validity
│   ├── 10_conclusion.tex     X    Conclusion and future work
│   └── 11_appendix.tex       Appendices A–E
├── figures/
│   ├── console.png           live console screenshot (read-only capture of the
│   │                         running Docker deployment, 1400×900)
│   └── data/*.dat            pgfplots tables, generated — do not edit
└── scripts/
    └── make_figure_data.py   writes figures/data/*.dat from ../results/*.json
```

All diagrams (architecture, protocol, argumentation) are TikZ and all plots are
pgfplots, drawn at compile time from `figures/data/*.dat`. No plotted value is
typed by hand.

## Build

```bash
python paper/scripts/make_figure_data.py      # from the repo root; refreshes plot data

cd paper
pdflatex main.tex
bibtex main
pdflatex main.tex
pdflatex main.tex
```

Required packages (all in TeX Live / MiKTeX): `IEEEtran`, `amsmath`, `amssymb`,
`booktabs`, `multirow`, `array`, `tabularx`, `cite`, `graphicx`, `xcolor`,
`algorithms`, `algorithmicx`, `pgf`/`tikz`, `pgfplots`, `url`, `hyperref`.
On a minimal TeX Live: `tlmgr install ieeetran pgfplots multirow algorithms algorithmicx`.

No LaTeX installed? Docker works:

```bash
docker run --rm -v "$PWD:/w" -w /w texlive/texlive:latest-medium sh -c \
  "tlmgr update --self && tlmgr install ieeetran pgfplots multirow algorithms algorithmicx && \
   pdflatex main && bibtex main && pdflatex main && pdflatex main"
```

Check the page count:

```bash
python -c "import pypdf; print(len(pypdf.PdfReader('main.pdf').pages))"
# or: pdfinfo main.pdf | grep Pages
```

The last page is about half full, so small edits will not change the count. If
you add more than roughly half a column of text, it will reach 26 pages; trim
the Discussion or Appendix A tables rather than shrinking fonts or margins.

## Reference verification report

Every DOI was resolved against the Crossref API on 2026-10-24; title, authors,
venue, year, volume and pages in `references.bib` come from the Crossref
record. Open-access links were fetched (HTTP 200) unless noted.

### Open-access papers listed in the paper for reviewers

| # | Work | Venue, year | DOI | Free full text | Relevance |
|---|---|---|---|---|---|
| 1 | Churpek et al., ICU transfer delay and mortality | J. Hosp. Med. 2016 | 10.1002/jhm.2630 | https://pmc.ncbi.nlm.nih.gov/articles/PMC5119525/ | ~3% higher odds of death per hour of delay; motivates time-to-care objective |
| 2 | Wong & Harris, transfer delays are organisational | Crit. Care 2015 (abstract) | 10.1186/cc14590 | https://pmc.ncbi.nlm.nih.gov/articles/PMC4472710/ | delay weakly tied to distance; targets the brokering process |
| 3 | Greenwood-Ericksen et al., boarding, census and transfer acceptance | JAMA Netw. Open 2025 | 10.1001/jamanetworkopen.2025.12299 | https://pmc.ncbi.nlm.nih.gov/articles/PMC12120653/ | observed: acceptance falls with receiver census (r = −0.87) |
| 4 | Iwashyna et al., structure of critical care transfer networks | Med. Care 2009 | 10.1097/MLR.0b013e318197b1f5 | https://pmc.ncbi.nlm.nih.gov/articles/PMC2760433/ | hub-and-spoke transfer topology |
| 5 | Villarroel et al., Arizona Surge Line | Front. Public Health 2023 | 10.3389/fpubh.2022.1028353 | publisher OA (frontiersin.org) | real centralised transfer line in the replication state |
| 6 | Parker et al., optimal resource/demand redistribution | arXiv 2020 (**preprint**) | — | https://arxiv.org/abs/2011.03528 | centralised optimiser assuming full visibility |
| 7 | Rahwan et al., argumentation-based negotiation | Knowl. Eng. Rev. 2003 | 10.1017/S0269888904000098 | https://eprints.soton.ac.uk/258850/ | ABN framework; MAHROS uses a restricted form |
| 8 | Amgoud et al., unified framework for ABN | AAMAS 2007 | 10.1145/1329125.1329317 | IFAAMAS proceedings PDF | when arguments change outcomes |
| 9 | Ramchurn et al., trust in multi-agent systems | Knowl. Eng. Rev. 2004 | 10.1017/S0269888904000116 | https://eprints.soton.ac.uk/259564/ | reputation alternative to verification |
| 10 | Léauté & Faltings, privacy through distributed computation | JAIR 2013 | 10.1613/jair.3983 | JAIR (OA) and arXiv 1402.0589 | cryptographic private coordination |
| 11 | Bernstein et al., Ed25519 | J. Cryptogr. Eng. 2012 | 10.1007/s13389-012-0027-1 | Springer OA | signature scheme used for attested refusals |
| 12 | Crosby & Wallach, tamper-evident logging | USENIX Security 2009 | — (no DOI) | https://www.usenix.org/legacy/event/sec09/tech/full_papers/crosby.pdf | tamper-evident log reference |
| 13 | Lakens, equivalence tests primer | SPPS 2017 | 10.1177/1948550617697177 | https://pmc.ncbi.nlm.nih.gov/articles/PMC5502906/ | TOST used for equivalence claims |
| 14 | Jain, Chiu & Hawe, fairness index | DEC TR-301, 1984 | — | https://arxiv.org/abs/cs/9809099 | Jain's index |

### Items flagged

- **Southampton ePrints links (#7, #9)**: automated fetch returned 401/403 (bot
  blocking); the records were confirmed through a search index, not fetched
  directly. Open them in a browser before submission.
- **FIPA Contract Net spec**: cited as "FIPA document 00029, 2002". The exact
  version letter (G vs H) was not confirmed; the fipa.org page returned 403 to
  automated fetch.
- **GAO-21-600** and **CMS Provider of Services** pages returned 403 to
  automated fetch; their existence and titles were confirmed via search.
- **Holm (1979)** has no DOI (JSTOR 4615733); details are standard but not
  Crossref-checked.
- **Crosby & Wallach (2009)** has no DOI; pages 317–334 are from secondary
  listings.
- **Wong & Harris (2015)** is a conference abstract, labelled as such.
- **Parker et al. (2020)** and **Baas et al. (2023)** are arXiv preprints,
  labelled as such.
- The NEJM Catalyst case study on the Arizona Surge Line could not be resolved
  to a DOI and was **not** cited; the peer-reviewed Frontiers article is cited
  instead.

## Quality-control checklist

| Check | Status |
|---|---|
| Project source inspected | Yes: protocol, agents, verification, argumentation, attestation, collusion, calibration, baselines, metrics, statistics, schema, server, tests, all `results/*.json` |
| Claims match available evidence | Every number is from `results/*.json` / `docs/RESULTS.md`; inferred explanations are labelled as unverified |
| Research gap supported by literature | Yes, with a targeted (not systematic) search, stated in Sec. III |
| References verified | All 43 DOIs resolved via Crossref; the 11 entries without a DOI (datasets, reports, regulation, preprints, Holm, Crosby & Wallach, FIPA) were checked by URL or search and are flagged above |
| Figures present | Yes: 1 PNG + TikZ/pgfplots from generated data |
| LaTeX compilation | pdfLaTeX + BibTeX: no errors, no undefined references or citations, no overfull boxes |
| Page count | **25 pages, verified** on the compiled PDF |

### Things found in the code that the paper does **not** claim

- `negotiation/argumentation.py` says it is cross-checked against PyArg; no
  such test exists.
- `ledger/interface.py` mentions an `EvmLedger`; it does not exist.
- `eval/stats.py` refers to `tests/test_stats.py`; the scipy cross-check is
  actually `tests/test_core.py::test_paired_t_matches_scipy_when_available`.
- README says peers are "the 8 nearest hospitals that list the needed
  specialty"; `select_peers` takes the 8 nearest by road time, and capability
  is handled at bid time. The paper describes the code.

### Remaining limitations of the manuscript

- Author names, affiliations, e-mail and acknowledgment are placeholders.
- Hardware for the reported runs is not recorded in the result files, so
  latency is reported as order-of-magnitude only.
- `paper/` is not yet committed; Appendix E cites `paper/scripts/make_figure_data.py`
  as part of the repository, so commit it before sharing the paper.

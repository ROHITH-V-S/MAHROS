#!/bin/sh
# Full build: LaTeX pass -> grouped IEEE references -> final passes.
# Needs pdflatex + bibtex (TeX Live/MiKTeX) and Python 3.
set -e
cd "$(dirname "$0")"
pdflatex -interaction=nonstopmode main.tex >/dev/null || true
python3 scripts/build_refs.py aux
bibtex refs_tmp >/dev/null
python3 scripts/build_refs.py group
pdflatex -interaction=nonstopmode main.tex >/dev/null
pdflatex -interaction=nonstopmode main.tex | grep "Output written"
rm -f refs_tmp.aux refs_tmp.bbl refs_tmp.blg

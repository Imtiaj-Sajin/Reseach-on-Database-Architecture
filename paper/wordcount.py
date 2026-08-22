"""
Word count for the manuscript, excluding what journals do not count.

Strips comments, floats (tables, figures), listings, and LaTeX control
sequences, then counts what remains between the Introduction and the
bibliography. Reports the abstract separately, since Elsevier caps it.
"""
import re
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "paper.tex"
t = open(path, encoding="utf-8").read()

body = t.split(r"\section{Introduction}")[1].split(r"\bibliographystyle")[0]
body = re.sub(r"%.*", "", body)
for env in ("lstlisting", "table", "figure", "tabular"):
    body = re.sub(r"\\begin\{" + env + r"\}.*?\\end\{" + env + r"\}", "", body, flags=re.S)
body = re.sub(r"\\[a-zA-Z]+\*?(\[[^\]]*\])?(\{[^}]*\})?", " ", body)
body = re.sub(r"[{}$\\&_^~]", " ", body)
words = [x for x in body.split() if any(c.isalpha() for c in x)]

ab = t.split(r"\begin{abstract}")[1].split(r"\end{abstract}")[0]
ab = re.sub(r"\\[a-zA-Z]+\*?(\{[^}]*\})?", " ", ab)
ab = re.sub(r"[{}$\\%~]", " ", ab)

print("body words     %5d   (target 8000 for this venue)" % len(words))
print("abstract words %5d   (Elsevier prefers 200-250)"
      % len([x for x in ab.split() if any(c.isalpha() for c in x)]))
print("figures        %5d" % len(re.findall(r"\\begin\{figure\}", t)))
print("tables         %5d" % len(re.findall(r"\\begin\{table\}", t)))
print("citations      %5d distinct" % len(set(re.findall(r"\\cite[tp]?\{([^}]*)\}", t))))

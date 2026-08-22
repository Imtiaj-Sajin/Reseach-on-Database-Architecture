"""
Word count for the report, excluding what the word limit does not cover.

Counts the numbered body only: front matter, floats (tables, figures),
listings, appendices and the bibliography are stripped, since the course
guide's 3,000-5,000 limit refers to the written body. The abstract is
reported separately.

Handles the course template's \section*{Abstract} form as well as the
\begin{abstract} environment used by journal classes.
"""
import re
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "main.tex"
t = open(path, encoding="utf-8").read()

# Body runs from the Introduction to the bibliography.
body = re.split(r"\\section\{Introduction\}", t)[1]
body = re.split(r"\\printbibliography|\\bibliographystyle", body)[0]
body = re.sub(r"%.*", "", body)
for env in ("lstlisting", "table", "figure", "tabular"):
    body = re.sub(r"\\begin\{" + env + r"\}.*?\\end\{" + env + r"\}", "", body, flags=re.S)
body = re.sub(r"\\[a-zA-Z]+\*?(\[[^\]]*\])?(\{[^}]*\})?", " ", body)
body = re.sub(r"[{}$\\&_^~]", " ", body)
words = [x for x in body.split() if any(c.isalpha() for c in x)]

# Abstract, either form.
m = re.search(r"\\section\*\{Abstract\}(.*?)\\newpage", t, flags=re.S)
if not m:
    m = re.search(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", t, flags=re.S)
ab = m.group(1) if m else ""
ab = re.sub(r"\\[a-zA-Z]+\*?(\{[^}]*\})?", " ", ab)
ab = re.sub(r"[{}$\\%~]", " ", ab)
ab_words = [x for x in ab.split() if any(c.isalpha() for c in x)]

print("body words     %5d   (course limit 3,000-5,000)" % len(words))
print("abstract words %5d" % len(ab_words))
print("figures        %5d" % len(re.findall(r"\\begin\{figure\}", t)))
print("tables         %5d" % len(re.findall(r"\\begin\{table\}", t)))
print("citations      %5d distinct" % len(set(
    k.strip() for c in re.findall(r"\\cite\{([^}]*)\}", t) for k in c.split(","))))

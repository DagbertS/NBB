"""Markdown-analyse -> PDF (fpdf2, geen systeemafhankelijkheden).

De one-pagers zijn eenvoudige markdown (koppen, tabellen, lijsten); dit
zet ze om naar een nette A4-PDF. De kern-fonts van PDF zijn latin-1,
dus typografische tekens worden eerst naar een veilig equivalent vertaald.
"""

import re

from fpdf import FPDF

_REPLACEMENTS = {
    "—": "-", "–": "-", "’": "'", "‘": "'", "“": '"', "”": '"',
    "⚠": "(!)", "→": "->", "←": "<-", "↔": "<->", "€": "EUR ",
    "…": "...", "×": "x", "≥": ">=", "≤": "<=", "☑": "[x]", "✓": "v",
}

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
_LINE_HEIGHT = 5


def _latin(text: str) -> str:
    for src, dst in _REPLACEMENTS.items():
        text = text.replace(src, dst)
    return text.encode("latin-1", "replace").decode("latin-1")


def _plain(text: str) -> str:
    """Markdown-nadruk strippen voor tabelcellen en koppen."""
    text = _BOLD_RE.sub(r"\1", text)
    text = _ITALIC_RE.sub(r"\1", text)
    return _latin(text)


class _Pdf(FPDF):
    def __init__(self, footer_text: str):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.footer_text = footer_text
        self.set_auto_page_break(auto=True, margin=18)

    def footer(self):
        self.set_y(-14)
        self.set_font("helvetica", "I", 7.5)
        self.set_text_color(120)
        self.cell(0, 5, _latin(self.footer_text), align="C")
        self.set_text_color(0)


def _truncate(pdf: FPDF, text: str, width: float) -> str:
    if pdf.get_string_width(text) <= width:
        return text
    while text and pdf.get_string_width(text + "...") > width:
        text = text[:-1]
    return text + "..."


def _render_table(pdf: _Pdf, rows: list[list[str]]) -> None:
    if not rows:
        return
    n_cols = max(len(r) for r in rows)
    usable = pdf.w - pdf.l_margin - pdf.r_margin
    # eerste kolom (labels) wat breder als er >2 kolommen zijn
    if n_cols > 2:
        first = usable * 0.3
        rest = (usable - first) / (n_cols - 1)
        widths = [first] + [rest] * (n_cols - 1)
    else:
        widths = [usable / n_cols] * n_cols

    for i, row in enumerate(rows):
        style = "B" if i == 0 else ""
        pdf.set_font("helvetica", style, 8)
        cells = row + [""] * (n_cols - len(row))
        for width, cell in zip(widths, cells):
            pdf.cell(width, _LINE_HEIGHT, _truncate(pdf, _plain(cell), width - 1),
                     border="B", align="L")
        pdf.ln(_LINE_HEIGHT)
    pdf.ln(2)


def markdown_to_pdf(markdown: str, footer_text: str = "") -> bytes:
    pdf = _Pdf(footer_text or "NBB M&A Screening - geschatte waarden zijn nooit feiten")
    pdf.add_page()
    pdf.set_font("helvetica", size=9)

    table_rows: list[list[str]] = []

    def flush_table():
        nonlocal table_rows
        if table_rows:
            _render_table(pdf, table_rows)
            table_rows = []

    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()

        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(set(c) <= {"-", ":", " "} for c in cells):
                continue  # scheidingsrij |---|---|
            table_rows.append(cells)
            continue
        flush_table()

        if not line.strip():
            pdf.ln(2)
        elif line.startswith("# "):
            pdf.set_font("helvetica", "B", 15)
            pdf.multi_cell(0, 7, _plain(line[2:]), new_x="LMARGIN", new_y="NEXT")
            pdf.ln(1)
            pdf.set_font("helvetica", size=9)
        elif line.startswith("## "):
            pdf.ln(1)
            pdf.set_font("helvetica", "B", 11.5)
            pdf.multi_cell(0, 6, _plain(line[3:]), new_x="LMARGIN", new_y="NEXT")
            pdf.ln(0.5)
            pdf.set_font("helvetica", size=9)
        elif line.startswith("- "):
            pdf.set_font("helvetica", size=9)
            pdf.multi_cell(0, _LINE_HEIGHT, _latin("  \x95 ") + _plain(line[2:]),
                           new_x="LMARGIN", new_y="NEXT")
        elif line.startswith("> "):
            pdf.set_font("helvetica", "I", 8.5)
            pdf.set_text_color(90)
            pdf.multi_cell(0, _LINE_HEIGHT, _plain(line[2:]), new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(0)
            pdf.set_font("helvetica", size=9)
        elif set(line.strip()) <= {"-", "_"} and len(line.strip()) >= 3:
            y = pdf.get_y() + 1
            pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
            pdf.ln(3)
        elif line.strip().startswith("_") and line.strip().endswith("_"):
            pdf.set_font("helvetica", "I", 9)
            pdf.multi_cell(0, _LINE_HEIGHT, _plain(line.strip().strip("_")),
                           new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("helvetica", size=9)
        else:
            pdf.set_font("helvetica", size=9)
            pdf.multi_cell(0, _LINE_HEIGHT, _plain(line), new_x="LMARGIN", new_y="NEXT")

    flush_table()
    return bytes(pdf.output())

"""
Inspect the saved probate detail page HTML and report structural landmarks.

Reads runs/maricopa_az/pipeline_output/probate_detail_probe.html and prints
a structural summary: tag counts, table headers, label/value pairs, form
fields. Does NOT print any data values — only tag names, attribute names,
and cell labels.

Usage:
    python runs/maricopa_az/analyze_probate_detail_structure.py
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

HTML_PATH = Path(__file__).parent / "pipeline_output" / "probate_detail_probe.html"


class _StructureParser(HTMLParser):
    """Collect tags, table-cell labels, and form field names — not values."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tag_counts: Counter = Counter()
        self.ids: list[str] = []
        self.class_names: Counter = Counter()
        self.input_names: list[str] = []
        self.table_headers: list[str] = []
        self.label_texts: list[str] = []
        self._current_tag: str = ""
        self._in_th = False
        self._in_label = False
        self._text_buf = ""
        # Collect all text in cells to find field labels (not values)
        self._in_td = False
        self._td_texts: list[str] = []
        self._current_td: str = ""
        # Track heading elements
        self._in_heading = False
        self.headings: list[str] = []
        # Track div ids/classes with "party" or "case" in them
        self.party_containers: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        self.tag_counts[tag] += 1
        attr = dict(attrs)

        tag_id = attr.get("id") or ""
        tag_class = attr.get("class") or ""

        if tag_id:
            self.ids.append(tag_id)
        for cls in tag_class.split():
            self.class_names[cls] += 1
        if "party" in tag_id.lower() or "party" in tag_class.lower():
            self.party_containers.append(f"{tag}#{tag_id}.{tag_class}")
        if "case" in tag_id.lower():
            self.party_containers.append(f"{tag}#{tag_id}")

        if tag == "input":
            name = attr.get("name") or attr.get("id") or ""
            if name:
                self.input_names.append(name)

        self._current_tag = tag
        self._in_th = tag == "th"
        self._in_label = tag == "label"
        self._in_td = tag == "td"
        self._in_heading = tag in ("h1", "h2", "h3", "h4", "h5", "h6")
        if self._in_td:
            self._current_td = ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "th" and self._in_th:
            t = self._text_buf.strip()
            if t:
                self.table_headers.append(t)
            self._text_buf = ""
            self._in_th = False
        if tag == "label" and self._in_label:
            t = self._text_buf.strip()
            if t:
                self.label_texts.append(t)
            self._text_buf = ""
            self._in_label = False
        if tag == "td" and self._in_td:
            self._td_texts.append(self._current_td.strip())
            self._in_td = False
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6") and self._in_heading:
            t = self._text_buf.strip()
            if t:
                self.headings.append(t)
            self._text_buf = ""
            self._in_heading = False

    def handle_data(self, data: str) -> None:
        if self._in_th or self._in_label or self._in_heading:
            self._text_buf += data
        if self._in_td:
            self._current_td += data


def _looks_like_label(text: str) -> bool:
    """True if text looks like a field label rather than a data value."""
    t = text.strip().rstrip(":")
    # Labels are short, title-case or ALL-CAPS, no digits dominant
    if len(t) > 60:
        return False
    if re.search(r"\d{4}", t):
        return False
    return bool(re.match(r"[A-Z][A-Za-z \/\-()]+$", t))


def main() -> None:
    if not HTML_PATH.exists():
        print(f"ERROR: {HTML_PATH} not found. Run probe_probate_detail.py first.")
        sys.exit(1)

    html = HTML_PATH.read_text(encoding="utf-8")
    print(f"HTML size: {len(html)} bytes")
    print()

    parser = _StructureParser()
    parser.feed(html)

    print("=== Tag frequency (top 20) ===")
    for tag, cnt in parser.tag_counts.most_common(20):
        print(f"  <{tag}>: {cnt}")
    print()

    print("=== Element IDs found ===")
    for eid in parser.ids[:40]:
        print(f"  #{eid}")
    if len(parser.ids) > 40:
        print(f"  ... ({len(parser.ids) - 40} more)")
    print()

    print("=== CSS classes (top 20) ===")
    for cls, cnt in parser.class_names.most_common(20):
        print(f"  .{cls}: {cnt}")
    print()

    print("=== Table headers / <th> text ===")
    for h in parser.table_headers[:30]:
        print(f"  TH: {h!r}")
    print()

    print("=== Headings (h1–h6) ===")
    for h in parser.headings[:20]:
        print(f"  H: {h!r}")
    print()

    print("=== Label texts ===")
    for lb in parser.label_texts[:30]:
        print(f"  LABEL: {lb!r}")
    print()

    print("=== Form input names ===")
    for inp in parser.input_names[:30]:
        print(f"  INPUT[name={inp!r}]")
    print()

    print("=== Candidate field labels in <td> cells ===")
    candidate_labels = [t for t in parser._td_texts if _looks_like_label(t)]
    for lb in candidate_labels[:40]:
        print(f"  TD-LABEL: {lb!r}")
    print()

    print("=== Party / case containers ===")
    for pc in parser.party_containers[:20]:
        print(f"  {pc}")
    print()

    # Keyword search in raw HTML (structural markers only)
    kw_targets = [
        "filing", "filed", "case type", "type", "status", "decedent",
        "petitioner", "representative", "executor", "administrator",
        "attorney", "property", "address", "parcel", "apn", "estate",
        "party", "role", "MainContent",
    ]
    print("=== Keyword presence in raw HTML ===")
    html_lower = html.lower()
    for kw in kw_targets:
        count = html_lower.count(kw.lower())
        if count:
            print(f"  {kw!r}: {count} occurrence(s)")
    print()

    print("Done. Use this output to design the detail parser.")


if __name__ == "__main__":
    main()

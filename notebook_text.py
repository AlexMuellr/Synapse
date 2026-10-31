"""Plain-text view of notebook pages, used to hand selected notes to the AI assistant.

Pure functions only (no Anki imports) so they can be tested without a running profile.
The notebook stores ``{"pages": [...], "folders": [...]}`` (or a legacy block list) as JSON;
each block is ``{"type", "html", ...}``.
"""
from __future__ import annotations

import json
from html.parser import HTMLParser
from typing import Any, Iterable

MAX_PAGE_CHARS = 12000
MAX_TOTAL_CHARS = 40000
_MAX_INDENT = 5
_BLOCK_TAGS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0
        self._links: list[str | None] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")
        elif tag == "img":
            self.parts.append("[image]")
        elif tag == "a":
            self._links.append(dict(attrs).get("href"))
        elif tag == "td" or tag == "th":
            self.parts.append(" | ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag == "a" and self._links:
            href = self._links.pop()
            if href and href.startswith(("http://", "https://")):
                self.parts.append(f" ({href})")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(value: Any) -> str:
    """Visible text of a block's HTML; formatting such as colors is dropped."""
    parser = _TextExtractor()
    parser.feed(str(value or ""))
    parser.close()
    text = "".join(parser.parts).replace("\xa0", " ")
    lines = [line.strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line)


def _table_text(html: str) -> str:
    rows = []
    parser = _TextExtractor()
    for chunk in str(html or "").split("</tr>"):
        parser = _TextExtractor()
        parser.feed(chunk)
        parser.close()
        cells = [c.strip() for c in "".join(parser.parts).replace("\n", " ").split(" | ")]
        cells = [c for c in cells if c]
        if cells:
            rows.append("| " + " | ".join(cells) + " |")
    return "\n".join(rows)


def _indent_of(block: dict) -> int:
    try:
        return max(0, min(_MAX_INDENT, int(block.get("indent") or 0)))
    except (TypeError, ValueError):
        return 0


def _label(n: int, level: int) -> str:
    if level % 3 == 1:
        out = ""
        while n > 0:
            n, rest = divmod(n - 1, 26)
            out = chr(97 + rest) + out
        return out or "a"
    if level % 3 == 2:
        numerals = [(10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")]
        out = ""
        for value, symbol in numerals:
            while n >= value:
                out += symbol
                n -= value
        return out or "i"
    return str(n)


def blocks_to_text(blocks: Iterable[Any]) -> str:
    """Markdown-like text. Nesting and numbering mirror what the editor displays."""
    lines: list[str] = []
    counters = [0] * (_MAX_INDENT + 1)
    for block in blocks:
        if not isinstance(block, dict):
            continue
        kind = block.get("type") or "text"
        html = block.get("html", "")
        pad = "  " * _indent_of(block)
        if kind in ("bullet", "numbered", "todo"):
            level = _indent_of(block)
            for deeper in range(level + 1, _MAX_INDENT + 1):
                counters[deeper] = 0
            text = html_to_text(html).replace("\n", " ")
            if kind == "numbered":
                counters[level] += 1
                lines.append(f"{pad}{_label(counters[level], level)}. {text}")
            elif kind == "todo":
                lines.append(f"{pad}- [{'x' if block.get('checked') else ' '}] {text}")
            else:
                lines.append(f"{pad}- {text}")
            continue
        counters = [0] * (_MAX_INDENT + 1)
        if kind == "latex":
            source = str(block.get("latex") or "").strip()
            if source:
                lines.append(f"\\[ {source} \\]")
        elif kind in ("h1", "h2", "h3"):
            text = html_to_text(html).replace("\n", " ")
            if text:
                lines.append("#" * int(kind[1]) + " " + text)
        elif kind == "quote" or kind == "callout":
            text = html_to_text(html)
            if text:
                lines.extend("> " + line for line in text.split("\n"))
        elif kind == "code":
            lines.append("```\n" + html_to_text(html) + "\n```")
        elif kind == "toggle":
            title = html_to_text(html).replace("\n", " ")
            body = html_to_text(block.get("body", ""))
            lines.append(f"▸ {title}")
            lines.extend("  " + line for line in body.split("\n") if line)
        elif kind == "table":
            table = _table_text(html)
            if table:
                lines.append(table)
        elif kind == "divider":
            lines.append("---")
        else:
            text = html_to_text(html)
            if text:
                lines.append(text)
    return "\n".join(lines).strip()


def parse_pages(body: str) -> list[dict]:
    """Pages from a stored notebook snapshot; a legacy block list becomes one page."""
    try:
        data = json.loads(body)
    except (TypeError, ValueError):
        return []
    if isinstance(data, list):
        return [{"id": "legacy", "title": "My Notes", "blocks": data}]
    if isinstance(data, dict) and isinstance(data.get("pages"), list):
        return [p for p in data["pages"] if isinstance(p, dict)]
    return []


def page_outline(body: str) -> list[dict]:
    """Page tree data for a picker: id, title, icon, parent and section."""
    return [
        {
            "id": str(page.get("id", "")),
            "title": str(page.get("title") or ""),
            "icon": str(page.get("icon") or ""),
            "parentPageId": page.get("parentPageId") or None,
            "folderId": page.get("folderId") or None,
        }
        for page in parse_pages(body)
        if page.get("id") is not None
    ]


def notebook_outline(body: str) -> dict:
    """Everything a page picker needs: sections ("folders") and the page list."""
    try:
        data = json.loads(body)
    except (TypeError, ValueError):
        data = None
    folders = []
    if isinstance(data, dict) and isinstance(data.get("folders"), list):
        folders = [
            {"id": str(f.get("id")), "name": str(f.get("name") or "")}
            for f in data["folders"] if isinstance(f, dict) and f.get("id") is not None
        ]
    return {"folders": folders, "pages": page_outline(body)}


def pages_to_context(body: str, page_ids: Iterable[str],
                     per_page: int = MAX_PAGE_CHARS, total: int = MAX_TOTAL_CHARS) -> str:
    """Text for the selected pages (in the given order), clipped per page and in total."""
    by_id = {str(page.get("id")): page for page in parse_pages(body)}
    sections: list[str] = []
    used = 0
    for page_id in page_ids:
        page = by_id.get(str(page_id))
        if not page:
            continue
        text = blocks_to_text(page.get("blocks") or [])
        if len(text) > per_page:
            text = text[:per_page].rsplit(" ", 1)[0].rstrip() + " […]"
        title = str(page.get("title") or "").strip() or "Untitled"
        section = f'<notebook_page title="{title.replace(chr(34), "&quot;")}">\n{text}\n</notebook_page>'
        if used + len(section) > total:
            sections.append("<notebook_note>Further selected pages were omitted (size limit).</notebook_note>")
            break
        sections.append(section)
        used += len(section)
    return "\n\n".join(sections)

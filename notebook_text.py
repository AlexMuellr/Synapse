"""Plain-text view of notebook pages, used to hand selected notes to the AI assistant.

Pure functions only (no Anki imports) so they can be tested without a running profile.
The notebook stores ``{"pages": [...], "folders": [...]}`` (or a legacy block list) as JSON;
each block is ``{"type", "html", ...}``.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
from html.parser import HTMLParser
from typing import Any, Iterable, Optional

MAX_PAGE_CHARS = 12000
MAX_TOTAL_CHARS = 40000
_MAX_INDENT = 5
_BLOCK_TAGS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3"}

MAX_IMAGES = 40
MAX_IMAGE_BYTES = 5 * 1024 * 1024
_DATA_IMAGE = re.compile(r"^data:image/(png|jpe?g|gif|webp);base64,([A-Za-z0-9+/=\s]+)$", re.IGNORECASE)
_MARKER = re.compile(r"\[img(\d+)\]")


def decode_data_image(src: Any, max_bytes: int = MAX_IMAGE_BYTES) -> Optional[tuple[bytes, str]]:
    """(bytes, extension) of a base64 data: image, or None if it is not a safe raster image.

    The type is verified from the bytes themselves, not from the declared MIME type.
    SVG and everything else is rejected.
    """
    match = _DATA_IMAGE.match(str(src or "").strip())
    if not match:
        return None
    try:
        data = base64.b64decode(re.sub(r"\s+", "", match.group(2)), validate=True)
    except (binascii.Error, ValueError):
        return None
    if not data or len(data) > max_bytes:
        return None
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return data, "png"
    if data.startswith(b"\xff\xd8\xff"):
        return data, "jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return data, "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return data, "webp"
    return None


class ImageCollector:
    """Gives each distinct pasted image of a request a short marker ([img1], [img2], ...).

    The AI only ever sees the markers; the image data stays local.
    """

    def __init__(self, limit: int = MAX_IMAGES) -> None:
        self.limit = limit
        self.images: dict[str, str] = {}
        self._ids: dict[str, str] = {}

    def marker(self, src: Any) -> str:
        src = str(src or "").strip()
        known = self._ids.get(src)
        if known:
            return f" [{known}] "
        if len(self.images) >= self.limit or decode_data_image(src) is None:
            return "[image]"
        image_id = f"img{len(self.images) + 1}"
        self.images[image_id] = src
        self._ids[src] = image_id
        return f" [{image_id}] "

    def referenced(self, text: str) -> dict[str, str]:
        """Only the images whose marker actually made it into ``text``."""
        found = {f"img{n}" for n in _MARKER.findall(text or "")}
        return {key: value for key, value in self.images.items() if key in found}


class _TextExtractor(HTMLParser):
    def __init__(self, images: Optional[ImageCollector] = None) -> None:
        super().__init__(convert_charrefs=True)
        self.images = images
        self.parts: list[str] = []
        self._skip = 0
        self._math = 0        # >0 while inside an inline formula (its content is not text)
        self._links: list[str | None] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._math:
            if tag == "span":
                self._math += 1
            return
        if tag == "span" and "math-inline" in (dict(attrs).get("class") or "").split():
            # Inline formula: keep the LaTeX source in the delimiters Anki renders itself.
            self.parts.append("\\(" + (dict(attrs).get("data-latex") or "") + "\\)")
            self._math = 1
            return
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")
        elif tag == "img":
            self.parts.append(self.images.marker(dict(attrs).get("src")) if self.images else "[image]")
        elif tag == "a":
            self._links.append(dict(attrs).get("href"))
        elif tag == "td" or tag == "th":
            self.parts.append(" | ")

    def handle_endtag(self, tag: str) -> None:
        if self._math:
            if tag == "span":
                self._math -= 1
            return
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag == "a" and self._links:
            href = self._links.pop()
            if href and href.startswith(("http://", "https://")):
                self.parts.append(f" ({href})")

    def handle_data(self, data: str) -> None:
        if not self._skip and not self._math:
            self.parts.append(data)


def html_to_text(value: Any, images: Optional[ImageCollector] = None) -> str:
    """Visible text of a block's HTML; formatting such as colors is dropped."""
    parser = _TextExtractor(images)
    parser.feed(str(value or ""))
    parser.close()
    text = "".join(parser.parts).replace("\xa0", " ")
    if images:
        # Markers carry their own padding; keep a single space around them.
        text = re.sub(r"[ \t]{2,}", " ", text)
    lines = [line.strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line)


def _table_text(html: str, images: Optional[ImageCollector] = None) -> str:
    rows = []
    for chunk in str(html or "").split("</tr>"):
        parser = _TextExtractor(images)
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


def blocks_to_text(blocks: Iterable[Any], images: Optional[ImageCollector] = None) -> str:
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
            text = html_to_text(html, images).replace("\n", " ")
            if kind == "numbered":
                start = block.get("start")
                if isinstance(start, int) and not isinstance(start, bool) and start > 1:
                    counters[level] = start      # the item starts at its own number
                else:
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
            text = html_to_text(html, images).replace("\n", " ")
            if text:
                lines.append("#" * int(kind[1]) + " " + text)
        elif kind == "quote" or kind == "callout":
            text = html_to_text(html, images)
            if text:
                lines.extend("> " + line for line in text.split("\n"))
        elif kind == "code":
            lines.append("```\n" + html_to_text(html, images) + "\n```")
        elif kind == "toggle":
            title = html_to_text(html, images).replace("\n", " ")
            body = html_to_text(block.get("body", ""), images)
            lines.append(f"▸ {title}")
            lines.extend("  " + line for line in body.split("\n") if line)
        elif kind == "table":
            table = _table_text(html, images)
            if table:
                lines.append(table)
        elif kind == "divider":
            lines.append("---")
        else:
            text = html_to_text(html, images)
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
                     per_page: int = MAX_PAGE_CHARS, total: int = MAX_TOTAL_CHARS,
                     images: Optional[ImageCollector] = None) -> str:
    """Text for the selected pages (in the given order), clipped per page and in total."""
    by_id = {str(page.get("id")): page for page in parse_pages(body)}
    sections: list[str] = []
    used = 0
    for page_id in page_ids:
        page = by_id.get(str(page_id))
        if not page:
            continue
        text = blocks_to_text(page.get("blocks") or [], images)
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

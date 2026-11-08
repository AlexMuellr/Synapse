"""Anki field HTML for the Card Creators: a strict allow-list sanitizer.

The Card Creators of the notebook and the PDF tab are small rich-text editors. They send each field as
HTML that uses only tags every Anki client renders without this add-on (iPad, phone, desktop):
headings, bold/italic/underline, nested lists, line breaks and pictures. Formulas are plain text
``\\( … \\)`` / ``\\[ … \\]``, which Anki's MathJax renders. This module re-checks everything the web view
sends (it is untrusted) and turns picture placeholders ``<img data-img="N">`` into real media files.

Pure functions, no Anki imports.
"""
from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from typing import Optional

from .notebook_cards import media_name

_TAGS = {"div", "p", "br", "b", "i", "u", "s", "sub", "sup", "ul", "ol", "li", "h1", "h2", "h3", "img"}
_ALIASES = {"strong": "b", "em": "i", "strike": "s", "del": "s", "h4": "h3", "h5": "h3", "h6": "h3"}
_VOID = {"br", "img"}
_DROP = {"script", "style", "template", "iframe", "object", "embed", "svg", "math", "noscript", "head",
         "title", "textarea", "select", "button", "form", "canvas", "audio", "video", "link", "meta"}
_OL_TYPES = {"1", "a", "A", "i", "I"}
_ID = re.compile(r"\d{1,6}")
_EMPTY_TAIL = re.compile(r"(?:<br>|<div>(?:<br>)?</div>)+$")
_EMPTY_HEAD = re.compile(r"^(?:<br>|<div>(?:<br>)?</div>)+")
MAX_DEPTH = 12


class _Cleaner(HTMLParser):
    def __init__(self, images: Optional[dict]):
        super().__init__(convert_charrefs=True)
        self.images = images
        self.out: list[str] = []
        self.stack: list[str] = []
        self.skip = 0
        self.text: list[str] = []
        self.pictures: list[str] = []
        self.media: list[tuple[str, bytes]] = []

    def _lists(self) -> int:
        return sum(1 for tag in self.stack if tag in ("ul", "ol"))

    def handle_starttag(self, tag, attrs):
        tag = _ALIASES.get(tag, tag)
        if self.skip:
            if tag in _DROP:
                self.skip += 1
            return
        if tag in _DROP:
            self.skip = 1
            return
        if tag not in _TAGS:
            return
        values = dict(attrs)
        if tag == "img":
            self._image(values)
            return
        if tag == "br":
            self.out.append("<br>")
            self.text.append("\n")
            return
        if tag == "li" and not self._lists():
            tag = "div"
        if tag in ("ul", "ol") and self._lists() >= MAX_DEPTH:
            return
        extra = ""
        if tag == "ol":
            start = (values.get("start") or "").strip()
            if start.isdigit() and 1 < int(start) <= 100000:
                extra += f' start="{int(start)}"'
            if (values.get("type") or "") in _OL_TYPES and values.get("type") != "1":
                extra += f' type="{values["type"]}"'
        self.stack.append(tag)
        self.out.append(f"<{tag}{extra}>")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if _ALIASES.get(tag, tag) not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        tag = _ALIASES.get(tag, tag)
        if self.skip:
            if tag in _DROP:
                self.skip -= 1
            return
        if tag in _VOID or tag not in _TAGS:
            return
        if tag == "li" and "li" not in self.stack:
            tag = "div"
        if tag not in self.stack:
            return
        while self.stack:
            top = self.stack.pop()
            self.out.append(f"</{top}>")
            if top == tag:
                break
        if tag in ("div", "p", "li", "h1", "h2", "h3"):
            self.text.append("\n")

    def handle_data(self, data):
        if self.skip:
            return
        data = data.replace("\x00", "").replace("​", "").replace(" ", " ")
        if data:
            self.out.append(html.escape(data, quote=False))
            self.text.append(data)

    def _image(self, values):
        pid = (values.get("data-img") or "").strip()
        if not _ID.fullmatch(pid):
            return
        self.pictures.append(pid)
        if self.images is None:
            self.out.append(f'<img data-img="{pid}">')
            return
        picture = self.images.get(pid)
        if picture is None:
            raise ValueError("missing picture")
        data, extension = picture
        name = media_name(data, extension)
        self.media.append((name, data))
        self.out.append(f'<img src="{name}">')


def _run(source, images) -> _Cleaner:
    cleaner = _Cleaner(images)
    cleaner.feed(str(source or ""))
    cleaner.close()
    while cleaner.stack:
        cleaner.out.append(f"</{cleaner.stack.pop()}>")
    return cleaner


def _tidy(markup: str) -> str:
    markup = markup.strip()
    previous = None
    while previous != markup:
        previous = markup
        markup = _EMPTY_HEAD.sub("", _EMPTY_TAIL.sub("", markup)).strip()
    return markup


def sanitize(source, images: Optional[dict] = None) -> tuple[str, list[tuple[str, bytes]]]:
    """Clean field HTML. Returns (html, media) where media lists (file name, bytes) still to be stored.

    ``images`` maps a picture id to (bytes, extension); a placeholder whose picture was not sent raises
    ValueError. With ``images=None`` placeholders are kept as they are (no media).
    """
    cleaner = _run(source, images)
    seen: set[str] = set()
    media = [item for item in cleaner.media if not (item[0] in seen or seen.add(item[0]))]
    return _tidy("".join(cleaner.out)), media


def image_ids(source) -> list[str]:
    return _run(source, None).pictures


def plain_text(source) -> str:
    return "".join(_run(source, None).text).strip()


def has_content(source) -> bool:
    cleaner = _run(source, None)
    return bool("".join(cleaner.text).strip() or cleaner.pictures)

"""Plain-Python HTML-to-text for JD fragments that are already pure
content, not a whole scraped page — arbeitnow's `description` and
Adzuna's `description` snippet (DESIGN §3, units 21-22).

`trafilatura.extract` (see fallback.py) is for whole pages: it detects
and strips site boilerplate, and drops short/fragment-only markup like
these because there's no boilerplate to distinguish it from. A plain
tag-strip is the right tool here instead.
"""

from html.parser import HTMLParser

_BLOCK_TAGS = {"p", "div", "li", "br", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("- ")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")


def strip_html(raw: str) -> str:
    """Tags out, block-level structure kept as line breaks."""
    extractor = _TextExtractor()
    extractor.feed(raw)
    lines = (line.strip() for line in "".join(extractor.parts).splitlines())
    return "\n".join(line for line in lines if line).strip()

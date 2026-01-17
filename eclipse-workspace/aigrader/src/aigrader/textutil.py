# src/aigrader/textutil.py
#
# Text normalization utilities for AIGrader.
# Designed for Canvas text-entry submissions.
#
# Responsibilities:
#   - Convert Canvas HTML -> plain text
#   - Normalize whitespace
#   - Provide basic text statistics (word count)

from __future__ import annotations

import re
from html import unescape


# Matches any HTML tag
_TAG_RE = re.compile(r"<[^>]+>")


def html_to_text(html: str) -> str:
    """
    Convert Canvas HTML submission body into clean plain text.

    This is intentionally lightweight:
    - Preserves paragraph breaks
    - Strips tags
    - Decodes HTML entities
    - Normalizes whitespace

    It is NOT a full HTML parser by design.
    """
    if html is None:
        return ""

    s = str(html)

    # Normalize common block-level separators into newlines
    s = s.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    s = s.replace("</p>", "\n\n")
    s = s.replace("</div>", "\n")
    s = s.replace("</li>", "\n")
    s = s.replace("</h1>", "\n\n")
    s = s.replace("</h2>", "\n\n")
    s = s.replace("</h3>", "\n\n")

    # Strip remaining tags
    s = _TAG_RE.sub("", s)

    # Decode HTML entities (&nbsp;, &amp;, etc.)
    s = unescape(s)
    s = s.replace("\xa0", " ")

    # Normalize line endings
    s = re.sub(r"\r\n?", "\n", s)

    # Collapse excessive blank lines (3+ -> 2)
    s = re.sub(r"\n{3,}", "\n\n", s)

    # Collapse repeated spaces/tabs
    s = re.sub(r"[ \t]{2,}", " ", s)

    return s.strip()


def word_count(text: str) -> int:
    """
    Count words in normalized text.

    Uses a conservative definition of a word:
    sequences of alphanumeric characters.
    """
    if not text:
        return 0
    return len(re.findall(r"\b\w+\b", text))

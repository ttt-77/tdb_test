"""Small shared UI helpers (no Streamlit import at module level)."""

from __future__ import annotations

# Browsers that support `field-sizing` grow the box live while typing; the
# computed height from autoheight() is the cross-version fallback (the Space
# runs Streamlit 1.39, where height="content" does not exist).
TEXTAREA_AUTOGROW_CSS = (
    "<style>.stTextArea textarea{field-sizing:content;min-height:5rem;"
    "max-height:26rem;}</style>"
)


def autoheight(text: str, min_h: int = 80, max_h: int = 420, per_line: int = 80) -> int:
    """Rough pixel height that fits `text` (assumes wrapping at ~per_line chars)."""
    lines = 0
    for para in (text or "").split("\n"):
        lines += max(1, -(-len(para) // per_line))  # ceil division
    return max(min_h, min(max_h, 34 + 22 * lines))

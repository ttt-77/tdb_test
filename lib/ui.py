"""Small shared UI helpers for auto-growing text areas."""

from __future__ import annotations

import streamlit as st

# Streamlit >= 1.44 supports height="content" (the box sizes itself to the text).
# Feature-detect from the docstring so this works on any version without
# hard-coding a version number.
SUPPORTS_CONTENT_HEIGHT = '"content"' in (st.text_area.__doc__ or "")

# Streamlit sets the textarea height as an INLINE style, which beats any
# stylesheet rule — so to grow while typing we must override it with
# `height: auto !important`. Gated behind @supports so browsers without
# field-sizing keep Streamlit's (computed) height instead of collapsing.
TEXTAREA_AUTOGROW_CSS = """<style>
@supports (field-sizing: content) {
  .stTextArea [data-baseweb="textarea"] { height: auto !important; }
  .stTextArea textarea {
    field-sizing: content !important;
    height: auto !important;
    min-height: 5rem;
    max-height: 26rem;
  }
}
</style>"""


def autoheight(text: str, min_h: int = 80, max_h: int = 420, per_line: int = 80) -> int:
    """Rough pixel height that fits `text` (assumes wrapping at ~per_line chars)."""
    lines = 0
    for para in (text or "").split("\n"):
        lines += max(1, -(-len(para) // per_line))  # ceil division
    return max(min_h, min(max_h, 34 + 22 * lines))


def textarea_height(text: str, min_h: int = 80, max_h: int = 420, per_line: int = 80):
    """Height for a text area: the native "content" mode when available,
    otherwise a pixel height estimated from the current text."""
    if SUPPORTS_CONTENT_HEIGHT:
        return "content"
    return autoheight(text, min_h, max_h, per_line)

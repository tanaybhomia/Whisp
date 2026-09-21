"""Inline text color for notes, stored as ``<span style="color:#rrggbb">…</span>``.

Notes are plain Markdown files and the highlighter re-derives every text tag
from the raw text, so a color has to live in the text itself. Inline HTML keeps
the file valid Markdown, needs no migration, and notes without colors are
untouched. Malformed or unknown spans are left alone as ordinary text.

Everything here is pure string logic (no GTK) so it can be unit tested.
"""

import re
from collections import namedtuple

# GNOME HIG palette, mid tones that stay legible on light and dark pages.
PALETTE = (
    ("#e01b24", "Red"),
    ("#ff7800", "Orange"),
    ("#e5a50a", "Yellow"),
    ("#2ec27e", "Green"),
    ("#3584e4", "Blue"),
    ("#9141ac", "Purple"),
    ("#986a44", "Brown"),
    ("#77767b", "Gray"),
)

_HEX_RE = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_SPAN_TAG_RE = re.compile(r"<span\b[^>]*>|</span\s*>", re.IGNORECASE)
_COLOR_OPEN_RE = re.compile(
    r'^<span\s+style="color:\s*(#[0-9a-fA-F]{6})\s*;?\s*">$', re.IGNORECASE
)
# Markdown line prefix that must stay outside a span so the line still parses.
_LINE_PREFIX_RE = re.compile(r"^\s*(?:#{1,6}\s+|[-*+]\s+(?:\[[ xX]\]\s+)?|\d+\.\s+|[☐☑]\s*)?")

ColorSpan = namedtuple("ColorSpan", "open_start open_end close_start close_end color")
ColorEdit = namedtuple("ColorEdit", "start end replacement sel_start sel_end")


def normalize_color(value):
    """Return ``#rrggbb`` (lowercase) for a valid hex color, else None."""
    if not isinstance(value, str) or not _HEX_RE.match(value.strip()):
        return None
    value = value.strip().lower()
    if len(value) == 4:
        value = "#" + "".join(c * 2 for c in value[1:])
    return value


def open_tag(color):
    return f'<span style="color:{color}">'


CLOSE_TAG = "</span>"


def find_color_spans(text):
    """Balanced color spans in ``text``, ordered by opening position.

    Every ``<span>`` is tracked so a foreign ``</span>`` never closes one of
    ours; unbalanced tags are ignored.
    """
    spans = []
    stack = []
    for m in _SPAN_TAG_RE.finditer(text):
        if m.group(0)[1] != "/":
            color_m = _COLOR_OPEN_RE.match(m.group(0))
            stack.append((m, normalize_color(color_m.group(1)) if color_m else None))
        elif stack:
            open_m, color = stack.pop()
            if color:
                spans.append(ColorSpan(open_m.start(), open_m.end(), m.start(), m.end(), color))
    spans.sort(key=lambda s: s.open_start)
    return spans


def strip_color_markup(text):
    """Text with our color tags removed, for titles, excerpts and matching."""
    spans = find_color_spans(text)
    if not spans:
        return text
    cut = []
    for s in spans:
        cut.append((s.open_start, s.open_end))
        cut.append((s.close_start, s.close_end))
    cut.sort()
    out, pos = [], 0
    for a, b in cut:
        out.append(text[pos:a])
        pos = b
    out.append(text[pos:])
    return "".join(out)


def color_marker_ranges(text):
    """(start, end) of every color open/close tag, i.e. the text the editor hides."""
    ranges = []
    for s in find_color_spans(text):
        ranges.append((s.open_start, s.open_end))
        ranges.append((s.close_start, s.close_end))
    return sorted(ranges)


def plain_to_raw_offset(text, pos):
    """Map an offset in ``strip_color_markup(text)`` back to an offset in ``text``.

    Lets search work on the visible text while the editor still addresses the
    buffer, which holds the (hidden) markup.
    """
    raw = pos
    for a, b in color_marker_ranges(text):
        if a > raw:
            break
        raw += b - a
    return raw


def prune_empty_color_spans(text):
    """Drop spans with nothing inside (left behind when their text is deleted)."""
    while True:
        empty = [s for s in find_color_spans(text) if s.open_end == s.close_start]
        if not empty:
            return text
        for s in reversed(empty):
            text = text[:s.open_start] + text[s.close_end:]


def _flatten(text, spans, lo, hi):
    """Plain text of ``text[lo:hi]`` with per-character colors.

    Returns ``(plain, colors, raw_to_plain)``; ``raw_to_plain(pos)`` maps an
    absolute raw offset inside the region to an offset in ``plain``. Inner spans
    override the outer ones they sit in.
    """
    tags = []
    for s in spans:
        tags.append((s.open_start, s.open_end))
        tags.append((s.close_start, s.close_end))
    tags = sorted(t for t in tags if lo <= t[0] and t[1] <= hi)

    def raw_to_plain(pos):
        return pos - lo - sum(min(b, pos) - a for a, b in tags if a < pos)

    pieces, pos = [], lo
    for a, b in tags:
        pieces.append(text[pos:a])
        pos = b
    pieces.append(text[pos:hi])
    plain = "".join(pieces)

    colors = [None] * len(plain)
    for s in spans:  # sorted by open_start: outer first, inner overrides
        if s.open_start >= lo and s.close_end <= hi:
            for i in range(raw_to_plain(s.open_end), raw_to_plain(s.close_start)):
                colors[i] = s.color
    return plain, colors, raw_to_plain


def _region(text, spans, sel_start, sel_end):
    """Selection widened to whole lines and to every span it touches."""
    lo, hi = sel_start, sel_end
    while True:
        prev = (lo, hi)
        for s in spans:
            if s.open_start < hi and s.close_end > lo:
                lo, hi = min(lo, s.open_start), max(hi, s.close_end)
        lo = text.rfind("\n", 0, lo) + 1
        nl = text.find("\n", hi)
        hi = len(text) if nl == -1 else nl
        if (lo, hi) == prev:
            return lo, hi


def _prefix_indices(plain):
    """Indices of Markdown line prefixes (``# ``, ``- ``, ``☐`` …) in ``plain``."""
    indices, pos = set(), 0
    for line in plain.split("\n"):
        indices.update(range(pos, pos + _LINE_PREFIX_RE.match(line).end()))
        pos += len(line) + 1
    return indices


def selection_color(text, sel_start, sel_end):
    """The one color shared by every non-blank character selected, else None."""
    spans = find_color_spans(text)
    lo, hi = _region(text, spans, sel_start, sel_end)
    plain, colors, to_plain = _flatten(text, spans, lo, hi)
    a, b = to_plain(sel_start), to_plain(sel_end)
    seen = {colors[i] for i in range(a, b) if not plain[i].isspace()}
    return seen.pop() if len(seen) == 1 else None


def compute_color_edit(text, sel_start, sel_end, color):
    """Minimal text edit that sets (or with ``color=None`` clears) the color of a selection.

    Only the color spans on the touched lines are rewritten, one span per line,
    so other formatting is preserved and spans never straddle a paragraph.
    Returns a ColorEdit (replace ``text[start:end]``, then select
    ``sel_start:sel_end`` in the new text), or None if nothing is selected or
    the color is invalid.
    """
    if color is not None:
        color = normalize_color(color)
        if color is None:
            return None
    spans = find_color_spans(text)
    lo, hi = _region(text, spans, sel_start, sel_end)
    plain, colors, to_plain = _flatten(text, spans, lo, hi)
    a, b = to_plain(sel_start), to_plain(sel_end)
    if a >= b:
        return None

    prefix = _prefix_indices(plain) if color is not None else set()
    for i in range(a, b):
        colors[i] = None if plain[i] == "\n" or i in prefix else color

    out = []
    size = 0  # length of ``out`` joined so far
    raw_pos = []  # raw offset (relative to region) of each plain character
    open_len = {}  # plain index -> length of the open tag emitted before it
    close_len = {}  # plain index -> length of the close tag emitted after it
    i, n = 0, len(plain)
    while i < n:
        j = i + 1
        while j < n and colors[j] == colors[i] and plain[j] != "\n" and plain[i] != "\n":
            j += 1
        run = plain[i:j]
        if colors[i] and run.strip():
            tag = open_tag(colors[i])
            open_len[i] = len(tag)
            close_len[j - 1] = len(CLOSE_TAG)
            out += [tag, run, CLOSE_TAG]
            raw_pos.extend(range(size + len(tag), size + len(tag) + len(run)))
            size += len(tag) + len(run) + len(CLOSE_TAG)
        else:
            out.append(run)
            raw_pos.extend(range(size, size + len(run)))
            size += len(run)
        i = j
    new_region = "".join(out)
    raw_pos.append(len(new_region))

    new_a = raw_pos[a] - open_len.get(a, 0)
    new_b = raw_pos[b - 1] + 1 + close_len.get(b - 1, 0)

    # Shrink to the differing middle so the edit (and undo step) stays small.
    old_region = text[lo:hi]
    p = 0
    limit = min(len(old_region), len(new_region))
    while p < limit and old_region[p] == new_region[p]:
        p += 1
    q = 0
    while q < limit - p and old_region[-1 - q] == new_region[-1 - q]:
        q += 1
    return ColorEdit(
        start=lo + p,
        end=hi - q,
        replacement=new_region[p:len(new_region) - q],
        sel_start=lo + new_a,
        sel_end=lo + new_b,
    )


def apply_color_edit(text, edit):
    """Apply a ColorEdit to ``text`` (used by tests; the editor edits its buffer)."""
    return text[:edit.start] + edit.replacement + text[edit.end:]

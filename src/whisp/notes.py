import os
import re
from pathlib import Path

from whisp.text_color import strip_color_markup
from whisp.text_search import iter_body_match_offsets

TITLE_RE = re.compile(r"^#+\s*")
TAG_RE = re.compile(r"#(\w+)")
DEFAULT_TITLE = "New Note"


class NoteIndex:
    """Parse notes once and reuse the result until each file's mtime changes."""

    def __init__(self):
        self._cache = {}

    def load(self, path):
        path = Path(path)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return None
        cached = self._cache.get(path)
        if cached is not None and cached["mtime"] == mtime:
            return cached
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None  # missing or corrupt: skip the note rather than fail the listing
        # The visible text: titles, tags, search matches and snippets must not see
        # the hidden color markup. Uncolored notes get the same object back, not a copy.
        plain = strip_color_markup(content)
        first_line = plain.split("\n", 1)[0].strip()
        title = TITLE_RE.sub("", first_line) if first_line else DEFAULT_TITLE
        # A "#rrggbb" inside color markup is not a tag.
        tags = set(TAG_RE.findall(plain))
        low_content = content.lower()
        tag_str = " ".join(f"#{t}" for t in tags)
        entry = {
            "path": path,
            "mtime": mtime,
            "content": content,
            "low_content": low_content,
            "plain": plain,
            # Search text without color markup, so "span" never matches a colored note.
            "plain_low": plain.lower(),
            "title": title,
            "tag_str": tag_str,
            "blank": not content.strip(),
        }
        self._cache[path] = entry
        return entry

    def load_dir(self, data_dir):
        files = sorted(
            Path(data_dir).glob("*.md"),
            key=lambda f: os.path.getmtime(f) if f.exists() else 0,
            reverse=True,
        )
        entries = []
        for f in files:
            entry = self.load(f)
            if entry is not None and not entry["blank"]:
                entries.append(entry)
        return entries

    def iter_body_offsets(self, entry, term):
        """Offsets into ``entry["plain"]``, i.e. the note without color markup."""
        return iter_body_match_offsets(entry["plain"], term, entry["plain_low"])


def match_all_terms(entry, terms):
    """Determine whether all non-empty search terms match the note entry."""
    valid_terms = [t.lower().strip() for t in terms if t and t.strip()]
    if not valid_terms:
        return True

    content_low = entry.get("plain_low", entry["low_content"])
    tag_str_low = entry.get("tag_str", "").lower()
    full_query = " ".join(valid_terms)

    if full_query in content_low or full_query in tag_str_low:
        return True

    return all((t in content_low or t in tag_str_low) for t in valid_terms)


def first_match_offset(content, low_content, terms):
    """Offset of the first occurrence of any term, or -1 if none."""
    best = -1
    for term in terms:
        if not term:
            continue
        i = low_content.find(term.lower())
        if i != -1 and (best == -1 or i < best):
            best = i
    return best


def build_snippet(content, term, idx, pre=12, post=60):
    """Plain-text snippet around a match, for use as a result description."""
    start = max(0, idx - pre)
    end = min(len(content), idx + len(term) + post)
    snippet = re.sub(r"\s+", " ", content[start:end]).strip()
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(content) else ""
    return prefix + snippet + suffix


def body_excerpt(content, max_len=120):
    """First body line(s) collapsed onto one line; empty if only a title."""
    body = content.split("\n", 1)[1] if "\n" in content else ""
    text = re.sub(r"\s+", " ", strip_color_markup(body)).strip()
    if len(text) > max_len:
        text = text[:max_len].rstrip() + "…"
    return text


def sidebar_entries(entries, query, is_pinned):
    """Entries for the sidebar list: filtered by ``query``, pinned notes first.

    ``entries`` arrive newest-first (see NoteIndex.load_dir) and keep that
    order within the pinned and unpinned groups.
    """
    terms = query.split()
    matching = [e for e in entries if match_all_terms(e, terms)]
    return sorted(matching, key=lambda e: not is_pinned(e["path"].name))

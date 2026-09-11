import os
import re
from pathlib import Path

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
        except OSError:
            return None
        first_line = content.split("\n", 1)[0].strip()
        title = TITLE_RE.sub("", first_line) if first_line else DEFAULT_TITLE
        tags = set(TAG_RE.findall(content))
        low_content = content.lower()
        tag_str = " ".join(f"#{t}" for t in tags)
        entry = {
            "path": path,
            "mtime": mtime,
            "content": content,
            "low_content": low_content,
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
        return iter_body_match_offsets(entry["content"], term, entry["low_content"])


def match_all_terms(entry, terms):
    """Determine whether all non-empty search terms match the note entry."""
    valid_terms = [t.lower().strip() for t in terms if t and t.strip()]
    if not valid_terms:
        return True

    content_low = entry["low_content"]
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
    text = re.sub(r"\s+", " ", body).strip()
    if len(text) > max_len:
        text = text[:max_len].rstrip() + "…"
    return text

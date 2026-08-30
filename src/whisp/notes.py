import os
import re
from pathlib import Path

from whisp.text_search import iter_body_match_offsets

TITLE_RE = re.compile(r'^#+\s*')
TAG_RE = re.compile(r'#(\w+)')
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
            content = path.read_text(encoding='utf-8')
        except OSError:
            return None
        first_line = content.split('\n', 1)[0].strip()
        title = TITLE_RE.sub('', first_line) if first_line else DEFAULT_TITLE
        tags = set(TAG_RE.findall(content))
        entry = {
            "path": path,
            "mtime": mtime,
            "content": content,
            "low_content": content.lower(),
            "title": title,
            "tag_str": " ".join(f"#{t}" for t in tags),
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
    """True if terms match (supports exact, fzf-subsequence, and typo-fuzzy matching)."""
    import difflib
    
    query = " ".join(t for t in terms if t).lower()
    if not query:
        return True
        
    low = entry["low_content"]
    
    # 1. Exact substring match (Original fast behavior)
    if query in low:
        return True
        
    # 2. All terms exist somewhere in the document (Original fallback)
    if all(t.lower() in low for t in terms if t):
        return True
        
    # 3. FZF style subsequence match (letters appear in order anywhere)
    query_clean = query.replace(" ", "")
    it = iter(low)
    if all(c in it for c in query_clean):
        return True
        
    # 4. Typo tolerance on the title (using difflib)
    title_low = entry["title"].lower()
    q_len = len(query)
    t_len = len(title_low)
    
    # Ultra-fast math heuristic: Calculate the maximum possible ratio based purely on length difference.
    # If the max possible ratio is mathematically < 0.75, skip difflib entirely! (O(1) instead of O(N^2))
    max_ratio = (2.0 * min(q_len, t_len)) / (q_len + t_len) if (q_len + t_len) > 0 else 0
    if max_ratio > 0.75:
        if difflib.SequenceMatcher(None, query, title_low).ratio() > 0.75:
            return True
            
    # Same ultra-fast math heuristic for the space-stripped version
    title_clean = title_low.replace(" ", "")
    qc_len = len(query_clean)
    tc_len = len(title_clean)
    max_ratio_clean = (2.0 * min(qc_len, tc_len)) / (qc_len + tc_len) if (qc_len + tc_len) > 0 else 0
    if max_ratio_clean > 0.85:
        if difflib.SequenceMatcher(None, query_clean, title_clean).ratio() > 0.85:
            return True
            
    return False


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
    snippet = re.sub(r'\s+', ' ', content[start:end]).strip()
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(content) else ""
    return prefix + snippet + suffix


def body_excerpt(content, max_len=120):
    """First body line(s) collapsed onto one line; empty if only a title."""
    body = content.split('\n', 1)[1] if '\n' in content else ""
    text = re.sub(r'\s+', ' ', body).strip()
    if len(text) > max_len:
        text = text[:max_len].rstrip() + "…"
    return text
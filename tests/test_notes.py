import sys
import os
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from whisp.notes import (NoteIndex, match_all_terms, first_match_offset,
                         build_snippet, body_excerpt, sidebar_entries)


class TestNoteIndex(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.dir = self.tmpdir.name
        self.index = NoteIndex()

    def tearDown(self):
        self.tmpdir.cleanup()

    def write_note(self, name, content, mtime=None):
        path = os.path.join(self.dir, name)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def test_title_from_first_line(self):
        path = self.write_note("a.md", "# Groceries\nbuy milk")
        entry = self.index.load(path)
        self.assertEqual(entry["title"], "Groceries")

    def test_title_without_heading_marker(self):
        path = self.write_note("a.md", "Plain Title\nbody")
        entry = self.index.load(path)
        self.assertEqual(entry["title"], "Plain Title")

    def test_title_default_for_empty_title_line(self):
        path = self.write_note("a.md", "\njust a body")
        entry = self.index.load(path)
        self.assertEqual(entry["title"], "New Note")

    def test_tags_extracted(self):
        path = self.write_note("a.md", "Title #todo #work\nbody #home")
        entry = self.index.load(path)
        self.assertEqual(set(entry["tag_str"].split()), {"#todo", "#work", "#home"})

    def test_load_missing_file(self):
        self.assertIsNone(self.index.load(os.path.join(self.dir, "nope.md")))

    def test_cache_reused_until_mtime_changes(self):
        path = self.write_note("a.md", "Title\nbody")
        entry1 = self.index.load(path)
        self.assertTrue(entry1 is self.index.load(path))
        os.utime(path, (os.path.getmtime(path) + 10,) * 2)
        entry2 = self.index.load(path)
        self.assertIsNot(entry1, entry2)

    def test_load_dir_skips_blank_and_sorts_by_mtime(self):
        self.write_note("old.md", "Old\nbody", mtime=100)
        self.write_note("new.md", "New\nbody", mtime=200)
        self.write_note("blank.md", "   \n", mtime=300)
        entries = self.index.load_dir(self.dir)
        self.assertEqual([e["title"] for e in entries], ["New", "Old"])

    def test_iter_body_offsets_skips_title(self):
        path = self.write_note("a.md", "Title\nword word")
        entry = self.index.load(path)
        self.assertEqual(list(self.index.iter_body_offsets(entry, "word")), [6, 11])


class TestMatching(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.index = NoteIndex()
        path = os.path.join(self.tmpdir.name, "a.md")
        with open(path, 'w', encoding='utf-8') as f:
            f.write("# Grocery List\nbuy milk and eggs\n")
        self.entry = self.index.load(path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_all_terms_must_match(self):
        self.assertTrue(match_all_terms(self.entry, ["grocery", "milk"]))
        self.assertFalse(match_all_terms(self.entry, ["grocery", "pizza"]))

    def test_matching_is_case_insensitive(self):
        self.assertTrue(match_all_terms(self.entry, ["GROCERY", "Milk"]))

    def test_matches_title_and_tags(self):
        self.assertTrue(match_all_terms(self.entry, ["grocery"]))
        self.assertTrue(match_all_terms(self.entry, ["list"]))

    def test_empty_terms_never_block(self):
        self.assertTrue(match_all_terms(self.entry, ["milk", ""]))

    def test_first_match_offset(self):
        entry = self.entry
        low = entry["low_content"]
        self.assertEqual(first_match_offset(entry["content"], low, ["milk"]), 19)
        self.assertEqual(first_match_offset(entry["content"], low, ["nope"]), -1)

    def test_build_snippet(self):
        content = "a b c d e f g h i j k l m n o p q r s milk t u v w x y z"
        snippet = build_snippet(content, "milk", 38)
        self.assertIn("milk", snippet)
        self.assertTrue(snippet.startswith("…"))
        self.assertEqual(build_snippet("milk at start", "milk", 0), "milk at start")

    def test_body_excerpt(self):
        self.assertEqual(body_excerpt("# T\nhello world"), "hello world")
        self.assertEqual(body_excerpt("# T"), "")


    def test_unrelated_fuzzy_queries_do_not_match(self):
        self.assertFalse(match_all_terms(self.entry, ["cat"]))
        self.assertFalse(match_all_terms(self.entry, ["dog"]))
        self.assertFalse(match_all_terms(self.entry, ["pizza"]))
        self.assertFalse(match_all_terms(self.entry, ["unrelatedquery"]))

    def test_typo_and_prefix_matching(self):
        self.assertTrue(match_all_terms(self.entry, ["groc"]))
        self.assertFalse(match_all_terms(self.entry, ["grocry"]))

class TestSidebarAndColors(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.dir = self.tmpdir.name
        self.index = NoteIndex()

    def tearDown(self):
        self.tmpdir.cleanup()

    def write(self, name, content, mtime):
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        os.utime(path, (mtime, mtime))
        return path

    def test_title_and_excerpt_ignore_color_markup(self):
        colored = '<span style="color:#e01b24">Big News</span>\nsome <span style="color:#3584e4">blue</span> body'
        entry = self.index.load(self.write("a.md", colored, 100))
        self.assertEqual(entry["title"], "Big News")
        self.assertEqual(body_excerpt(colored), "some blue body")

    def test_color_markup_is_not_searchable(self):
        entry = self.index.load(self.write("a.md", '<span style="color:#e01b24">hello</span>', 100))
        self.assertTrue(match_all_terms(entry, ["hello"]))
        self.assertFalse(match_all_terms(entry, ["span"]))

    def test_sidebar_entries_sort_and_filter(self):
        self.write("old.md", "Old note", 100)
        self.write("new.md", "New note", 300)
        self.write("pinned.md", "Pinned note", 200)
        entries = self.index.load_dir(self.dir)

        def names(es):
            return [e["path"].name for e in es]

        def pinned(name):
            return name == "old.md"

        self.assertEqual(names(sidebar_entries(entries, "", pinned)), ["old.md", "new.md", "pinned.md"])
        self.assertEqual(names(sidebar_entries(entries, "  NOTE  pinned ", pinned)), ["pinned.md"])
        self.assertEqual(names(sidebar_entries(entries, "nothing", pinned)), [])

    def test_search_does_not_modify_notes(self):
        path = self.write("a.md", "keep me", 100)
        sidebar_entries(self.index.load_dir(self.dir), "keep", lambda n: False)
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "keep me")

    def test_corrupt_note_is_skipped_not_fatal(self):
        with open(os.path.join(self.dir, "bad.md"), "wb") as f:
            f.write(b"\xff\xfe\x00garbage\xc3\x28")
        self.write("good.md", "fine", 100)
        entries = self.index.load_dir(self.dir)
        self.assertEqual([e["path"].name for e in entries], ["good.md"])

    def test_empty_directory(self):
        self.assertEqual(sidebar_entries(self.index.load_dir(self.dir), "", lambda n: False), [])

    def test_body_offsets_are_in_plain_space(self):
        # The Ctrl+F list builds snippets from entry["plain"] with these offsets,
        # so a colored match must not point into the hidden markup.
        content = 'Title\nplain hit and a <span style="color:#e01b24">hit</span> here'
        entry = self.index.load(self.write("a.md", content, 100))
        offsets = list(self.index.iter_body_offsets(entry, "hit"))
        self.assertEqual([entry["plain"][i:i + 3] for i in offsets], ["hit", "hit"])
        self.assertEqual(entry["plain"], "Title\nplain hit and a hit here")

    def test_markup_words_yield_no_body_offsets(self):
        entry = self.index.load(self.write("a.md", 'T\n<span style="color:#e01b24">x</span>', 100))
        self.assertEqual(list(self.index.iter_body_offsets(entry, "span")), [])
        self.assertEqual(list(self.index.iter_body_offsets(entry, "color")), [])

    def test_hex_color_in_markup_is_not_a_tag(self):
        entry = self.index.load(self.write("a.md", 'T\n<span style="color:#e01b24">x</span> #real', 100))
        self.assertEqual(entry["tag_str"], "#real")
        self.assertFalse(match_all_terms(entry, ["e01b24"]))

    def test_uncolored_note_shares_content_object(self):
        entry = self.index.load(self.write("a.md", "T\nno markup here", 100))
        self.assertIs(entry["plain"], entry["content"])


if __name__ == '__main__':
    unittest.main()
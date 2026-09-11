import sys
import os
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from whisp.notes import (NoteIndex, match_all_terms, first_match_offset,
                         build_snippet, body_excerpt)


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

if __name__ == '__main__':
    unittest.main()
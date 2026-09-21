import os
import tempfile
import unittest
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk

from whisp.notes import NoteIndex
from whisp.sidebar import NotesSidebar


class TestSidebar(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Gtk.init()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.index = NoteIndex()
        self.pinned = set()
        self.opened = []
        self.deleted = []
        self.sidebar = NotesSidebar(
            get_entries=lambda: self.index.load_dir(self.dir),
            is_pinned=lambda name: name in self.pinned,
            on_open=self.opened.append,
            on_delete=self.deleted.append,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, text, mtime):
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        os.utime(path, (mtime, mtime))
        return path

    def names(self):
        out, row = [], self.sidebar.listbox.get_first_child()
        while row:
            out.append(row.file_path.name)
            row = row.get_next_sibling()
        return out

    def test_empty_directory_shows_empty_state(self):
        self.sidebar.refresh()
        self.assertEqual(self.names(), [])
        self.assertEqual(self.sidebar.stack.get_visible_child_name(), "empty")
        self.assertEqual(self.sidebar.empty_page.get_title(), "No Notes")

    def test_lists_notes_newest_first_with_titles(self):
        self.write("old.md", "# Old\nbody", 100)
        self.write("new.md", "New title\nbody", 200)
        self.sidebar.refresh()
        self.assertEqual(self.names(), ["new.md", "old.md"])
        self.assertEqual(self.sidebar.stack.get_visible_child_name(), "list")

    def test_pinned_first(self):
        self.write("a.md", "a", 100)
        self.write("b.md", "b", 200)
        self.pinned.add("a.md")
        self.sidebar.refresh()
        self.assertEqual(self.names(), ["a.md", "b.md"])

    def test_search_filters_without_touching_files(self):
        path = self.write("a.md", "apples", 100)
        self.write("b.md", "bananas", 200)
        self.sidebar.refresh()
        self.sidebar.search_entry.set_text("apple")
        self.sidebar.refresh()
        self.assertEqual(self.names(), ["a.md"])
        self.sidebar.search_entry.set_text("zzz")
        self.sidebar.refresh()
        self.assertEqual(self.sidebar.empty_page.get_title(), "No Results")
        self.assertEqual(path.read_text(encoding="utf-8"), "apples")

    def test_selection_and_activation(self):
        a = self.write("a.md", "a", 100)
        b = self.write("b.md", "b", 200)
        self.sidebar.refresh()
        self.sidebar.select_path(a)
        self.assertEqual(self.sidebar.listbox.get_selected_row().file_path, a)
        self.sidebar.select_path(None)
        self.assertIsNone(self.sidebar.listbox.get_selected_row())
        self.sidebar.listbox.emit("row-activated", self.sidebar._rows[b])
        self.assertEqual(self.opened, [b])

    def test_selection_survives_rebuild(self):
        a = self.write("a.md", "a", 100)
        self.sidebar.refresh()
        self.sidebar.select_path(a)
        self.write("b.md", "b", 200)
        self.sidebar.refresh()
        self.assertEqual(self.sidebar.listbox.get_selected_row().file_path, a)

    def test_deleted_file_disappears(self):
        a = self.write("a.md", "a", 100)
        self.write("b.md", "b", 200)
        self.sidebar.refresh()
        a.unlink()
        self.sidebar.refresh()
        self.assertEqual(self.names(), ["b.md"])

    def test_long_title_is_ellipsized(self):
        self.write("a.md", "# " + "very long title " * 20, 100)
        self.sidebar.refresh()
        row = self.sidebar._rows[self.dir / "a.md"]
        label = row.get_child().get_first_child().get_first_child()
        self.assertEqual(label.get_ellipsize().value_nick, "end")

    def test_many_notes_are_paged_and_selectable(self):
        for i in range(NotesSidebar.PAGE_SIZE * 2 + 5):
            self.write(f"n{i:04d}.md", f"note {i}", 1000 + i)
        self.sidebar.refresh()
        self.assertEqual(len(self.names()), NotesSidebar.PAGE_SIZE)
        oldest = self.dir / "n0000.md"
        self.sidebar.select_path(oldest, reveal=False)  # forces the page holding it
        self.assertIn(oldest, self.sidebar._rows)
        self.assertEqual(self.sidebar.listbox.get_selected_row().file_path, oldest)

    def test_rows_have_accessible_labels(self):
        self.write("a.md", "Hello\nbody", 100)
        self.pinned.add("a.md")
        self.sidebar.refresh()
        row = self.sidebar._rows[self.dir / "a.md"]
        self.assertEqual(row.get_tooltip_text(), "Hello")


if __name__ == "__main__":
    unittest.main()

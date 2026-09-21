import tempfile
import unittest
from pathlib import Path
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk
from whisp.editor import NoteEditor

class TestEditor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Gtk.init()

    def test_im_context_attached(self):
        editor = NoteEditor()
        controllers = editor.textview.observe_controllers()
        im_ctrls = []
        for i in range(controllers.get_n_items()):
            c = controllers.get_item(i)
            if isinstance(c, Gtk.EventControllerKey) and c.get_im_context() is not None:
                im_ctrls.append(c)
        
        self.assertGreaterEqual(len(im_ctrls), 2, "Bubble key controller should share textview IMContext")


    def test_toggle_empty_checkbox_removes_prefix(self):
        editor = NoteEditor()
        editor.buffer.set_text("☐ ")
        editor.toggle_checkbox()
        text = editor.buffer.get_text(editor.buffer.get_start_iter(), editor.buffer.get_end_iter(), False)
        self.assertEqual(text, "", "Empty checkbox line should revert to normal empty line on toggle")

    def test_toggle_checkbox_with_text(self):
        editor = NoteEditor()
        editor.buffer.set_text("☐ Buy milk")
        editor.toggle_checkbox()
        text = editor.buffer.get_text(editor.buffer.get_start_iter(), editor.buffer.get_end_iter(), False)
        self.assertEqual(text, "☑ Buy milk", "Checkbox with text should toggle between unchecked and checked")


    def test_auto_convert_markdown_checkbox(self):
        editor = NoteEditor()
        editor.buffer.set_text("- [ ] Task")
        text = editor.buffer.get_text(editor.buffer.get_start_iter(), editor.buffer.get_end_iter(), False)
        self.assertEqual(text, "☐ Task", "Markdown - [ ] should automatically convert to unicode ☐ checkbox")

    def test_slate_mode_mouse_motion_with_banner(self):
        from gi.repository import Adw
        app = Adw.Application(application_id="test.slate.app")
        test_self = self
        def on_activate(app):
            from whisp.window import WhispWindow
            win = WhispWindow(application=app)
            win.is_slate_mode = True
            win.update_banner = Adw.Banner(title="Test", button_label="Click")
            win.update_banner.set_revealed(True)
            win.toolbar_view.add_top_bar(win.update_banner)
            win.toolbar_view.set_reveal_top_bars(True)
            
            win.on_mouse_motion(None, 0, 60)
            test_self.assertTrue(win.toolbar_view.get_reveal_top_bars(), "Top bars should stay revealed over banner area in Slate Mode")
            
            win.on_mouse_motion(None, 0, 150)
            test_self.assertFalse(win.toolbar_view.get_reveal_top_bars(), "Top bars should hide when cursor moves below top bars area")
            app.quit()
        app.connect("activate", on_activate)
        app.run([])

class TestTextColor(unittest.TestCase):
    RED = "#e01b24"
    BLUE = "#3584e4"

    @classmethod
    def setUpClass(cls):
        Gtk.init()

    def make_editor(self, text, path=None):
        editor = NoteEditor(file_path=path)
        editor.buffer.set_text(text)
        editor.highlighter.highlight()
        return editor

    def text_of(self, editor):
        return editor.buffer.get_text(*editor.buffer.get_bounds(), True)

    def select(self, editor, needle):
        start = self.text_of(editor).index(needle)
        editor.buffer.select_range(
            editor.buffer.get_iter_at_offset(start), editor.buffer.get_iter_at_offset(start + len(needle))
        )

    def tags_at(self, editor, offset):
        return {t.props.name for t in editor.buffer.get_iter_at_offset(offset).get_tags()}

    def test_color_applies_only_to_selection(self):
        editor = self.make_editor("This is important text.")
        self.select(editor, "important")
        editor.apply_text_color(self.RED)
        editor.highlighter.highlight()
        text = self.text_of(editor)
        self.assertEqual(text, f'This is <span style="color:{self.RED}">important</span> text.')
        self.assertIn(f"color_{self.RED}", self.tags_at(editor, text.index("important")))
        self.assertNotIn(f"color_{self.RED}", self.tags_at(editor, 0))
        self.assertNotIn(f"color_{self.RED}", self.tags_at(editor, text.index(" text.") + 1))
        self.assertIn("invisible", self.tags_at(editor, text.index("<span")))
        self.assertIn("invisible", self.tags_at(editor, text.index("</span>")))

    def test_color_keeps_bold_and_removal_keeps_bold(self):
        editor = self.make_editor("say **loud** now")
        self.select(editor, "**loud**")
        editor.apply_text_color(self.RED)
        editor.highlighter.highlight()
        text = self.text_of(editor)
        tags = self.tags_at(editor, text.index("loud"))
        self.assertTrue({"bold", f"color_{self.RED}"} <= tags)
        editor.apply_text_color(None)
        editor.highlighter.highlight()
        self.assertEqual(self.text_of(editor), "say **loud** now")
        self.assertIn("bold", self.tags_at(editor, self.text_of(editor).index("loud")))

    def test_undo_and_redo_are_one_step(self):
        editor = self.make_editor("one two three")
        editor.buffer.set_enable_undo(True)
        self.select(editor, "two")
        editor.apply_text_color(self.BLUE)
        colored = self.text_of(editor)
        self.assertIn("span", colored)
        editor.buffer.undo()
        self.assertEqual(self.text_of(editor), "one two three")
        editor.buffer.redo()
        self.assertEqual(self.text_of(editor), colored)

    def test_search_highlight_outranks_color(self):
        editor = self.make_editor(f'<span style="color:{self.RED}">found</span>')
        color_tag = editor.buffer.get_tag_table().lookup(f"color_{self.RED}")
        self.assertGreater(editor.highlighter.tag_search.get_priority(), color_tag.get_priority())

    def test_empty_selection_is_a_noop(self):
        editor = self.make_editor("abc")
        editor.buffer.place_cursor(editor.buffer.get_iter_at_offset(1))
        editor.apply_text_color(self.RED)
        self.assertEqual(self.text_of(editor), "abc")
        self.assertFalse(editor.color_action.get_enabled())

    def test_action_enabled_with_selection(self):
        editor = self.make_editor("abc")
        self.select(editor, "b")
        self.assertTrue(editor.color_action.get_enabled())

    def test_color_survives_save_and_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "note.md"
            editor = self.make_editor("red and blue", path)
            self.select(editor, "red")
            editor.apply_text_color(self.RED)
            self.select(editor, "blue")
            editor.apply_text_color(self.BLUE)
            editor.save_file()
            saved = path.read_text(encoding="utf-8")
            self.assertEqual(
                saved,
                f'<span style="color:{self.RED}">red</span> and <span style="color:{self.BLUE}">blue</span>',
            )
            reopened = NoteEditor(file_path=path)
            reopened.highlighter.highlight()
            self.assertIn(f"color_{self.RED}", self.tags_at(reopened, saved.index("red")))
            self.assertIn(f"color_{self.BLUE}", self.tags_at(reopened, saved.index("blue")))
            self.assertEqual(reopened.get_title(), "red and blue")

    def test_multiline_selection(self):
        editor = self.make_editor("# Head\n- item\nplain")
        editor.buffer.select_range(*editor.buffer.get_bounds())
        editor.apply_text_color(self.RED)
        editor.highlighter.highlight()
        lines = self.text_of(editor).split("\n")
        self.assertTrue(lines[0].startswith("# <span"))
        self.assertTrue(lines[1].startswith("- <span"))
        self.assertTrue(lines[2].startswith("<span"))

    def test_uncolored_note_is_unchanged_on_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "note.md"
            editor = self.make_editor("# Plain\n\n**bold** text", path)
            editor.save_file()
            self.assertEqual(path.read_text(encoding="utf-8"), "# Plain\n\n**bold** text")

    def test_backspace_and_delete_never_split_hidden_tags(self):
        span = f'<span style="color:{self.RED}">'
        editor = self.make_editor(f"one {span}two</span> three")
        text = self.text_of(editor)
        # Delete at the end of the colored word removes the space after the hidden tag.
        editor.buffer.place_cursor(editor.buffer.get_iter_at_offset(text.index("two") + 3))
        self.assertTrue(editor.delete_past_color_markup(forward=True))
        self.assertEqual(self.text_of(editor), f"one {span}two</span>three")
        # Backspace at its start removes the space before the hidden tag.
        editor.buffer.place_cursor(editor.buffer.get_iter_at_offset(self.text_of(editor).index("two")))
        self.assertTrue(editor.delete_past_color_markup(forward=False))
        self.assertEqual(self.text_of(editor), f"one{span}two</span>three")

    def test_plain_backspace_is_left_to_gtk(self):
        editor = self.make_editor("abc")
        editor.buffer.place_cursor(editor.buffer.get_iter_at_offset(2))
        self.assertFalse(editor.delete_past_color_markup(forward=False))
        self.assertFalse(editor.delete_past_color_markup(forward=True))

    def test_empty_span_is_pruned_on_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "note.md"
            editor = self.make_editor(f'a <span style="color:{self.RED}"></span>b', path)
            editor.save_file()
            self.assertEqual(path.read_text(encoding="utf-8"), "a b")

    def test_invalid_color_is_ignored(self):
        editor = self.make_editor('<span style="color:notacolor">x</span> y')
        editor.highlighter.highlight()  # must not raise
        self.assertEqual(self.text_of(editor), '<span style="color:notacolor">x</span> y')


if __name__ == "__main__":
    unittest.main()

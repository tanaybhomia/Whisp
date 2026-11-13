import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from unittest.mock import MagicMock, patch
from gi.repository import GLib, Gtk
from whisp.editor import NoteEditor
from whisp.editor_helpers import editor_paste
from whisp.editor_helpers.editor_paste import strip_markdown

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

    def test_toggle_checkbox_cursor_remains_on_same_line(self):
        editor = NoteEditor()
        editor.buffer.set_text("☐ Buy milk\nSecond line")
        iter0 = editor.buffer.get_start_iter()
        editor.buffer.place_cursor(iter0)
        editor.toggle_checkbox()
        insert_iter = editor.buffer.get_iter_at_mark(editor.buffer.get_insert())
        self.assertEqual(insert_iter.get_line(), 0, "Cursor should remain on line 0 after toggling checkbox")


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
            banner = Adw.Banner(title="Test", button_label="Click")
            win.update_banner = banner
            banner.set_revealed(True)
            win.toolbar_view.add_top_bar(banner)
            win.toolbar_view.set_reveal_top_bars(True)
            
            win.on_mouse_motion(None, 0, 60)
            test_self.assertTrue(win.toolbar_view.get_reveal_top_bars(), "Top bars should stay revealed over banner area in Slate Mode")
            
            win.on_mouse_motion(None, 0, 150)
            test_self.assertFalse(win.toolbar_view.get_reveal_top_bars(), "Top bars should hide when cursor moves below top bars area")
            app.quit()
        app.connect("activate", on_activate)
        app.run([])

    def test_is_paste_note(self):
        editor = NoteEditor()
        editor.buffer.set_text("paste")
        self.assertTrue(editor.is_paste_note())

        editor.buffer.set_text(":paste")
        self.assertTrue(editor.is_paste_note())

        editor.buffer.set_text("# paste")
        self.assertTrue(editor.is_paste_note())

        editor.buffer.set_text("## :paste: my note")
        self.assertTrue(editor.is_paste_note())

        editor.buffer.set_text("Some normal note")
        self.assertFalse(editor.is_paste_note())

        editor.buffer.set_text("toothpaste")
        self.assertFalse(editor.is_paste_note())

    def test_paste_keyword_highlighting(self):
        editor = NoteEditor()
        editor.buffer.set_text("paste\nFirst item")
        editor.highlighter.highlight()
        start = editor.buffer.get_start_iter()
        tags = start.get_tags()
        tag_names = [t.get_property("name") for t in tags]
        self.assertIn("paste_keyword", tag_names)

    def test_start_and_stop_autopaste(self):
        editor = NoteEditor()
        self.assertFalse(editor.is_autopaste_active)

        editor.start_autopaste()
        self.assertTrue(editor.is_autopaste_active)
        toast = editor._autopaste_toast
        self.assertIsNotNone(toast)
        assert toast is not None
        self.assertEqual(toast.get_title(), "AutoPaste active")
        self.assertEqual(toast.get_button_label(), "Stop")

        editor.stop_autopaste(silent=True)
        self.assertFalse(editor.is_autopaste_active)
        self.assertIsNone(editor._autopaste_toast)

    def test_handle_paste_return_toggle(self):
        editor = NoteEditor()
        editor.buffer.set_text("paste")
        editor.buffer.place_cursor(editor.buffer.get_end_iter())

        # First return should activate AutoPaste and add a newline
        res = editor.handle_paste_return()
        self.assertTrue(res)
        self.assertTrue(editor.is_autopaste_active)
        text = editor.buffer.get_text(editor.buffer.get_start_iter(), editor.buffer.get_end_iter(), False)
        self.assertEqual(text, "paste\n")

        # Typing paste again on next line and pressing return should disable AutoPaste and remove the command
        end_iter = editor.buffer.get_end_iter()
        editor.buffer.insert(end_iter, "paste")
        editor.buffer.place_cursor(editor.buffer.get_end_iter())

        res2 = editor.handle_paste_return()
        self.assertTrue(res2)
        self.assertFalse(editor.is_autopaste_active)
        text2 = editor.buffer.get_text(editor.buffer.get_start_iter(), editor.buffer.get_end_iter(), False)
        self.assertEqual(text2, "paste\n")

    def test_autopaste_text_read_separates_instances_with_empty_line(self):
        class DummyClipboard:
            def __init__(self, text):
                self.text = text

            def read_text_finish(self, res):
                return self.text

        editor = NoteEditor()
        editor.buffer.set_text("paste\n")
        editor.start_autopaste()

        editor._on_autopaste_text_read(DummyClipboard("First copied line"), None)
        text1 = editor.buffer.get_text(editor.buffer.get_start_iter(), editor.buffer.get_end_iter(), False)
        self.assertEqual(text1, "paste\n\nFirst copied line\n")

        editor._on_autopaste_text_read(DummyClipboard("Second copied line"), None)
        text2 = editor.buffer.get_text(editor.buffer.get_start_iter(), editor.buffer.get_end_iter(), False)
        self.assertEqual(text2, "paste\n\nFirst copied line\n\nSecond copied line\n")

        # Duplicate copy should be ignored
        editor._on_autopaste_text_read(DummyClipboard("Second copied line"), None)
        text3 = editor.buffer.get_text(editor.buffer.get_start_iter(), editor.buffer.get_end_iter(), False)
        self.assertEqual(text3, "paste\n\nFirst copied line\n\nSecond copied line\n")

        editor.stop_autopaste(silent=True)

    def test_autopaste_middle_of_note(self):
        class DummyClipboard:
            def __init__(self, text):
                self.text = text

            def read_text_finish(self, res):
                return self.text

        editor = NoteEditor()
        editor.buffer.set_text("# Notes\nSome thoughts\n\npaste\n\n## Footer\nEnd of note")
        # Place cursor at end of 'paste' line
        paste_iter = editor.buffer.get_iter_at_offset(len("# Notes\nSome thoughts\n\npaste"))
        editor.buffer.place_cursor(paste_iter)

        res = editor.handle_paste_return()
        self.assertTrue(res)
        self.assertTrue(editor.is_autopaste_active)

        editor._on_autopaste_text_read(DummyClipboard("Middle clipped item"), None)
        full_text = editor.buffer.get_text(editor.buffer.get_start_iter(), editor.buffer.get_end_iter(), False)
        self.assertIn("paste\n\nMiddle clipped item\n", full_text)
        self.assertIn("## Footer\nEnd of note", full_text)

        editor.stop_autopaste(silent=True)

    def test_highlight_multiple_paste_keywords_in_note(self):
        editor = NoteEditor()
        editor.buffer.set_text(":paste\n\nFirst copied line\n\n:paste\n\nSecond copied line")
        editor.highlighter.highlight()

        # First :paste is tagged
        iter0 = editor.buffer.get_iter_at_offset(0)
        self.assertTrue(iter0.has_tag(editor.highlighter.tag_paste_keyword))

        # Body text below first :paste is not tagged
        content = editor.buffer.get_text(editor.buffer.get_start_iter(), editor.buffer.get_end_iter(), False)
        offset_body1 = content.find("First copied line")
        iter_body1 = editor.buffer.get_iter_at_offset(offset_body1)
        self.assertFalse(iter_body1.has_tag(editor.highlighter.tag_paste_keyword))

        # Second :paste is tagged
        offset2 = content.find("\n:paste") + 1
        iter2 = editor.buffer.get_iter_at_offset(offset2)
        self.assertTrue(iter2.has_tag(editor.highlighter.tag_paste_keyword))

        # Body text below second :paste is not tagged
        offset_body2 = content.find("Second copied line")
        iter_body2 = editor.buffer.get_iter_at_offset(offset_body2)
        self.assertFalse(iter_body2.has_tag(editor.highlighter.tag_paste_keyword))

    def test_autopaste_undo(self):
        class DummyClipboard:
            def __init__(self, text):
                self.text = text

            def read_text_finish(self, res):
                return self.text

        editor = NoteEditor()
        editor.buffer.set_text("paste\n")
        editor.start_autopaste()

        editor._on_autopaste_text_read(DummyClipboard("Undoable item"), None)
        self.assertIn("Undoable item", editor.buffer.get_text(editor.buffer.get_start_iter(), editor.buffer.get_end_iter(), False))

        if editor.buffer.get_can_undo():
            editor.buffer.undo()
            self.assertEqual(editor.buffer.get_text(editor.buffer.get_start_iter(), editor.buffer.get_end_iter(), False), "paste\n")

        editor.stop_autopaste(silent=True)

    def test_autopaste_titled_header_and_midline_return(self):
        editor = NoteEditor()
        editor.buffer.set_text("# paste: Research Links")
        mid_iter = editor.buffer.get_iter_at_offset(7)
        editor.buffer.place_cursor(mid_iter)

        handled = editor.handle_paste_return()
        self.assertTrue(handled)
        self.assertTrue(editor.is_autopaste_active)
        text = editor.buffer.get_text(editor.buffer.get_start_iter(), editor.buffer.get_end_iter(), False)
        self.assertEqual(text, "# paste: Research Links\n")

        # Titled line should not accidentally stop AutoPaste when active
        top_iter = editor.buffer.get_start_iter()
        editor.buffer.place_cursor(top_iter)
        self.assertFalse(editor.handle_paste_return())
        self.assertTrue(editor.is_autopaste_active)

        # Standalone paste command on end line stops AutoPaste
        end_iter = editor.buffer.get_end_iter()
        editor.buffer.insert(end_iter, "paste")
        editor.buffer.place_cursor(editor.buffer.get_end_iter())
        self.assertTrue(editor.handle_paste_return())
        self.assertFalse(editor.is_autopaste_active)

    def test_autopaste_activation_ensures_empty_line_above(self):
        editor = NoteEditor()
        editor.buffer.set_text("First paragraph.\n:paste")
        end_iter = editor.buffer.get_end_iter()
        editor.buffer.place_cursor(end_iter)

        handled = editor.handle_paste_return()
        self.assertTrue(handled)
        self.assertTrue(editor.is_autopaste_active)
        text = editor.buffer.get_text(editor.buffer.get_start_iter(), editor.buffer.get_end_iter(), False)
        self.assertEqual(text, "First paragraph.\n\n:paste\n")
        editor.stop_autopaste(silent=True)

    def test_autopaste_sync_and_ocr_dedup(self):
        class DummyFormats:
            def __init__(self, has_texture):
                self.has_texture = has_texture

            def contain_gtype(self, gtype):
                return self.has_texture

        class DummyBytes:
            def __init__(self, data):
                self._data = data

            def get_data(self):
                return self._data

        class DummyTexture:
            def __init__(self, payload):
                self._payload = payload

            def save_to_png_bytes(self):
                return DummyBytes(self._payload)

        class DummyClipboard:
            def __init__(self, payload=None, text=None):
                self.payload = payload
                self.text = text
                self._handler = None

            def connect(self, sig, cb):
                self._handler = cb
                return 1

            def disconnect(self, handler_id):
                self._handler = None

            def get_formats(self):
                return DummyFormats(self.payload is not None)

            def read_texture_async(self, cancellable, callback):
                callback(self, None)

            def read_texture_finish(self, res):
                return DummyTexture(self.payload)

            def read_text_async(self, cancellable, callback):
                callback(self, None)

            def read_text_finish(self, res):
                return self.text

        class ImmediateThread:
            def __init__(self, target=None, daemon=None):
                self._target = target

            def start(self):
                self._target()

        clip = DummyClipboard(payload=None, text="initial")
        fake_display = MagicMock()
        fake_display.get_clipboard.return_value = clip

        with patch.object(editor_paste.Gdk.Display, "get_default", return_value=fake_display), \
             patch.object(editor_paste, "HAS_OCR", True), \
             patch.object(editor_paste, "Image", MagicMock()), \
             patch.object(editor_paste, "extract_text_from_image", return_value="Captured OCR line") as mock_ocr, \
             patch.object(editor_paste.threading, "Thread", ImmediateThread):
            editor = NoteEditor()
            editor.buffer.set_text("paste\n")
            editor.start_autopaste()

            clip.payload = b"screenshot-bytes"
            editor.sync_autopaste_from_clipboard()
            while GLib.MainContext.default().pending():
                GLib.MainContext.default().iteration(False)

            self.assertEqual(mock_ocr.call_count, 1)
            full_text = editor.buffer.get_text(editor.buffer.get_start_iter(), editor.buffer.get_end_iter(), False)
            self.assertIn("Captured OCR line", full_text)

            # Refocus sync with identical screenshot must not run OCR or paste again
            editor.sync_autopaste_from_clipboard()
            while GLib.MainContext.default().pending():
                GLib.MainContext.default().iteration(False)
            self.assertEqual(mock_ocr.call_count, 1)

            editor.stop_autopaste(silent=True)

    def test_strip_markdown_full(self):
        md = (
            "# Title ###\n"
            "> > Nested quote\n"
            "```python\n"
            "def f(x_val):\n"
            "    return x_val\n"
            "```\n"
            "- [x] Done task\n"
            "1. First item\n"
            "Text with **bold**, _italic_, `code`, ~~old~~, ==mark==, "
            "![alt](https://example.com/a.png), [link][ref], <https://whisp.app> and \\*escaped\\*.\n"
            "[ref]: https://example.com\n"
            "---\n"
            "<span>Inline HTML</span><br/>"
        )
        cleaned = strip_markdown(md)
        self.assertIn("Title\n", cleaned)
        self.assertIn("Nested quote\n", cleaned)
        self.assertIn("def f(x_val):", cleaned)
        self.assertIn("Done task\n", cleaned)
        self.assertIn("First item\n", cleaned)
        self.assertIn("Text with bold, italic, code, old, mark, alt, link, https://whisp.app and *escaped*.", cleaned)
        self.assertIn("Inline HTML", cleaned)
        self.assertNotIn("```", cleaned)
        self.assertNotIn("[ref]:", cleaned)
        self.assertNotIn("---", cleaned)
        self.assertNotIn("<span>", cleaned)

    def test_auto_renumber_ordered_lists_on_deletion(self):
        editor = NoteEditor()
        editor.buffer.set_text("1. First\n2. Second\n3. Third")
        _, start = editor.buffer.get_iter_at_line(1)
        _, end = editor.buffer.get_iter_at_line(2)
        editor.buffer.delete(start, end)
        text = editor.buffer.get_text(editor.buffer.get_start_iter(), editor.buffer.get_end_iter(), False)
        self.assertEqual(text, "1. First\n2. Third")

    def test_auto_renumber_ordered_lists_on_first_item_deletion(self):
        editor = NoteEditor()
        editor.buffer.set_text("1. First\n2. Second\n3. Third")
        _, start = editor.buffer.get_iter_at_line(0)
        _, end = editor.buffer.get_iter_at_line(1)
        editor.buffer.delete(start, end)
        text = editor.buffer.get_text(editor.buffer.get_start_iter(), editor.buffer.get_end_iter(), False)
        self.assertEqual(text, "1. Second\n2. Third")

    def test_auto_renumber_ordered_lists_on_insert(self):
        editor = NoteEditor()
        editor.buffer.set_text("1. First\n2. Second")
        _, insert_pos = editor.buffer.get_iter_at_line(1)
        editor.buffer.insert(insert_pos, "2. \n")
        text = editor.buffer.get_text(editor.buffer.get_start_iter(), editor.buffer.get_end_iter(), False)
        self.assertEqual(text, "1. First\n2. \n3. Second")

    def test_auto_renumber_nested_sublists(self):
        editor = NoteEditor()
        editor.buffer.set_text("1. Parent 1\n    1. Child 1\n    2. Child 2\n2. Parent 2")
        _, start = editor.buffer.get_iter_at_line(1)
        _, end = editor.buffer.get_iter_at_line(2)
        editor.buffer.delete(start, end)
        text = editor.buffer.get_text(editor.buffer.get_start_iter(), editor.buffer.get_end_iter(), False)
        self.assertEqual(text, "1. Parent 1\n    1. Child 2\n2. Parent 2")

    def test_auto_renumber_ignores_code_blocks(self):
        editor = NoteEditor()
        editor.buffer.set_text("1. First\n```python\n1. Inside\n2. Inside\n```\n3. Second")
        _, start = editor.buffer.get_iter_at_line(0)
        _, end = editor.buffer.get_iter_at_line(1)
        editor.buffer.delete(start, end)
        text = editor.buffer.get_text(editor.buffer.get_start_iter(), editor.buffer.get_end_iter(), False)
        self.assertEqual(text, "```python\n1. Inside\n2. Inside\n```\n1. Second")

    def test_auto_renumber_loose_lists_and_resets(self):
        editor = NoteEditor()
        editor.buffer.set_text("1. First\n\n3. Second\n\n\n5. Third")
        editor.auto_renumber_ordered_lists()
        text = editor.buffer.get_text(editor.buffer.get_start_iter(), editor.buffer.get_end_iter(), False)
        self.assertEqual(text, "1. First\n\n2. Second\n\n\n1. Third")

    def test_auto_renumber_undo(self):
        editor = NoteEditor()
        editor.buffer.set_text("1. First\n2. Second\n3. Third")
        editor.buffer.begin_user_action()
        _, start = editor.buffer.get_iter_at_line(1)
        _, end = editor.buffer.get_iter_at_line(2)
        editor.buffer.delete(start, end)
        editor.buffer.end_user_action()
        text = editor.buffer.get_text(editor.buffer.get_start_iter(), editor.buffer.get_end_iter(), False)
        self.assertEqual(text, "1. First\n2. Third")
        editor.buffer.undo()
        text_undo = editor.buffer.get_text(editor.buffer.get_start_iter(), editor.buffer.get_end_iter(), False)
        self.assertEqual(text_undo, "1. First\n2. Second\n3. Third")

    def test_auto_renumber_move_line(self):
        editor = NoteEditor()
        editor.buffer.set_text("1. First\n2. Second\n3. Third")
        _, iter_line1 = editor.buffer.get_iter_at_line(1)
        editor.buffer.place_cursor(iter_line1)
        res = editor.move_line(1)
        self.assertTrue(res)
        text = editor.buffer.get_text(editor.buffer.get_start_iter(), editor.buffer.get_end_iter(), False)
        self.assertEqual(text, "1. First\n2. Third\n3. Second")


if __name__ == "__main__":
    unittest.main()

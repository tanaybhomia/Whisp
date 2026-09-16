import unittest
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

if __name__ == "__main__":
    unittest.main()

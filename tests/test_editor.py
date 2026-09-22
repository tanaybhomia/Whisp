import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

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

if __name__ == "__main__":
    unittest.main()

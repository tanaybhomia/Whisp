import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw
from whisp.dialogs.preferences import PreferencesDialog, ThemeSnippet, show_preferences_dialog
from whisp.config import config


class TestPreferences(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()

    def setUp(self):
        self.mock_window = MagicMock()
        self.mock_window.carousel.get_n_pages.return_value = 0
        self.dialog = PreferencesDialog(self.mock_window)

    def test_preferences_dialog_init(self):
        self.assertIsInstance(self.dialog, Adw.PreferencesDialog)
        self.assertEqual(self.dialog.parent_window, self.mock_window)

    def test_theme_snippet(self):
        snippet = ThemeSnippet("blank")
        self.assertEqual(snippet.theme_id, "blank")
        self.assertTrue(snippet.has_css_class("theme-snippet-btn"))

    def test_spacing_changed(self):
        dropdown = MagicMock()
        dropdown.get_selected_item().get_string.return_value = "1.5"
        self.dialog.on_spacing_changed(dropdown, None)
        self.assertEqual(config.get("line_spacing"), "1.5")
        self.mock_window.update_line_spacing.assert_called_once()

    def test_window_background_changed(self):
        dropdown = MagicMock()
        dropdown.get_selected.return_value = 1
        self.dialog.on_window_background_changed(dropdown, None)
        self.assertEqual(config.get("window_background"), "transparent")
        self.assertTrue(self.dialog.opacity_row.get_visible())
        self.mock_window.update_window_background.assert_called_once()

        dropdown.get_selected.return_value = 2
        self.dialog.on_window_background_changed(dropdown, None)
        self.assertEqual(config.get("window_background"), "opaque")
        self.assertFalse(self.dialog.opacity_row.get_visible())

    def test_window_opacity_changed(self):
        spin_button = MagicMock()
        spin_button.get_value.return_value = 0.6000000001
        self.dialog.on_window_opacity_changed(spin_button)
        self.assertEqual(config.get("window_background_opacity"), 0.6)
        self.mock_window.update_window_background.assert_called_once()

    def test_confirm_delete_changed(self):
        switch = MagicMock()
        switch.get_active.return_value = False
        self.dialog.on_confirm_delete_changed(switch, None)
        self.assertFalse(config.get("confirm_delete"))

    def test_startup_behavior_changed(self):
        dropdown = MagicMock()
        dropdown.get_selected.return_value = 1
        self.dialog.on_startup_behavior_changed(dropdown, None)
        self.assertEqual(config.get("startup_behavior"), "empty_note")

    def test_archive_days_changed(self):
        dropdown = MagicMock()
        dropdown.get_selected.return_value = 2
        self.dialog.on_archive_days_changed(dropdown, None)
        self.assertEqual(config.get("archive_days"), 30)

    def test_carousel_size_changed(self):
        dropdown = MagicMock()
        dropdown.get_selected.return_value = 1
        self.dialog.on_carousel_size_changed(dropdown, None)
        self.assertEqual(config.get("max_carousel_size"), 25)

    def test_show_preferences_dialog(self):
        dialog = show_preferences_dialog(self.mock_window)
        self.assertIsInstance(dialog, PreferencesDialog)

    def test_shortcut_reset(self):
        mock_btn = MagicMock()
        mock_shortcut_label = MagicMock()
        self.dialog.on_shortcut_reset(mock_btn, "win.new-note", mock_shortcut_label, mock_btn)
        mock_btn.set_visible.assert_called_with(False)
        mock_shortcut_label.set_accelerator.assert_called_once()

    def test_gtk_shortcut_label_fallback(self):
        import whisp.dialogs.preferences as pref_mod
        from gi.repository import Gtk
        orig_cls = pref_mod.ShortcutLabel
        try:
            pref_mod.ShortcutLabel = Gtk.ShortcutLabel
            dialog = PreferencesDialog(self.mock_window)
            self.assertIsInstance(dialog, Adw.PreferencesDialog)
        finally:
            pref_mod.ShortcutLabel = orig_cls


if __name__ == "__main__":
    unittest.main()

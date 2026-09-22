import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

import unittest
from unittest.mock import MagicMock
from whisp.config import config
from whisp.global_shortcuts import GlobalShortcutManager, gtk_to_portal_trigger, portal_trigger_to_gtk

class TestGlobalShortcuts(unittest.TestCase):
    def test_default_config_shortcut(self):
        self.assertIsNotNone(config.get("global_toggle_shortcut"))

    def test_trigger_conversions(self):
        self.assertEqual(gtk_to_portal_trigger("<Super>n"), "Super+n")
        self.assertEqual(portal_trigger_to_gtk("Super+n"), "<Super>n")
        self.assertEqual(gtk_to_portal_trigger("<Alt>n"), "Alt+n")
        self.assertEqual(portal_trigger_to_gtk("Alt+n"), "<Alt>n")

    def test_bind_shortcuts_calls_dbus(self):
        manager = GlobalShortcutManager(app=MagicMock())
        manager.session_handle = "/org/freedesktop/portal/desktop/session/whisp_test"
        manager.proxy = MagicMock()
        
        manager.bind_shortcuts(window=None)
        self.assertTrue(manager.proxy.call.called)

    def test_trigger_toggle_calls_app(self):
        app_mock = MagicMock()
        manager = GlobalShortcutManager(app=app_mock)
        res = manager._trigger_toggle()
        self.assertFalse(res)
        app_mock.toggle_visibility.assert_called_once()


    def test_preferences_dialog_creation(self):
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw
        app = Adw.Application(application_id="test.pref.app")
        def on_activate(app):
            from whisp.window import WhispWindow
            win = WhispWindow(application=app)
            win.on_preferences(None, None)
            app.quit()
        app.connect("activate", on_activate)
        app.run([])

if __name__ == "__main__":
    unittest.main()

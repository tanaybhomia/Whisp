import sys
import urllib.parse
from pathlib import Path

from gi.repository import Gio, GLib

from whisp.config import config, DATA_DIR
from whisp.notes import NoteIndex, match_all_terms, body_excerpt

BUS_NAME = "io.github.tanaybhomia.Whisp.SearchProvider"
OBJECT_PATH = "/io/github/tanaybhomia/Whisp/SearchProvider"
APP_BUS_NAME = "io.github.tanaybhomia.Whisp"
APP_OBJECT_PATH = "/io/github/tanaybhomia/Whisp"
APP_DESKTOP_ID = "io.github.tanaybhomia.Whisp.desktop"
ICON_NAME = "io.github.tanaybhomia.Whisp"

INTERFACE_XML = """
<node>
  <interface name="org.gnome.Shell.SearchProvider2">
    <method name="GetInitialResultSet">
      <arg type="as" direction="in" name="terms"/>
      <arg type="as" direction="out" name="results"/>
    </method>
    <method name="GetSubsearchResultSet">
      <arg type="as" direction="in" name="previous_results"/>
      <arg type="as" direction="in" name="terms"/>
      <arg type="as" direction="out" name="results"/>
    </method>
    <method name="GetResultMetas">
      <arg type="as" direction="in" name="ids"/>
      <arg type="aa{sv}" direction="out" name="metas"/>
    </method>
    <method name="ActivateResult">
      <arg type="s" direction="in" name="id"/>
      <arg type="as" direction="in" name="terms"/>
      <arg type="u" direction="in" name="timestamp"/>
    </method>
    <method name="LaunchSearch">
      <arg type="as" direction="in" name="terms"/>
      <arg type="u" direction="in" name="timestamp"/>
    </method>
  </interface>
</node>
"""


class SearchProviderService(Gio.Application):
    def __init__(self):
        super().__init__(application_id=BUS_NAME, flags=Gio.ApplicationFlags.IS_SERVICE)
        self.note_index = NoteIndex()

    def do_startup(self):
        Gio.Application.do_startup(self)
        self.hold()

    def do_dbus_register(self, connection, object_path):
        if not Gio.Application.do_dbus_register(self, connection, object_path):
            return False
        node_info = Gio.DBusNodeInfo.new_for_xml(INTERFACE_XML)
        connection.register_object(OBJECT_PATH, node_info.interfaces[0],
                                   self._on_method_call, None, None)
        return True

    def _on_method_call(self, connection, sender, object_path, interface_name,
                        method_name, parameters, invocation):
        try:
            if method_name == "GetInitialResultSet":
                terms = list(parameters[0])
                invocation.return_value(GLib.Variant("(as)", (self._search(terms),)))
            elif method_name == "GetSubsearchResultSet":
                previous = list(parameters[0])
                terms = list(parameters[1])
                invocation.return_value(GLib.Variant("(as)", (self._search(terms, previous),)))
            elif method_name == "GetResultMetas":
                ids = list(parameters[0])
                invocation.return_value(GLib.Variant("(aa{sv})", (self._result_metas(ids),)))
            elif method_name == "ActivateResult":
                self._activate_result(str(parameters[0]))
                invocation.return_value(None)
            elif method_name == "LaunchSearch":
                self._launch_search(list(parameters[0]))
                invocation.return_value(None)
        except Exception as e:
            invocation.return_dbus_error("org.freedesktop.DBus.Error.Failed", str(e))

    def _search(self, terms, previous=None):
        terms = [t.strip() for t in terms if t.strip()]
        if not terms:
            return []
        previous = set(previous) if previous is not None else None
        results = []
        config.load()
        for entry in self.note_index.load_dir(config.data_dir):
            if not match_all_terms(entry, terms):
                continue
            note_id = str(entry["path"])
            if previous is not None and note_id not in previous:
                continue
            results.append(note_id)
        return results

    def _result_metas(self, ids):
        icon = Gio.ThemedIcon.new(ICON_NAME).serialize()
        metas = []
        for note_id in ids:
            path = self._resolve_id(note_id)
            entry = self.note_index.load(path) if path else None
            if entry is None:
                name = Path(note_id).name
                description = ""
            else:
                name = entry["title"]
                description = body_excerpt(entry["content"]) or entry["tag_str"]
            metas.append({
                "id": GLib.Variant("s", note_id),
                "name": GLib.Variant("s", name),
                "description": GLib.Variant("s", description),
                "icon": icon,
            })
        return metas

    def _activate_result(self, note_id):
        path = self._resolve_id(note_id)
        if path is None:
            return
        self._open_in_app(path.as_uri())

    def _launch_search(self, terms):
        terms = [t for t in terms if t.strip()]
        if not terms:
            self._open_in_app(None)
            return
        uri = "whisp://search?q=" + urllib.parse.quote(" ".join(terms))
        self._open_in_app(uri)

    def _open_in_app(self, uri):
        # D-Bus activation starts the app if it is not running; the app's
        # do_open handles both file URIs and whisp://search URIs.
        if uri is not None:
            params = GLib.Variant("(asa{sv})", ([uri], {}))
            method = "Open"
        else:
            params = GLib.Variant("(a{sv})", ({},))
            method = "Activate"
        try:
            conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            conn.call(APP_BUS_NAME, APP_OBJECT_PATH, "org.freedesktop.Application",
                      method, params, None, Gio.DBusCallFlags.NONE, -1, None,
                      self._on_open_reply)
        except GLib.Error:
            self._fallback_launch()

    def _on_open_reply(self, connection, result):
        try:
            connection.call_finish(result)
        except GLib.Error:
            self._fallback_launch()

    def _fallback_launch(self):
        # Defensive fallback for installs without the app D-Bus service file.
        try:
            appinfo = Gio.DesktopAppInfo.new(APP_DESKTOP_ID)
            if appinfo:
                appinfo.launch([], None)
        except GLib.Error:
            pass

    def _resolve_id(self, note_id):
        try:
            path = Path(note_id).resolve()
        except (OSError, ValueError):
            return None
        config.load()
        data_dir = Path(config.data_dir).resolve()
        if path.parent != data_dir or path.suffix != ".md" or not path.is_file():
            return None
        return path


def main():
    if '--search-provider' in sys.argv:
        sys.argv.remove('--search-provider')
    app = SearchProviderService()
    return app.run(sys.argv)


if __name__ == '__main__':
    sys.exit(main())
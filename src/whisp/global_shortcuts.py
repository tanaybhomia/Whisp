import logging
from gi.repository import Gio, GLib
from whisp.config import config

logger = logging.getLogger(__name__)

PORTAL_BUS_NAME = "org.freedesktop.portal.Desktop"
PORTAL_OBJECT_PATH = "/org/freedesktop/portal/desktop"
PORTAL_IFACE = "org.freedesktop.portal.GlobalShortcuts"
REQUEST_IFACE = "org.freedesktop.portal.Request"


def gtk_to_portal_trigger(accel):
    """Convert GTK accelerator string (e.g. <Super>n) to portal format (e.g. Super+n)."""
    if not accel:
        return ""
    s = accel.replace("<Primary>", "Control+").replace("<Ctrl>", "Control+")
    s = s.replace("<Super>", "Super+").replace("<Alt>", "Alt+").replace("<Shift>", "Shift+").replace("<Meta>", "Meta+")
    return s


def portal_trigger_to_gtk(trigger):
    """Convert portal trigger string (e.g. Super+n or Control+Alt+k) to GTK format (e.g. <Super>n or <Ctrl><Alt>k)."""
    if not trigger:
        return ""
    parts = trigger.split("+")
    key = parts[-1]
    accel = ""
    for p in parts[:-1]:
        mod = p
        if mod == "Control":
            mod = "Ctrl"
        accel += f"<{mod}>"
    accel += key
    return accel


def _parse_shortcut_trigger(info):
    if isinstance(info, dict):
        val = info.get("trigger_description") or info.get("trigger") or info.get("preferred_trigger") or ""
        if isinstance(val, GLib.Variant):
            return str(val.unpack())
        return str(val)
    return ""


def get_window_handle_str(window, callback):
    """Export Wayland or X11 window handle string for XDG portal calls."""
    if not window:
        callback("")
        return

    native = window.get_native() if hasattr(window, "get_native") else None
    surface = native.get_surface() if native else None
    if not surface:
        callback("")
        return

    try:
        from gi.repository import GdkWayland
        if isinstance(surface, GdkWayland.WaylandToplevel):
            def _on_wayland_exported(top, handle):
                callback(f"wayland:{handle}")
            surface.export_handle(_on_wayland_exported)
            return
    except (ImportError, AttributeError):
        pass

    try:
        from gi.repository import GdkX11
        if isinstance(surface, GdkX11.X11Surface):
            xid = surface.get_xid()
            callback(f"x11:{xid:x}")
            return
    except (ImportError, AttributeError):
        pass

    callback("")


def setup_gnome_gsettings_shortcut(accel):
    """Automated fallback: register keybinding directly in GNOME Settings GSettings."""
    try:
        schema_source = Gio.SettingsSchemaSource.get_default()
        if not schema_source or not schema_source.lookup("org.gnome.settings-daemon.plugins.media-keys", True):
            logger.info("GNOME media-keys GSettings schema not installed. Skipping GSettings shortcut setup.")
            return False
        if not schema_source.lookup("org.gnome.settings-daemon.plugins.media-keys.custom-keybinding", True):
            logger.info("GNOME custom-keybinding GSettings schema not installed. Skipping GSettings shortcut setup.")
            return False

        import shutil, sys, os
        from pathlib import Path

        main_py = (Path(__file__).parent / "main.py").resolve()
        if os.path.exists("/.flatpak-info"):
            command = "whisp --toggle"
        elif shutil.which("whisp") and not str(main_py).startswith("/home/"):
            command = "whisp --toggle"
        else:
            command = f"{sys.executable} {main_py} --toggle"

        settings = Gio.Settings.new("org.gnome.settings-daemon.plugins.media-keys")
        custom_list = list(settings.get_strv("custom-keybindings"))
        name = "Launch Whisp"

        target_path = None
        for path in custom_list:
            try:
                kb = Gio.Settings.new_with_path("org.gnome.settings-daemon.plugins.media-keys.custom-keybinding", path)
                if kb.get_string("name") == name:
                    target_path = path
                    break
            except Exception:
                pass

        if not target_path:
            idx = 0
            while f"/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom{idx}/" in custom_list:
                idx += 1
            target_path = f"/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom{idx}/"
            custom_list.append(target_path)
            settings.set_strv("custom-keybindings", custom_list)

        kb = Gio.Settings.new_with_path("org.gnome.settings-daemon.plugins.media-keys.custom-keybinding", target_path)
        kb.set_string("name", name)
        kb.set_string("command", command)
        kb.set_string("binding", accel if accel else "")
        logger.info(f"Registered GNOME shortcut via GSettings: {accel} -> {command}")
        return True
    except Exception as e:
        logger.warning(f"Could not auto-register GNOME GSettings keybinding: {e}")
        return False


class GlobalShortcutManager:
    """Manages system-wide global keybindings using XDG Portal & GNOME GSettings fallback."""

    def __init__(self, app):
        self.app = app
        self.bus = None
        self.proxy = None
        self.session_handle = None
        self.signal_subscription_id = None
        self.shortcuts_changed_sub_id = None
        self.ui_update_callback = None
        self.using_gsettings_fallback = False

    def update_shortcut(self, new_accel):
        """Update global toggle shortcut across config, GSettings, Portal, and UI."""
        config.set("global_toggle_shortcut", new_accel)
        setup_gnome_gsettings_shortcut(new_accel)
        if self.session_handle:
            self.bind_shortcuts()
        if self.ui_update_callback:
            self.ui_update_callback(new_accel)

    def start(self, window=None):
        try:
            self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            self.proxy = Gio.DBusProxy.new_sync(
                self.bus,
                Gio.DBusProxyFlags.NONE,
                None,
                PORTAL_BUS_NAME,
                PORTAL_OBJECT_PATH,
                PORTAL_IFACE,
                None
            )
            self._create_session(window)
        except Exception as e:
            logger.warning(f"Global Shortcuts portal initialization error: {e}")
            self._fallback_gsettings()

    def _fallback_gsettings(self):
        self.using_gsettings_fallback = True
        accel = config.get("global_toggle_shortcut", "<Super>n")
        setup_gnome_gsettings_shortcut(accel)

    def _create_session(self, window=None):
        if not self.proxy:
            self._fallback_gsettings()
            return
        token = f"whisp_sess_{GLib.get_monotonic_time()}"
        options = {
            "session_handle_token": GLib.Variant("s", "whisp_session_toggle"),
            "handle_token": GLib.Variant("s", token)
        }
        try:
            self.proxy.call(
                "CreateSession",
                GLib.Variant("(a{sv})", (options,)),
                Gio.DBusCallFlags.NONE,
                -1,
                None,
                lambda obj, res, *user_data: self._on_create_session_done(obj, res, window),
                None
            )
        except Exception as e:
            logger.warning(f"Failed to create Global Shortcuts portal session: {e}")
            self._fallback_gsettings()

    def _on_create_session_done(self, obj, res, window):
        if not self.bus:
            self._fallback_gsettings()
            return
        try:
            request_handle_variant = obj.call_finish(res)
            request_path = request_handle_variant.unpack()[0]
            self.bus.signal_subscribe(
                PORTAL_BUS_NAME,
                REQUEST_IFACE,
                "Response",
                request_path,
                None,
                Gio.DBusSignalFlags.NONE,
                lambda conn, sender, path, iface, sig, params, udata:
                    self._on_create_session_response(params, window),
                None
            )
        except Exception as e:
            logger.warning(f"CreateSession call failed: {e}")
            self._fallback_gsettings()

    def _on_create_session_response(self, parameters, window):
        response_code, results = parameters.unpack()
        if response_code == 0 and "session_handle" in results:
            self.session_handle = results["session_handle"]
            self._subscribe_signals()
            self.bind_shortcuts(window)
        else:
            logger.info("XDG Portal creation not allowed/supported in current environment. Using GNOME GSettings auto-registration.")
            self._fallback_gsettings()

    def bind_shortcuts(self, window=None):
        if not self.session_handle:
            self._fallback_gsettings()
            return

        def _do_bind(handle_str):
            if not self.proxy:
                self._fallback_gsettings()
                return
            pref_accel = config.get("global_toggle_shortcut", "<Super>n")
            portal_trigger = gtk_to_portal_trigger(pref_accel)

            shortcut_info = (
                "toggle-whisp",
                "Launch Whisp",
                {
                    "description": GLib.Variant("s", "Launch Whisp"),
                    "preferred_trigger": GLib.Variant("s", portal_trigger)
                }
            )

            token = f"whisp_bind_{GLib.get_monotonic_time()}"
            options = {
                "handle_token": GLib.Variant("s", token)
            }

            try:
                self.proxy.call(
                    "BindShortcuts",
                    GLib.Variant(
                        "(oa(ssa{sv})sa{sv})",
                        (self.session_handle, [shortcut_info], handle_str, options)
                    ),
                    Gio.DBusCallFlags.NONE,
                    -1,
                    None,
                    self._on_bind_shortcuts_done,
                    None
                )
            except Exception as e:
                logger.warning(f"Failed to bind global shortcuts: {e}")
                self._fallback_gsettings()

        get_window_handle_str(window, _do_bind)

    def _on_bind_shortcuts_done(self, obj, res, user_data):
        if not self.bus:
            return
        try:
            req_variant = obj.call_finish(res)
            req_path = req_variant.unpack()[0]
            sub_id = None
            def _on_response(conn, sender, path, iface, signal, params, udata):
                if sub_id and self.bus:
                    self.bus.signal_unsubscribe(sub_id)
                self._on_bind_shortcuts_response(conn, sender, path, iface, signal, params, udata)

            sub_id = self.bus.signal_subscribe(
                PORTAL_BUS_NAME,
                REQUEST_IFACE,
                "Response",
                req_path,
                None,
                Gio.DBusSignalFlags.NONE,
                _on_response,
                None
            )
        except Exception as e:
            logger.warning(f"BindShortcuts call finish error: {e}")

    def _on_bind_shortcuts_response(self, connection, sender, path, iface, signal, params, user_data):
        response_code, results = params.unpack()
        if response_code == 0 and "shortcuts" in results:
            shortcuts = results["shortcuts"]
            for shortcut_entry in shortcuts:
                if isinstance(shortcut_entry, (list, tuple)) and len(shortcut_entry) >= 2:
                    shortcut_id, info = shortcut_entry[0], shortcut_entry[1]
                    if shortcut_id == "toggle-whisp":
                        bound_trigger = _parse_shortcut_trigger(info)
                        if bound_trigger:
                            gtk_accel = portal_trigger_to_gtk(bound_trigger)
                            config.set("global_toggle_shortcut", gtk_accel)
                            if self.ui_update_callback:
                                GLib.idle_add(self.ui_update_callback, gtk_accel)
        else:
            self._fallback_gsettings()

    def configure_shortcuts(self, window=None):
        """Open native portal UI or auto-update GNOME shortcut."""
        if self.session_handle:
            def _do_configure(handle_str):
                if not self.proxy:
                    self._fallback_gsettings()
                    return
                token = f"whisp_cfg_{GLib.get_monotonic_time()}"
                options = {
                    "handle_token": GLib.Variant("s", token)
                }
                try:
                    self.proxy.call(
                        "ConfigureShortcuts",
                        GLib.Variant(
                            "(osa{sv})",
                            (self.session_handle, handle_str, options)
                        ),
                        Gio.DBusCallFlags.NONE,
                        -1,
                        None,
                        self._on_configure_shortcuts_done,
                        None
                    )
                except Exception as e:
                    logger.warning(f"ConfigureShortcuts portal call failed: {e}")
                    self._fallback_gsettings()

            get_window_handle_str(window, _do_configure)
        else:
            self._fallback_gsettings()

    def _on_configure_shortcuts_done(self, obj, res, user_data):
        if not self.bus:
            return
        try:
            req_variant = obj.call_finish(res)
            req_path = req_variant.unpack()[0]
            sub_id = None
            def _on_response(conn, sender, path, iface, signal, params, udata):
                if sub_id and self.bus:
                    self.bus.signal_unsubscribe(sub_id)
                self._on_bind_shortcuts_response(conn, sender, path, iface, signal, params, udata)

            sub_id = self.bus.signal_subscribe(
                PORTAL_BUS_NAME,
                REQUEST_IFACE,
                "Response",
                req_path,
                None,
                Gio.DBusSignalFlags.NONE,
                _on_response,
                None
            )
        except Exception as e:
            logger.warning(f"ConfigureShortcuts response error: {e}")

    def _subscribe_signals(self):
        if not self.bus:
            return
        if self.signal_subscription_id is None:
            self.signal_subscription_id = self.bus.signal_subscribe(
                PORTAL_BUS_NAME,
                PORTAL_IFACE,
                "Activated",
                PORTAL_OBJECT_PATH,
                None,
                Gio.DBusSignalFlags.NONE,
                self._on_shortcut_activated,
                None
            )

        if self.shortcuts_changed_sub_id is None:
            self.shortcuts_changed_sub_id = self.bus.signal_subscribe(
                PORTAL_BUS_NAME,
                PORTAL_IFACE,
                "ShortcutsChanged",
                PORTAL_OBJECT_PATH,
                None,
                Gio.DBusSignalFlags.NONE,
                self._on_shortcuts_changed,
                None
            )

    def _on_shortcut_activated(self, connection, sender, path, iface, signal, params, user_data):
        session, shortcut_id, timestamp, options = params.unpack()
        if session == self.session_handle and shortcut_id == "toggle-whisp":
            GLib.idle_add(self._trigger_toggle)

    def _on_shortcuts_changed(self, connection, sender, path, iface, signal, params, user_data):
        session, shortcuts = params.unpack()
        if session == self.session_handle:
            for shortcut_entry in shortcuts:
                if isinstance(shortcut_entry, (list, tuple)) and len(shortcut_entry) >= 2:
                    shortcut_id, info = shortcut_entry[0], shortcut_entry[1]
                    if shortcut_id == "toggle-whisp":
                        bound_trigger = _parse_shortcut_trigger(info)
                        if bound_trigger:
                            gtk_accel = portal_trigger_to_gtk(bound_trigger)
                            config.set("global_toggle_shortcut", gtk_accel)
                            if self.ui_update_callback:
                                GLib.idle_add(self.ui_update_callback, gtk_accel)

    def _trigger_toggle(self):
        if hasattr(self.app, "toggle_visibility"):
            self.app.toggle_visibility()
        return False

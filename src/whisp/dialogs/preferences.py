import os
import shutil
import gettext
from pathlib import Path
from typing import cast, TYPE_CHECKING, Any

from gi.repository import Gtk, Adw, Gdk, Gio, GLib, Pango
from whisp.config import config, DATA_DIR, Config

if TYPE_CHECKING:
    from whisp.window import WhispWindow
    from whisp.main import WhispApp
    from whisp.editor import NoteEditor

_ = gettext.gettext

ShortcutLabel = getattr(Adw, "ShortcutLabel", None) or Gtk.ShortcutLabel


class ThemeSnippet(Gtk.ToggleButton):
    def __init__(self, theme_id, group=None):
        super().__init__(group=group)
        self.theme_id = theme_id
        self.set_valign(Gtk.Align.CENTER)
        self.set_halign(Gtk.Align.CENTER)
        self.add_css_class("theme-snippet-btn")
        
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.add_css_class("theme-snippet-preview")
        box.add_css_class(f"paper-{theme_id}")
        
        # Add fake text lines to simulate an editor
        for width in [60, 80, 40]:
            line = Gtk.Box()
            line.add_css_class("fake-text-line")
            line.set_size_request(width, 4)
            line.set_halign(Gtk.Align.START)
            box.append(line)
            
        self.set_child(box)


class PreferencesDialog(Adw.PreferencesDialog):
    """Preferences dialog managing appearance, behavior, shortcuts, and storage location."""
    
    def __init__(self, parent_window: "WhispWindow"):
        super().__init__()
        self.parent_window = parent_window
        self.set_search_enabled(True)
        
        # --- Appearance Page ---
        appearance_page = Adw.PreferencesPage(title=_("Appearance"), icon_name="preferences-desktop-appearance-symbolic")
        
        # Appearance Group
        font_group = Adw.PreferencesGroup(title=_("Text"))
        font_row = Adw.ActionRow(title=_("Editor Font"))
        
        font_dialog = Gtk.FontDialog()
        
        font_btn = Gtk.Button()
        font_btn.set_valign(Gtk.Align.CENTER)
        
        font_label = Gtk.Label()
        font_label.set_ellipsize(Pango.EllipsizeMode.END)
        font_label.set_max_width_chars(15)
        font_btn.set_child(font_label)
        
        font_name = config.get("font_name")
        font_desc = None
        if font_name:
            font_desc = Pango.FontDescription.from_string(font_name)
            font_label.set_label(font_desc.to_string())
        else:
            font_label.set_label(_("System Default"))
            
        def on_font_btn_clicked(btn):
            def on_font_chosen(dialog, result):
                try:
                    new_desc = dialog.choose_font_finish(result)
                    if new_desc:
                        new_name = new_desc.to_string()
                        font_label.set_label(new_name)
                        config.set("font_name", new_name)
                        self.parent_window.apply_theme()
                except GLib.Error:
                    pass
            font_dialog.choose_font(self.parent_window, font_desc, None, on_font_chosen)
            
        font_btn.connect("clicked", on_font_btn_clicked)
        font_row.add_suffix(font_btn)
        font_group.add(font_row)
        
        # Theme Snippets Group
        theme_group = Adw.PreferencesGroup(title=_("Paper Theme"))
        
        flowbox = Gtk.FlowBox()
        flowbox.set_valign(Gtk.Align.START)
        flowbox.set_selection_mode(Gtk.SelectionMode.NONE)
        flowbox.set_max_children_per_line(3)
        flowbox.set_min_children_per_line(1)
        flowbox.set_row_spacing(12)
        flowbox.set_column_spacing(12)
        flowbox.set_margin_top(8)
        flowbox.set_margin_bottom(8)
        
        themes = ["blank", "dotted", "grid", "large_grid"]
        current_theme = config.get("paper_theme", "blank")
        
        group = None
        for t in themes:
            snippet = ThemeSnippet(t, group=group)
            if group is None:
                group = snippet
            if t == current_theme:
                snippet.set_active(True)
                
            snippet.connect("toggled", self.on_theme_snippet_toggled)
            flowbox.append(snippet)
            
        theme_group.add(flowbox)
        appearance_page.add(theme_group)
        
        # Line Spacing
        spacing_row = Adw.ActionRow(title=_("Line Spacing"))
        spacing_model = Gtk.StringList.new(["1.0", "1.2", "1.5", "2.0"])
        spacing_dropdown = Gtk.DropDown(model=spacing_model)
        spacing_dropdown.set_valign(Gtk.Align.CENTER)
        
        current_spacing = config.get("line_spacing", "1.2")
        try:
            idx = ["1.0", "1.2", "1.5", "2.0"].index(current_spacing)
            spacing_dropdown.set_selected(idx)
        except ValueError:
            pass
            
        spacing_dropdown.connect("notify::selected-item", self.on_spacing_changed)
        spacing_row.add_suffix(spacing_dropdown)
        font_group.add(spacing_row)
        
        appearance_page.add(font_group)

        window_group = Adw.PreferencesGroup(title=_("Window"))

        background_row = Adw.ActionRow(title=_("Background"))
        background_model = Gtk.StringList.new([_("Follow System"), _("Transparent"), _("Opaque")])
        background_dropdown = Gtk.DropDown(model=background_model)
        background_dropdown.set_valign(Gtk.Align.CENTER)

        current_background = config.get("window_background", "system")
        idx = {"transparent": 1, "opaque": 2}.get(current_background, 0)
        background_dropdown.set_selected(idx)

        background_dropdown.connect("notify::selected-item", self.on_window_background_changed)
        background_row.add_suffix(background_dropdown)
        window_group.add(background_row)

        self.opacity_row = Adw.ActionRow(title=_("Background Opacity"))
        opacity_adjustment = Gtk.Adjustment(
            value=config.get("window_background_opacity", 0.8),
            lower=0.1, upper=1.0, step_increment=0.1, page_increment=0.1
        )
        opacity_spin = Gtk.SpinButton(adjustment=opacity_adjustment, digits=1)
        opacity_spin.set_valign(Gtk.Align.CENTER)
        opacity_spin.connect("value-changed", self.on_window_opacity_changed)
        self.opacity_row.add_suffix(opacity_spin)
        self.opacity_row.set_visible(current_background == "transparent")
        window_group.add(self.opacity_row)

        appearance_page.add(window_group)
        self.add(appearance_page)

        # --- Behavior Page ---
        behavior_page = Adw.PreferencesPage(title=_("Behavior"), icon_name="preferences-system-symbolic")

        # Behavior Group
        behavior_group = Adw.PreferencesGroup(title=_("Workflow"))
        
        wysiwyg_scope_row = Adw.ActionRow(
            title=_("WYSIWYG Scope"),
            subtitle=_("Apply Live Formatting globally to all notes, or remember it per note.")
        )
        wysiwyg_scope_model = Gtk.StringList.new([_("Global"), _("Per Note")])
        wysiwyg_scope_dropdown = Gtk.DropDown(model=wysiwyg_scope_model)
        wysiwyg_scope_dropdown.set_valign(Gtk.Align.CENTER)
        
        current_scope = config.get("wysiwyg_scope", "global")
        idx = 1 if current_scope == "per_note" else 0
        wysiwyg_scope_dropdown.set_selected(idx)
        
        wysiwyg_scope_dropdown.connect("notify::selected-item", self.on_wysiwyg_scope_changed)
        wysiwyg_scope_row.add_suffix(wysiwyg_scope_dropdown)
        behavior_group.add(wysiwyg_scope_row)
        
        startup_row = Adw.ActionRow(title=_("Startup Behavior"))
        startup_model = Gtk.StringList.new([_("Restore last active note"), _("Start with empty note")])
        startup_dropdown = Gtk.DropDown(model=startup_model)
        startup_dropdown.set_valign(Gtk.Align.CENTER)
        
        current_startup = config.get("startup_behavior", "last_note")
        idx = 1 if current_startup == "empty_note" else 0
        startup_dropdown.set_selected(idx)
        
        startup_dropdown.connect("notify::selected-item", self.on_startup_behavior_changed)
        startup_row.add_suffix(startup_dropdown)
        behavior_group.add(startup_row)

        archive_row = Adw.ActionRow(
            title=_("Auto-Archive Inactive Notes"),
            subtitle=_("Notes are not deleted. They are simply hidden from the app to reduce clutter, but remain fully searchable via Ctrl+F."),
        )
        archive_model = Gtk.StringList.new([_("Never"), _("1 Week"), _("1 Month"), _("1 Year")])
        archive_dropdown = Gtk.DropDown(model=archive_model)
        archive_dropdown.set_valign(Gtk.Align.CENTER)
        
        current_archive = config.get("archive_days", 0)
        idx = 0
        if current_archive == 7:
            idx = 1
        elif current_archive == 30:
            idx = 2
        elif current_archive == 365:
            idx = 3
            
        archive_dropdown.set_selected(idx)
        archive_dropdown.connect("notify::selected-item", self.on_archive_days_changed)
        archive_row.add_suffix(archive_dropdown)
        behavior_group.add(archive_row)

        carousel_size_row = Adw.ActionRow(
            title=_("Max Carousel Size"),
            subtitle=_("Limit the number of notes loaded into the swipable carousel. Older notes remain fully searchable."),
        )
        carousel_size_model = Gtk.StringList.new([_("10 Notes"), _("25 Notes"), _("50 Notes"), _("Unlimited")])
        carousel_size_dropdown = Gtk.DropDown(model=carousel_size_model)
        carousel_size_dropdown.set_valign(Gtk.Align.CENTER)
        
        current_size = config.get("max_carousel_size", 10)
        idx = 0
        if current_size == 25:
            idx = 1
        elif current_size == 50:
            idx = 2
        elif current_size == 0:
            idx = 3
            
        carousel_size_dropdown.set_selected(idx)
        carousel_size_dropdown.connect("notify::selected-item", self.on_carousel_size_changed)
        carousel_size_row.add_suffix(carousel_size_dropdown)
        behavior_group.add(carousel_size_row)

        confirm_row = Adw.ActionRow(
            title=_("Confirm Before Deleting"),
            subtitle=_("Ask for confirmation when deleting a note"),
        )
        confirm_switch = Gtk.Switch()
        confirm_switch.set_valign(Gtk.Align.CENTER)
        confirm_switch.set_active(config.get("confirm_delete", True))
        confirm_switch.connect("notify::active", self.on_confirm_delete_changed)
        confirm_row.add_suffix(confirm_switch)
        confirm_row.set_activatable_widget(confirm_switch)
        behavior_group.add(confirm_row)
        
        toast_row = Adw.ActionRow(
            title=_("Show Command Confirmations"),
            subtitle=_("Display a small confirmation message when you use a text manipulation command"),
        )
        toast_switch = Gtk.Switch()
        toast_switch.set_valign(Gtk.Align.CENTER)
        toast_switch.set_active(config.get("show_command_toasts", True))
        
        def on_toast_switch_changed(switch, param):
            config.set("show_command_toasts", switch.get_active())
            
        toast_switch.connect("notify::active", on_toast_switch_changed)
        toast_row.add_suffix(toast_switch)
        toast_row.set_activatable_widget(toast_switch)
        behavior_group.add(toast_row)
        
        start_slate_row = Adw.ActionRow(
            title=_("Enable Slate Mode on Startup"),
            subtitle=_("Always launch Whisp directly into Slate Mode")
        )
        start_slate_switch = Gtk.Switch()
        start_slate_switch.set_valign(Gtk.Align.CENTER)
        start_slate_switch.set_active(config.get("start_in_slate_mode", False))
        
        def on_start_slate_changed(switch, param):
            config.set("start_in_slate_mode", switch.get_active())
            
        start_slate_switch.connect("notify::active", on_start_slate_changed)
        start_slate_row.add_suffix(start_slate_switch)
        start_slate_row.set_activatable_widget(start_slate_switch)
        behavior_group.add(start_slate_row)
        
        behavior_page.add(behavior_group)

        # --- Running Group ---
        running_group = Adw.PreferencesGroup(title=_("Running"))

        self.bg_row = Adw.ActionRow(
            title=_("Keep Running in Background"),
            subtitle=_("Hides the application when closed instead of terminating"),
        )
        self.bg_switch = Gtk.Switch()
        self.bg_switch.set_valign(Gtk.Align.CENTER)
        self.bg_switch.set_active(config.get("run_in_background", False))
        self.bg_switch.connect("notify::active", self.on_bg_switch_changed)
        self.bg_row.add_suffix(self.bg_switch)
        self.bg_row.set_activatable_widget(self.bg_switch)
        running_group.add(self.bg_row)

        self.startup_row = Adw.ActionRow(
            title=_("Run on Startup"),
            subtitle=_("Automatically launch the application when you log in"),
        )
        self.startup_switch = Gtk.Switch()
        self.startup_switch.set_valign(Gtk.Align.CENTER)
        self.startup_switch.set_active(config.get("run_on_startup", False))
        self.startup_switch.connect("notify::active", self.on_startup_switch_changed)
        self.startup_row.add_suffix(self.startup_switch)
        self.startup_row.set_activatable_widget(self.startup_switch)
        running_group.add(self.startup_row)

        self.hidden_row = Adw.ActionRow(
            title=_("Start Hidden"),
            subtitle=_("Launch in the background without opening a window"),
        )
        self.hidden_switch = Gtk.Switch()
        self.hidden_switch.set_valign(Gtk.Align.CENTER)
        self.hidden_switch.set_active(config.get("start_hidden", False))
        self.hidden_switch.connect("notify::active", self.on_hidden_switch_changed)
        self.hidden_row.add_suffix(self.hidden_switch)
        self.hidden_row.set_activatable_widget(self.hidden_switch)
        running_group.add(self.hidden_row)

        self._update_hidden_switch_sensitivity()
        behavior_page.add(running_group)
        self.add(behavior_page)

        # --- Shortcuts Page ---
        shortcuts_page = Adw.PreferencesPage(title=_("Shortcuts"), icon_name="input-keyboard-symbolic")

        shortcuts = config.get("shortcuts", {})
        if not isinstance(shortcuts, dict):
            shortcuts = {}
        
        shortcut_categories = {
            _("Application"): {
                "win.new-note": _("New Note"),
                "win.search": _("Search Notes"),
                "win.preferences": _("Preferences"),
                "win.show-shortcuts": _("Show Shortcuts"),
                "win.slate-mode": _("Toggle Slate Mode"),
                "win.quit": _("Quit Application")
            },
            _("Note Management"): {
                "win.delete-note": _("Delete Note"),
                "win.undo-delete": _("Undo Delete"),
                "win.pin-note": _("Pin Note"),
                "win.bump-note": _("Move Note to Front"),
                "win.copy-note": _("Copy Note"),
                "win.export-note": _("Export Note"),
                "win.toggle-wysiwyg": _("Toggle WYSIWYG")
            },
            _("Navigation"): {
                "win.nav-prev": _("Previous Note"),
                "win.nav-next": _("Next Note"),
                "win.nav-first": _("First Note"),
                "win.nav-last": _("Last Note")
            }
        }
        
        for category_title, actions in shortcut_categories.items():
            group = Adw.PreferencesGroup(title=category_title)
            
            for action, label in actions.items():
                accels = shortcuts.get(action, [])
                display_accel = accels[0] if accels else ""
                
                row = Adw.ActionRow(title=label)
                
                shortcut_label = ShortcutLabel(accelerator=display_accel)
                shortcut_label.set_disabled_text(_("Disabled"))
                shortcut_label.set_valign(Gtk.Align.CENTER)
                
                default_accels = Config.DEFAULT_SHORTCUTS.get(action, [])
                is_custom = accels != default_accels
                
                reset_btn = Gtk.Button(icon_name="edit-undo-symbolic")
                reset_btn.set_valign(Gtk.Align.CENTER)
                reset_btn.add_css_class("flat")
                reset_btn.add_css_class("circular")
                reset_btn.set_visible(is_custom)
                reset_btn.set_tooltip_text(_("Reset to Default"))
                
                reset_btn.connect("clicked", self.on_shortcut_reset, action, shortcut_label, reset_btn)
                row.connect("activated", self.on_shortcut_record, action, label, shortcut_label, reset_btn)
                row.set_activatable(True)
                
                suffix_box = Gtk.Box(spacing=12)
                suffix_box.append(reset_btn)
                suffix_box.append(shortcut_label)
                
                row.add_suffix(suffix_box)
                group.add(row)
                
            shortcuts_page.add(group)
        self.add(shortcuts_page)

        # --- Storage Page ---
        storage_page = Adw.PreferencesPage(title=_("Storage"), icon_name="drive-harddisk-symbolic")
        storage_group = Adw.PreferencesGroup(title=_("Data Location"))
        
        row = Adw.ActionRow(title=_("Notes Directory"), subtitle=str(DATA_DIR))
        
        box = Gtk.Box(spacing=6)
        box.set_valign(Gtk.Align.CENTER)
        
        reset_btn = Gtk.Button(icon_name="edit-undo-symbolic")
        reset_btn.set_tooltip_text(_("Reset to Default"))
        reset_btn.add_css_class("flat")
        reset_btn.connect("clicked", self.on_reset_dir, row)
        
        btn = Gtk.Button(label=_("Change…"))
        btn.connect("clicked", self.on_change_dir, row)
        
        box.append(reset_btn)
        box.append(btn)
        row.add_suffix(box)
        
        storage_group.add(row)
        storage_page.add(storage_group)
        self.add(storage_page)

    def _update_hidden_switch_sensitivity(self):
        can_start_hidden = self.startup_switch.get_active()
        self.hidden_row.set_sensitive(can_start_hidden)
        if not can_start_hidden:
            self.hidden_switch.set_active(False)
            config.set("start_hidden", False)

    def on_bg_switch_changed(self, switch, param):
        is_bg = switch.get_active()
        config.set("run_in_background", is_bg)
        self.parent_window.set_hide_on_close(is_bg)

    def on_startup_switch_changed(self, switch, param):
        is_active = switch.get_active()
        config.set("run_on_startup", is_active)
        self._update_hidden_switch_sensitivity()
        self._update_background_portal()

    def on_hidden_switch_changed(self, switch, param):
        config.set("start_hidden", switch.get_active())
        self._update_background_portal()

    def _update_background_portal(self):
        is_startup = self.startup_switch.get_active()
        is_hidden = self.hidden_switch.get_active()
        
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            
            command = ["whisp"]
            if is_hidden:
                command.append("--hidden")
                
            opts_dict = {
                "reason": GLib.Variant("s", "Start Whisp in the background"),
                "autostart": GLib.Variant("b", is_startup)
            }
            if is_startup:
                opts_dict["commandline"] = GLib.Variant("as", command)
                
            options = GLib.Variant("a{sv}", opts_dict)
            parameters = GLib.Variant.new_tuple(GLib.Variant("s", ""), options)
            
            def on_bg_response(conn, sender, path, iface, signame, params, ud):
                pass
                
            def on_request(source, result, ud):
                try:
                    ret = source.call_finish(result)
                    request_handle = ret.unpack()[0]
                    bus.signal_subscribe(
                        "org.freedesktop.portal.Desktop", "org.freedesktop.portal.Request",
                        "Response", request_handle, None, Gio.DBusSignalFlags.NONE,
                        on_bg_response, None
                    )
                except Exception as e:
                    print("Background request error:", e)

            bus.call(
                "org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop",
                "org.freedesktop.portal.Background", "RequestBackground",
                parameters, None, Gio.DBusCallFlags.NONE, -1, None,
                on_request, None
            )
        except Exception as e:
            print("Failed to request background portal via DBus:", e)

    def on_theme_snippet_toggled(self, snippet):
        if snippet.get_active():
            old_theme = config.get("paper_theme", "blank")
            theme_id = snippet.theme_id
            config.set("paper_theme", theme_id)
            
            for i in range(self.parent_window.carousel.get_n_pages()):
                editor = cast("NoteEditor", self.parent_window.carousel.get_nth_page(i))
                editor.textview.remove_css_class(f"paper-{old_theme}")
                editor.textview.add_css_class(f"paper-{theme_id}")

    def on_spacing_changed(self, dropdown, param):
        selected = dropdown.get_selected_item()
        if selected:
            spacing = selected.get_string()
            config.set("line_spacing", spacing)
            self.parent_window.update_line_spacing()

    def on_window_background_changed(self, dropdown, param):
        idx = dropdown.get_selected()
        mode = {1: "transparent", 2: "opaque"}.get(idx, "system")
        config.set("window_background", mode)
        self.opacity_row.set_visible(mode == "transparent")
        self.parent_window.update_window_background()

    def on_window_opacity_changed(self, spin_button):
        # Rounded so the spin button's float noise (0.7000000000000001) stays out of the config and CSS
        config.set("window_background_opacity", round(spin_button.get_value(), 1))
        self.parent_window.update_window_background()

    def on_confirm_delete_changed(self, switch, param):
        config.set("confirm_delete", switch.get_active())

    def on_startup_behavior_changed(self, dropdown, param):
        selected = dropdown.get_selected()
        val = "empty_note" if selected == 1 else "last_note"
        config.set("startup_behavior", val)

    def on_wysiwyg_scope_changed(self, dropdown, param):
        idx = dropdown.get_selected()
        scope = "per_note" if idx == 1 else "global"
        config.set("wysiwyg_scope", scope)
        
        if scope == "global":
            global_mode = config.get("wysiwyg_mode", False)
            self.parent_window._ignore_wysiwyg_toggle = True
            self.parent_window.wysiwyg_btn.set_active(global_mode)
            self.parent_window._ignore_wysiwyg_toggle = False
        else:
            editor = self.parent_window.get_current_editor()
            if editor:
                fname = editor.file_path.name
                note_mode = self.parent_window.metadata.get(fname, {}).get("wysiwyg", False)
                self.parent_window._ignore_wysiwyg_toggle = True
                self.parent_window.wysiwyg_btn.set_active(note_mode)
                self.parent_window._ignore_wysiwyg_toggle = False
                
        for i in range(self.parent_window.carousel.get_n_pages()):
            editor = cast("NoteEditor", self.parent_window.carousel.get_nth_page(i))
            editor.highlighter.highlight()

    def on_archive_days_changed(self, dropdown, param):
        idx = dropdown.get_selected()
        day_map = {0: 0, 1: 7, 2: 30, 3: 365}
        days = day_map.get(idx, 0)
        config.set("archive_days", days)

    def on_carousel_size_changed(self, dropdown, param):
        idx = dropdown.get_selected()
        size_map = {0: 10, 1: 25, 2: 50, 3: 0}
        size = size_map.get(idx, 10)
        config.set("max_carousel_size", size)

    def on_shortcut_reset(self, btn, action, shortcut_label, reset_btn):
        default_accels = Config.DEFAULT_SHORTCUTS.get(action, [])
        shortcuts = config.get("shortcuts", {})
        shortcuts[action] = default_accels.copy()
        config.set("shortcuts", shortcuts)
        
        app = cast("WhispApp | None", self.parent_window.get_application())
        if app and hasattr(app, "set_accels_for_action"):
            app.set_accels_for_action(action, default_accels)
            
        display_accel = default_accels[0] if default_accels else ""
        shortcut_label.set_accelerator(display_accel)
        reset_btn.set_visible(False)

    def on_shortcut_record(self, btn, action, label_text, shortcut_label, reset_btn):
        dialog = Adw.MessageDialog(
            transient_for=self.parent_window,
            heading=_("Set Shortcut"),
            body=_("Recording Shortcut for <b>{}</b>").format(label_text),
            body_use_markup=True
        )
        
        icon = Gtk.Image.new_from_icon_name("preferences-desktop-keyboard-shortcuts-symbolic")
        icon.set_pixel_size(64)
        icon.add_css_class("dim-label")
        
        instructions = Gtk.Label(
            label=_("<span foreground='gray' size='small'>press backspace to disable</span>"),
            use_markup=True,
            wrap=True,
            justify=Gtk.Justification.CENTER
        )
        
        extra_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=12)
        squeezer = Adw.Squeezer()
        squeezer.add(icon)
        squeezer.add(Gtk.Box())
        
        extra_box.append(squeezer)
        extra_box.append(instructions)
        dialog.set_extra_child(extra_box)
        
        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self._on_shortcut_key_pressed, dialog, action, shortcut_label, reset_btn)
        dialog.add_controller(key_ctrl)
        
        dialog.add_response("cancel", _("Cancel"))
        dialog.set_response_appearance("cancel", Adw.ResponseAppearance.DEFAULT)
        dialog.connect("response", lambda d, r: d.close())
        dialog.present()

    def _on_shortcut_key_pressed(self, ctrl, keyval, keycode, state, dialog, action, shortcut_label, reset_btn):
        modifier_keys = (
            Gdk.KEY_Control_L, Gdk.KEY_Control_R,
            Gdk.KEY_Shift_L, Gdk.KEY_Shift_R,
            Gdk.KEY_Alt_L, Gdk.KEY_Alt_R,
            Gdk.KEY_Super_L, Gdk.KEY_Super_R,
            Gdk.KEY_Meta_L, Gdk.KEY_Meta_R
        )
        if keyval in modifier_keys:
            return False
            
        if keyval == Gdk.KEY_Escape:
            dialog.close()
            return True
            
        shortcuts = config.get("shortcuts", {})

        if keyval == Gdk.KEY_BackSpace:
            accel_str = ""
            accels = []
        else:
            mask = state & Gtk.accelerator_get_default_mod_mask()
            required_masks = Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.SUPER_MASK
            if not (mask & required_masks):
                return False
                
            accel_str = Gtk.accelerator_name(keyval, mask)
            if not accel_str:
                return False
                
            for existing_action, existing_accels in shortcuts.items():
                if existing_action != action and accel_str in existing_accels:
                    dialog.close()
                    toast = Adw.Toast.new(_("This shortcut is already in use"))
                    toast.set_timeout(4)
                    self.add_toast(toast)
                    return True
                    
            accels = [accel_str]
            
        shortcuts[action] = accels
        config.set("shortcuts", shortcuts)
        
        app = cast("WhispApp | None", self.parent_window.get_application())
        if app and hasattr(app, "set_accels_for_action"):
            app.set_accels_for_action(action, accels)
        
        shortcut_label.set_accelerator(accel_str)
        default_accels = Config.DEFAULT_SHORTCUTS.get(action, [])
        reset_btn.set_visible(accels != default_accels)
        
        dialog.close()
        return True

    def on_change_dir(self, btn, row):
        dialog = Gtk.FileDialog()
        dialog.set_title(_("Select Notes Directory"))
        dialog.select_folder(self.parent_window, None, self.on_folder_selected, row)

    def migrate_to_dir(self, new_dir, row):
        global DATA_DIR
        if new_dir == DATA_DIR:
            return
            
        old_dir = DATA_DIR
        new_dir.mkdir(parents=True, exist_ok=True)
        
        try:
            if old_dir.exists():
                for item in old_dir.iterdir():
                    if item.name == ".trash":
                        new_trash = new_dir / ".trash"
                        if not new_trash.exists():
                            shutil.move(str(item), str(new_trash))
                    elif item.name == "metadata.json":
                        new_meta = new_dir / "metadata.json"
                        if new_meta.exists():
                            import json
                            try:
                                old_data = json.loads(item.read_text(encoding='utf-8'))
                                new_data = json.loads(new_meta.read_text(encoding='utf-8'))
                                new_data.update(old_data)
                                new_meta.write_text(json.dumps(new_data), encoding='utf-8')
                                item.unlink()
                            except Exception:
                                pass
                        else:
                            shutil.move(str(item), str(new_meta))
                    elif item.suffix == ".md":
                        dest = new_dir / item.name
                        if not dest.exists():
                            shutil.move(str(item), str(dest))
        except Exception as e:
            print(f"Migration error: {e}")

        import whisp.config as whisp_config
        config.data_dir = new_dir
        whisp_config.DATA_DIR = new_dir
        whisp_config.TRASH_DIR = new_dir / ".trash"
        DATA_DIR = new_dir
        row.set_subtitle(str(DATA_DIR))
        
        self.parent_window.metadata = {}
        meta_file = DATA_DIR / "metadata.json"
        if meta_file.exists():
            import json
            try:
                self.parent_window.metadata = json.loads(meta_file.read_text(encoding='utf-8'))
            except Exception:
                pass
                
        while self.parent_window.carousel.get_n_pages() > 0:
            self.parent_window.carousel.remove(self.parent_window.carousel.get_nth_page(0))
        self.parent_window.load_notes()

    def on_folder_selected(self, dialog, result, row):
        try:
            folder = dialog.select_folder_finish(result)
            if folder:
                new_dir = Path(folder.get_path())
                self.migrate_to_dir(new_dir, row)
        except GLib.Error:
            pass

    def on_reset_dir(self, btn, row):
        default_dir = Path(GLib.get_user_data_dir()) / "whisp" / "notes"
        self.migrate_to_dir(default_dir, row)


def show_preferences_dialog(parent_window: "WhispWindow") -> PreferencesDialog:
    dialog = PreferencesDialog(parent_window)
    if isinstance(parent_window, Gtk.Widget):
        dialog.present(parent_window)
    else:
        try:
            dialog.present()
        except (TypeError, Exception):
            try:
                dialog.present(None)
            except Exception:
                pass
    return dialog

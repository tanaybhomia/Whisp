import re
import os
import sys
import shutil
import gettext
import locale
from pathlib import Path
from typing import cast, Any, TYPE_CHECKING
from gi.repository import Gtk, Adw, Gdk, Gio, GLib, Pango
from whisp.config import config, DATA_DIR, TRASH_DIR
from whisp.editor import NoteEditor
from whisp.stats import tracker
from whisp.search_popover import NoteSearchPopover, NoteListBoxRow
from whisp.dialogs import (
    WhatsNewWindow,
    show_shortcuts_dialog,
    show_preferences_dialog,
    show_about_dialog,
    open_donate_url,
    get_latest_release_info,
    get_dynamic_version,
    export_note,
)

if TYPE_CHECKING:
    from whisp.main import WhispApp

try:
    locale.setlocale(locale.LC_ALL, '')
except locale.Error:
    pass

_ = gettext.gettext

class WhispWindow(Adw.ApplicationWindow):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        
        # Window State
        width = config.get("window_width")
        height = config.get("window_height")
        if width is None or height is None:
            width = 360
            height = 500
        self.set_default_size(int(width), int(height))
        self.set_size_request(360, 400)
        if config.get("is_maximized"):
            self.maximize()
            
        self.set_title("Whisp")
        self.connect("close-request", self.on_close_request)
        
        if config.get("run_in_background", False):
            self.set_hide_on_close(True)
        
        # Font and Theme styling
        self.css_provider = Gtk.CssProvider()
        display = Gdk.Display.get_default()
        if display:
            Gtk.StyleContext.add_provider_for_display(
                display,
                self.css_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
        self.apply_theme()

        # Above the user's gtk.css, so Transparent and Opaque win over it and Follow System leaves it be
        self.add_css_class("whisp-main-window")
        self.background_css_provider = Gtk.CssProvider()
        if display:
            Gtk.StyleContext.add_provider_for_display(
                display,
                self.background_css_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_USER + 1
            )
        self.update_window_background()
        
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        import json
        self.metadata = {}
        meta_file = DATA_DIR / "metadata.json"
        if meta_file.exists():
            try:
                self.metadata = json.loads(meta_file.read_text())
            except:
                pass
        self._ignore_pin_toggle = False

        self.toolbar_view = Adw.ToolbarView()
        self.toast_overlay = Adw.ToastOverlay()
        self.toast_overlay.set_child(self.toolbar_view)
        self.set_content(self.toast_overlay)
        
        self.last_deleted_file = None
        self.update_banner: Adw.Banner | None = None
        self.is_compact_mode = False

        try:
            bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 480px"))
            bp.connect("apply", lambda b: self.on_compact_mode_changed(True))
            bp.connect("unapply", lambda b: self.on_compact_mode_changed(False))
            self.add_breakpoint(bp)
        except Exception:
            pass

        # Actions
        new_note_action = Gio.SimpleAction.new("new-note", None)
        new_note_action.connect("activate", self.on_new_note)
        self.add_action(new_note_action)
        
        undo_action = Gio.SimpleAction.new("undo-delete", None)
        undo_action.connect("activate", self.on_undo_delete)
        self.add_action(undo_action)
        
        shortcuts_action = Gio.SimpleAction.new("show-shortcuts", None)
        shortcuts_action.connect("activate", self.on_show_shortcuts)
        self.add_action(shortcuts_action)
        
        del_note_action = Gio.SimpleAction.new("delete-note", None)
        del_note_action.connect("activate", self.on_delete_note)
        self.add_action(del_note_action)
        
        pref_action = Gio.SimpleAction.new("preferences", None)
        pref_action.connect("activate", self.on_preferences)
        self.add_action(pref_action)

        quit_action = Gio.SimpleAction.new("quit", None)
        def _on_quit(*_):
            app = self.get_application()
            if app:
                app.quit()
        quit_action.connect("activate", _on_quit)
        self.add_action(quit_action)

        inspector_action = Gio.SimpleAction.new("toggle-inspector", None)
        inspector_action.connect("activate", lambda a, p: Gtk.Window.set_interactive_debugging(True))
        self.add_action(inspector_action)
        
        nav_next_action = Gio.SimpleAction.new("nav-next", None)
        nav_next_action.connect("activate", self.on_nav_next)
        self.add_action(nav_next_action)
        
        nav_prev_action = Gio.SimpleAction.new("nav-prev", None)
        nav_prev_action.connect("activate", self.on_nav_prev)
        self.add_action(nav_prev_action)
        
        wysiwyg_action = Gio.SimpleAction.new("toggle-wysiwyg", None)
        wysiwyg_action.connect("activate", self.on_wysiwyg_shortcut)
        self.add_action(wysiwyg_action)

        about_action = Gio.SimpleAction.new("about", None)
        about_action.connect("activate", self.on_about)
        self.add_action(about_action)
        
        donate_action = Gio.SimpleAction.new("donate", None)
        donate_action.connect("activate", self.on_donate)
        self.add_action(donate_action)
        
        export_action = Gio.SimpleAction.new("export-note", None)
        export_action.connect("activate", self.on_export_note)
        self.add_action(export_action)

        search_action = Gio.SimpleAction.new("search", None)
        search_action.connect("activate", self.on_search_shortcut)
        self.add_action(search_action)

        pin_note_action = Gio.SimpleAction.new("pin-note", None)
        pin_note_action.connect("activate", self.on_pin_note)
        self.add_action(pin_note_action)

        nav_first_action = Gio.SimpleAction.new("nav-first", None)
        nav_first_action.connect("activate", self.on_nav_first)
        self.add_action(nav_first_action)

        nav_last_action = Gio.SimpleAction.new("nav-last", None)
        nav_last_action.connect("activate", self.on_nav_last)
        self.add_action(nav_last_action)

        copy_note_action = Gio.SimpleAction.new("copy-note", None)
        copy_note_action.connect("activate", self.on_copy_note)
        self.add_action(copy_note_action)

        bump_note_action = Gio.SimpleAction.new("bump-note", None)
        bump_note_action.connect("activate", self.on_bump_note)
        self.add_action(bump_note_action)

        slate_mode_action = Gio.SimpleAction.new("slate-mode", None)
        slate_mode_action.connect("activate", self.on_slate_mode_toggle)
        self.add_action(slate_mode_action)

        toggle_action = Gio.SimpleAction.new("toggle-visibility", None)
        toggle_action.connect("activate", lambda a, p: self.toggle_visibility())
        self.add_action(toggle_action)

        # Slate Mode state and hover logic
        self.is_slate_mode = config.get("start_in_slate_mode", False)
            
        motion_ctrl = Gtk.EventControllerMotion.new()
        motion_ctrl.connect("motion", self.on_mouse_motion)
        self.add_controller(motion_ctrl)

        # HeaderBar
        self.header_bar = Adw.HeaderBar()
        self.header_bar.add_css_class("flat")
        self.toolbar_view.add_top_bar(self.header_bar)

        # Apply Libadwaita headerbar appearance
        self.toolbar_view.set_reveal_top_bars(not self.is_slate_mode)
        
        # Delete Note Button
        del_btn = Gtk.Button(icon_name="user-trash-symbolic")
        del_btn.set_action_name("win.delete-note")
        del_btn.add_css_class("destructive-action")
        self.header_bar.pack_start(del_btn)

        # WYSIWYG Toggle Button
        self.wysiwyg_btn = Gtk.ToggleButton(icon_name="view-reveal-symbolic")
        self.wysiwyg_btn.set_tooltip_text("Toggle Live Formatting")
        self.wysiwyg_btn.set_active(config.get("wysiwyg_mode", False))
        self.wysiwyg_btn.connect("toggled", self.on_wysiwyg_toggled)
        self.header_bar.pack_start(self.wysiwyg_btn)

        # Pin Note Button
        self.pin_btn = Gtk.ToggleButton(icon_name="io.github.tanaybhomia.Whisp-pin-symbolic")
        self.pin_btn.set_tooltip_text("Pin Note")
        self.pin_btn.connect("toggled", self.on_pin_note_toggled)
        self.header_bar.pack_start(self.pin_btn)

        # Search Toggle Button
        self.search_btn = Gtk.MenuButton()
        self.search_btn.set_icon_name("system-search-symbolic")
        self.header_bar.pack_end(self.search_btn)

        self.popover = NoteSearchPopover(self)
        self.search_entry = self.popover.search_entry
        self.note_listbox = self.popover.note_listbox
        self.search_btn.set_popover(self.popover)


        # Hamburger menu
        self.menu_button = Gtk.MenuButton()
        self.menu_button.set_icon_name("open-menu-symbolic")
        
        theme_item = Gio.MenuItem.new(None, None)
        theme_item.set_attribute_value("custom", GLib.Variant.new_string("theme-switcher"))
        
        main_menu = Gio.Menu()
        main_menu.append_item(theme_item)
        
        export_section = Gio.Menu()
        export_section.append(_("Export Note"), "win.export-note")
        main_menu.append_section(None, export_section)
        
        section = Gio.Menu()
        section.append(_("Keyboard Shortcuts"), "win.show-shortcuts")
        section.append(_("Preferences"), "win.preferences")
        section.append(_("About Whisp"), "win.about")
        main_menu.append_section(None, section)
        
        donate_section = Gio.Menu()
        donate_section.append(_("Donate to Whisp"), "win.donate")
        main_menu.append_section(None, donate_section)
        
        popover = Gtk.PopoverMenu.new_from_model(main_menu)
        
        theme_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        theme_box.set_halign(Gtk.Align.CENTER)
        theme_box.set_margin_top(8)
        theme_box.set_margin_bottom(8)
        theme_box.set_margin_start(12)
        theme_box.set_margin_end(12)
        
        self.btn_system = Gtk.ToggleButton()
        self.btn_system.add_css_class("theme-btn")
        self.btn_system.add_css_class("system")
        self.btn_system.add_css_class("circular")
        self.btn_system.connect("toggled", self.on_theme_btn_toggled, "system")
        
        self.btn_light = Gtk.ToggleButton()
        self.btn_light.add_css_class("theme-btn")
        self.btn_light.add_css_class("light")
        self.btn_light.add_css_class("circular")
        self.btn_light.set_group(self.btn_system)
        self.btn_light.connect("toggled", self.on_theme_btn_toggled, "light")
        
        self.btn_dark = Gtk.ToggleButton()
        self.btn_dark.add_css_class("theme-btn")
        self.btn_dark.add_css_class("dark")
        self.btn_dark.add_css_class("circular")
        self.btn_dark.set_group(self.btn_system)
        self.btn_dark.connect("toggled", self.on_theme_btn_toggled, "dark")
        
        theme_box.append(self.btn_system)
        theme_box.append(self.btn_light)
        theme_box.append(self.btn_dark)
        
        popover.add_child(theme_box, "theme-switcher")
        self.menu_button.set_popover(popover)
        self.header_bar.pack_end(self.menu_button)

        # Apply initial theme state
        current_theme = config.get("color_scheme", "system")
        if current_theme == "light":
            self.btn_light.set_active(True)
        elif current_theme == "dark":
            self.btn_dark.set_active(True)
        else:
            self.btn_system.set_active(True)
            
        self._apply_color_scheme(current_theme)

        # Carousel
        self.carousel = Adw.Carousel()
        self.carousel.set_opacity(0)
        self.carousel.set_spacing(16)
        self.carousel.set_interactive(True)
        self.carousel.set_allow_mouse_drag(False)  # Allow mouse text selection without swiping
        self.carousel.connect("page-changed", self.on_page_changed)
        self.carousel.connect("notify::position", self.on_carousel_position_notify)
        self.toolbar_view.set_content(self.carousel)

        # Wheel paging is reimplemented in on_carousel_scroll; touchpad is untouched.
        self.carousel.set_allow_scroll_wheel(False)
        self._scroll_last_time = 0.0
        self._scroll_changes_note = False
        scroll_ctrl = Gtk.EventControllerScroll()
        scroll_ctrl.set_flags(Gtk.EventControllerScrollFlags.VERTICAL)
        scroll_ctrl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        scroll_ctrl.connect("scroll", self.on_carousel_scroll)
        self.carousel.add_controller(scroll_ctrl)

    def on_theme_btn_toggled(self, btn, scheme):
        if btn.get_active():
            config.set("color_scheme", scheme)
            self._apply_color_scheme(scheme)

    def _apply_color_scheme(self, scheme):
        manager = Adw.StyleManager.get_default()
        if scheme == "light":
            manager.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
        elif scheme == "dark":
            manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        else:
            manager.set_color_scheme(Adw.ColorScheme.DEFAULT)

    def _get_latest_release_info(self, last_seen="0.0.0", only_latest=False, as_list=False):
        return get_latest_release_info(last_seen=last_seen, only_latest=only_latest, as_list=as_list)

    def _get_dynamic_version(self):
        return get_dynamic_version()

    def on_export_note(self, action=None, param=None):
        editor = self.get_current_editor()
        if editor:
            export_note(self, editor, getattr(self, "toast_overlay", None))

    def on_about(self, action=None, param=None, open_page=None):
        show_about_dialog(self, open_page=open_page)

    def on_donate(self, action=None, param=None):
        open_donate_url(self)

    def on_nav_next(self, action=None, param=None):
        if action is not None:
            tracker.increment("shortcuts_used")
        n_pages = self.carousel.get_n_pages()
        if n_pages == 0:
            return
        current = self.get_current_page_index()
        if current < n_pages - 1:
            editor = cast(NoteEditor, self.carousel.get_nth_page(current + 1))
            self.carousel.scroll_to(editor, True)
            GLib.idle_add(lambda: [editor.textview.grab_focus(), False][-1])
            
    def on_nav_prev(self, action=None, param=None):
        if action is not None:
            tracker.increment("shortcuts_used")
        n_pages = self.carousel.get_n_pages()
        if n_pages == 0:
            return
        current = self.get_current_page_index()
        if current > 0:
            editor = cast(NoteEditor, self.carousel.get_nth_page(current - 1))
            self.carousel.scroll_to(editor, True)
            GLib.idle_add(lambda: [editor.textview.grab_focus(), False][-1])

    def on_carousel_scroll(self, controller, dx, dy):
        if controller.get_unit() != Gdk.ScrollUnit.WHEEL or dy == 0:
            return False
        editor = self.get_current_editor()
        if editor is None:
            return False

        import time
        now = time.monotonic()
        # A gap between ticks marks a new scroll motion.
        new_motion = now - self._scroll_last_time > 0.15
        self._scroll_last_time = now

        vadj = editor.scrolled.get_vadjustment()
        direction = 1 if dy > 0 else -1
        at_top = vadj.get_value() <= vadj.get_lower() + 1
        at_bottom = vadj.get_value() + vadj.get_page_size() >= vadj.get_upper() - 1
        at_edge = (direction > 0 and at_bottom) or (direction < 0 and at_top)

        # A motion changes note only if it starts at the edge.
        if new_motion:
            self._scroll_changes_note = at_edge
            if at_edge:
                self._wheel_change_note(direction)
        return self._scroll_changes_note

    def _wheel_change_note(self, direction):
        n_pages = self.carousel.get_n_pages()
        target = self.get_current_page_index() + direction
        if target < 0 or target >= n_pages:
            return
        editor = cast(NoteEditor, self.carousel.get_nth_page(target))
        self.carousel.scroll_to(editor, True)

        # Land on the edge we enter from, so reversing the scroll goes back.
        def place():
            vadj = editor.scrolled.get_vadjustment()
            vadj.set_value(vadj.get_lower() if direction > 0
                           else vadj.get_upper() - vadj.get_page_size())
            editor.textview.grab_focus()
            return False
        GLib.idle_add(place)
        GLib.timeout_add(50, place)

    def on_wysiwyg_toggled(self, btn):
        if hasattr(self, '_ignore_wysiwyg_toggle') and self._ignore_wysiwyg_toggle:
            return
            
        scope = config.get("wysiwyg_scope", "global")
        active = btn.get_active()
        
        if scope == "global":
            config.set("wysiwyg_mode", active)
            for i in range(self.carousel.get_n_pages()):
                editor = cast(NoteEditor, self.carousel.get_nth_page(i))
                editor.highlighter.highlight()
        else:
            editor = self.get_current_editor()
            if editor:
                fname = Path(editor.file_path).name if editor.file_path else ""
                if fname not in self.metadata:
                    self.metadata[fname] = {}
                self.metadata[fname]["wysiwyg"] = active
                self.save_metadata()
                editor.highlighter.highlight()
            
    def on_wysiwyg_shortcut(self, action, param):
        self.wysiwyg_btn.set_active(not self.wysiwyg_btn.get_active())

    def on_show_shortcuts(self, action, param):
        show_shortcuts_dialog(self)

    def save_metadata(self):
        import json
        meta_file = DATA_DIR / "metadata.json"
        meta_file.write_text(json.dumps(self.metadata))

    def on_pin_note_toggled(self, btn):
        if self._ignore_pin_toggle:
            return
            
        try:
            current_page_idx = self.get_current_page_index()
            if current_page_idx < 0 or current_page_idx >= self.carousel.get_n_pages():
                return
                
            current_page = cast(NoteEditor, self.carousel.get_nth_page(current_page_idx))
            if not current_page:
                return
                
            is_pinned = btn.get_active()
            fname = Path(current_page.file_path).name if current_page.file_path else ""
            
            if fname not in self.metadata:
                self.metadata[fname] = {}
            self.metadata[fname]["pinned"] = is_pinned
            self.save_metadata()
            
            if is_pinned:
                self.carousel.remove(current_page)
                self.carousel.insert(current_page, 0)
                
                def animate_pin():
                    self.carousel.scroll_to(current_page, True)
                    current_page.textview.grab_focus()
                    return False
                
                GLib.idle_add(animate_pin)
                GLib.timeout_add(50, animate_pin)
                
                if hasattr(self, '_pin_toast') and self._pin_toast:
                    self._pin_toast.dismiss()
                self._pin_toast = Adw.Toast.new(_("Note pinned"))
                self.toast_overlay.add_toast(self._pin_toast)
            else:
                self.carousel.remove(current_page)
                target_pos = 0
                current_mtime = os.path.getmtime(current_page.file_path) if current_page.file_path and Path(current_page.file_path).exists() else 0
                n_pages = self.carousel.get_n_pages()
                for i in range(n_pages - 1): # Ignore empty note at end
                    p = cast(NoteEditor, self.carousel.get_nth_page(i))
                    p_name = Path(p.file_path).name if p.file_path else ""
                    p_pinned = self.metadata.get(p_name, {}).get("pinned", False)
                    if p_pinned:
                        target_pos = i + 1
                        continue
                    p_mtime = os.path.getmtime(p.file_path) if p.file_path and Path(p.file_path).exists() else 0
                    if current_mtime < p_mtime:
                        break
                    target_pos = i + 1
                    
                self.carousel.insert(current_page, target_pos)
                
                def animate_unpin():
                    self.carousel.scroll_to(current_page, True)
                    current_page.textview.grab_focus()
                    return False
                
                GLib.idle_add(animate_unpin)
                GLib.timeout_add(50, animate_unpin)
                
                if hasattr(self, '_pin_toast') and self._pin_toast:
                    self._pin_toast.dismiss()
                self._pin_toast = Adw.Toast.new(_("Note Unpinned"))
                self.toast_overlay.add_toast(self._pin_toast)
                
        except Exception as e:
            print("Error toggling pin:", e)

    def on_pin_note(self, action=None, param=None):
        self.pin_btn.set_active(not self.pin_btn.get_active())

    def load_notes(self, skip_restore=False):
        is_first_run = config.get("first_run", True)
        if is_first_run:
            config.set("first_run", False)
            welcome_file = DATA_DIR / "Welcome to Whisp.md"
            if not welcome_file.exists():
                welcome_text = _(
                    "# 👋 Welcome to Whisp!\n\nWhisp is the frictionless anti-note. There are no save buttons or files to manage here.\n\n## Navigation\n👉 **Swipe left and right** on your touchpad (or use `Ctrl+[` and `Ctrl+]`) to switch between notes.\n➕ To create a new note, simply swipe past the last note!\n\n## Features\n☑ **Checklists**: Press `Ctrl+S` on any line to instantly create or toggle a checkbox.\n🔗 **Smart Links**: Paste any long URL, and Whisp will automatically shorten it to keep your notes clean.\n📋 **Plain Paste**: Use `Ctrl+Shift+V` to paste text cleanly without weird formatting.\n🎨 **Themes**: Open Preferences (`Ctrl+,`) to pick a paper background (like Grid or Dotted) and color scheme.\n\n📖 **Manual**: For a full list of features, check out the [User Manual]({url})\n\n🗑️ Press `Ctrl+D` to delete this note when you're done reading it!"
                ).format(url="https://tanaybhomia.github.io/Whisp/manual.html")
                welcome_file.write_text(welcome_text, encoding='utf-8')
                
        files = sorted(DATA_DIR.glob("*.md"), key=lambda f: os.path.getmtime(f) if f.exists() else 0, reverse=True)
        
        archive_days = config.get("archive_days", 0)
        import time
        now = time.time()
        
        active_files = []
        pinned_files = []
        for f in files:
            if not f.exists():
                continue
                
            # Clean up completely empty files on disk during startup
            try:
                if not f.read_text(encoding='utf-8').strip():
                    f.unlink(missing_ok=True)
                    continue
            except Exception:
                pass
                
            is_pinned = self.metadata.get(f.name, {}).get("pinned", False)
            if is_pinned:
                pinned_files.append(f)
                continue
                
            if archive_days > 0:
                age_days = (now - os.path.getmtime(f)) / (24 * 3600)
                if age_days > archive_days:
                    continue
            active_files.append(f)
            
        max_notes = config.get("max_carousel_size", 10)
        if max_notes > 0:
            recent_files = active_files[:max_notes]
        else:
            recent_files = active_files
        
        if not recent_files and not pinned_files:
            self.add_note(grab_focus=False)
        else:
            for f in reversed(pinned_files):
                self.add_note(f, grab_focus=False)
            for f in reversed(recent_files):
                self.add_note(f, grab_focus=False)
        
        self.ensure_empty_note_at_end()
        
        n_pages = self.carousel.get_n_pages()
        if n_pages > 0:
            target_idx = n_pages - 2 if n_pages > 1 else 0
            
            startup_behavior = config.get("startup_behavior", "last_note")
            if startup_behavior == "empty_note":
                target_idx = n_pages - 1
            else:
                last_active = config.get("last_active_note")
                if last_active:
                    for i in range(n_pages):
                        p_editor = cast(NoteEditor, self.carousel.get_nth_page(i))
                        if str(p_editor.file_path) == last_active:
                            target_idx = i
                            break
                        
            target_editor = cast(NoteEditor, self.carousel.get_nth_page(target_idx))
            
            def restore_session():
                if self.carousel.get_width() == 0:
                    attempts = getattr(restore_session, 'attempts', 0) + 1
                    setattr(restore_session, 'attempts', attempts)
                    if attempts < 20:
                        return True # Try again in 50ms
                    # If we waited 1 second and still no width, just reveal it anyway
                
                if self.carousel.get_width() > 0:
                    self.carousel.scroll_to(target_editor, False)
                    target_editor.textview.grab_focus()
                    buffer = target_editor.buffer
                    buffer.place_cursor(buffer.get_end_iter())
                
                self.carousel.set_opacity(1)
                self.update_title()
                
                last_seen = config.get("last_seen_version", "0.0.0")
                latest_version, releases_list = self._get_latest_release_info(last_seen=last_seen, as_list=True)
                
                if latest_version != "Unknown" and latest_version != last_seen:
                    config.set("last_seen_version", latest_version)
                    if not is_first_run and releases_list:
                        if hasattr(Adw, "Banner"):
                            self.update_banner = Adw.Banner(
                                title=_("Whisp just got better!"),
                                button_label=_("Changelogs")
                            )
                            self.update_banner.add_css_class("whisp-update-banner")
                            
                            def on_banner_clicked(btn):
                                if self.update_banner:
                                    self.update_banner.set_revealed(False)
                                self.show_whats_new_dialog(version=latest_version, releases_list=releases_list)

                            self.update_banner.connect("button-clicked", on_banner_clicked)
                            self.toolbar_view.add_top_bar(self.update_banner)
                            self.update_banner.set_revealed(True)
                        else:
                            self.on_about(None, None)
                        
                return False
            
            if not skip_restore:
                GLib.timeout_add(50, restore_session)

    def on_compact_mode_changed(self, compact: bool):
        self.is_compact_mode = compact
        for i in range(self.carousel.get_n_pages()):
            editor = cast(NoteEditor, self.carousel.get_nth_page(i))
            if editor and hasattr(editor, "set_compact_margins"):
                editor.set_compact_margins(compact)
    def show_whats_new_dialog(self, version=None, releases_list=None):
        if not version or not releases_list:
            version, releases_list = self._get_latest_release_info(last_seen="0.0.0", only_latest=True, as_list=True)
            
        date_str = releases_list[0].get("date", "") if releases_list else ""
        win = WhatsNewWindow(self, version, date_str, releases_list)
        win.present()

    def add_note(self, file_path=None, grab_focus=True, index=None):
        if file_path is None:
            tracker.increment("notes_created")
            from datetime import datetime
            hour = datetime.now().hour
            if 22 <= hour or hour < 5:
                tracker.increment("notes_created_night")
            else:
                tracker.increment("notes_created_day")
                
        editor = NoteEditor(file_path=file_path, on_title_changed=self.on_editor_title_changed)
        setattr(editor, 'window', self)
        if getattr(self, 'is_compact_mode', False):
            editor.set_compact_margins(True)
        if index is not None:
            self.carousel.insert(editor, index)
        else:
            self.carousel.append(editor)
        if grab_focus:
            def grab_it():
                self.carousel.scroll_to(editor, False)
                editor.textview.grab_focus()
                self.update_title()
                return False
            GLib.idle_add(grab_it)
            GLib.timeout_add(50, grab_it)
            GLib.timeout_add(150, grab_it)
        self.update_line_spacing()
        return editor

    def on_carousel_position_notify(self, carousel, param):
        pos = carousel.get_position()
        if pos == int(pos):
            self.cleanup_abandoned_empty_notes(int(pos))
            
    def cleanup_abandoned_empty_notes(self, current_idx):
        n_pages = self.carousel.get_n_pages()
        for i in range(n_pages - 2, current_idx, -1):
            editor = cast(NoteEditor, self.carousel.get_nth_page(i))
            if editor.is_empty():
                try:
                    if editor.file_path and Path(editor.file_path).exists():
                        Path(editor.file_path).unlink()
                except:
                    pass
                self.carousel.remove(editor)

    def ensure_empty_note_at_end(self):
        n_pages = self.carousel.get_n_pages()
        if n_pages == 0:
            self.add_note(grab_focus=False)
            return
            
        last_editor = cast(NoteEditor, self.carousel.get_nth_page(n_pages - 1))
        if not last_editor.is_empty():
            self.add_note(grab_focus=False)

    def on_nav_first(self, action=None, param=None):
        n_pages = self.carousel.get_n_pages()
        if n_pages > 0:
            self.carousel.scroll_to(self.carousel.get_nth_page(0), True)

    def on_nav_last(self, action=None, param=None):
        n_pages = self.carousel.get_n_pages()
        if n_pages > 0:
            self.carousel.scroll_to(self.carousel.get_nth_page(n_pages - 1), True)

    def on_copy_note(self, action=None, param=None):
        editor = self.get_current_editor()
        if editor:
            start, end = editor.buffer.get_bounds()
            text = editor.buffer.get_text(start, end, True)
            editor.textview.get_clipboard().set(text)
            self.toast_overlay.add_toast(Adw.Toast.new(_("Note Copied")))

    def on_bump_note(self, action=None, param=None):
        editor = self.get_current_editor()
        if editor and editor.file_path and Path(editor.file_path).exists():
            import os
            os.utime(editor.file_path, None)
            
            is_pinned = self.metadata.get(Path(editor.file_path).name, {}).get("pinned", False)
            if not is_pinned:
                self.carousel.remove(editor)
                target_pos = self.carousel.get_n_pages() - 1 
                self.carousel.insert(editor, target_pos)
                
                def animate_bump():
                    self.carousel.scroll_to(editor, True)
                    editor.textview.grab_focus()
                    return False
                
                GLib.idle_add(animate_bump)
                GLib.timeout_add(50, animate_bump)
                    
            self.toast_overlay.add_toast(Adw.Toast.new(_("Note moved to front")))

    def on_new_note(self, action=None, param=None):
        n_pages = self.carousel.get_n_pages()
        if n_pages > 0:
            self.carousel.scroll_to(self.carousel.get_nth_page(n_pages - 1), True)

    def on_undo_delete(self, action=None, param=None):
        if not self.last_deleted_file:
            return
            
        trash_path = TRASH_DIR / self.last_deleted_file
        data_path = DATA_DIR / self.last_deleted_file
        
        if trash_path.exists():
            shutil.move(str(trash_path), str(data_path))
            idx = getattr(self, 'last_deleted_index', None)
            self.add_note(data_path, grab_focus=True, index=idx)
            tracker.increment("notes_rescued")
            self.last_deleted_file = None
            self.last_deleted_index = None

    def on_delete_note(self, action=None, param=None):
        n_pages = self.carousel.get_n_pages()
        if n_pages == 0:
            return

        current_page_idx = self.get_current_page_index()
        editor = cast(NoteEditor, self.carousel.get_nth_page(current_page_idx))

        # Don't allow deleting an already empty note (prevents app locking bug)
        if editor.is_empty():
            if hasattr(self, 'current_toast') and self.current_toast:
                self.current_toast.dismiss()
            self.current_toast = Adw.Toast.new(_("Cannot delete the default empty note"))
            self.toast_overlay.add_toast(self.current_toast)
            return

        # Skip confirmation if user disabled it
        if not config.get("confirm_delete", True):
            self.perform_delete(editor)
            return

        title = editor.get_title()
        dialog = Adw.AlertDialog(
            heading=_("Delete Note?"),
            body=_("“{title}” will be moved to the trash. You can undo this with Ctrl+Shift+T.").format(title=title),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("delete", _("Delete"))
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self.on_delete_dialog_response, editor)
        dialog.present(self)

    def on_delete_dialog_response(self, dialog, response, editor):
        if response == "delete":
            self.perform_delete(editor)

    def perform_delete(self, editor):
        # Cancel any pending save timeout to prevent a "ghost note" from writing itself to disk after deletion
        if hasattr(editor, 'save_timeout_id') and editor.save_timeout_id:
            GLib.source_remove(editor.save_timeout_id)
            editor.save_timeout_id = 0

        # Find index to allow restoring to the exact same position
        idx = -1
        for i in range(self.carousel.get_n_pages()):
            if self.carousel.get_nth_page(i) == editor:
                idx = i
                break
        self.last_deleted_index = idx if idx != -1 else None

        toast_msg = _("Note deleted")
        if editor.file_path.exists():
            try:
                TRASH_DIR.mkdir(parents=True, exist_ok=True)
                dest_path = TRASH_DIR / editor.file_path.name
                
                # shutil.move crashes if the destination file already exists
                if dest_path.exists():
                    dest_path.unlink()
                    
                # Try to move to trash first
                shutil.move(str(editor.file_path), str(dest_path))
                self.last_deleted_file = editor.file_path.name
                tracker.increment("notes_trashed")
            except Exception as e:
                # Fallback
                try:
                    editor.file_path.unlink(missing_ok=True)
                    self.last_deleted_file = None
                    toast_msg = _("Permanent delete fallback (Error: {error})").format(error=str(e))
                except Exception as e2:
                    toast_msg = _("Failed to delete completely: {error}").format(error=str(e2))
        else:
            # Note was never saved to disk
            self.last_deleted_file = None

        if hasattr(self, 'current_toast') and self.current_toast:
            self.current_toast.dismiss()

        self.current_toast = Adw.Toast.new(toast_msg)
        if self.last_deleted_file:
            self.current_toast.set_button_label("Undo")
            self.current_toast.set_action_name("win.undo-delete")
        self.current_toast.set_timeout(5)
        self.toast_overlay.add_toast(self.current_toast)

        if getattr(editor, "is_autopaste_active", False):
            editor.stop_autopaste(silent=True)

        self.carousel.remove(editor)

        if self.carousel.get_n_pages() == 0:
            self.add_note()
        else:
            self.update_title()

    def on_page_changed(self, carousel, index):
        tracker.increment("total_swipes")
        self.update_title()
        editor = carousel.get_nth_page(int(round(float(index))))
        for i in range(carousel.get_n_pages()):
            page = cast(NoteEditor, carousel.get_nth_page(i))
            if page != editor and getattr(page, "is_autopaste_active", False):
                page.stop_autopaste(silent=False)
        if editor:
            fname = editor.file_path.name
            is_pinned = self.metadata.get(fname, {}).get("pinned", False)
            self._ignore_pin_toggle = True
            self.pin_btn.set_active(is_pinned)
            self._ignore_pin_toggle = False
            
            scope = config.get("wysiwyg_scope", "global")
            if scope == "per_note":
                is_wysiwyg = self.metadata.get(fname, {}).get("wysiwyg", False)
                self._ignore_wysiwyg_toggle = True
                self.wysiwyg_btn.set_active(is_wysiwyg)
                self._ignore_wysiwyg_toggle = False
            
            if self.popover.get_visible():
                editor.set_search_highlight(self.search_entry.get_text())
            else:
                GLib.idle_add(lambda: [editor.textview.grab_focus(), False][-1])

    def get_current_page_index(self) -> int:
        """Return the integer index of the current carousel page."""
        return int(round(float(self.carousel.get_position())))

    def on_editor_title_changed(self, editor):
        self.ensure_empty_note_at_end()
        if self.carousel.get_n_pages() == 0:
            return
        current_page_idx = self.get_current_page_index()
        if current_page_idx < self.carousel.get_n_pages():
            current_editor = self.carousel.get_nth_page(current_page_idx)
            if editor == current_editor:
                self.update_title()

    def update_title(self):
        pass

    def get_current_editor(self) -> NoteEditor | None:
        n_pages = self.carousel.get_n_pages()
        if n_pages == 0:
            return None
        idx = max(0, min(self.get_current_page_index(), n_pages - 1))
        return cast(NoteEditor, self.carousel.get_nth_page(idx))

    def on_search_shortcut(self, action=None, param=None):
        self.search_btn.set_active(True)

    def open_search(self, terms):
        """Open the in-app search popover, pre-filled with the given terms."""
        self.popover.open_search(terms)

    def open_note_at_match(self, file_path, match_term="", match_index=None):
        target: NoteEditor | None = None
        for i in range(self.carousel.get_n_pages()):
            editor = cast(NoteEditor, self.carousel.get_nth_page(i))
            if editor.file_path == file_path:
                self.carousel.scroll_to(editor, True)
                target = editor
                break

        if target is None:
            target = self.add_note(file_path)

        if match_index is not None and target:
            target.scroll_to_match(match_term, match_index)

        self.update_title()

    def on_preferences(self, action=None, param=None):
        show_preferences_dialog(self)

    def update_line_spacing(self):
        spacing_str = config.get("line_spacing", "1.2")
        try:
            spacing_val = float(spacing_str)
        except ValueError:
            spacing_val = 1.2
            
        # Convert multiplier to rough pixels (assuming ~16px font size)
        # Normal (1.0) = 0px
        # Relaxed (1.2) = 3px above/below
        # Loose (1.5) = 8px above/below
        # Very Loose (2.0) = 16px above/below
        extra_pixels = int((spacing_val - 1.0) * 16)
        above_below = max(0, extra_pixels // 2)
        inside_wrap = max(0, extra_pixels)
        
        for i in range(self.carousel.get_n_pages()):
            editor = cast(NoteEditor, self.carousel.get_nth_page(i))
            editor.textview.set_pixels_above_lines(above_below)
            editor.textview.set_pixels_below_lines(above_below)
            editor.textview.set_pixels_inside_wrap(inside_wrap)



    def on_pin_toggled(self, btn):
        if hasattr(self, "set_keep_above"):
            getattr(self, "set_keep_above")(btn.get_active())

    def on_close_request(self, window):
        for i in range(self.carousel.get_n_pages()):
            page = cast(NoteEditor, self.carousel.get_nth_page(i))
            if getattr(page, "is_autopaste_active", False):
                page.stop_autopaste(silent=True)

        config.set("window_width", self.get_width())
        config.set("window_height", self.get_height())
        config.set("is_maximized", self.is_maximized())
        
        current_page_idx = self.get_current_page_index()
        if current_page_idx < self.carousel.get_n_pages():
            editor = cast(NoteEditor, self.carousel.get_nth_page(current_page_idx))
            config.set("last_active_note", str(editor.file_path))
            
        if not config.get("run_in_background", False):
            app = cast("WhispApp | None", self.get_application())
            if app:
                app.quit()
            
        return False

    def apply_theme(self):
        font_name = config.get("font_name")
        font_css = ""
        if font_name:
            font_desc = Pango.FontDescription.from_string(font_name)
            family = font_desc.get_family()
            size = font_desc.get_size() / Pango.SCALE
            font_css = f"font-family: '{family}'; font-size: {size}pt;"
            
        custom_css = f"""
        .editor-textview {{ {font_css} }}
        .whisp-update-banner,
        .whisp-update-banner > revealer > widget {{
            background: mix(@dialog_bg_color, @accent_bg_color, 0.15);
            color: @window_fg_color;
        }}
        
        .theme-btn {{
            min-width: 48px;
            min-height: 48px;
            border: 1px solid alpha(currentColor, 0.15);
            padding: 0;
            box-shadow: none;
            background-clip: padding-box;
        }}
        .theme-btn.system {{
            background: linear-gradient(135deg, #ffffff 49.5%, #242424 50.5%);
        }}
        .theme-btn.light {{
            background: #ffffff;
        }}
        .theme-btn.dark {{
            background: #242424;
        }}
        .theme-btn:checked {{
            border: 2px solid @accent_bg_color;
            box-shadow: inset 0 0 0 2px @window_bg_color;
        }}
        """
        try:
            self.css_provider.load_from_data(custom_css.encode('utf-8'))
        except GLib.Error:
            pass

    def update_window_background(self):
        mode = config.get("window_background", "system")
        if mode == "transparent":
            opacity = config.get("window_background_opacity", 0.8)
            # GTK asks the compositor to blur only where a backdrop-filter is drawn
            css = f"""
            window.whisp-main-window {{
                background: alpha(@view_bg_color, {opacity});
                background-color: alpha(@view_bg_color, {opacity});
                backdrop-filter: blur(18px);
            }}
            """
        elif mode == "opaque":
            css = """
            window.whisp-main-window {
                background: @view_bg_color;
                background-color: @view_bg_color;
            }
            """
        else:
            css = ""
        try:
            self.background_css_provider.load_from_data(css.encode('utf-8'))
        except GLib.Error:
            pass

    def toggle_visibility(self):
        if not self.is_visible():
            self.show()
        self.present()
    def on_slate_mode_toggle(self, action, param):
        self.is_slate_mode = not self.is_slate_mode
        self.toolbar_view.set_reveal_top_bars(not self.is_slate_mode)
        
        if hasattr(self, '_slate_toast') and self._slate_toast:
            self._slate_toast.dismiss()
            
        status = _("Entered Slate mode") if self.is_slate_mode else _("Exited Slate mode")
        self._slate_toast = Adw.Toast.new(status)
        self.toast_overlay.add_toast(self._slate_toast)
    def on_mouse_motion(self, controller, x, y):
        if self.is_slate_mode:
            search_popover_open = hasattr(self, "popover") and self.popover and self.popover.get_visible()
            menu_popover = self.menu_button.get_popover() if hasattr(self, "menu_button") and self.menu_button else None
            menu_popover_open = menu_popover and menu_popover.get_visible()
            
            if search_popover_open or menu_popover_open:
                self.toolbar_view.set_reveal_top_bars(True)
                return

            threshold = 45
            if self.toolbar_view.get_reveal_top_bars():
                header_h = max(45, self.header_bar.get_height() if hasattr(self, "header_bar") and self.header_bar else 45)
                banner_h = 0
                if hasattr(self, "update_banner") and self.update_banner and self.update_banner.get_revealed():
                    banner_h = max(40, self.update_banner.get_height())
                threshold = max(threshold, header_h + banner_h)

            if y < threshold:
                self.toolbar_view.set_reveal_top_bars(True)
            else:
                self.toolbar_view.set_reveal_top_bars(False)

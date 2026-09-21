import gettext
from pathlib import Path

from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

from whisp.notes import body_excerpt, sidebar_entries

_ = gettext.gettext


class NotesSidebar(Adw.NavigationPage):
    """List of every note with search, meant for the sidebar of an AdwNavigationSplitView.

    The sidebar only reads notes and reports user intent; the window owns
    opening, creating and deleting them.

    get_entries() -> NoteIndex entries, newest first
    is_pinned(filename) -> bool
    on_open(path) / on_delete(path)
    """

    PAGE_SIZE = 100  # rows realised at once; more are added as the list scrolls

    def __init__(self, get_entries, is_pinned, on_open, on_delete):
        super().__init__(title=_("Notes"))
        self.get_entries = get_entries
        self.is_pinned = is_pinned
        self.on_open = on_open
        self.on_delete = on_delete
        self.selected_path = None
        self._rows = {}
        self._pending = []  # row descriptors not yet turned into widgets
        self._signature = None

        actions = Gio.SimpleActionGroup()
        delete_action = Gio.SimpleAction.new("delete", GLib.VariantType.new("s"))
        delete_action.connect("activate", lambda _a, param: self.on_delete(Path(param.get_string())))
        actions.add_action(delete_action)
        self.insert_action_group("sidebar", actions)

        self.toolbar_view = Adw.ToolbarView()
        self.set_child(self.toolbar_view)

        header = Adw.HeaderBar()
        header.add_css_class("flat")
        new_btn = Gtk.Button(icon_name="list-add-symbolic", action_name="win.new-note")
        new_btn.set_tooltip_text(_("New Note"))
        new_btn.update_property([Gtk.AccessibleProperty.LABEL], [_("New Note")])
        header.pack_start(new_btn)
        self.toolbar_view.add_top_bar(header)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toolbar_view.set_content(box)

        self.search_entry = Gtk.SearchEntry(placeholder_text=_("Search notes…"))
        self.search_entry.set_margin_start(12)
        self.search_entry.set_margin_end(12)
        self.search_entry.set_margin_bottom(6)
        self.search_entry.update_property([Gtk.AccessibleProperty.LABEL], [_("Search notes")])
        self.search_entry.connect("search-changed", lambda _e: self.refresh())
        self.search_entry.connect("stop-search", lambda e: e.set_text(""))
        self.search_entry.connect("activate", self._on_search_activate)
        box.append(self.search_entry)

        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.listbox.add_css_class("navigation-sidebar")
        self.listbox.update_property([Gtk.AccessibleProperty.LABEL], [_("Notes")])
        self.listbox.connect("row-activated", self._on_row_activated)
        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self._on_key_pressed)
        self.listbox.add_controller(key_ctrl)

        self.scrolled = Gtk.ScrolledWindow(vexpand=True)
        self.scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scrolled.set_child(self.listbox)
        self.scrolled.connect("edge-reached", lambda _s, pos: pos == Gtk.PositionType.BOTTOM and self._append_page())
        self.scrolled.get_vadjustment().connect("value-changed", self._on_scrolled)

        self.empty_page = Adw.StatusPage(icon_name="document-edit-symbolic")
        self.empty_page.add_css_class("compact")

        self.stack = Gtk.Stack(vexpand=True)
        self.stack.add_named(self.scrolled, "list")
        self.stack.add_named(self.empty_page, "empty")
        box.append(self.stack)

    # -- data -----------------------------------------------------------

    def refresh(self):
        """Rebuild the list from disk, keeping scroll and selection."""
        query = self.search_entry.get_text()
        try:
            entries = sidebar_entries(self.get_entries(), query, self.is_pinned)
        except OSError as e:
            print(f"Could not list notes: {e}")
            entries = []

        rows = [
            (e["path"], e["title"], body_excerpt(e["content"], 80), self.is_pinned(e["path"].name))
            for e in entries
        ]
        if rows != self._signature:
            self._signature = rows
            self._rebuild(rows)

        if rows:
            self.stack.set_visible_child_name("list")
        else:
            self.empty_page.set_title(_("No Results") if query.strip() else _("No Notes"))
            self.empty_page.set_description(
                _("Try a different search.") if query.strip() else _("New notes will show up here.")
            )
            self.stack.set_visible_child_name("empty")

    def _rebuild(self, rows):
        adj = self.scrolled.get_vadjustment()
        scroll = adj.get_value()
        while child := self.listbox.get_first_child():
            self.listbox.remove(child)
        self._rows = {}
        self._pending = list(rows)
        self._append_page()
        self.select_path(self.selected_path, reveal=False)

        def restore_scroll():
            adj.set_value(scroll)
            return False
        GLib.idle_add(restore_scroll)

    def _append_page(self, until=None):
        """Turn the next descriptors into rows; with ``until``, stop once that path exists."""
        while self._pending:
            for _i in range(self.PAGE_SIZE):
                if not self._pending:
                    break
                path, title, excerpt, pinned = self._pending.pop(0)
                row = self._make_row(path, title, excerpt, pinned)
                self._rows[path] = row
                self.listbox.append(row)
            if until is None or until in self._rows:
                return

    def _on_scrolled(self, adj):
        # Prefetch a screenful early so loading stays invisible.
        if self._pending and adj.get_upper() - (adj.get_value() + adj.get_page_size()) < adj.get_page_size():
            self._append_page()

    def _make_row(self, path, title, excerpt, pinned):
        row = Gtk.ListBoxRow()
        row.file_path = path
        row.set_tooltip_text(title)
        row.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("{title}, pinned").format(title=title) if pinned else title],
        )

        box = Gtk.Box(spacing=6)
        box.set_margin_top(4)
        box.set_margin_bottom(4)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        title_label = Gtk.Label(label=title, xalign=0, ellipsize=Pango.EllipsizeMode.END)
        text.append(title_label)
        if excerpt:
            excerpt_label = Gtk.Label(label=excerpt, xalign=0, ellipsize=Pango.EllipsizeMode.END)
            excerpt_label.add_css_class("dim-label")
            excerpt_label.add_css_class("caption")
            text.append(excerpt_label)
        box.append(text)
        if pinned:
            pin = Gtk.Image(icon_name="io.github.tanaybhomia.Whisp-pin-symbolic")
            pin.add_css_class("dim-label")
            box.append(pin)
        row.set_child(box)

        right_click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        right_click.connect("pressed", lambda _g, _n, x, y: self._popup_menu(row, x, y))
        row.add_controller(right_click)
        long_press = Gtk.GestureLongPress()
        long_press.connect("pressed", lambda _g, x, y: self._popup_menu(row, x, y))
        row.add_controller(long_press)
        return row

    # -- selection ------------------------------------------------------

    def select_path(self, path, reveal=True):
        """Highlight the row for ``path`` (or clear the highlight if it has none)."""
        self.selected_path = path
        if path is not None and path not in self._rows:
            self._append_page(until=path)
        row = self._rows.get(path)
        if row is None:
            self.listbox.unselect_all()
            return
        self.listbox.select_row(row)
        if reveal:
            GLib.idle_add(self._reveal_row, row)

    def _reveal_row(self, row, attempts=60):
        """Scroll so ``row`` is visible; rows created a moment ago aren't laid out yet, so retry."""
        if row.get_parent() is None:
            return False  # rebuilt away in the meantime
        alloc = row.get_allocation()
        if alloc.height <= 0:
            if attempts > 0:
                GLib.timeout_add(50, self._reveal_row, row, attempts - 1)
            return False
        adj = self.scrolled.get_vadjustment()
        if alloc.y < adj.get_value():
            adj.set_value(alloc.y)
        elif alloc.y + alloc.height > adj.get_value() + adj.get_page_size():
            adj.set_value(alloc.y + alloc.height - adj.get_page_size())
        return False

    def focus_search(self):
        self.search_entry.grab_focus()

    # -- interaction ----------------------------------------------------

    def _on_row_activated(self, _listbox, row):
        self.on_open(row.file_path)

    def _on_search_activate(self, _entry):
        first = self.listbox.get_row_at_index(0)
        if first is not None:
            self.on_open(first.file_path)

    def _focused_row(self):
        root = self.get_root()
        widget = root.get_focus() if root else None
        while widget is not None and not isinstance(widget, Gtk.ListBoxRow):
            widget = widget.get_parent()
        return widget if widget is not None and widget.get_parent() is self.listbox else None

    def _on_key_pressed(self, _ctrl, keyval, _keycode, state):
        row = self._focused_row()
        if row is None:
            return False
        if keyval in (Gdk.KEY_Delete, Gdk.KEY_KP_Delete):
            self.on_delete(row.file_path)
            return True
        if keyval == Gdk.KEY_Menu or (keyval == Gdk.KEY_F10 and state & Gdk.ModifierType.SHIFT_MASK):
            self._popup_menu(row)
            return True
        return False

    def _popup_menu(self, row, x=None, y=None):
        menu = Gio.Menu()
        item = Gio.MenuItem.new(_("Delete Note"), None)
        item.set_action_and_target_value("sidebar.delete", GLib.Variant.new_string(str(row.file_path)))
        menu.append_item(item)

        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.set_has_arrow(False)
        popover.set_parent(row)
        if x is not None:
            rect = Gdk.Rectangle()
            rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
            popover.set_pointing_to(rect)
        popover.connect("closed", lambda p: GLib.idle_add(p.unparent))
        popover.popup()

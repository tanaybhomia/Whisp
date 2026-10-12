import re
import uuid
import gettext
import locale
from pathlib import Path
from gi.repository import Gtk, GLib, Gdk, Adw, Graphene, Pango
import threading

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from whisp.window import WhispWindow

from whisp.config import config
from whisp.highlighter import MarkdownHighlighter
from whisp.math_eval import MathError
from whisp.text_search import body_match_offsets
from whisp.stats import tracker
from whisp.editor_helpers import EditorListMixin, EditorExpansionsMixin, extract_text_from_image, HAS_OCR

try:
    locale.setlocale(locale.LC_ALL, '')
except locale.Error:
    pass

_ = gettext.gettext

class AutocompleteRow(Gtk.ListBoxRow):
    cmd_match: str = ""

class MathTextView(Gtk.TextView):
    """TextView that draws math-note results in a column reserved on the right."""
    RESULT_MAX_WIDTH = 200
    RESULT_GAP = 24
    math_results: list = []
    base_right_margin = 0

    def set_right_margin(self, margin):
        self.base_right_margin = margin
        self._update_result_column()

    def set_math_results(self, results):
        if results == self.math_results:
            return
        self.math_results = results
        self._update_result_column()
        self.queue_draw()

    def _result_layout(self, result):
        layout = self.create_pango_layout(result)
        layout.set_width(self.RESULT_MAX_WIDTH * Pango.SCALE)
        layout.set_ellipsize(Pango.EllipsizeMode.END)
        return layout

    def _update_result_column(self):
        # Widen the right margin so text wraps before the results instead of under them.
        widths = [self._result_layout(r).get_pixel_size()[0] for r in self.math_results if r is not None]
        column = max(widths) + self.RESULT_GAP if widths else 0
        Gtk.TextView.set_right_margin(self, self.base_right_margin + column)

    def do_snapshot_layer(self, layer, snapshot):
        if layer != Gtk.TextViewLayer.ABOVE_TEXT or not self.math_results:
            return
        color = self.get_color()
        color.alpha = 0.55
        error_color = Gdk.RGBA()
        error_color.parse("#bf616a")
        visible = self.get_visible_rect()
        right = visible.x + visible.width - self.base_right_margin
        for line, result in enumerate(self.math_results):
            if result is None:
                continue
            ok, it = self.get_buffer().get_iter_at_line(line)
            if not ok:
                continue
            if not it.ends_line():
                it.forward_to_line_end()
            loc = self.get_iter_location(it)
            if loc.y + loc.height < visible.y or loc.y > visible.y + visible.height:
                continue
            layout = self._result_layout(result)
            w, h = layout.get_pixel_size()
            snapshot.save()
            snapshot.translate(Graphene.Point().init(right - w, loc.y + (loc.height - h) / 2))
            snapshot.append_layout(layout, error_color if isinstance(result, MathError) else color)
            snapshot.restore()


class NoteEditor(Gtk.Overlay, EditorListMixin, EditorExpansionsMixin):
    window: "WhispWindow | None"

    def __init__(self, file_path=None, on_title_changed=None):
        super().__init__()
        self.window = None
        self.set_hexpand(True)
        self.set_vexpand(True)
        
        self.file_path = Path(file_path) if file_path else config.data_dir / f"{uuid.uuid4().hex}.md"
        self.on_title_changed = on_title_changed
        
        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scrolled.set_propagate_natural_width(False)
        self.set_child(self.scrolled)
        
        self.textview = MathTextView()
        self.textview.add_css_class("editor-textview")
        self.textview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.textview.set_left_margin(32)
        self.textview.set_right_margin(32)
        self.textview.set_top_margin(32)
        self.textview.set_bottom_margin(60) # Modest overscroll padding
        self.textview.add_css_class(f"paper-{config.get('paper_theme', 'blank')}")
        self.scrolled.set_child(self.textview)

        self.buffer = self.textview.get_buffer()
        self.highlighter = MarkdownHighlighter(self.buffer, editor=self)

        self.load_file()

        self.buffer.connect("changed", self.on_buffer_changed)
        self.save_timeout_id = 0

        # Re-tag search highlight for the new viewport on scroll.
        self.search_scroll_timeout_id = 0
        self.scrolled.get_vadjustment().connect("value-changed", self.on_editor_scrolled)
        
        # Use ShortcutController for capture-phase keys to avoid breaking IMContext
        self.shortcut_ctrl = Gtk.ShortcutController()
        self.shortcut_ctrl.set_scope(Gtk.ShortcutScope.LOCAL)
        self.textview.add_controller(self.shortcut_ctrl)

        def add_sc(keyval, mods, cb):
            trigger = Gtk.KeyvalTrigger.new(keyval, mods)
            action = Gtk.CallbackAction.new(cb)
            shortcut = Gtk.Shortcut.new(trigger, action)
            self.shortcut_ctrl.add_shortcut(shortcut)
            
        def on_backspace(w, a):
            if not self.buffer.get_has_selection():
                insert_mark = self.buffer.get_insert()
                cursor_iter = self.buffer.get_iter_at_mark(insert_mark)
                line_start = cursor_iter.copy()
                line_start.set_line_offset(0)
                line_end = cursor_iter.copy()
                line_end.forward_to_line_end()
                
                text_before = self.buffer.get_text(line_start, cursor_iter, False)
                full_line = self.buffer.get_text(line_start, line_end, False)
                
                if self.is_list_note():
                    if re.match(r'^\s*[☐☑]\s*$', text_before):
                        if re.match(r'^\s*[☐☑]\s*$', full_line):
                            self.buffer.delete(line_start, line_end)
                            if line_start.backward_char() and line_start.get_char() == '\n':
                                t = line_start.copy()
                                t.forward_char()
            return False
            
        def on_ctrl_backspace(w, a):
            if not self.buffer.get_has_selection():
                insert_mark = self.buffer.get_insert()
                cursor_iter = self.buffer.get_iter_at_mark(insert_mark)
                line_start = cursor_iter.copy()
                line_start.set_line_offset(0)
                text_before = self.buffer.get_text(line_start, cursor_iter, False)
                
                if cursor_iter.equal(line_start):
                    return True
                if re.match(r'^(\s*[-*+]|\s*[☐☑])\s*$', text_before):
                    self.buffer.delete(line_start, cursor_iter)
                    return True
            return False
            
        def on_hash(w, a):
            if not self.buffer.get_has_selection():
                insert_mark = self.buffer.get_insert()
                cursor_iter = self.buffer.get_iter_at_mark(insert_mark)
                line_start = cursor_iter.copy()
                line_start.set_line_offset(0)
                text_before = self.buffer.get_text(line_start, cursor_iter, False)
                
                if self.is_list_note() and re.match(r'^\s*[☐☑]\s*$', text_before):
                    self.buffer.delete(line_start, cursor_iter)
            return False
            
        add_sc(Gdk.KEY_BackSpace, 0, on_backspace)
        add_sc(Gdk.KEY_BackSpace, Gdk.ModifierType.CONTROL_MASK, on_ctrl_backspace)
        add_sc(Gdk.KEY_numbersign, 0, on_hash)
        
        def cb_paste(w, a):
            self.handle_smart_paste()
            return True
        add_sc(Gdk.KEY_v, Gdk.ModifierType.CONTROL_MASK, cb_paste)
        add_sc(Gdk.KEY_V, Gdk.ModifierType.CONTROL_MASK, cb_paste)
        
        def cb_paste_plain(w, a):
            self.paste_plain_text()
            return True
        add_sc(Gdk.KEY_v, Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK, cb_paste_plain)
        add_sc(Gdk.KEY_V, Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK, cb_paste_plain)
        
        def cb_link(w, a):
            self.shorten_link()
            return True
        add_sc(Gdk.KEY_l, Gdk.ModifierType.CONTROL_MASK, cb_link)
        add_sc(Gdk.KEY_L, Gdk.ModifierType.CONTROL_MASK, cb_link)
        
        def cb_up(w, a):
            return self.move_line(-1)
        add_sc(Gdk.KEY_Up, Gdk.ModifierType.ALT_MASK, cb_up)
        
        def cb_down(w, a):
            return self.move_line(1)
        add_sc(Gdk.KEY_Down, Gdk.ModifierType.ALT_MASK, cb_down)
        
        # Add keyboard shortcuts (Bubble phase for normal shortcuts)
        key_ctrl_bubble = Gtk.EventControllerKey()
        controllers = self.textview.observe_controllers()
        for i in range(controllers.get_n_items()):
            c = controllers.get_item(i)
            if isinstance(c, Gtk.EventControllerKey) and c.get_im_context():
                key_ctrl_bubble.set_im_context(c.get_im_context())
                break
        key_ctrl_bubble.connect("key-pressed", self.on_key_pressed_bubble)
        key_ctrl_bubble.connect("key-released", self.on_key_released)
        self.textview.add_controller(key_ctrl_bubble)
        # Add gesture click for link opening
        self.click_gesture = Gtk.GestureClick()
        self.click_gesture.set_button(Gdk.BUTTON_PRIMARY)
        self.click_gesture.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self.click_gesture.connect("pressed", self.on_click_pressed)
        self.textview.add_controller(self.click_gesture)
        
        # Add motion controller for cursor
        self.last_mouse_x = None
        self.last_mouse_y = None
        self.last_mouse_state = 0
        self.cursor_pointer = Gdk.Cursor.new_from_name("pointer")
        self.is_pointer_cursor = False
        
        self.motion_controller = Gtk.EventControllerMotion()
        self.motion_controller.connect("motion", self.on_mouse_motion)
        self.motion_controller.connect("leave", self.on_mouse_leave)
        self.textview.add_controller(self.motion_controller)
        # Autocomplete Hint Overlay
        self.autocomplete_list = Gtk.ListBox()
        self.autocomplete_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.autocomplete_list.connect("row-activated", self.on_autocomplete_row_activated)
        self.autocomplete_list.connect("row-selected", self.on_autocomplete_row_selected)
        
        self.autocomplete_scroll = Gtk.ScrolledWindow()
        self.autocomplete_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.autocomplete_scroll.set_max_content_height(250)
        self.autocomplete_scroll.set_max_content_width(260)
        self.autocomplete_scroll.set_propagate_natural_height(True)
        self.autocomplete_scroll.set_propagate_natural_width(False)
        self.autocomplete_scroll.set_margin_top(6)
        self.autocomplete_scroll.set_margin_bottom(6)
        self.autocomplete_scroll.set_margin_start(6)
        self.autocomplete_scroll.set_margin_end(6)
        self.autocomplete_scroll.set_child(self.autocomplete_list)
        
        self.autocomplete_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.autocomplete_box.add_css_class("autocomplete-overlay")
        self.autocomplete_box.set_halign(Gtk.Align.START)
        self.autocomplete_box.set_valign(Gtk.Align.START)
        self.autocomplete_box.append(self.autocomplete_scroll)
        self.autocomplete_box.set_visible(False)
        self.add_overlay(self.autocomplete_box)

    def set_compact_margins(self, compact: bool):
        margin = 14 if compact else 32
        self.textview.set_left_margin(margin)
        self.textview.set_right_margin(margin)

    def on_autocomplete_row_activated(self, listbox, row):
        if hasattr(row, 'cmd_match'):
            self.apply_autocomplete(row.cmd_match, " ")
            self.textview.grab_focus()

    def on_autocomplete_row_selected(self, listbox, row):
        if row and hasattr(row, 'cmd_match'):
            self.current_top_match = row.cmd_match
            # Ensure the row is visible
            alloc = row.get_allocation()
            adj = self.autocomplete_scroll.get_vadjustment()
            if alloc.y < adj.get_value():
                adj.set_value(alloc.y)
            elif alloc.y + alloc.height > adj.get_value() + adj.get_page_size():
                adj.set_value(alloc.y + alloc.height - adj.get_page_size())

    def on_click_pressed(self, gesture, n_press, x, y):
        state = gesture.get_current_event_state()
        if state & Gdk.ModifierType.CONTROL_MASK:
            window_x = int(x)
            window_y = int(y)
            buffer_x, buffer_y = self.textview.window_to_buffer_coords(Gtk.TextWindowType.WIDGET, window_x, window_y)
            _, iter = self.textview.get_iter_at_location(buffer_x, buffer_y)
            
            if iter.has_tag(self.highlighter.tag_link):
                url = self.extract_url_at_iter(iter)
                if url:
                    gesture.set_state(Gtk.EventSequenceState.CLAIMED)
                    from gi.repository import Gio
                    try:
                        Gio.AppInfo.launch_default_for_uri(url, None)
                    except Exception as e:
                        print(f"Failed to open URL: {e}")

    def extract_url_at_iter(self, iter):
        line_start = iter.copy()
        line_start.set_line_offset(0)
        line_end = line_start.copy()
        line_end.forward_to_line_end()
        line_text = self.buffer.get_text(line_start, line_end, False)
        
        line_offset = iter.get_line_offset()
        
        # Check Markdown links [text](url)
        for m in re.finditer(r'\[(.*?)\]\((.*?)\)', line_text):
            if m.start(0) <= line_offset <= m.end(0):
                return m.group(2)
                
        # Check bare URLs
        for m in re.finditer(r'(?<!\()https?://[^\s]+', line_text):
            if m.start(0) <= line_offset <= m.end(0):
                return m.group(0)
                
        return None

    def on_mouse_motion(self, controller, x, y):
        self.last_mouse_x = x
        self.last_mouse_y = y
        self.last_mouse_state = controller.get_current_event_state()
        self.update_cursor()

    def on_mouse_leave(self, controller):
        self.last_mouse_x = None
        self.last_mouse_y = None
        if self.is_pointer_cursor:
            self.textview.set_cursor(None)
            self.is_pointer_cursor = False

    def update_cursor(self):
        if self.last_mouse_x is None or self.last_mouse_y is None:
            return
            
        is_ctrl = bool(self.last_mouse_state & Gdk.ModifierType.CONTROL_MASK)
        should_be_pointer = False
        
        if is_ctrl:
            buffer_x, buffer_y = self.textview.window_to_buffer_coords(
                Gtk.TextWindowType.WIDGET, int(self.last_mouse_x), int(self.last_mouse_y)
            )
            _, iter = self.textview.get_iter_at_location(buffer_x, buffer_y)
            if iter.has_tag(self.highlighter.tag_link):
                should_be_pointer = True
                
        if should_be_pointer and not self.is_pointer_cursor:
            self.textview.set_cursor(self.cursor_pointer)
            self.is_pointer_cursor = True
        elif not should_be_pointer and self.is_pointer_cursor:
            self.textview.set_cursor(None)
            self.is_pointer_cursor = False

    def on_buffer_changed(self, buffer):
        self.convert_markdown_checkboxes()
        if self.save_timeout_id:
            GLib.source_remove(self.save_timeout_id)
        self.save_timeout_id = GLib.timeout_add(1000, self.save_file)
        
        if self.on_title_changed:
            self.on_title_changed(self)
            
        self.check_autocomplete()

    def is_wysiwyg_enabled(self):
        scope = config.get("wysiwyg_scope", "global")
        if scope == "global":
            return config.get("wysiwyg_mode", False)
        else:
            if hasattr(self, 'window') and self.window:
                return self.window.metadata.get(self.file_path.name, {}).get("wysiwyg", False)
            return False

    def check_autocomplete(self):
        insert_mark = self.buffer.get_insert()
        cursor_iter = self.buffer.get_iter_at_mark(insert_mark)
        
        word_start = cursor_iter.copy()
        while word_start.backward_char():
            if word_start.get_char() in (' ', '\n', '\t'):
                word_start.forward_char()
                break
                
        word = self.buffer.get_text(word_start, cursor_iter, False)
        
        if word.startswith("::") and len(word) >= 2: # Show after typing at least "::"
            # Anchor the popup to the start of the word (the '::') so it doesn't jump as you type
            rect = self.textview.get_iter_location(word_start)
            win_x, win_y = self.textview.buffer_to_window_coords(Gtk.TextWindowType.WIDGET, rect.x, rect.y + rect.height)
            
            trans = self.textview.translate_coordinates(self, win_x, win_y)
            if trans:
                x, y = trans
            else:
                x, y = win_x, win_y
                
            x, y = int(x), int(y)
            
            while child := self.autocomplete_list.get_first_child():
                self.autocomplete_list.remove(child)
                
            suggestions = [
                ("::count", _("Insert word count of current note")),
                ("::today", _("Current date")),
                ("::today(5)", _("Date offset (+/- days)")),
                ("::tomorrow", _("Tomorrow's date")),
                ("::yesterday", _("Yesterday's date")),
                ("::date", _("Current date")),
                ("::date(5)", _("Date offset (+/- days)")),
                ("::timestamp", _("Current date and time")),
                ("::now", _("Current time")),
                ("::time", _("Current time")),
                ("::random(int,20)", _("Random numbers")),
                ("::random(str,20)", _("Random letters")),
                ("::random(alnum,20)", _("Random alphanumeric")),
                ("::random_quote", _("Insert a random inspirational quote")),
                ("::random_wiki", _("Open a random Wikipedia page")),
                ("::magic8ball", _("Ask the Magic 8-Ball")),
                ("::lorem", _("Insert standard placeholder text")),
                ("::password", _("Generate a secure 16-char password")),
                ("::uuid", _("Generate a standard developer UUID")),
                ("::roll(20)", _("Roll a 20-sided die")),
                ("::roll(d20)", _("Roll a d20 die")),
                ("::roll(4d6)", _("Roll 4 d6 dice")),
                ("::uppercase", _("Convert document to uppercase")),
                ("::lowercase", _("Convert document to lowercase")),
                ("::sentence_case", _("Convert document to sentence case")),
                ("::title_case", _("Convert document to title case")),
                ("::capitalize_first", _("Capitalize first letter")),
                ("::remove_quotes", _("Strip surrounding quotes")),
                ("::append(text)", _("Add text to the end of every line")),
                ("::prepend(text)", _("Add text to the beginning of every line")),
                ("::replace(old,new)", _("Find and replace text")),
                ("::sort_lines_alpha", _("Sort lines alphabetically")),
                ("::sort_lines_number", _("Sort lines by first number")),
                ("::sort_lines_reverse", _("Reverse line order")),
                ("::remove_lines_empty", _("Remove empty lines")),
                ("::remove_lines_with(text)", _("Remove lines containing text")),
                ("::remove_lines_without(text)", _("Remove lines not containing text")),
                ("::keep_lines_with(text)", _("Keep only lines containing text")),
                ("::keep_lines_without(text)", _("Keep only lines not containing text")),
                ("::trim_each_whitespace", _("Trim whitespace from all lines")),
                ("::dedupe_lines", _("Remove duplicate lines")),
                ("::get_dupes", _("Find and group duplicate lines")),
                ("::commas_to_list", _("Convert comma-separated items to list")),
                ("::commas_to(delim)", _("Convert commas to custom delimiter")),
                ("::lines_to_commas", _("Convert lines to comma-separated")),
                ("::lines_to(delim)", _("Convert lines to custom delimiter")),
                ("::checked_to_bottom", _("Move checked items to bottom")),
                ("::remove_checked", _("Remove all checked items"))
            ]
            
            matches = [s for s in suggestions if s[0].startswith(word.lower())]
            if matches:
                self.current_top_match = matches[0][0]
                first_row = None
                for cmd, desc in matches:
                    row_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                    row_box.set_margin_start(12)
                    row_box.set_margin_end(12)
                    row_box.set_margin_top(8)
                    row_box.set_margin_bottom(8)
                    
                    cmd_lbl = Gtk.Label(label=f"<b>{cmd}</b>")
                    cmd_lbl.set_use_markup(True)
                    from gi.repository import Pango
                    cmd_lbl.set_ellipsize(Pango.EllipsizeMode.END)
                    cmd_lbl.set_halign(Gtk.Align.START)
                    
                    desc_lbl = Gtk.Label(label=f"<small>{desc}</small>")
                    desc_lbl.set_use_markup(True)
                    desc_lbl.add_css_class("dim-label")
                    desc_lbl.set_ellipsize(Pango.EllipsizeMode.END)
                    desc_lbl.set_halign(Gtk.Align.START)
                    
                    row_box.append(cmd_lbl)
                    row_box.append(desc_lbl)
                    
                    row = AutocompleteRow()
                    row.set_child(row_box)
                    row.cmd_match = cmd
                    self.autocomplete_list.append(row)
                    
                    if first_row is None:
                        first_row = row
                        
                if first_row:
                    self.autocomplete_list.select_row(first_row)
                    
                # Ensure the dropdown doesn't get cut off on the right or bottom
                # Estimate height since GTK layout is lazy and nat_req might return stale values
                box_h = min(300, len(matches) * 56 + 12) # Taller estimate for vertical layout
                
                editor_w = self.get_width()
                editor_h = self.get_height()
                
                # Use a stable assumed width for bounds checking to prevent X-coordinate jitter
                assumed_box_w = 260
                final_x = x - 8 # Shift left to align text inside the popover with editor text
                if editor_w > 0 and final_x + assumed_box_w > editor_w - 16:
                    final_x = max(16, editor_w - assumed_box_w - 16)
                final_x = max(8, final_x)
                    
                self.autocomplete_box.set_margin_start(final_x)
                
                if editor_w > 0:
                    available_w = max(100, editor_w - final_x - 16)
                    target_w = min(260, available_w)
                    self.autocomplete_scroll.set_size_request(target_w, -1)
                else:
                    self.autocomplete_scroll.set_size_request(260, -1)
                    
                final_y = y + 16 # Add 16px gap for dropdown to clear shadow
                if editor_h > 0:
                    # Subtract a buffer to guarantee a visual gap at the bottom of the screen
                    space_below = editor_h - final_y - 32
                    space_above = y - rect.height - 16 - 32
                    self.autocomplete_box.set_margin_bottom(32)
                    
                    # Heavily prefer dropping DOWN. Only drop UP if space below is tiny (< 160px)
                    if space_below < 160 and space_above > space_below and box_h > space_below:
                        # Drop up!
                        actual_box_h = min(box_h, space_above)
                        # We use 32px gap here (instead of 16px) because the popup casts a 16px shadow DOWNWARDS.
                        # This extra 16px ensures the visual gap above the text perfectly matches the dropdown!
                        final_y = max(16, y - rect.height - 32 - actual_box_h)
                        self.autocomplete_scroll.set_max_content_height(int(actual_box_h))
                    else:
                        # Drop down but strictly constrain to space below
                        self.autocomplete_scroll.set_max_content_height(int(min(300, max(50, space_below))))
                else:
                    self.autocomplete_scroll.set_max_content_height(300)
                        
                self.autocomplete_box.set_margin_top(int(final_y))
                    
                self.autocomplete_box.set_visible(True)
            else:
                self.autocomplete_box.set_visible(False)
        else:
            self.autocomplete_box.set_visible(False)

    def on_key_released(self, controller, keyval, keycode, state):
        if keyval in (Gdk.KEY_Control_L, Gdk.KEY_Control_R):
            self.last_mouse_state &= ~Gdk.ModifierType.CONTROL_MASK
            self.update_cursor()
        return False

    def on_key_pressed_bubble(self, controller, keyval, keycode, state):
        if keyval in (Gdk.KEY_Control_L, Gdk.KEY_Control_R):
            self.last_mouse_state |= Gdk.ModifierType.CONTROL_MASK
            self.update_cursor()
        if state & Gdk.ModifierType.CONTROL_MASK:
            if state & Gdk.ModifierType.SHIFT_MASK:
                if keyval == Gdk.KEY_c or keyval == Gdk.KEY_C:
                    self.buffer.insert_at_cursor("☐ ")
                    return True
                elif keyval == Gdk.KEY_s or keyval == Gdk.KEY_S:
                    self.wrap_text("~~", "~~", "strikethrough")
                    return True
                    
            if keyval == Gdk.KEY_b or keyval == Gdk.KEY_B:
                self.wrap_text("**", "**", "bold")
                return True
            elif keyval == Gdk.KEY_i or keyval == Gdk.KEY_I:
                self.wrap_text("*", "*", "italic")
                return True
            elif keyval == Gdk.KEY_u or keyval == Gdk.KEY_U:
                self.wrap_text("_", "_", "underline")
                return True
            elif keyval == Gdk.KEY_s or keyval == Gdk.KEY_S:
                self.toggle_checkbox()
                return True
                
        if keyval == Gdk.KEY_bracketright:
            insert_mark = self.buffer.get_insert()
            cursor_iter = self.buffer.get_iter_at_mark(insert_mark)
            line_start = cursor_iter.copy()
            line_start.set_line_offset(0)
            line_text = self.buffer.get_text(line_start, cursor_iter, False) + "]"
            m_empty = re.match(r"^(\s*)([-*+])\s*\[\s*\]$", line_text)
            if m_empty:
                self.buffer.delete(line_start, cursor_iter)
                self.buffer.insert_at_cursor(m_empty.group(1) + "☐ ")
                return True
            m_checked = re.match(r"^(\s*)([-*+])\s*\[[xX]\]$", line_text)
            if m_checked:
                self.buffer.delete(line_start, cursor_iter)
                self.buffer.insert_at_cursor(m_checked.group(1) + "☑ ")
                return True

        if keyval == Gdk.KEY_Escape:
            if self.autocomplete_box.get_visible():
                self.autocomplete_box.set_visible(False)
                return True
                
        if keyval == Gdk.KEY_Down and self.autocomplete_box.get_visible():
            row = self.autocomplete_list.get_selected_row()
            if row:
                next_row = self.autocomplete_list.get_row_at_index(row.get_index() + 1)
                if next_row:
                    self.autocomplete_list.select_row(next_row)
            return True
            
        if keyval == Gdk.KEY_Up and self.autocomplete_box.get_visible():
            row = self.autocomplete_list.get_selected_row()
            if row and row.get_index() > 0:
                prev_row = self.autocomplete_list.get_row_at_index(row.get_index() - 1)
                if prev_row:
                    self.autocomplete_list.select_row(prev_row)
            return True
                
        if keyval == Gdk.KEY_Return or keyval == Gdk.KEY_KP_Enter:
            if not (state & Gdk.ModifierType.SHIFT_MASK):
                if self.autocomplete_box.get_visible() and getattr(self, 'current_top_match', None):
                    self.apply_autocomplete(self.current_top_match, " ")
                    return True
                        
                if self.handle_expansion("\n"):
                    return True
                return self.handle_return()
            
        if keyval in (Gdk.KEY_Tab, Gdk.KEY_KP_Tab, Gdk.KEY_ISO_Left_Tab):
            if (state & Gdk.ModifierType.SHIFT_MASK) or keyval == Gdk.KEY_ISO_Left_Tab or (state & Gdk.ModifierType.CONTROL_MASK):
                return self.handle_shift_tab()
            else:
                return self.handle_tab()
                
        if keyval == Gdk.KEY_space and not (state & Gdk.ModifierType.SHIFT_MASK):
            if self.autocomplete_box.get_visible() and getattr(self, 'current_top_match', None):
                insert_mark = self.buffer.get_insert()
                cursor_iter = self.buffer.get_iter_at_mark(insert_mark)
                word_start = cursor_iter.copy()
                while word_start.backward_char():
                    if word_start.get_char() in (' ', '\n', '\t'):
                        word_start.forward_char()
                        break
                word = self.buffer.get_text(word_start, cursor_iter, False)
                
                if word != "::":
                    self.apply_autocomplete(self.current_top_match, " ")
                    return True
                else:
                    self.autocomplete_box.set_visible(False)
                    
            if self.handle_expansion(" "):
                return True
            
        return False

    def apply_autocomplete(self, match_str, insert_char):
        insert_mark = self.buffer.get_insert()
        cursor_iter = self.buffer.get_iter_at_mark(insert_mark)
        
        word_start = cursor_iter.copy()
        while word_start.backward_char():
            if word_start.get_char() in (' ', '\n', '\t'):
                word_start.forward_char()
                break
                
        self.buffer.delete(word_start, cursor_iter)
        
        if "(" in match_str:
            self.buffer.insert_at_cursor(match_str)
            
            insert_mark = self.buffer.get_insert()
            cursor_iter = self.buffer.get_iter_at_mark(insert_mark)
            cursor_iter.backward_char()
            
            open_paren = cursor_iter.copy()
            while open_paren.backward_char():
                if open_paren.get_char() == '(':
                    open_paren.forward_char()
                    break
            
            self.buffer.select_range(open_paren, cursor_iter)
            self.autocomplete_box.set_visible(False)
        else:
            self.buffer.insert_at_cursor(match_str)
            if not self.handle_expansion(insert_char):
                self.buffer.insert_at_cursor(insert_char)
                self.autocomplete_box.set_visible(False)
            self.textview.scroll_to_mark(self.buffer.get_insert(), 0.05, False, 0.0, 0.0)

    def wrap_text(self, prefix, suffix, default_text):
        bounds = self.buffer.get_selection_bounds()
        if not bounds:
            insert_iter = self.buffer.get_iter_at_mark(self.buffer.get_insert())
            
            check_start = insert_iter.copy()
            check_start.backward_chars(len(prefix))
            check_end = insert_iter.copy()
            check_end.forward_chars(len(suffix))
            
            text_before = self.buffer.get_text(check_start, insert_iter, False)
            text_after = self.buffer.get_text(insert_iter, check_end, False)
            
            if text_before == prefix and text_after == suffix:
                self.buffer.delete(check_start, check_end)
                return
                
            if text_after == suffix:
                insert_iter.forward_chars(len(suffix))
                self.buffer.place_cursor(insert_iter)
                return
                
            self.buffer.insert(insert_iter, prefix + suffix)
            insert_iter = self.buffer.get_iter_at_mark(self.buffer.get_insert())
            insert_iter.backward_chars(len(suffix))
            self.buffer.place_cursor(insert_iter)
            return
            
        start, end = bounds
        text = self.buffer.get_text(start, end, False)
        self.buffer.delete(start, end)
        
        start = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        
        # Check if the text is already wrapped
        if text.startswith(prefix) and text.endswith(suffix) and len(text) >= len(prefix) + len(suffix):
            # Toggle off (unwrap)
            new_text = text[len(prefix):-len(suffix)]
        else:
            # Toggle on (wrap)
            new_text = f"{prefix}{text}{suffix}"
            
        self.buffer.insert(start, new_text)
        
        # Re-select the newly modified text
        insert_iter = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        insert_iter.backward_chars(len(new_text))
        bound_iter = insert_iter.copy()
        bound_iter.forward_chars(len(new_text))
        self.buffer.select_range(insert_iter, bound_iter)

    def paste_plain_text(self):
        clipboard = self.textview.get_clipboard()
        clipboard.read_text_async(None, self.on_clipboard_read)

    def on_clipboard_read(self, clipboard, result):
        try:
            text = clipboard.read_text_finish(result)
            if text:
                # Strip markdown syntax for plain text paste
                text = re.sub(r'#{1,6}\s+', '', text)
                text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
                text = re.sub(r'(?<!\*)\*(.*?)\*(?!\*)', r'\1', text)
                text = re.sub(r'__(.*?)__', r'\1', text)
                text = re.sub(r'(?<!_)_(.*?)_(?!_)', r'\1', text)
                text = re.sub(r'`(.*?)`', r'\1', text)
                text = re.sub(r'~~(.*?)~~', r'\1', text)
                text = re.sub(r'\[(.*?)\]\(.*?\)', r'\1', text)
                text = re.sub(r'^\s*[-*+]\s+', '', text, flags=re.MULTILINE)
                text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)
                text = re.sub(r'^\s*[☐☑]\s*', '', text, flags=re.MULTILINE)
                
                def do_insert():
                    self.buffer.delete_selection(True, self.textview.get_editable())
                    self.buffer.insert_at_cursor(text)
                    return False
                GLib.idle_add(do_insert)
        except GLib.Error:
            pass

    def shorten_link(self):
        bounds = self.buffer.get_selection_bounds()
        start = end = None
        url = ""
        
        if bounds:
            start, end = bounds
            url = self.buffer.get_text(start, end, False).strip()
        else:
            # Grab word under cursor
            insert_iter = self.buffer.get_iter_at_mark(self.buffer.get_insert())
            start = insert_iter.copy()
            end = insert_iter.copy()
            
            # Move start to beginning of word (or non-whitespace)
            while start.backward_char():
                if start.get_char() in (' ', '\n', '\t'):
                    start.forward_char()
                    break
                    
            # Move end to end of word
            while end.forward_char():
                if end.get_char() in (' ', '\n', '\t'):
                    break
                    
            url = self.buffer.get_text(start, end, False).strip()
            
        if re.match(r'^https?://[^\s]+$', url) and len(url) > 30:
            display_url = url.replace("https://", "").replace("http://", "").replace("www.", "")
            if len(display_url) > 25:
                display_url = display_url[:25] + "…"
            markdown_link = f"[{display_url}]({url})"
            
            start_mark = self.buffer.create_mark(None, start, True)
            end_mark = self.buffer.create_mark(None, end, False)
            GLib.idle_add(self.replace_mark_range, start_mark, end_mark, markdown_link)

    def replace_mark_range(self, start_mark, end_mark, new_text):
        start = self.buffer.get_iter_at_mark(start_mark)
        end = self.buffer.get_iter_at_mark(end_mark)
        self.buffer.delete(start, end)
        self.buffer.insert(start, new_text)
        self.buffer.delete_mark(start_mark)
        self.buffer.delete_mark(end_mark)
        return False

    def load_file(self):
        if self.file_path.exists():
            content = self.file_path.read_text(encoding='utf-8')
            self.buffer.set_text(content)
            self.highlighter.highlight()
            self.last_word_count = len(content.split())
        else:
            self.last_word_count = 0

    def set_search_highlight(self, term):
        self.highlighter.set_search_term(term)

    def on_editor_scrolled(self, vadj):
        # Throttle (not debounce) so highlights refresh during a continuous scroll.
        if not self.highlighter.search_term:
            return
        if self.search_scroll_timeout_id:
            return
        self.search_scroll_timeout_id = GLib.timeout_add(16, self._reapply_search_highlight)

    def _reapply_search_highlight(self):
        self.search_scroll_timeout_id = 0
        self.highlighter.highlight_search()
        return False

    def scroll_to_match(self, term, occurrence_index):
        # Search the live buffer, not a disk offset (an open editor can diverge).
        if not term:
            return
        # Defer + retry: a just-inserted editor has no layout to scroll to yet.
        def do_scroll():
            start, end = self.buffer.get_bounds()
            offsets = body_match_offsets(self.buffer.get_text(start, end, True), term)
            if not offsets:
                return False
            offset = offsets[min(occurrence_index, len(offsets) - 1)]
            s_iter = self.buffer.get_iter_at_offset(offset)
            e_iter = self.buffer.get_iter_at_offset(offset + len(term))
            self.buffer.select_range(s_iter, e_iter)
            self.textview.scroll_to_mark(self.buffer.get_insert(), 0.1, True, 0.0, 0.3)
            self.textview.grab_focus()
            return False
        GLib.idle_add(do_scroll)
        GLib.timeout_add(80, do_scroll)
        GLib.timeout_add(180, do_scroll)

    def save_file(self):
        self.save_timeout_id = 0
        start, end = self.buffer.get_bounds()
        text = self.buffer.get_text(start, end, True)
        self.file_path.write_text(text, encoding='utf-8')
        
        current_words = len(text.split())
        diff = current_words - getattr(self, 'last_word_count', 0)
        if diff > 0:
            tracker.increment("total_words_written", diff)
        self.last_word_count = current_words
        
        return False

    def get_title(self, max_length=50):
        start, end = self.buffer.get_bounds()
        text = self.buffer.get_text(start, end, True)
        first_line = text.split('\n')[0].strip() if text else ""
        first_line = re.sub(r'^#+\s*', '', first_line)
        if first_line and len(first_line) > max_length:
            first_line = first_line[:max_length].rstrip() + "…"
        return first_line if first_line else "New Note"

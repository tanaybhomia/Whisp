import re
import uuid
import gettext
import locale
from pathlib import Path
from gi.repository import Gtk, GLib, Gdk, Adw
import os
import tempfile
import threading

try:
    import pytesseract
    from PIL import Image, ImageOps
    HAS_OCR = True
except ImportError:
    HAS_OCR = False
from whisp.config import config, DATA_DIR
from whisp.highlighter import MarkdownHighlighter
from whisp.text_search import body_match_offsets
from whisp.stats import tracker

try:
    locale.setlocale(locale.LC_ALL, '')
except locale.Error:
    pass

_ = gettext.gettext

def extract_text_from_image(img):
    """Extract text from a PIL Image using Tesseract OCR with noise filtering and indentation preservation."""
    if not HAS_OCR:
        return ""
    import time, re
    start_time = time.time()
    try:
        # Convert to grayscale ('L' mode) to significantly speed up Tesseract
        img = img.convert('L')
        
        # Boost contrast to make text pop against backgrounds and reduce hallucinations
        from PIL import ImageEnhance
        img = ImageEnhance.Contrast(img).enhance(1.5)
        
        # Upscale the image by 2x using Lanczos resampling (sharper edges for text than Bilinear)
        width, height = img.size
        if width < 3000 and height < 3000:
            img = img.resize((width * 2, height * 2), Image.Resampling.LANCZOS)
        
        # Add a white border so Tesseract doesn't fail on text too close to the edge
        img = ImageOps.expand(img, border=20, fill='white')
        
        data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT, config='--psm 6')
        
        lines = {}
        char_widths = []
        
        for i in range(len(data['text'])):
            word = data['text'][i].strip()
            if not word:
                continue
                
            try:
                conf = int(data['conf'][i])
                if conf != -1 and conf < 35:
                    continue
            except (ValueError, TypeError, KeyError):
                pass
                
            line_num = data['line_num'][i]
            block_num = data['block_num'][i]
            par_num = data['par_num'][i]
            
            char_widths.append(data['width'][i] / len(word))
            
            key = (block_num, par_num, line_num)
            if key not in lines:
                lines[key] = []
                
            lines[key].append({
                'text': word,
                'left': data['left'][i],
                'width': data['width'][i]
            })
            
        char_widths.sort()
        median_char_width = char_widths[len(char_widths)//2] if char_widths else 20
        space_width = median_char_width * 1.2 
        
        out = []
        min_left = min([words[0]['left'] for words in lines.values()]) if lines else 0
        
        for key in sorted(lines.keys()):
            words = lines[key]
            first_left = words[0]['left']
            
            # Calculate indentation but cap it heavily so missed words don't cause massive indents
            indent_spaces = int(round((first_left - min_left) / space_width))
            if indent_spaces > 8:
                indent_spaces = 8
            elif indent_spaces == 1:
                indent_spaces = 0
                
            line_str = " " * indent_spaces
            
            for j, w in enumerate(words):
                line_str += w['text']
                if j < len(words) - 1:
                    # Just use a single space between words on the same line.
                    # Attempting to preserve exact gaps often results in weird artifacts
                    # if Tesseract misses a word or miscalculates bounds.
                    line_str += " "
                    
            out.append(line_str)
            
        extracted_text = "\n".join(out)
        
        elapsed = time.time() - start_time
        benchmark_str = f"[OCR Benchmark] Extracted {len(extracted_text)} chars in {elapsed:.3f} seconds."
        print(benchmark_str)
        
        # Sanity check against Tesseract hallucinating random noise on non-text images (like textures or landscapes)
        has_valid_word = bool(re.search(r'[a-zA-Z0-9]{2,}', extracted_text))
        total_alphanumeric = len(re.findall(r'[a-zA-Z0-9]', extracted_text))
        is_valid_text = bool(extracted_text.strip()) and has_valid_word and (total_alphanumeric >= 3)
        
        return extracted_text.strip() if is_valid_text else ""
    except Exception as e:
        print(f"OCR Error: {e}")
        return ""

class NoteEditor(Gtk.Overlay):
    def __init__(self, file_path=None, on_title_changed=None):
        super().__init__()
        self.set_hexpand(True)
        self.set_vexpand(True)
        
        self.file_path = Path(file_path) if file_path else config.data_dir / f"{uuid.uuid4().hex}.md"
        self.on_title_changed = on_title_changed
        
        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scrolled.set_propagate_natural_width(False)
        self.set_child(self.scrolled)
        
        self.textview = Gtk.TextView()
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

    def convert_markdown_checkboxes(self):
        if getattr(self, "_converting_checkboxes", False):
            return
        
        start_iter = self.buffer.get_start_iter()
        end_iter = self.buffer.get_end_iter()
        text = self.buffer.get_text(start_iter, end_iter, False)
        
        m_empty = list(re.finditer(r"^(\s*)([-*+])\s*\[\s*\](\s?)", text, re.MULTILINE))
        m_checked = list(re.finditer(r"^(\s*)([-*+])\s*\[[xX]\](\s?)", text, re.MULTILINE))
        
        if not m_empty and not m_checked:
            return

        self._converting_checkboxes = True
        try:
            all_matches = []
            for m in m_empty:
                all_matches.append((m.start(), m.end(), m.group(1) + "☐ "))
            for m in m_checked:
                all_matches.append((m.start(), m.end(), m.group(1) + "☑ "))
                
            all_matches.sort(key=lambda x: x[0], reverse=True)
            
            for start_off, end_off, new_str in all_matches:
                s_mark = self.buffer.create_mark(None, self.buffer.get_iter_at_offset(start_off), True)
                e_mark = self.buffer.create_mark(None, self.buffer.get_iter_at_offset(end_off), False)
                self.buffer.delete(self.buffer.get_iter_at_mark(s_mark), self.buffer.get_iter_at_mark(e_mark))
                self.buffer.insert(self.buffer.get_iter_at_mark(s_mark), new_str)
                self.buffer.delete_mark(s_mark)
                self.buffer.delete_mark(e_mark)
        finally:
            self._converting_checkboxes = False

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
                    
                    row = Gtk.ListBoxRow()
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

    def get_line_text(self, line_num):
        if line_num < 0 or line_num >= self.buffer.get_line_count():
            return ""
        _, start = self.buffer.get_iter_at_line(line_num)
        end = start.copy()
        if not end.ends_line():
            end.forward_to_line_end()
        return self.buffer.get_text(start, end, False)

    def move_line(self, direction):
        """Moves current line (and its subtree if it's a list item) up (-1) or down (1)."""
        insert_mark = self.buffer.get_insert()
        cursor_iter = self.buffer.get_iter_at_mark(insert_mark)
        
        curr_line = cursor_iter.get_line()
        curr_text = self.get_line_text(curr_line)
        
        list_regex = r'^(\s*)([-*+]\s+|\d+\.\s+|- \[ \]\s+|[☐☑]\s*)'
        curr_match = re.match(list_regex, curr_text)
        
        if not curr_match:
            # Normal single-line swap
            target_line = curr_line + direction
            if target_line < 0 or target_line >= self.buffer.get_line_count():
                return False
            upper_start = min(curr_line, target_line)
            upper_end = upper_start
            lower_start = max(curr_line, target_line)
            lower_end = lower_start
        else:
            # Tree-aware swap
            curr_indent = len(curr_match.group(1))
            subtree_start = curr_line
            subtree_end = curr_line
            
            # Find end of current subtree
            for i in range(curr_line + 1, self.buffer.get_line_count()):
                text = self.get_line_text(i)
                if not text.strip():
                    break
                m = re.match(r'^(\s*)', text)
                indent = len(m.group(1)) if m else 0
                if indent > curr_indent:
                    subtree_end = i
                else:
                    break
                    
            sibling_start = None
            sibling_end = None
            
            if direction == -1:
                # Look up for previous sibling
                for i in range(curr_line - 1, -1, -1):
                    text = self.get_line_text(i)
                    if not text.strip():
                        break
                    m = re.match(r'^(\s*)', text)
                    indent = len(m.group(1)) if m else 0
                    if indent < curr_indent:
                        break # hit parent
                    if indent == curr_indent and re.match(list_regex, text):
                        sibling_start = i
                        sibling_end = curr_line - 1
                        break
            else:
                # Look down for next sibling
                for i in range(subtree_end + 1, self.buffer.get_line_count()):
                    text = self.get_line_text(i)
                    if not text.strip():
                        break
                    m = re.match(r'^(\s*)', text)
                    indent = len(m.group(1)) if m else 0
                    if indent < curr_indent:
                        break # hit next parent
                    if indent == curr_indent and re.match(list_regex, text):
                        sibling_start = i
                        sibling_end = i
                        for j in range(i + 1, self.buffer.get_line_count()):
                            t = self.get_line_text(j)
                            if not t.strip():
                                break
                            m2 = re.match(r'^(\s*)', t)
                            ind = len(m2.group(1)) if m2 else 0
                            if ind > curr_indent:
                                sibling_end = j
                            else:
                                break
                        break
                        
            if sibling_start is None:
                return False # No sibling in that direction
                
            if direction == -1:
                upper_start = sibling_start
                upper_end = sibling_end
                lower_start = subtree_start
                lower_end = subtree_end
            else:
                upper_start = subtree_start
                upper_end = subtree_end
                lower_start = sibling_start
                lower_end = sibling_end

        # Extract blocks
        _, us = self.buffer.get_iter_at_line(upper_start)
        _, ue = self.buffer.get_iter_at_line(upper_end)
        if not ue.ends_line():
            ue.forward_to_line_end()
        upper_text = self.buffer.get_text(us, ue, False)

        _, ls = self.buffer.get_iter_at_line(lower_start)
        _, le = self.buffer.get_iter_at_line(lower_end)
        if not le.ends_line():
            le.forward_to_line_end()
        lower_text = self.buffer.get_text(ls, le, False)

        self.buffer.begin_user_action()
        
        _, del_start = self.buffer.get_iter_at_line(upper_start)
        _, del_end = self.buffer.get_iter_at_line(lower_end)
        if not del_end.ends_line():
            del_end.forward_to_line_end()

        self.buffer.delete(del_start, del_end)

        _, ins = self.buffer.get_iter_at_line(upper_start)
        self.buffer.insert(ins, lower_text + "\n" + upper_text)
        
        self.buffer.end_user_action()
        
        if direction == -1:
            new_curr_line = curr_line - (upper_end - upper_start + 1)
        else:
            new_curr_line = curr_line + (lower_end - lower_start + 1)
            
        _, new_cursor_iter = self.buffer.get_iter_at_line(new_curr_line)
        match = re.match(list_regex, curr_text)
        offset = len(match.group(0)) if match else 0
        new_cursor_iter.set_line_offset(min(offset, len(curr_text)))
        self.buffer.place_cursor(new_cursor_iter)
        
        self.textview.scroll_to_mark(self.buffer.get_insert(), 0.05, False, 0.0, 0.0)
        
        return True

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

    def handle_expansion(self, insert_char):
        insert_mark = self.buffer.get_insert()
        cursor_iter = self.buffer.get_iter_at_mark(insert_mark)
        
        word_start = cursor_iter.copy()
        while word_start.backward_char():
            if word_start.get_char() in (' ', '\n', '\t'):
                word_start.forward_char()
                break
                
        word_end = cursor_iter.copy()
        while not word_end.is_end():
            char = word_end.get_char()
            if char in (' ', '\n', '\t'):
                break
            word_end.forward_char()
                
        word = self.buffer.get_text(word_start, word_end, False)
        
        # Check for Markdown checkboxes
        line_start = cursor_iter.copy()
        line_start.set_line_offset(0)
        line_text = self.buffer.get_text(line_start, cursor_iter, False)
        
        m_empty = re.match(r'^(\s*)([-*+])\s*\[\s*\]$', line_text)
        if m_empty:
            self.buffer.delete(line_start, cursor_iter)
            self.buffer.insert_at_cursor(m_empty.group(1) + "☐" + insert_char)
            self.autocomplete_box.set_visible(False)
            return True
            
        m_checked = re.match(r'^(\s*)([-*+])\s*\[[xX]\]$', line_text)
        if m_checked:
            self.buffer.delete(line_start, cursor_iter)
            self.buffer.insert_at_cursor(m_checked.group(1) + "☑" + insert_char)
            self.autocomplete_box.set_visible(False)
            return True
        
        # Check for ::today(offset) or ::date(offset) or ::timestamp
        m_date = re.match(r'^::(today|date|tomorrow|yesterday|timestamp)(?:\(([-+]*\d+)\))?$', word, re.IGNORECASE)
        if m_date:
            from gi.repository import GLib
            base = m_date.group(1)
            offset_days = int(m_date.group(2)) if m_date.group(2) else 0
            
            if base == "tomorrow":
                offset_days += 1
            elif base == "yesterday":
                offset_days -= 1
                
            now = GLib.DateTime.new_now_local()
            if offset_days != 0:
                now = now.add_days(offset_days)
                
            if base == "timestamp":
                t_str = now.format("%X")
                t_str = re.sub(r':\d{2}(?=\s|$)', '', t_str)
                date_str = f"{now.format('%x')} {t_str}"
            else:
                date_str = now.format("%x")
                
            self.buffer.delete(word_start, word_end)
            self.buffer.insert_at_cursor(date_str + insert_char)
            self.autocomplete_box.set_visible(False)
            self.textview.scroll_to_mark(self.buffer.get_insert(), 0.05, False, 0.0, 0.0)
            return True
            
        # Check for ::count or ::words or ::word_count
        m_count = re.match(r"^::(count|words|word_count)$", word, re.IGNORECASE)
        if m_count:
            self.buffer.delete(word_start, word_end)
            full_text = self.buffer.get_text(self.buffer.get_start_iter(), self.buffer.get_end_iter(), False)
            word_count = str(len(full_text.split()))
            self.buffer.insert_at_cursor(word_count + insert_char)
            self.autocomplete_box.set_visible(False)
            self.textview.scroll_to_mark(self.buffer.get_insert(), 0.05, False, 0.0, 0.0)
            return True

        # Check for ::time or ::now
        m_time = re.match(r'^::(time|now)$', word, re.IGNORECASE)
        if m_time:
            from gi.repository import GLib
            time_str = GLib.DateTime.new_now_local().format("%X")
            time_str = re.sub(r':\d{2}(?=\s|$)', '', time_str)
            self.buffer.delete(word_start, word_end)
            self.buffer.insert_at_cursor(time_str + insert_char)
            self.autocomplete_box.set_visible(False)
            self.textview.scroll_to_mark(self.buffer.get_insert(), 0.05, False, 0.0, 0.0)
            return True
            
        # Check for ::roll(N)
        m_roll = re.match(r'^::roll\((.+)\)$', word, re.IGNORECASE)
        if m_roll:
            import random
            roll_expr = m_roll.group(1).lower()
            try:
                if 'd' in roll_expr:
                    parts = roll_expr.split('d')
                    num_dice = int(parts[0]) if parts[0] else 1
                    sides = int(parts[1])
                else:
                    num_dice = 1
                    sides = int(roll_expr)
                    
                if sides < 1 or num_dice < 1 or num_dice > 100:
                    raise ValueError
                    
                total = sum(random.randint(1, sides) for _ in range(num_dice))
                res = str(total)
                self.buffer.delete(word_start, word_end)
                self.buffer.insert_at_cursor(res + insert_char)
                self.autocomplete_box.set_visible(False)
                self.textview.scroll_to_mark(self.buffer.get_insert(), 0.05, False, 0.0, 0.0)
                return True
            except:
                pass # Ignore invalid roll syntax and let it remain as text
                
        # Check for ::random(type, length)
        m_random = re.match(r'^::random\((int|alnum|str),\s*(\d+)\)$', word, re.IGNORECASE)
        if m_random:
            import string
            import random
            rtype = m_random.group(1).lower()
            length = min(int(m_random.group(2)), 1000) # Cap length to avoid freezing
            
            if rtype == "int":
                chars = string.digits
            elif rtype == "str":
                chars = string.ascii_letters
            else: # alnum
                chars = string.ascii_letters + string.digits
                
            res = ''.join(random.choices(chars, k=length))
            self.buffer.delete(word_start, word_end)
            self.buffer.insert_at_cursor(res + insert_char)
            self.autocomplete_box.set_visible(False)
            self.textview.scroll_to_mark(self.buffer.get_insert(), 0.05, False, 0.0, 0.0)
            return True
            
        m_simple_insert = re.match(r'^::(password|uuid|lorem|magic8ball|random_quote)$', word, re.IGNORECASE)
        if m_simple_insert:
            import uuid, random, string
            cmd = m_simple_insert.group(1).lower()
            res = ""
            if cmd == "password":
                chars = string.ascii_letters + string.digits + "!@#$%^&*"
                res = ''.join(random.choice(chars) for _ in range(16))
            elif cmd == "uuid":
                res = str(uuid.uuid4())
            elif cmd == "lorem":
                res = "Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat."
            elif cmd == "magic8ball":
                answers = [_("It is certain."), _("It is decidedly so."), _("Without a doubt."), _("Yes definitely."), _("You may rely on it."), _("As I see it, yes."), _("Most likely."), _("Outlook good."), _("Yes."), _("Signs point to yes."), _("Reply hazy, try again."), _("Ask again later."), _("Better not tell you now."), _("Cannot predict now."), _("Concentrate and ask again."), _("Don't count on it."), _("My reply is no."), _("My sources say no."), _("Outlook not so good."), _("Very doubtful.")]
                res = random.choice(answers)
            elif cmd == "random_quote":
                quotes = [
                    "The only way to do great work is to love what you do. - Steve Jobs",
                    "Simplicity is the ultimate sophistication. - Leonardo da Vinci",
                    "Make it work, make it right, make it fast. - Kent Beck",
                    "Talk is cheap. Show me the code. - Linus Torvalds",
                    "Software is like sex: it's better when it's free. - Linus Torvalds",
                    "First, solve the problem. Then, write the code. - John Johnson",
                    "Experience is the name everyone gives to their mistakes. - Oscar Wilde",
                    "In order to be irreplaceable, one must always be different. - Coco Chanel",
                    "Fix the cause, not the symptom. - Steve Maguire",
                    "Before software can be reusable it first has to be usable. - Ralph Johnson",
                    "Truth can only be found in one place: the code. - Robert C. Martin",
                    "It's not a bug. It's an undocumented feature! - Anonymous"
                ]
                res = random.choice(quotes)
                
            self.buffer.delete(word_start, word_end)
            self.buffer.insert_at_cursor(res + insert_char)
            self.autocomplete_box.set_visible(False)
            self.textview.scroll_to_mark(self.buffer.get_insert(), 0.05, False, 0.0, 0.0)
            return True
            
        if word.lower() == "::random_wiki":
            from gi.repository import Gio
            self.buffer.delete(word_start, word_end)
            self.autocomplete_box.set_visible(False)
            try:
                Gio.AppInfo.launch_default_for_uri("https://en.wikipedia.org/wiki/Special:Random", None)
            except Exception as e:
                print(f"Failed to launch URL: {e}")
            return True

        m_simple = re.match(r'^::(uppercase|lowercase|sentence_case|title_case|capitalize_first|remove_quotes|sort_lines_alpha|sort_lines_number|sort_lines_reverse|remove_lines_empty|trim_each_whitespace|dedupe_lines|get_dupes|commas_to_list|lines_to_commas|checked_to_bottom|remove_checked)$', word, re.IGNORECASE)
        if m_simple:
            self.execute_text_command(m_simple.group(1).lower(), None, word_start, word_end)
            self.autocomplete_box.set_visible(False)
            return True
            
        m_args = re.match(r'^::(append|prepend|remove_lines_with|remove_lines_without|keep_lines_with|keep_lines_without|commas_to|lines_to)\((.*?)\)$', word, re.IGNORECASE)
        if m_args:
            self.execute_text_command(m_args.group(1).lower(), [m_args.group(2)], word_start, word_end)
            self.autocomplete_box.set_visible(False)
            return True
            
        m_replace = re.match(r'^::replace\((.*?),(.*?)\)$', word, re.IGNORECASE)
        if m_replace:
            self.execute_text_command("replace", [m_replace.group(1), m_replace.group(2)], word_start, word_end)
            self.autocomplete_box.set_visible(False)
            return True
            
        return False
        
    def execute_text_command(self, cmd, args, word_start, word_end):
        self.buffer.begin_user_action()
        self.buffer.delete(word_start, word_end)
        
        start_iter = self.buffer.get_start_iter()
        end_iter = self.buffer.get_end_iter()
            
        text = self.buffer.get_text(start_iter, end_iter, False)
        new_text = text
        msg = ""
        
        try:
            if cmd == "uppercase":
                new_text = text.upper()
                msg = _("Converted to uppercase")
            elif cmd == "lowercase":
                new_text = text.lower()
                msg = _("Converted to lowercase")
            elif cmd == "title_case":
                new_text = text.title()
                msg = _("Converted to Title Case")
            elif cmd == "sentence_case":
                import re
                sentences = re.split(r'(?<=[.!?])\s+', text)
                new_text = ' '.join(s.capitalize() for s in sentences if s)
                if not new_text and text: new_text = text.capitalize()
                msg = _("Converted to Sentence case")
            elif cmd == "capitalize_first":
                import re
                new_text = re.sub(r'[a-zA-Z]', lambda m: m.group(0).upper(), text, count=1)
                msg = _("Capitalized first letter")
            elif cmd == "remove_quotes":
                import re
                new_text = re.sub(r'["\'“”‘’]', '', text)
                msg = _("Stripped quotes")
            elif cmd == "append" and args:
                new_text = '\n'.join(line + args[0] for line in text.split('\n'))
                msg = f"Appended '{args[0]}'"
            elif cmd == "prepend" and args:
                new_text = '\n'.join(args[0] + line for line in text.split('\n'))
                msg = f"Prepended '{args[0]}'"
            elif cmd == "replace" and args and len(args) == 2:
                old, new = args
                count = text.count(old)
                new_text = text.replace(old, new)
                msg = f"Replaced {count} instances"
            elif cmd in ("sort_lines_alpha", "sort_lines_number", "sort_lines_reverse"):
                import re
                def sort_sections(lines, sort_fn):
                    out = []
                    current_chunk = []
                    for line in lines:
                        if re.match(r'^\s*#{1,6}\s', line):
                            if current_chunk:
                                out.extend(sort_fn(current_chunk))
                                current_chunk = []
                            out.append(line)
                        else:
                            current_chunk.append(line)
                    if current_chunk:
                        out.extend(sort_fn(current_chunk))
                    return out
                
                lines = text.strip('\n').split('\n')
                if cmd == "sort_lines_alpha":
                    new_text = '\n'.join(sort_sections(lines, lambda c: sorted(c, key=lambda x: (x.strip() == "", x.lower()))))
                    msg = _("Sorted lines alphabetically")
                elif cmd == "sort_lines_number":
                    import re
                    def get_num(line):
                        m = re.search(r'\d+', line)
                        return float(m.group()) if m else float('inf')
                    new_text = '\n'.join(sort_sections(lines, lambda c: sorted(c, key=lambda x: (x.strip() == "", get_num(x)))))
                    msg = _("Sorted lines numerically")
                elif cmd == "sort_lines_reverse":
                    new_text = '\n'.join(sort_sections(lines, lambda c: c[::-1]))
                    msg = _("Reversed line order")
            elif cmd == "remove_lines_empty":
                new_text = '\n'.join(line for line in text.split('\n') if line.strip())
                msg = _("Removed empty lines")
            elif cmd == "remove_lines_with" and args:
                search = args[0].lower()
                new_text = '\n'.join(line for line in text.strip('\n').split('\n') if search not in line.lower())
                msg = f"Removed lines containing '{args[0]}'"
            elif cmd == "remove_lines_without" and args:
                search = args[0].lower()
                new_text = '\n'.join(line for line in text.strip('\n').split('\n') if search in line.lower())
                msg = f"Removed lines without '{args[0]}'"
            elif cmd == "keep_lines_with" and args:
                search = args[0].lower()
                new_text = '\n'.join(line for line in text.strip('\n').split('\n') if search in line.lower())
                msg = f"Kept lines containing '{args[0]}'"
            elif cmd == "keep_lines_without" and args:
                search = args[0].lower()
                new_text = '\n'.join(line for line in text.strip('\n').split('\n') if search not in line.lower())
                msg = f"Kept lines without '{args[0]}'"
            elif cmd == "trim_each_whitespace":
                new_text = '\n'.join(line.strip() for line in text.split('\n'))
                msg = _("Trimmed whitespace from all lines")
            elif cmd == "dedupe_lines":
                seen = set()
                res = []
                for line in text.split('\n'):
                    if line not in seen:
                        seen.add(line)
                        res.append(line)
                new_text = '\n'.join(res)
                msg = _("Removed duplicate lines")
            elif cmd == "get_dupes":
                lines = text.strip('\n').split('\n')
                counts = {}
                for line in lines:
                    cleaned = line.strip()
                    if cleaned:
                        counts[cleaned] = counts.get(cleaned, 0) + 1
                dupes = [f"{count}x: {line}" for line, count in counts.items() if count > 1]
                if dupes:
                    new_text = text + "\n\n--- Duplicates ---\n" + '\n'.join(dupes)
                    msg = _("Found duplicate lines")
                else:
                    msg = _("No duplicates found")
            elif cmd == "commas_to_list":
                items = [item.strip() for item in text.replace('\n', ',').split(',')]
                new_text = '\n'.join(f"- {item}" for item in items if item)
                msg = _("Converted commas to list")
            elif cmd == "commas_to" and args:
                items = [item.strip() for item in text.replace('\n', ',').split(',')]
                new_text = args[0].join(item for item in items if item)
                msg = f"Converted commas to {args[0]}"
            elif cmd == "lines_to_commas":
                items = [line.strip() for line in text.split('\n') if line.strip()]
                new_text = ', '.join(items)
                msg = _("Converted lines to comma-separated")
            elif cmd == "lines_to" and args:
                items = [line.strip() for line in text.split('\n') if line.strip()]
                new_text = args[0].join(items)
                msg = f"Converted lines to '{args[0]}'"
            elif cmd == "checked_to_bottom":
                import re
                lines = text.strip('\n').split('\n')
                checked = []
                unchecked = []
                for line in lines:
                    if re.match(r'^(\s*)☑', line) or "- [x]" in line.lower() or "- [X]" in line:
                        checked.append(line)
                    else:
                        unchecked.append(line)
                new_text = '\n'.join(unchecked + checked)
                msg = _("Moved checked items to bottom")
            elif cmd == "remove_checked":
                import re
                lines = text.strip('\n').split('\n')
                unchecked = [line for line in lines if not (re.match(r'^(\s*)☑', line) or "- [x]" in line.lower() or "- [X]" in line)]
                new_text = '\n'.join(unchecked)
                msg = _("Removed all checked items")
                
            if new_text != text:
                self.buffer.delete(start_iter, end_iter)
                self.buffer.insert(self.buffer.get_start_iter(), new_text)
                    
                if config.get("show_command_toasts", True) and hasattr(self, 'window') and self.window:
                    from gi.repository import Adw
                    self.window.toast_overlay.add_toast(Adw.Toast.new(msg))
                    
                self.textview.scroll_to_mark(self.buffer.get_insert(), 0.0, False, 0.0, 0.0)
                    
        except Exception as e:
            print(f"Error executing text command: {e}")
            
        self.buffer.end_user_action()

    def handle_tab(self):
        insert_mark = self.buffer.get_insert()
        cursor_iter = self.buffer.get_iter_at_mark(insert_mark)
        line_start = cursor_iter.copy()
        line_start.set_line_offset(0)
        
        # Get current line text to find its indent
        line_end = cursor_iter.copy()
        line_end.forward_to_line_end()
        current_text = self.buffer.get_text(line_start, line_end, False)
        m_curr = re.match(r'^(\s*)', current_text)
        curr_indent_len = len(m_curr.group(1)) if m_curr else 0
        
        # Check previous line
        prev_line_iter = line_start.copy()
        if not prev_line_iter.backward_line():
            return True # First line, no indentation allowed
            
        prev_line_end = prev_line_iter.copy()
        prev_line_end.forward_to_line_end()
        prev_text = self.buffer.get_text(prev_line_iter, prev_line_end, False)
        
        # Ignore empty previous lines for indentation calculation? 
        # Actually, standard markdown usually bases it on the previous item.
        m_prev = re.match(r'^(\s*)', prev_text)
        prev_indent_len = len(m_prev.group(1)) if m_prev else 0
        
        if curr_indent_len >= prev_indent_len + 4:
            return True # Already indented 1 level deeper than the previous line
            
        self.buffer.insert(line_start, "    ")
        self.recalculate_list_number(line_start)
        return True

    def handle_shift_tab(self):
        insert_mark = self.buffer.get_insert()
        cursor_iter = self.buffer.get_iter_at_mark(insert_mark)
        line_start = cursor_iter.copy()
        line_start.set_line_offset(0)
        
        check_iter = line_start.copy()
        check_iter.forward_chars(4)
        text = self.buffer.get_text(line_start, check_iter, False)
        
        spaces_to_remove = 0
        for char in text:
            if char == ' ':
                spaces_to_remove += 1
            else:
                break
                
        if spaces_to_remove > 0:
            del_iter = line_start.copy()
            del_iter.forward_chars(spaces_to_remove)
            self.buffer.delete(line_start, del_iter)
            self.recalculate_list_number(line_start)
            return True
        return False

    def recalculate_list_number(self, line_start_iter):
        line_start = line_start_iter.copy()
        line_start.set_line_offset(0)
        line_end = line_start.copy()
        line_end.forward_to_line_end()
        text = self.buffer.get_text(line_start, line_end, False)
        
        m_ol = re.match(r'^(\s*)(\d+)\.(\s+.*)$', text)
        if not m_ol:
            return
            
        current_indent_len = len(m_ol.group(1))
        
        search_iter = line_start.copy()
        prev_num = 0
        while search_iter.backward_line():
            s_end = search_iter.copy()
            s_end.forward_to_line_end()
            s_text = self.buffer.get_text(search_iter, s_end, False)
            
            if not s_text.strip():
                continue
            
            m_search = re.match(r'^(\s*)(\d+)\.\s+.*$', s_text)
            if m_search:
                s_indent_len = len(m_search.group(1))
                if s_indent_len == current_indent_len:
                    prev_num = int(m_search.group(2))
                    break
                elif s_indent_len < current_indent_len:
                    break
            else:
                m_other = re.match(r'^(\s*)', s_text)
                if m_other and len(m_other.group(1)) < current_indent_len:
                    break
                    
        new_num = prev_num + 1
        current_num_str = m_ol.group(2)
        new_num_str = str(new_num)
        
        if current_num_str != new_num_str:
            num_start = line_start.copy()
            num_start.forward_chars(current_indent_len)
            num_end = num_start.copy()
            num_end.forward_chars(len(current_num_str))
            
            self.buffer.delete(num_start, num_end)
            self.buffer.insert(num_start, new_num_str)

    def toggle_checkbox(self):
        insert_mark = self.buffer.get_insert()
        cursor_iter = self.buffer.get_iter_at_mark(insert_mark)
        line_start = cursor_iter.copy()
        line_start.set_line_offset(0)
        line_end = cursor_iter.copy()
        line_end.forward_to_line_end()
        line_text = self.buffer.get_text(line_start, line_end, False)

        # 1. If line is an empty checkbox (no content after box), remove it -> normal line
        m_empty_box = re.match(r'^(\s*)([☐☑]|[-*+]\s*\[[ xX]\])\s*$', line_text)
        if m_empty_box:
            indent = m_empty_box.group(1)
            self.buffer.delete(line_start, line_end)
            self.buffer.insert(line_start, indent)
            return True

        # 2. Checkbox with text -> toggle ☐ <-> ☑
        m_box = re.match(r'^(\s*)([☐☑])\s+(.*)$', line_text)
        if m_box:
            indent, current_box, content = m_box.groups()
            new_box = "☑" if current_box == "☐" else "☐"
            self.buffer.delete(line_start, line_end)
            self.buffer.insert(line_start, f"{indent}{new_box} {content}")
            return True

        m_md_box = re.match(r'^(\s*)([-*+]\s*\[[ xX]\])\s+(.*)$', line_text)
        if m_md_box:
            indent, current_box, content = m_md_box.groups()
            new_box = "☑" if "[ ]" in current_box else "☐"
            self.buffer.delete(line_start, line_end)
            self.buffer.insert(line_start, f"{indent}{new_box} {content}")
            return True

        # 3. Replace bullet with checkbox
        m_bullet = re.match(r'^(\s*)([-*+])\s+(.*)$', line_text)
        if m_bullet:
            indent, bullet, content = m_bullet.groups()
            self.buffer.delete(line_start, line_end)
            self.buffer.insert(line_start, f"{indent}☐ {content}")
            return True

        # 4. Otherwise prepend checkbox
        m_indent = re.match(r'^(\s*)', line_text)
        indent = m_indent.group(1) if m_indent else ""
        content = line_text[len(indent):]
        self.buffer.delete(line_start, line_end)
        self.buffer.insert(line_start, f"{indent}☐ {content}")
        return True

    def count_checkboxes(self):
        start, end = self.buffer.get_bounds()
        text = self.buffer.get_text(start, end, False)
        return len(re.findall(r'^(\s*)[☐☑]', text, re.MULTILINE))

    def is_empty(self):
        start, end = self.buffer.get_bounds()
        text = self.buffer.get_text(start, end, False).strip()
        return len(text) == 0

    def is_list_note(self):
        start_iter = self.buffer.get_start_iter()
        end_iter = start_iter.copy()
        end_iter.forward_to_line_end()
        first_line = self.buffer.get_text(start_iter, end_iter, False).strip().lower()
        return bool(re.match(r'^(#{1,6}\s*)?list(\s*[:\s].*)?$', first_line))

    def handle_return(self):
        insert_mark = self.buffer.get_insert()
        cursor_iter = self.buffer.get_iter_at_mark(insert_mark)
        
        line_start = cursor_iter.copy()
        line_start.set_line_offset(0)
        line_text = self.buffer.get_text(line_start, cursor_iter, False)
        
        is_list = self.is_list_note()

        def insert_sync(text):
            self.buffer.insert_at_cursor(text)
            GLib.idle_add(lambda: self.textview.scroll_mark_onscreen(self.buffer.get_insert()) or False)
            return True

        if is_list:
            # Empty checkbox
            m_empty = re.match(r'^(\s*)[☐☑]\s*$', line_text)
            if m_empty:
                # Delete the entire line
                line_end = cursor_iter.copy()
                line_end.forward_to_line_end()
                self.buffer.delete(line_start, line_end)
                
                # Delete the preceding newline so we don't leave an empty line
                if line_start.backward_char() and line_start.get_char() == '\n':
                    tmp = line_start.copy()
                    tmp.forward_char()
                    self.buffer.delete(line_start, tmp)
                    
                GLib.idle_add(lambda: self.textview.scroll_mark_onscreen(self.buffer.get_insert()) or False)
                return True
                
            m_indent = re.match(r'^(\s*)', line_text)
            indent = m_indent.group(1) if m_indent else ""
            return insert_sync(f"\n{indent}☐ ")

        # Check if current line is an empty checkbox
        m_empty = re.match(r'^(\s*)([☐☑]|[-*+]\s*\[[ xX]\])\s*$', line_text)
        if m_empty:
            self.buffer.delete(line_start, cursor_iter)
            return insert_sync("\n")
            
        # Check if current line is a checkbox
        m_box = re.match(r'^(\s*)([☐☑]|[-*+]\s*\[[ xX]\])\s+(.*)$', line_text)
        if m_box:
            indent, box, content = m_box.groups()
            return insert_sync(f"\n{indent}☐ ")
        
        # Match unordered lists (- or *)
        m_ul = re.match(r'^(\s*)([-*+])\s+(.*)$', line_text)
        if m_ul:
            indent, bullet, content = m_ul.groups()
            if not content.strip():
                self.buffer.delete(line_start, cursor_iter)
                return insert_sync("\n")
            else:
                return insert_sync(f"\n{indent}{bullet} ")
                
        # Match ordered lists (1., 2., etc)
        m_ol = re.match(r'^(\s*)(\d+)\.\s+(.*)$', line_text)
        if m_ol:
            indent, num, content = m_ol.groups()
            if not content.strip():
                self.buffer.delete(line_start, cursor_iter)
                return insert_sync("\n")
            else:
                next_num = int(num) + 1
                return insert_sync(f"\n{indent}{next_num}. ")
                
        return False

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

    def handle_smart_paste(self):
        clipboard = self.textview.get_clipboard()
        formats = clipboard.get_formats()
        
        # Check if the clipboard contains an image (texture)
        if HAS_OCR and formats.contain_gtype(Gdk.Texture.__gtype__):
            clipboard.read_texture_async(None, self.on_smart_paste_texture_read)
        else:
            clipboard.read_text_async(None, self.on_smart_paste_read)

    def on_smart_paste_texture_read(self, clipboard, result):
        try:
            texture = clipboard.read_texture_finish(result)
            if texture:
                # Show toast notification about OCR extraction
                window = self.get_root()
                toast = None
                if hasattr(window, 'toast_overlay'):
                    toast = Adw.Toast.new(_("Extracting text from image…"))
                    toast.set_timeout(0)  # Make it persistent until we dismiss it manually
                    window.toast_overlay.add_toast(toast)
                    
                # Run OCR in a background thread to avoid freezing the UI
                def run_ocr():
                    try:
                        import io
                        png_bytes = texture.save_to_png_bytes()
                        img = Image.open(io.BytesIO(png_bytes.get_data()))
                        extracted_text = extract_text_from_image(img)
                        
                        def on_success():
                            if toast:
                                toast.dismiss()
                            self._insert_extracted_text(extracted_text)
                            return False
                            
                        def on_empty():
                            if toast:
                                toast.dismiss()
                            self._show_ocr_error(_("No text found in image"))
                            return False
                            
                        if extracted_text:
                            GLib.idle_add(on_success)
                        else:
                            GLib.idle_add(on_empty)
                    except Exception as e:
                        print(f"OCR Error: {e}")
                        def on_error():
                            if toast:
                                toast.dismiss()
                            self._show_ocr_error(_("Failed to extract text from image"))
                            return False
                        GLib.idle_add(on_error)
                        
                threading.Thread(target=run_ocr, daemon=True).start()
        except GLib.Error as e:
            print(f"Failed to read texture from clipboard: {e}")

    def _insert_extracted_text(self, text):
        self.buffer.delete_selection(True, self.textview.get_editable())
        self.buffer.insert_at_cursor(text.strip())
        return False
        
    def _show_ocr_error(self, msg):
        window = self.get_root()
        if hasattr(window, 'toast_overlay'):
            toast = Adw.Toast.new(msg)
            window.toast_overlay.add_toast(toast)
        return False

    def on_smart_paste_read(self, clipboard, result):
        try:
            text = clipboard.read_text_finish(result)
            if text:
                url = text.strip()
                if re.match(r'^https?://[^\s]+$', url) and len(url) > 30:
                    display_url = url.replace("https://", "").replace("http://", "").replace("www.", "")
                    if len(display_url) > 25:
                        display_url = display_url[:25] + "…"
                    markdown_link = f"[{display_url}]({url})"
                    
                    def do_insert():
                        self.buffer.delete_selection(True, self.textview.get_editable())
                        self.buffer.insert_at_cursor(markdown_link)
                        return False
                    GLib.idle_add(do_insert)
                else:
                    def do_insert():
                        self.buffer.delete_selection(True, self.textview.get_editable())
                        self.buffer.insert_at_cursor(text)
                        return False
                    GLib.idle_add(do_insert)
        except GLib.Error:
            pass

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

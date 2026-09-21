import re
from gi.repository import GLib, Pango
from whisp.config import config
from whisp.text_color import find_color_spans, strip_color_markup

class MarkdownHighlighter:
    def __init__(self, buffer, textview=None, editor=None):
        self.buffer = buffer
        self.editor = editor
        self.textview = textview or (editor.textview if editor else None)
        self.search_term = ""
        self.create_tags()
        self.buffer.connect("changed", self.on_changed)
        self.buffer.connect("notify::cursor-position", self.on_cursor_moved)
        # Timeout ID for debouncing
        self.timeout_id = 0
        self.last_cursor_line = -1

    def create_tags(self):
        # Headings
        self.tag_h1 = self.buffer.create_tag("h1", weight=Pango.Weight.BOLD, scale=2.0)
        self.tag_h2 = self.buffer.create_tag("h2", weight=Pango.Weight.BOLD, scale=1.5)
        self.tag_h3 = self.buffer.create_tag("h3", weight=Pango.Weight.BOLD, scale=1.25)
        
        # Plain text headings (Orange color, bold, hashes grey)
        self.tag_heading_plain = self.buffer.create_tag("heading_plain", foreground="#d08770", weight=Pango.Weight.BOLD)
        self.tag_hash_plain = self.buffer.create_tag("hash_plain", foreground="#4c566a", weight=Pango.Weight.BOLD)
        
        # Bold, Italic, Underline, and Strikethrough
        self.tag_bold = self.buffer.create_tag("bold", weight=Pango.Weight.BOLD)
        self.tag_italic = self.buffer.create_tag("italic", style=Pango.Style.ITALIC)
        self.tag_underline = self.buffer.create_tag("underline", underline=Pango.Underline.SINGLE)
        self.tag_strikethrough = self.buffer.create_tag("strikethrough", strikethrough=True)
        self.tag_checkbox_checked = self.buffer.create_tag("checkbox_checked", strikethrough=True, foreground="#aaaaaa")
        self.tag_checkbox_icon = self.buffer.create_tag("checkbox_icon", weight=Pango.Weight.BOLD, foreground="#aaaaaa")
        self.tag_list_keyword = self.buffer.create_tag("list_keyword", foreground="#b48ead", weight=Pango.Weight.BOLD, pixels_below_lines=32)
        
        # Link styling
        self.tag_link = self.buffer.create_tag("link", foreground="#81a1c1", underline=Pango.Underline.SINGLE)
        
        # Monospace / Code
        self.tag_code = self.buffer.create_tag("code", family="monospace", background="rgba(128, 128, 128, 0.2)")
        
        # Bullet points
        self.tag_bullet = self.buffer.create_tag("bullet", indent=-15, left_margin=30)
        self.tag_bullet_bold = self.buffer.create_tag("bullet_bold", weight=Pango.Weight.BOLD)
        
        # Comments
        self.tag_comment = self.buffer.create_tag("comment", foreground="#888888", style=Pango.Style.ITALIC)
        
        # Invisible tag for WYSIWYG
        self.tag_invisible = self.buffer.create_tag("invisible", invisible=True)

        self.tag_search = self.buffer.create_tag("search_match", background="#f9f06b", foreground="#000000")

    def color_tag(self, color):
        """Tag that paints text ``color`` (``#rrggbb``), created on first use."""
        name = f"color_{color}"
        tag = self.buffer.get_tag_table().lookup(name)
        if tag is None:
            tag = self.buffer.create_tag(name, foreground=color)
            # New tags outrank older ones; keep search highlights on top.
            tag.set_priority(self.tag_search.get_priority())
        return tag

    def set_search_term(self, term):
        self.search_term = term or ""
        self.highlight_search()

    def highlight_search(self):
        start, end = self.buffer.get_bounds()
        self.buffer.remove_tag(self.tag_search, start, end)
        if not self.search_term:
            return
        # Tag only the viewport (+margin); the editor re-runs this on scroll.
        lo, hi = self._search_scan_range()
        base = lo.get_offset()
        text = self.buffer.get_text(lo, hi, True)
        for m in re.finditer(re.escape(self.search_term), text, re.IGNORECASE):
            s_iter = self.buffer.get_iter_at_offset(base + m.start())
            e_iter = self.buffer.get_iter_at_offset(base + m.end())
            self.buffer.apply_tag(self.tag_search, s_iter, e_iter)

    def _search_scan_range(self):
        tv = self.textview
        if tv is None:
            return self.buffer.get_bounds()
        rect = tv.get_visible_rect()
        if rect.height <= 0:
            return self.buffer.get_bounds()
        # Two screenfuls of slack each side so fast scroll stays in the band.
        margin = rect.height * 2
        _, top = tv.get_iter_at_location(0, max(0, rect.y - margin))
        _, bot = tv.get_iter_at_location(0, rect.y + rect.height + margin)
        return top, bot

    def on_cursor_moved(self, buffer, param):
        cursor_iter = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        cursor_line = cursor_iter.get_line()
        if cursor_line != self.last_cursor_line:
            self.last_cursor_line = cursor_line
            if self.timeout_id:
                GLib.source_remove(self.timeout_id)
            self.timeout_id = GLib.idle_add(self.highlight)

    def on_changed(self, buffer):
        if self.timeout_id:
            GLib.source_remove(self.timeout_id)
        self.timeout_id = GLib.idle_add(self.highlight)

    def highlight(self):
        self.timeout_id = 0
        start, end = self.buffer.get_bounds()
        text = self.buffer.get_text(start, end, True)
        
        cursor_iter = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        cursor_offset = cursor_iter.get_offset()
        
        # Remove all formatting tags first
        self.buffer.remove_all_tags(start, end)
        
        def apply_invisible(m, group_idx=1, outer_match=None):
            check_m = outer_match if outer_match else m
            start_offset = m.start(group_idx)
            end_offset = m.end(group_idx)
            start_iter = self.buffer.get_iter_at_offset(start_offset)
            end_iter = self.buffer.get_iter_at_offset(end_offset)
            
            if not (check_m.start() <= cursor_offset <= check_m.end()):
                self.buffer.apply_tag(self.tag_invisible, start_iter, end_iter)
        
        wysiwyg = False
        if hasattr(self, 'editor') and self.editor and hasattr(self.editor, 'is_wysiwyg_enabled'):
            wysiwyg = self.editor.is_wysiwyg_enabled()
        else:
            wysiwyg = config.get("wysiwyg_mode", False)
        
        # Check if it's a list note
        start_doc = self.buffer.get_start_iter()
        end_doc = start_doc.copy()
        end_doc.forward_to_line_end()
        first_line = strip_color_markup(self.buffer.get_text(start_doc, end_doc, False)).strip().lower()
        is_list_note = bool(re.match(r'^(#{1,6}\s*)?list(\s*[:\s].*)?$', first_line))
        
        if is_list_note:
            self.buffer.apply_tag(self.tag_list_keyword, start_doc, end_doc)
        else:
            # Apply headings (e.g. # Heading)
            for m in re.finditer(r'^(#{1,6}\s+)(.*)$', text, re.MULTILINE):
                level = len(m.group(1).strip())
                start_iter = self.buffer.get_iter_at_offset(m.start())
                hash_end = self.buffer.get_iter_at_offset(m.end(1))
                end_iter = self.buffer.get_iter_at_offset(m.end())
                
                if wysiwyg:
                    tag = getattr(self, f"tag_h{level}", self.tag_h1)
                    self.buffer.apply_tag(tag, start_iter, end_iter)
                    apply_invisible(m, 1, outer_match=m)
                else:
                    self.buffer.apply_tag(self.tag_hash_plain, start_iter, hash_end)
                    self.buffer.apply_tag(self.tag_heading_plain, hash_end, end_iter)
            
        # Apply bullet points (- item or * item) and numbered lists (1. item)
        for m in re.finditer(r'^(\s*(?:[-*+]|\d+\.)\s+)(.*)$', text, re.MULTILINE):
            start_iter = self.buffer.get_iter_at_offset(m.start())
            bullet_end_iter = self.buffer.get_iter_at_offset(m.start() + len(m.group(1)))
            if wysiwyg:
                self.buffer.apply_tag(self.tag_bullet, start_iter, bullet_end_iter)
            self.buffer.apply_tag(self.tag_bullet_bold, start_iter, bullet_end_iter)
            
        # Apply bold (**text**)
        for m in re.finditer(r'(?<!\\)(\*\*)(.*?)(?<!\\)(\*\*)', text):
            start_iter = self.buffer.get_iter_at_offset(m.start())
            end_iter = self.buffer.get_iter_at_offset(m.end())
            self.buffer.apply_tag(self.tag_bold, start_iter, end_iter)
            if wysiwyg:
                apply_invisible(m, 1)
                apply_invisible(m, 3)
            
        # Apply italic (*text*)
        for m in re.finditer(r'(?<!\\)(?<!\*)(\*)([^\s\*\n]|(?:[^\s\*\n](?:[^\*\n\\]|\\.)*?[^\s\*\n]))(?<!\\)(\*)(?!\*)', text):
            start_iter = self.buffer.get_iter_at_offset(m.start())
            end_iter = self.buffer.get_iter_at_offset(m.end())
            self.buffer.apply_tag(self.tag_italic, start_iter, end_iter)
            if wysiwyg:
                apply_invisible(m, 1)
                apply_invisible(m, 3)
            
        # Apply underline (<u>text</u>)
        for m in re.finditer(r'(<u>)(.*?)(</u>)', text):
            start_iter = self.buffer.get_iter_at_offset(m.start())
            end_iter = self.buffer.get_iter_at_offset(m.end())
            self.buffer.apply_tag(self.tag_underline, start_iter, end_iter)
            if wysiwyg:
                apply_invisible(m, 1, outer_match=m)
                apply_invisible(m, 3, outer_match=m)
                
        # Apply underline (_text_)
        for m in re.finditer(r'(?<!\w)(_+)([^\n]+?)(\1)(?!\w)', text):
            start_iter = self.buffer.get_iter_at_offset(m.start())
            end_iter = self.buffer.get_iter_at_offset(m.end())
            self.buffer.apply_tag(self.tag_underline, start_iter, end_iter)
            if wysiwyg:
                apply_invisible(m, 1, outer_match=m)
                apply_invisible(m, 3, outer_match=m)
            
        # Apply strikethrough (~~text~~)
        for m in re.finditer(r'(~~)(.*?)(~~)', text):
            start_iter = self.buffer.get_iter_at_offset(m.start())
            end_iter = self.buffer.get_iter_at_offset(m.end())
            self.buffer.apply_tag(self.tag_strikethrough, start_iter, end_iter)
            if wysiwyg:
                apply_invisible(m, 1)
                apply_invisible(m, 3)
            
        # Apply text colors (<span style="color:#hex">text</span>). The markup is
        # verbose, so it stays hidden in both modes; "Remove Color" edits it.
        for span in find_color_spans(text):
            content_start = self.buffer.get_iter_at_offset(span.open_end)
            content_end = self.buffer.get_iter_at_offset(span.close_start)
            self.buffer.apply_tag(self.color_tag(span.color), content_start, content_end)
            self.buffer.apply_tag(self.tag_invisible, self.buffer.get_iter_at_offset(span.open_start), content_start)
            self.buffer.apply_tag(self.tag_invisible, content_end, self.buffer.get_iter_at_offset(span.close_end))

        # Apply checkboxes (☐, ☑, - [ ], - [x])
        for m in re.finditer(r"^(\s*)([☐☑]|[-*+]\s*\[[ xX]\])\s*(.*)$", text, re.MULTILINE):
            box_start = self.buffer.get_iter_at_offset(m.start(2))
            box_end = self.buffer.get_iter_at_offset(m.end(2))
            line_end = self.buffer.get_iter_at_offset(m.end(3))
            
            self.buffer.apply_tag(self.tag_checkbox_icon, box_start, box_end)
            
            is_checked = m.group(2) == "☑" or "[x]" in m.group(2).lower()
            if is_checked:
                self.buffer.apply_tag(self.tag_checkbox_checked, box_start, line_end)

        # Apply link formatting for Markdown links [text](url)
        for m in re.finditer(r'\[(.*?)\]\((.*?)\)', text):
            text_start = self.buffer.get_iter_at_offset(m.start(1))
            text_end = self.buffer.get_iter_at_offset(m.end(1))
            self.buffer.apply_tag(self.tag_link, text_start, text_end)
            
            if wysiwyg:
                # If cursor is not on the link, hide everything except the text
                if not (m.start(0) <= cursor_offset <= m.end(0)):
                    # Hide '['
                    start_iter = self.buffer.get_iter_at_offset(m.start(0))
                    end_iter = self.buffer.get_iter_at_offset(m.start(1))
                    self.buffer.apply_tag(self.tag_invisible, start_iter, end_iter)
                    
                    # Hide '](url)'
                    start_iter = self.buffer.get_iter_at_offset(m.end(1))
                    end_iter = self.buffer.get_iter_at_offset(m.end(0))
                    self.buffer.apply_tag(self.tag_invisible, start_iter, end_iter)

        # Apply link formatting for bare URLs
        for m in re.finditer(r'(?<!\()https?://[^\s]+', text):
            start_iter = self.buffer.get_iter_at_offset(m.start())
            end_iter = self.buffer.get_iter_at_offset(m.end())
            self.buffer.apply_tag(self.tag_link, start_iter, end_iter)

        # Apply code (`text`)
        for m in re.finditer(r'(`)(.*?)(`)', text):
            if wysiwyg and not (m.start() <= cursor_offset <= m.end()):
                # Hide opening backtick
                start_iter = self.buffer.get_iter_at_offset(m.start(1))
                end_iter = self.buffer.get_iter_at_offset(m.end(1))
                self.buffer.apply_tag(self.tag_invisible, start_iter, end_iter)
                
                # Hide closing backtick
                start_iter = self.buffer.get_iter_at_offset(m.start(3))
                end_iter = self.buffer.get_iter_at_offset(m.end(3))
                self.buffer.apply_tag(self.tag_invisible, start_iter, end_iter)

                # Apply code styling to inner text only
                start_iter = self.buffer.get_iter_at_offset(m.start(2))
                end_iter = self.buffer.get_iter_at_offset(m.end(2))
                self.buffer.apply_tag(self.tag_code, start_iter, end_iter)
            else:
                # Apply code styling to the whole match including backticks
                start_iter = self.buffer.get_iter_at_offset(m.start())
                end_iter = self.buffer.get_iter_at_offset(m.end())
                self.buffer.apply_tag(self.tag_code, start_iter, end_iter)
            
        # Apply comments (// text)
        for m in re.finditer(r'^(\s*(?:[☐☑]\s*)?//)(.*)$', text, re.MULTILINE):
            start_iter = self.buffer.get_iter_at_offset(m.start())
            end_iter = self.buffer.get_iter_at_offset(m.end())
            self.buffer.apply_tag(self.tag_comment, start_iter, end_iter)

        self.highlight_search()

        return False

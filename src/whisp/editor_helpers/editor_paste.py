import gettext
import hashlib
import io
import re
import threading
from typing import TYPE_CHECKING
from gi.repository import GLib, Gdk, Adw
from whisp.editor_helpers.editor_expansions import HAS_OCR, Image, extract_text_from_image

if TYPE_CHECKING:
    from gi.repository import Gtk
    from whisp.window import WhispWindow

_ = gettext.gettext

_START_PASTE_LINE_RE = re.compile(r'^(#{1,6}\s*)?:{0,2}paste(\s*[:\s].*)?$', re.IGNORECASE)
_STOP_PASTE_LINE_RE = re.compile(r'^(#{1,6}\s*)?:{0,2}paste:?$', re.IGNORECASE)
_HTML_TAG_RE = re.compile(
    r'</?(?:a|b|blockquote|br|code|del|div|em|h[1-6]|hr|i|img|ins|kbd|li|mark|ol|p|pre|q|s|small|span|strong|sub|sup|u|ul)(?:\s+[^<>]*)?\s*/?>',
    re.IGNORECASE,
)


def strip_markdown(text: str) -> str:
    """Strip Markdown syntax while preserving readable plain text."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Unwrap fenced code blocks before inline rules run
    text = re.sub(r'^[ \t]*```[^\n]*\n(.*?)^[ \t]*```[ \t]*$', r'\1', text, flags=re.MULTILINE | re.DOTALL)
    text = re.sub(r'^[ \t]*~~~[^\n]*\n(.*?)^[ \t]*~~~[ \t]*$', r'\1', text, flags=re.MULTILINE | re.DOTALL)

    # Drop link definitions and horizontal rules
    text = re.sub(r'^[ \t]*\[[^\]]+\]:[ \t]+\S+.*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'^[ \t]*([-*_])(?:[ \t]*\1){2,}[ \t]*$', '', text, flags=re.MULTILINE)

    # Strip block-level prefixes
    text = re.sub(r'^[ \t]{0,3}#{1,6}[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$', r'\1', text, flags=re.MULTILINE)
    text = re.sub(r'^[ \t]{0,3}=+[ \t]*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'^[ \t]{0,3}(?:>[ \t]?)+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^[ \t]*(?:[-*+]|\d+[.)])[ \t]+(?:(?:\[[ xX]\]|[☐☑])[ \t]+)?', '', text, flags=re.MULTILINE)
    text = re.sub(r'^[ \t]*(?:\[[ xX]\]|[☐☑])[ \t]*', '', text, flags=re.MULTILINE)

    # Strip images before links so the leading '!' does not remain
    text = re.sub(r'!\[([^\]]*)\]\([^)]*\)', r'\1', text)
    text = re.sub(r'!\[([^\]]*)\]\[[^\]]*\]', r'\1', text)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    text = re.sub(r'\[([^\]]+)\]\[[^\]]*\]', r'\1', text)
    text = re.sub(r'<((?:https?://|mailto:)[^>]+)>', r'\1', text)

    # Strip inline code and emphasis markers while respecting backslash escapes
    text = re.sub(r'(?<!\\)``(.+?)(?<!\\)``', r'\1', text)
    text = re.sub(r'(?<!\\)`([^`\n]+?)(?<!\\)`', r'\1', text)
    text = re.sub(r'(?<!\\)\*\*\*(.+?)(?<!\\)\*\*\*', r'\1', text)
    text = re.sub(r'(?<!\\)___(.+?)(?<!\\)___', r'\1', text)
    text = re.sub(r'(?<!\\)\*\*(.+?)(?<!\\)\*\*', r'\1', text)
    text = re.sub(r'(?<!\\)__(.+?)(?<!\\)__', r'\1', text)
    text = re.sub(r'(?<![*\\])\*(?=\S)(.+?)(?<=\S)(?<!\\)\*(?!\*)', r'\1', text)
    text = re.sub(r'(?<![\w_\\])_(?=\S)([^_\n]+?)(?<=\S)(?<!\\)_(?![\w_])', r'\1', text)
    text = re.sub(r'(?<!\\)~~(.+?)(?<!\\)~~', r'\1', text)
    text = re.sub(r'(?<!\\)==(.+?)(?<!\\)==', r'\1', text)

    text = _HTML_TAG_RE.sub('', text)
    text = re.sub(r'\\([\\`*_{}\[\]()#+\-.!>~|])', r'\1', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text


def _format_short_markdown_link(url: str) -> str | None:
    """Format a long URL as a shortened local Markdown link."""
    if re.match(r'^https?://[^\s]+$', url) and len(url) > 30:
        display_url = url.replace("https://", "").replace("http://", "").replace("www.", "")
        if len(display_url) > 25:
            display_url = display_url[:25] + "…"
        return f"[{display_url}]({url})"
    return None


def _read_texture_png(texture) -> tuple[str, bytes] | None:
    """Encode a Gdk.Texture to PNG bytes and return (sha256_hex, raw_bytes)."""
    if not texture:
        return None
    png_bytes = texture.save_to_png_bytes()
    raw = png_bytes.get_data() if png_bytes else None
    if not raw:
        return None
    return hashlib.sha256(raw).hexdigest(), raw


class EditorPasteMixin:
    """Mixin class providing Smart Paste, Plain Paste, and AutoPaste for NoteEditor."""
    buffer: "Gtk.TextBuffer"
    textview: "Gtk.TextView"
    window: "WhispWindow | None"

    if TYPE_CHECKING:
        def get_root(self) -> "Gtk.Root | None": ...

    is_autopaste_active: bool = False
    _clipboard_handler_id = None
    _last_pasted_text: str | None = None
    _last_pasted_image_hash: str | None = None
    _pending_ocr_hash: str | None = None
    _autopaste_toast: Adw.Toast | None = None
    _autopaste_mark: "Gtk.TextMark | None" = None

    def is_paste_note(self) -> bool:
        """Check if note header indicates an AutoPaste note."""
        start_iter = self.buffer.get_start_iter()
        end_iter = start_iter.copy()
        end_iter.forward_to_line_end()
        first_line = self.buffer.get_text(start_iter, end_iter, False).strip().lower()
        return bool(re.match(r'^(#{1,6}\s*)?:?paste(\s*[:\s].*)?$', first_line))

    def start_autopaste(self):
        """Enable AutoPaste mode and start monitoring the clipboard."""
        if getattr(self, "is_autopaste_active", False):
            return

        self.is_autopaste_active = True
        self._last_pasted_text = None
        self._last_pasted_image_hash = None
        self._pending_ocr_hash = None

        if getattr(self, "_autopaste_mark", None) is None:
            insert_mark = self.buffer.get_insert()
            cursor_iter = self.buffer.get_iter_at_mark(insert_mark)
            self._autopaste_mark = self.buffer.create_mark("autopaste_target", cursor_iter, left_gravity=False)

        display = Gdk.Display.get_default()
        if display:
            clipboard = display.get_clipboard()
            self._snapshot_initial_clipboard(clipboard)
            self._clipboard_handler_id = clipboard.connect("changed", self._on_clipboard_changed)

        self._show_autopaste_banner()

    def _snapshot_initial_clipboard(self, clipboard):
        """Record existing clipboard state so it is not pasted on activation."""
        texture_gtype = getattr(Gdk.Texture, "__gtype__", Gdk.Texture)
        formats = clipboard.get_formats()

        def on_initial_text(clip, result):
            try:
                text = clip.read_text_finish(result)
                self._last_pasted_text = text.replace("\r\n", "\n").replace("\r", "\n") if text else None
            except Exception:
                self._last_pasted_text = None

        def on_initial_texture(clip, result):
            try:
                texture = clip.read_texture_finish(result)
                data = _read_texture_png(texture)
                self._last_pasted_image_hash = data[0] if data else None
            except Exception:
                self._last_pasted_image_hash = None

        if HAS_OCR and formats is not None and formats.contain_gtype(texture_gtype):
            clipboard.read_texture_async(None, on_initial_texture)
        else:
            clipboard.read_text_async(None, on_initial_text)

    def stop_autopaste(self, silent: bool = False):
        """Disable AutoPaste mode and disconnect clipboard monitoring."""
        if not getattr(self, "is_autopaste_active", False):
            return

        self.is_autopaste_active = False

        if self._clipboard_handler_id is not None:
            display = Gdk.Display.get_default()
            if display:
                clipboard = display.get_clipboard()
                try:
                    clipboard.disconnect(self._clipboard_handler_id)
                except Exception:
                    pass
            self._clipboard_handler_id = None

        self._last_pasted_text = None
        self._last_pasted_image_hash = None
        self._pending_ocr_hash = None

        mark = getattr(self, "_autopaste_mark", None)
        if mark is not None:
            try:
                self.buffer.delete_mark(mark)
            except Exception:
                pass
            self._autopaste_mark = None

        if self._autopaste_toast:
            try:
                self._autopaste_toast.dismiss()
            except Exception:
                pass
            self._autopaste_toast = None

        if not silent:
            toast = Adw.Toast.new(_("AutoPaste disabled"))
            toast.set_timeout(2)
            self._display_toast(toast)

    def _show_autopaste_banner(self):
        """Show persistent toast banner while AutoPaste is active."""
        if self._autopaste_toast:
            try:
                self._autopaste_toast.dismiss()
            except Exception:
                pass

        toast = Adw.Toast.new(_("AutoPaste active"))
        toast.set_timeout(0)
        toast.set_button_label(_("Stop"))
        toast.connect("button-clicked", lambda t: self.stop_autopaste())
        self._autopaste_toast = toast
        self._display_toast(toast)

    def handle_paste_return(self) -> bool:
        """Handle Return key for AutoPaste activation and deactivation."""
        if self.buffer.get_has_selection():
            return False

        insert_mark = self.buffer.get_insert()
        cursor_iter = self.buffer.get_iter_at_mark(insert_mark)

        line_start = cursor_iter.copy()
        line_start.set_line_offset(0)
        line_end = cursor_iter.copy()
        line_end.forward_to_line_end()
        line_text = self.buffer.get_text(line_start, line_end, False).strip()

        if getattr(self, "is_autopaste_active", False):
            if _STOP_PASTE_LINE_RE.match(line_text):
                self.buffer.delete(line_start, line_end)
                self.stop_autopaste()
                return True
            return False

        if _START_PASTE_LINE_RE.match(line_text):
            self.buffer.begin_user_action()
            try:
                if line_start.get_offset() > 0:
                    prev_iter = line_start.copy()
                    prev_iter.backward_char()
                    if prev_iter.get_offset() > 0:
                        prev2_iter = prev_iter.copy()
                        prev2_iter.backward_char()
                        if prev2_iter.get_char() != '\n':
                            self.buffer.insert(line_start, '\n')
                    else:
                        self.buffer.insert(line_start, '\n')

                cursor_iter = self.buffer.get_iter_at_mark(insert_mark)
                line_end = cursor_iter.copy()
                line_end.forward_to_line_end()

                self.buffer.place_cursor(line_end)
                self.buffer.insert_at_cursor("\n")
                new_cursor = self.buffer.get_iter_at_mark(insert_mark)
                old_mark = getattr(self, "_autopaste_mark", None)
                if old_mark is not None:
                    self.buffer.delete_mark(old_mark)
                self._autopaste_mark = self.buffer.create_mark("autopaste_target", new_cursor, left_gravity=False)
                self.start_autopaste()
                GLib.idle_add(lambda: self.textview.scroll_mark_onscreen(self.buffer.get_insert()) or False)
                return True
            finally:
                self.buffer.end_user_action()

        return False

    def sync_autopaste_from_clipboard(self):
        """Poll clipboard when window regains focus (required on Wayland)."""
        if not getattr(self, "is_autopaste_active", False):
            return
        display = Gdk.Display.get_default()
        if display:
            self._read_clipboard_for_autopaste(display.get_clipboard())

    def _read_clipboard_for_autopaste(self, clipboard):
        if not getattr(self, "is_autopaste_active", False) or clipboard is None:
            return
        formats = clipboard.get_formats()
        texture_gtype = getattr(Gdk.Texture, "__gtype__", Gdk.Texture)
        if HAS_OCR and formats is not None and formats.contain_gtype(texture_gtype):
            clipboard.read_texture_async(None, self._on_autopaste_texture_read)
        else:
            clipboard.read_text_async(None, self._on_autopaste_text_read)

    def _display_toast(self, toast: Adw.Toast):
        """Show a toast via window's toast_overlay."""
        win = getattr(self, "window", None)
        if win is None and hasattr(self, "get_root"):
            root = self.get_root()
            if root and hasattr(root, "toast_overlay"):
                win = root
        toast_overlay = getattr(win, "toast_overlay", None)
        if toast_overlay is not None:
            toast_overlay.add_toast(toast)

    def _on_clipboard_changed(self, clipboard):
        """Callback triggered when system clipboard content changes."""
        if not getattr(self, "is_autopaste_active", False):
            return
        self._read_clipboard_for_autopaste(clipboard)

    def _on_autopaste_text_read(self, clipboard, result):
        """Callback when clipboard text has been read asynchronously."""
        if not getattr(self, "is_autopaste_active", False):
            return

        try:
            text = clipboard.read_text_finish(result)
        except Exception:
            return

        if not text or not text.strip():
            return

        text = text.replace("\r\n", "\n").replace("\r", "\n")

        if text == self._last_pasted_text:
            return
        self._last_pasted_text = text
        self._last_pasted_image_hash = None

        self._insert_autopaste_text(text)

    def _on_autopaste_texture_read(self, clipboard, result):
        """Callback when clipboard texture has been read for AutoPaste OCR."""
        if not getattr(self, "is_autopaste_active", False):
            return

        try:
            texture = clipboard.read_texture_finish(result)
            data = _read_texture_png(texture)
        except Exception:
            return

        if not data or Image is None:
            return

        img_hash, raw = data
        if img_hash == self._last_pasted_image_hash or img_hash == self._pending_ocr_hash:
            return
        self._pending_ocr_hash = img_hash

        def run_ocr():
            extracted_text = ""
            try:
                img = Image.open(io.BytesIO(raw))
                extracted_text = extract_text_from_image(img)
            except Exception as e:
                print(f"AutoPaste OCR Error: {e}")

            def finish():
                if self._pending_ocr_hash == img_hash:
                    self._pending_ocr_hash = None
                if not getattr(self, "is_autopaste_active", False):
                    return False
                if extracted_text and extracted_text.strip():
                    self._last_pasted_image_hash = img_hash
                    self._last_pasted_text = None
                    self._insert_autopaste_text(extracted_text)
                return False

            GLib.idle_add(finish)

        threading.Thread(target=run_ocr, daemon=True).start()

    def _insert_autopaste_text(self, text: str):
        """Insert AutoPaste text at the target mark separated by an empty line between instances."""
        self.buffer.begin_user_action()
        try:
            mark = getattr(self, "_autopaste_mark", None)
            if mark is not None and not mark.get_deleted():
                target_iter = self.buffer.get_iter_at_mark(mark)
            else:
                target_iter = self.buffer.get_end_iter()

            if target_iter.get_offset() > 0:
                prev_iter = target_iter.copy()
                prev_iter.backward_char()
                if prev_iter.get_char() != '\n':
                    self.buffer.insert(target_iter, '\n\n')
                elif prev_iter.get_offset() > 0:
                    prev2_iter = prev_iter.copy()
                    prev2_iter.backward_char()
                    if prev2_iter.get_char() != '\n':
                        self.buffer.insert(target_iter, '\n')

            if mark is not None and not mark.get_deleted():
                target_iter = self.buffer.get_iter_at_mark(mark)
            else:
                target_iter = self.buffer.get_end_iter()

            insert_text = text.strip('\r\n') + '\n'
            self.buffer.insert(target_iter, insert_text)
        finally:
            self.buffer.end_user_action()

        mark = getattr(self, "_autopaste_mark", None)
        if mark is not None and not mark.get_deleted():
            end_paste_iter = self.buffer.get_iter_at_mark(mark)
        else:
            end_paste_iter = self.buffer.get_end_iter()
        self.buffer.place_cursor(end_paste_iter)
        GLib.idle_add(lambda: self.textview.scroll_mark_onscreen(self.buffer.get_insert()) or False)

    def handle_smart_paste(self):
        """Paste clipboard image via OCR or convert long URLs into Markdown links."""
        clipboard = self.textview.get_clipboard()
        formats = clipboard.get_formats()

        texture_gtype = getattr(Gdk.Texture, '__gtype__', Gdk.Texture)
        if HAS_OCR and formats.contain_gtype(texture_gtype):
            clipboard.read_texture_async(None, self.on_smart_paste_texture_read)
        else:
            clipboard.read_text_async(None, self.on_smart_paste_read)

    def on_smart_paste_texture_read(self, clipboard, result):
        try:
            texture = clipboard.read_texture_finish(result)
            if texture:
                toast = Adw.Toast.new(_("Extracting text from image…"))
                toast.set_timeout(0)
                self._display_toast(toast)

                def run_ocr():
                    try:
                        if Image is None:
                            return
                        png_bytes = texture.save_to_png_bytes()
                        img = Image.open(io.BytesIO(png_bytes.get_data()))
                        extracted_text = extract_text_from_image(img)

                        def on_success():
                            toast.dismiss()
                            self._insert_extracted_text(extracted_text)
                            return False

                        def on_empty():
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
        self._display_toast(Adw.Toast.new(msg))
        return False

    def on_smart_paste_read(self, clipboard, result):
        try:
            text = clipboard.read_text_finish(result)
            if text:
                markdown_link = _format_short_markdown_link(text.strip())
                inserted = markdown_link if markdown_link is not None else text

                def do_insert():
                    self.buffer.delete_selection(True, self.textview.get_editable())
                    self.buffer.insert_at_cursor(inserted)
                    return False

                GLib.idle_add(do_insert)
        except GLib.Error:
            pass

    def paste_plain_text(self):
        """Paste clipboard text with Markdown formatting stripped."""
        clipboard = self.textview.get_clipboard()
        clipboard.read_text_async(None, self.on_clipboard_read)

    def on_clipboard_read(self, clipboard, result):
        try:
            text = clipboard.read_text_finish(result)
            if text:
                cleaned = strip_markdown(text)

                def do_insert():
                    self.buffer.delete_selection(True, self.textview.get_editable())
                    self.buffer.insert_at_cursor(cleaned)
                    return False

                GLib.idle_add(do_insert)
        except GLib.Error:
            pass

    def shorten_link(self):
        """Convert the selected or under-cursor long URL into a Markdown link."""
        bounds = self.buffer.get_selection_bounds()
        start = end = None

        if bounds:
            start, end = bounds
        else:
            insert_iter = self.buffer.get_iter_at_mark(self.buffer.get_insert())
            start = insert_iter.copy()
            end = insert_iter.copy()

            while start.backward_char():
                if start.get_char() in (' ', '\n', '\t'):
                    start.forward_char()
                    break

            while end.forward_char():
                if end.get_char() in (' ', '\n', '\t'):
                    break

        url = self.buffer.get_text(start, end, False).strip()
        markdown_link = _format_short_markdown_link(url)
        if markdown_link is not None:
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

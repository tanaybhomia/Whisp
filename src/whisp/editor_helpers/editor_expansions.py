import re
import gettext
from gi.repository import GLib, Adw
from whisp.config import config

_ = gettext.gettext

has_ocr = False
try:
    import pytesseract
    from PIL import Image, ImageOps
    has_ocr = True
except ImportError:
    pytesseract = None
    Image = None
    ImageOps = None
    has_ocr = False
HAS_OCR = has_ocr

def extract_text_from_image(img):
    """Extract text from a PIL Image using Tesseract OCR with noise filtering and indentation preservation."""
    if not HAS_OCR or Image is None or ImageOps is None or pytesseract is None:
        return ""
    import time
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
            
            indent_spaces = int(round((first_left - min_left) / space_width))
            if indent_spaces > 8:
                indent_spaces = 8
            elif indent_spaces == 1:
                indent_spaces = 0
                
            line_str = " " * indent_spaces
            
            for j, w in enumerate(words):
                line_str += w['text']
                if j < len(words) - 1:
                    line_str += " "
                    
            out.append(line_str)
            
        extracted_text = "\n".join(out)
        
        elapsed = time.time() - start_time
        benchmark_str = f"[OCR Benchmark] Extracted {len(extracted_text)} chars in {elapsed:.3f} seconds."
        print(benchmark_str)
        
        has_valid_word = bool(re.search(r'[a-zA-Z0-9]{2,}', extracted_text))
        total_alphanumeric = len(re.findall(r'[a-zA-Z0-9]', extracted_text))
        is_valid_text = bool(extracted_text.strip()) and has_valid_word and (total_alphanumeric >= 3)
        
        return extracted_text.strip() if is_valid_text else ""
    except Exception as e:
        print(f"OCR Error: {e}")
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gi.repository import Gtk
    from whisp.window import WhispWindow

class EditorExpansionsMixin:
    """Mixin class containing text expansions (::count, ::date), text commands, and Smart OCR for NoteEditor."""
    buffer: "Gtk.TextBuffer"
    textview: "Gtk.TextView"
    autocomplete_box: "Gtk.Widget"
    window: "WhispWindow | None"

    if TYPE_CHECKING:
        def get_root(self) -> "Gtk.Root | None": ...
        def start_autopaste(self) -> None: ...
        def stop_autopaste(self, silent: bool = False) -> None: ...

    def handle_expansion(self, insert_char: str) -> bool:
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
            base = m_date.group(1)
            offset_days = int(m_date.group(2)) if m_date.group(2) else 0
            
            if base == "tomorrow":
                offset_days += 1
            elif base == "yesterday":
                offset_days -= 1
                
            now = GLib.DateTime.new_now_local()
            if now is not None:
                if offset_days != 0:
                    dt = now.add_days(offset_days)
                    if dt is not None:
                        now = dt
                    
                if base == "timestamp":
                    t_fmt = now.format("%X") or ""
                    t_str = re.sub(r':\d{2}(?=\s|$)', '', t_fmt)
                    date_str = f"{now.format('%x') or ''} {t_str}"
                else:
                    date_str = now.format("%x") or ""
            else:
                date_str = ""
                
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
            dt = GLib.DateTime.new_now_local()
            t_fmt = dt.format("%X") if dt else ""
            time_str = re.sub(r':\d{2}(?=\s|$)', '', t_fmt or "")
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
                pass
                
        # Check for ::random(type, length)
        m_random = re.match(r'^::random\((int|alnum|str),\s*(\d+)\)$', word, re.IGNORECASE)
        if m_random:
            import string
            import random
            rtype = m_random.group(1).lower()
            length = min(int(m_random.group(2)), 1000)
            
            if rtype == "int":
                chars = string.digits
            elif rtype == "str":
                chars = string.ascii_letters
            else:
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

        if word.lower() == "::paste":
            self.buffer.delete(word_start, word_end)
            self.autocomplete_box.set_visible(False)
            if getattr(self, "is_autopaste_active", False):
                self.stop_autopaste()
            else:
                self.start_autopaste()
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
                sentences = re.split(r'(?<=[.!?])\s+', text)
                new_text = ' '.join(s.capitalize() for s in sentences if s)
                if not new_text and text: new_text = text.capitalize()
                msg = _("Converted to Sentence case")
            elif cmd == "capitalize_first":
                new_text = re.sub(r'[a-zA-Z]', lambda m: m.group(0).upper(), text, count=1)
                msg = _("Capitalized first letter")
            elif cmd == "remove_quotes":
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
                lines = text.strip('\n').split('\n')
                unchecked = [line for line in lines if not (re.match(r'^(\s*)☑', line) or "- [x]" in line.lower() or "- [X]" in line)]
                new_text = '\n'.join(unchecked)
                msg = _("Removed all checked items")
                
            if new_text != text:
                self.buffer.delete(start_iter, end_iter)
                self.buffer.insert(self.buffer.get_start_iter(), new_text)
                    
                if config.get("show_command_toasts", True) and hasattr(self, 'window') and self.window:
                    self.window.toast_overlay.add_toast(Adw.Toast.new(msg))
                    
                self.textview.scroll_to_mark(self.buffer.get_insert(), 0.0, False, 0.0, 0.0)
                    
        except Exception as e:
            print(f"Error executing text command: {e}")
            
        self.buffer.end_user_action()

import unittest
import re

class TestHighlighter(unittest.TestCase):
    def test_asterisk_bullet_lists_not_italic(self):
        text = "* item one\n* item two\n* item three"
        italic_pattern = r"(?<!\\)(?<!\*)(\*)([^\s\*\n]|(?:[^\s\*\n](?:[^\*\n\\]|\\.)*?[^\s\*\n]))(?<!\\)(\*)(?!\*)"
        matches = list(re.finditer(italic_pattern, text))
        self.assertEqual(len(matches), 0, "Asterisk bullet points should not be matched as italic text")

    def test_inline_italic_inside_bullet_list(self):
        text = "* item one\n* item two with *italic text*\n* item three"
        italic_pattern = r"(?<!\\)(?<!\*)(\*)([^\s\*\n]|(?:[^\s\*\n](?:[^\*\n\\]|\\.)*?[^\s\*\n]))(?<!\\)(\*)(?!\*)"
        matches = list(re.finditer(italic_pattern, text))
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].group(0), "*italic text*")


    def test_markdown_checkbox_pattern(self):
        pattern = r"^(\s*)([☐☑]|[-*+]\s*\[[ xX]\])\s*(.*)$"
        self.assertTrue(re.match(pattern, "- [ ] test item"))
        self.assertTrue(re.match(pattern, "- [x] completed item"))
        self.assertTrue(re.match(pattern, "☐ unicode item"))
        self.assertTrue(re.match(pattern, "☑ finished unicode item"))

    def test_toggle_checkbox_stripping_logic(self):
        def toggle(line_text):
            m_indent = re.match(r'^(\s*)', line_text)
            indent = m_indent.group(1) if m_indent else ""
            rest = line_text[len(indent):]

            has_box_or_bullet = bool(re.match(r'^(?:[☐☑]|[-*+]\s*\[[ xX]\]|[-*+])(?:\s+|$)', rest))
            has_checked = bool(re.search(r'(?:☑|\[[xX]\])', rest))

            clean_content = rest
            while True:
                stripped = re.sub(r'^(?:[☐☑]|[-*+]\s*\[[ xX]\]|[-*+])\s*', '', clean_content)
                if stripped == clean_content:
                    break
                clean_content = stripped

            if not clean_content.strip():
                if has_box_or_bullet:
                    return indent
                else:
                    return f"{indent}☐ "
            else:
                if has_checked:
                    return f"{indent}☐ {clean_content}"
                elif has_box_or_bullet:
                    return f"{indent}☑ {clean_content}"
                else:
                    return f"{indent}☐ {clean_content}"

        # Test empty line toggle
        self.assertEqual(toggle(""), "☐ ")
        self.assertEqual(toggle("☐ "), "")
        self.assertEqual(toggle(""), "☐ ")

        # Test item toggling
        self.assertEqual(toggle("This is another item"), "☐ This is another item")
        self.assertEqual(toggle("☐ This is another item"), "☑ This is another item")
        self.assertEqual(toggle("☑ This is another item"), "☐ This is another item")

        # Test stacked checkboxes cleanup (from screenshot issue)
        self.assertEqual(toggle("☐ ☑ ☑ ☐ THis is a Check item"), "☐ THis is a Check item")

if __name__ == "__main__":
    unittest.main()

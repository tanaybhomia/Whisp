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

if __name__ == "__main__":
    unittest.main()

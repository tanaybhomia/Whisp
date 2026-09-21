import unittest

from whisp.text_color import (
    apply_color_edit,
    color_marker_ranges,
    compute_color_edit,
    find_color_spans,
    normalize_color,
    plain_to_raw_offset,
    prune_empty_color_spans,
    selection_color,
    strip_color_markup,
)

RED = "#e01b24"
BLUE = "#3584e4"


def span(color, inner):
    return f'<span style="color:{color}">{inner}</span>'


def colorize(text, needle, color, occurrence=0):
    """Apply ``color`` to the ``occurrence``-th ``needle``; returns (new_text, selected)."""
    start = -1
    for _ in range(occurrence + 1):
        start = text.index(needle, start + 1)
    edit = compute_color_edit(text, start, start + len(needle), color)
    assert edit is not None
    new = apply_color_edit(text, edit)
    return new, new[edit.sel_start:edit.sel_end]


class TestNormalize(unittest.TestCase):
    def test_valid_and_invalid(self):
        self.assertEqual(normalize_color("#E01B24"), RED)
        self.assertEqual(normalize_color("#f00"), "#ff0000")
        for bad in ("red", "#12", "#gggggg", "", None, "#1234567"):
            self.assertIsNone(normalize_color(bad))


class TestParsing(unittest.TestCase):
    def test_plain_note_is_untouched(self):
        text = "# Title\n\n- one\n- **two**\n"
        self.assertEqual(find_color_spans(text), [])
        self.assertEqual(strip_color_markup(text), text)

    def test_strip_and_find(self):
        text = f"a {span(RED, 'b')} c {span(BLUE, 'd')}"
        self.assertEqual([s.color for s in find_color_spans(text)], [RED, BLUE])
        self.assertEqual(strip_color_markup(text), "a b c d")

    def test_invalid_color_left_as_text(self):
        text = '<span style="color:red">x</span> <span style="color:#zzzzzz">y</span>'
        self.assertEqual(find_color_spans(text), [])
        self.assertEqual(strip_color_markup(text), text)

    def test_foreign_span_does_not_steal_our_close_tag(self):
        text = f'{span(RED, "a")}<span class="k">b</span>'
        spans = find_color_spans(text)
        self.assertEqual(len(spans), 1)
        self.assertEqual(text[spans[0].open_end:spans[0].close_start], "a")

    def test_unbalanced_tags_ignored(self):
        self.assertEqual(find_color_spans(f'<span style="color:{RED}">oops'), [])
        self.assertEqual(find_color_spans("oops</span>"), [])


class TestApply(unittest.TestCase):
    def test_single_word(self):
        new, selected = colorize("This is important text.", "important", RED)
        self.assertEqual(new, f"This is {span(RED, 'important')} text.")
        self.assertEqual(selected, span(RED, "important"))

    def test_only_selection_changes(self):
        text = "one two three"
        new, _ = colorize(text, "two", RED)
        self.assertEqual(strip_color_markup(new), text)

    def test_preserves_bold_and_italic(self):
        new, _ = colorize("**bold** and *it*", "**bold**", RED)
        self.assertEqual(new, f"{span(RED, '**bold**')} and *it*")
        new, _ = colorize("**bold** and *it*", "*it*", RED)
        self.assertEqual(strip_color_markup(new), "**bold** and *it*")

    def test_partial_overlap_replaces_only_selected_part(self):
        text = f"a {span(RED, 'red word')} b"
        new, _ = colorize(text, "word", BLUE)
        self.assertEqual(new, f"a {span(RED, 'red ')}{span(BLUE, 'word')} b")

    def test_recolor_whole_span_does_not_nest(self):
        text = f"a {span(RED, 'red')} b"
        new, _ = colorize(text, "red", BLUE)
        self.assertEqual(new, f"a {span(BLUE, 'red')} b")

    def test_selection_spanning_several_colors(self):
        text = f"{span(RED, 'aa')} {span(BLUE, 'bb')} cc"
        edit = compute_color_edit(text, 0, text.index("cc") + 2, RED)
        self.assertEqual(apply_color_edit(text, edit), span(RED, "aa bb cc"))

    def test_remove_color_from_middle_keeps_the_rest(self):
        text = f"a {span(RED, 'one two three')} b"
        new, _ = colorize(text, "two", None)
        self.assertEqual(new, f"a {span(RED, 'one ')}two{span(RED, ' three')} b")

    def test_remove_color_entirely(self):
        text = f"a {span(RED, 'red')} b"
        new, selected = colorize(text, "red", None)
        self.assertEqual(new, "a red b")
        self.assertEqual(selected, "red")

    def test_removing_uncolored_selection_is_a_noop_edit(self):
        text = "nothing here"
        edit = compute_color_edit(text, 0, 7, None)
        self.assertEqual(apply_color_edit(text, edit), text)

    def test_empty_selection_and_invalid_color(self):
        self.assertIsNone(compute_color_edit("abc", 1, 1, RED))
        self.assertIsNone(compute_color_edit("abc", 0, 2, "nope"))

    def test_multiline_gets_one_span_per_line(self):
        text = "first\nsecond\nthird"
        edit = compute_color_edit(text, 0, len(text), RED)
        new = apply_color_edit(text, edit)
        self.assertEqual(new, "\n".join(span(RED, w) for w in ("first", "second", "third")))
        self.assertEqual(strip_color_markup(new), text)

    def test_line_prefixes_stay_outside_the_span(self):
        text = "# Head\n- item\n1. num\n☐ todo\nplain"
        edit = compute_color_edit(text, 0, len(text), RED)
        new = apply_color_edit(text, edit)
        self.assertEqual(
            new.split("\n"),
            ["# " + span(RED, "Head"), "- " + span(RED, "item"), "1. " + span(RED, "num"),
             "☐ " + span(RED, "todo"), span(RED, "plain")],
        )

    def test_blank_lines_and_whitespace_are_not_wrapped(self):
        text = "a\n\n  \nb"
        edit = compute_color_edit(text, 0, len(text), RED)
        new = apply_color_edit(text, edit)
        self.assertEqual(new, f"{span(RED, 'a')}\n\n  \n{span(RED, 'b')}")

    def test_multiline_span_from_hand_edited_note_is_split(self):
        text = span(RED, "one\ntwo")
        new, _ = colorize(text, "two", None)
        self.assertEqual(new, f"{span(RED, 'one')}\ntwo")

    def test_adjacent_same_color_merges(self):
        text = f"{span(RED, 'aa')} bb"
        edit = compute_color_edit(text, text.index(" bb"), len(text), RED)
        self.assertEqual(apply_color_edit(text, edit), span(RED, "aa bb"))

    def test_selection_offsets_survive_unicode(self):
        text = "héllo 🙂 wörld"
        new, selected = colorize(text, "🙂 wörld", RED)
        self.assertEqual(selected, span(RED, "🙂 wörld"))
        self.assertEqual(new, f"héllo {span(RED, '🙂 wörld')}")

    def test_round_trip_is_stable(self):
        text = "alpha beta gamma"
        once, _ = colorize(text, "beta", RED)
        again, _ = colorize(once, "beta", RED)
        self.assertEqual(once, again)
        cleared, _ = colorize(once, "beta", None)
        self.assertEqual(cleared, text)

    def test_edit_is_minimal(self):
        text = "x" * 200 + " target " + "y" * 200
        start = text.index("target")
        edit = compute_color_edit(text, start, start + 6, RED)
        self.assertGreaterEqual(edit.start, start - 1)
        self.assertLess(edit.end - edit.start, 10)


class TestMarkers(unittest.TestCase):
    def test_marker_ranges(self):
        text = f"a {span(RED, 'b')} c"
        ranges = color_marker_ranges(text)
        self.assertEqual([text[a:b] for a, b in ranges], [f'<span style="color:{RED}">', "</span>"])

    def test_plain_to_raw_offset_round_trips_every_character(self):
        text = f"a {span(RED, 'bc')} d{span(BLUE, 'e')}"
        plain = strip_color_markup(text)
        for i, ch in enumerate(plain):
            self.assertEqual(text[plain_to_raw_offset(text, i)], ch)

    def test_plain_to_raw_offset_without_markup_is_identity(self):
        for i in range(6):
            self.assertEqual(plain_to_raw_offset("plain", i), i)

    def test_prune_empty_spans(self):
        self.assertEqual(prune_empty_color_spans(f"a {span(RED, '')} b"), "a  b")
        self.assertEqual(prune_empty_color_spans(f"a {span(RED, span(BLUE, ''))} b"), "a  b")
        keep = f"a {span(RED, 'x')} b"
        self.assertEqual(prune_empty_color_spans(keep), keep)


class TestSelectionColor(unittest.TestCase):
    def test_uniform_and_mixed(self):
        text = f"{span(RED, 'aa')} {span(BLUE, 'bb')} plain"
        self.assertEqual(selection_color(text, 0, len(span(RED, "aa"))), RED)
        self.assertIsNone(selection_color(text, 0, len(text)))
        self.assertIsNone(selection_color("plain", 0, 3))


if __name__ == "__main__":
    unittest.main()

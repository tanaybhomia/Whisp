import gettext

from gi.repository import Gtk, GLib

from whisp.text_color import PALETTE, normalize_color

_ = gettext.gettext

# Spelled out so gettext can extract the swatch names (keyed by PALETTE names).
COLOR_NAMES = {
    "Red": _("Red"),
    "Orange": _("Orange"),
    "Yellow": _("Yellow"),
    "Green": _("Green"),
    "Blue": _("Blue"),
    "Purple": _("Purple"),
    "Brown": _("Brown"),
    "Gray": _("Gray"),
}


def rgba_to_hex(rgba):
    return "#{:02x}{:02x}{:02x}".format(*(round(c * 255) for c in (rgba.red, rgba.green, rgba.blue)))


class TextColorPopover(Gtk.Popover):
    """Compact palette: preset swatches, a custom color and a reset button.

    ``on_color(color)`` is called with ``#rrggbb`` or None to remove the color.
    """

    def __init__(self, on_color):
        super().__init__()
        self.on_color = on_color
        self._swatches = {}

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for side in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{side}")(9)
        self.set_child(box)

        grid = Gtk.Grid(row_spacing=8, column_spacing=8, halign=Gtk.Align.CENTER)
        for i, (color, name) in enumerate(PALETTE):
            grid.attach(self._make_swatch(color, COLOR_NAMES[name]), i % 4, i // 4, 1, 1)
        box.append(grid)

        box.append(Gtk.Separator())

        custom_btn = Gtk.Button(label=_("Custom Color…"))
        custom_btn.add_css_class("flat")
        custom_btn.connect("clicked", self._on_custom_clicked)
        box.append(custom_btn)

        self.reset_btn = Gtk.Button(label=_("Remove Color"))
        self.reset_btn.add_css_class("flat")
        self.reset_btn.connect("clicked", lambda _b: self._choose(None))
        box.append(self.reset_btn)

    def _make_swatch(self, color, name):
        # The color is a Pango attribute, not CSS: a user theme that restyles
        # buttons cannot repaint the swatch, and nothing here needs cairo.
        label = Gtk.Label()
        label.set_markup(f'<span foreground="{color}" size="xx-large">\u25cf</span>')
        btn = Gtk.ToggleButton(child=label)
        btn.add_css_class("flat")
        btn.set_tooltip_text(name)
        btn.update_property([Gtk.AccessibleProperty.LABEL], [name])
        # Pressed state marks the current color without relying on color alone.
        btn.connect("clicked", lambda _b: self._choose(color))
        self._swatches[color] = btn
        return btn

    def set_current_color(self, color):
        """Mark ``color`` (``#rrggbb`` or None) as the selection's current color."""
        for swatch_color, btn in self._swatches.items():
            btn.set_active(swatch_color == color)
        self.reset_btn.set_sensitive(color is not None)

    def _choose(self, color):
        self.popdown()
        self.on_color(color)

    def _on_custom_clicked(self, _btn):
        self.popdown()
        dialog = Gtk.ColorDialog(title=_("Text Color"), with_alpha=False)

        def on_done(dlg, result):
            try:
                rgba = dlg.choose_rgba_finish(result)
            except GLib.Error:
                return  # dismissed
            color = normalize_color(rgba_to_hex(rgba))
            if color:
                self.on_color(color)

        dialog.choose_rgba(self.get_root(), None, None, on_done)

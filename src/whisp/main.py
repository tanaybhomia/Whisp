import sys
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gdk, Gio
from whisp.window import WhispWindow

IS_DEV_MODE = "--dev" in sys.argv

class WhispApp(Adw.Application):
    def __init__(self):
        app_id = "io.github.tanaybhomia.Whisp.Devel" if IS_DEV_MODE else "io.github.tanaybhomia.Whisp"
        super().__init__(application_id=app_id, flags=Gio.ApplicationFlags.HANDLES_OPEN)

    def do_startup(self):
        Adw.Application.do_startup(self)
        
        # Add local icon directory to search path for testing
        import os
        from pathlib import Path
        icon_theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        icon_dir = Path(__file__).parent.parent.parent / "data" / "icons"
        if icon_dir.exists():
            icon_theme.add_search_path(str(icon_dir))
            
        from whisp.config import config
        shortcuts = config.get("shortcuts")
        for action, accels in shortcuts.items():
            self.set_accels_for_action(action, accels)

        # Cohesive Background CSS
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(b"""
            window.background { background-color: @view_bg_color; }
            textview { 
                background-color: transparent; 
            }
            textview > text {
                padding: 0 8px; /* Prevent left-edge glyph clipping */
            }
            toast {
                margin-left: 48px;
                margin-right: 48px;
                margin-bottom: 24px;
            }
            window.about image.icon { transform: scale(0.8); }

            /* Paper Themes */
            .paper-dotted {
                background-image: radial-gradient(circle, alpha(currentColor, 0.15) 1px, transparent 1px);
                background-size: 20px 20px;
                background-position: 0 0;
            }
            .paper-grid {
                background-image: linear-gradient(to right, alpha(currentColor, 0.1) 1px, transparent 1px),
                                  linear-gradient(to bottom, alpha(currentColor, 0.1) 1px, transparent 1px);
                background-size: 14px 14px;
                background-position: 0 0;
            }
            .paper-large_grid {
                background-image: linear-gradient(to right, alpha(currentColor, 0.1) 1px, transparent 1px),
                                  linear-gradient(to bottom, alpha(currentColor, 0.1) 1px, transparent 1px);
                background-size: 36px 36px;
                background-position: 0 0;
            }
            .paper-blank { background-image: none; }
            
            /* Snippet styling */
            .theme-snippet-btn {
                padding: 4px;
                border-radius: 12px;
                border: 2px solid transparent;
            }
            .theme-snippet-btn:checked {
                border-color: @accent_bg_color;
                background-color: transparent;
            }
            .theme-snippet-preview {
                min-width: 120px;
                min-height: 80px;
                border-radius: 8px;
                border: 1px solid alpha(currentColor, 0.15);
                background-color: @view_bg_color;
                padding: 12px 8px;
            }
            .fake-text-line {
                background-color: alpha(currentColor, 0.3);
                border-radius: 2px;
            }
            .autocomplete-overlay {
                border-radius: 16px;
                border: 1px solid alpha(currentColor, 0.1);
                box-shadow: 0 4px 12px rgba(0,0,0,0.1);
                background-color: @view_bg_color;
            }
            .autocomplete-overlay row {
                border-radius: 8px;
            }
        """)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def do_activate(self):
        windows = self.get_windows()
        win = windows[0] if windows else None
        
        if not win:
            win = WhispWindow(application=self)
            win.load_notes()
            
        if hasattr(self, 'start_hidden') and self.start_hidden:
            self.start_hidden = False
        else:
            if win and not win.is_visible() and not getattr(self, '_opening_files', False):
                from whisp.config import config
                if config.get("startup_behavior", "last_note") == "empty_note":
                    win.ensure_empty_note_at_end()
                    win.on_nav_last()
            win.present()

    def do_open(self, files, n_files, hint):
        self._opening_files = True
        windows = self.get_windows()
        win = windows[0] if windows else None
        
        if not win:
            win = WhispWindow(application=self)
            win.load_notes(skip_restore=True)
            
        search_terms = None
        for file in files:
            uri = file.get_uri() or ""
            if uri.startswith("whisp://"):
                from urllib.parse import parse_qs, urlparse
                query = parse_qs(urlparse(uri).query)
                terms = query.get("q", [""])[0]
                if terms:
                    search_terms = terms
                continue
            path = file.get_path()
            if path:
                from pathlib import Path
                target_path = Path(path)
                # Check if it's already in the carousel
                found = False
                n_pages = win.carousel.get_n_pages()
                for i in range(n_pages):
                    editor = win.carousel.get_nth_page(i)
                    if editor.file_path and Path(editor.file_path).name == target_path.name:
                        def do_scroll(ed=editor):
                            if win.carousel.get_width() == 0:
                                do_scroll.attempts = getattr(do_scroll, 'attempts', 0) + 1
                                if do_scroll.attempts < 20:
                                    return True
                            win.carousel.scroll_to(ed, False)
                            ed.textview.grab_focus()
                            return False
                        
                        from gi.repository import GLib
                        GLib.timeout_add(50, do_scroll)
                        GLib.timeout_add(150, do_scroll)
                        found = True
                        break
                
                # If not, add it
                if not found:
                    insert_idx = None
                    if n_pages > 0:
                        last_editor = win.carousel.get_nth_page(n_pages - 1)
                        if last_editor.is_empty():
                            insert_idx = n_pages - 1
                            
                    if insert_idx is not None:
                        win.add_note(path, index=insert_idx)
                    else:
                        win.add_note(path)
                    
        win.present()
        if search_terms:
            win.open_search(search_terms)
            
        from gi.repository import GLib
        def reset_flag():
            self._opening_files = False
            return False
        GLib.timeout_add(1000, reset_flag)

def main():
    if '--search-provider' in sys.argv:
        sys.argv.remove('--search-provider')
        from whisp.search_provider import main as provider_main
        return provider_main()
        
    if '--dev' in sys.argv:
        sys.argv.remove('--dev')
        
    start_hidden = False
    if '--hidden' in sys.argv:
        start_hidden = True
        sys.argv.remove('--hidden')
        
    app = WhispApp()
    app.start_hidden = start_hidden
    return app.run(sys.argv)

if __name__ == '__main__':
    sys.exit(main())

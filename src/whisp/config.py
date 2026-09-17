import json
from pathlib import Path
from gi.repository import GLib

# Data directory configuration
CONFIG_DIR = Path(GLib.get_user_config_dir()) / "whisp"
CONFIG_FILE = CONFIG_DIR / "config.json"

STATE_DIR = Path(GLib.get_user_state_dir()) / "whisp"
STATE_FILE = STATE_DIR / "state.json"

STATE_KEYS = {
    "window_width",
    "window_height", 
    "is_maximized",
    "first_run",
    "last_seen_version",
    "last_active_note",
    "wysiwyg_mode"
}

class Config:
    DEFAULT_SHORTCUTS = {
        "win.new-note": ["<Ctrl>n"],
        "win.delete-note": ["<Ctrl>d", "<Shift>Delete"],
        "win.preferences": ["<Ctrl>comma"],
        "win.toggle-wysiwyg": ["<Ctrl>e"],
        "win.undo-delete": ["<Ctrl><Shift>t"],
        "win.show-shortcuts": ["<Ctrl>slash"],
        "win.nav-next": ["<Ctrl>bracketright"],
        "win.nav-prev": ["<Ctrl>bracketleft"],
        "win.search": ["<Ctrl>f"],
        "win.pin-note": ["<Ctrl><Shift>p"],
        "win.nav-first": ["<Alt>f"],
        "win.nav-last": ["<Alt>l"],
        "win.copy-note": ["<Ctrl><Shift>c"],
        "win.bump-note": ["<Ctrl><Shift>m"],
        "win.export-note": ["<Ctrl><Shift>s"],
        "win.slate-mode": ["<Alt>s", "F11"],
        "win.quit": ["<Ctrl>q"]
    }

    def __init__(self):
        self.config_data = {
            "data_dir": str(Path(GLib.get_user_data_dir()) / "whisp" / "notes"),
            "font_name": "Monospace 11",
            "paper_theme": "blank",
            "confirm_delete": True,
            "color_scheme": "system",
            "startup_behavior": "last_note",
            "run_in_background": False,
            "run_on_startup": False,
            "start_hidden": False,
            "show_command_toasts": True,
            "archive_days": 0,
            "max_carousel_size": 10,
            "start_in_slate_mode": False,
            "wysiwyg_scope": "global",
            "global_toggle_shortcut": "<Super>n",
            "shortcuts": self.DEFAULT_SHORTCUTS.copy()
        }
        
        self.state_data = {
            "window_width": 360,
            "window_height": 500,
            "is_maximized": False,
            "first_run": True,
            "last_seen_version": "0.0.0",
            "last_active_note": None,
            "wysiwyg_mode": False
        }
        self.load()

    def load(self):
        # Load config
        if CONFIG_FILE.exists():
            try:
                loaded_config = json.loads(CONFIG_FILE.read_text())
                
                # Migrate state keys out of old config files
                migrated = False
                for key in STATE_KEYS:
                    if key in loaded_config:
                        self.state_data[key] = loaded_config.pop(key)
                        migrated = True
                        
                if "remember_slate_mode" in loaded_config:
                    loaded_config["start_in_slate_mode"] = loaded_config.pop("remember_slate_mode")
                    migrated = True
                        
                self.config_data.update(loaded_config)
                
                if migrated:
                    self.save_state()
                    self.save_config()
            except:
                pass
        else:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            self.save_config()
            
        # Load state
        if STATE_FILE.exists():
            try:
                self.state_data.update(json.loads(STATE_FILE.read_text()))
            except:
                pass
        else:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            self.save_state()

    def save_config(self):
        try:
            CONFIG_FILE.write_text(json.dumps(self.config_data, indent=4))
        except Exception:
            pass
        
    def save_state(self):
        try:
            STATE_FILE.write_text(json.dumps(self.state_data, indent=4))
        except Exception:
            pass

    @property
    def data_dir(self):
        return Path(self.config_data.get("data_dir", str(Path(GLib.get_user_data_dir()) / "whisp" / "notes")))

    @data_dir.setter
    def data_dir(self, value):
        self.config_data["data_dir"] = str(value)
        self.save_config()

    def get(self, key, default=None):
        if key in STATE_KEYS:
            return self.state_data.get(key, default)
        return self.config_data.get(key, default)

    def set(self, key, value):
        if key in STATE_KEYS:
            self.state_data[key] = value
            self.save_state()
        else:
            self.config_data[key] = value
            self.save_config()

config = Config()
DATA_DIR = config.data_dir
TRASH_DIR = DATA_DIR / ".trash"

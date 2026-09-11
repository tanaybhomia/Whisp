<div align="center">
  
  <img src="data/icons/io.github.tanaybhomia.Whisp.svg" alt="Whisp Icon" width="192" height="192" style="vertical-align: middle;"> 
  <h1>Whisp</h1>
  <p><b>The Anti Note for GNOME</b></p>

  <a href="https://flathub.org/apps/io.github.tanaybhomia.Whisp">
    <img src="https://flathub.org/api/badge?svg&locale=en" alt="Download on Flathub" height="80">
  </a>
  <br><br>
  <a href="LICENSE"><img src="https://img.shields.io/badge/LICENSE-GPL--3.0-0AB3BB?labelColor=242424&style=flat-square" alt="License: GPLv3"></a>
  <a href="#"><img src="https://img.shields.io/badge/BUILD-PASSING-3CB32A?labelColor=242424&style=flat-square" alt="Build Status"></a>
  <a href="https://flathub.org/apps/io.github.tanaybhomia.Whisp"><img src="https://img.shields.io/flathub/downloads/io.github.tanaybhomia.Whisp?style=flat-square&logo=flathub&labelColor=242424&color=0AB3BB" alt="Flathub Downloads"></a>
  <a href="https://l10n.gnome.org/module/whisp/"><img src="https://img.shields.io/badge/TRANSLATED-GNOME%20L10N-0AB3BB?style=flat-square&logo=gnome&labelColor=242424" alt="Translation Status"></a>
</div>

<div align="center">
  <img alt="Whisp Main Interface" src="docs/assets/1-hero.png" style="max-width: 100%; height: auto;" />
</div>

Whisp is a fast note-taking application built for the GNOME desktop environment. It replaces traditional file hierarchies with a spatial, swipeable canvas. Inspired by the "anti-note" philosophy, it acts as a quick desktop scratchpad with Markdown editing, built natively with GTK4 and Libadwaita.

## Why Whisp?

Most note-taking apps force you into a heavy workflow of creating files, managing folders, and hitting "Save". **Whisp is different.**

- **Zero Friction**: There are no titles to type, no files to name, and no folders to manage. Just open the app and start typing.
- **Swipeable Canvas**: Instead of a sidebar of files, your notes exist in a spatial, horizontal carousel. A quick trackpad swipe instantly glides you to your next thought.
- **The "Anti-Note"**: Use it as a scratchpad. Jot down quick thoughts, paste temporary links, and when you're done, hit `Ctrl+D` to delete it forever and keep your desk clean.

## Core Features

- **Spatial Navigation**: Fluidly swipe between your recent notes using 1:1 touchpad gestures via Adwaita Carousel.
- **WYSIWYG Markdown**: Real-time rendering of Markdown. Toggle WYSIWYG mode to instantly hide Markdown syntax symbols and view clean rich text.
- **Paper Themes**: Native dynamic styling. Choose between Dotted, Grid, or Blank backgrounds to mimic physical engineering paper or scratchpads.
- **Smart Paste & OCR**: Copy any image or screenshot containing text and press `Ctrl+V` to run rapid, offline OCR that automatically extracts and inserts the text while mathematically preserving exact code indentation! Alternatively, paste a URL to automatically shrink it via TinyURL in the background, or use `Ctrl+Shift+V` to strip Markdown formatting when pasting rich text.
- **Keyboard-Centric Workflow**:
  - `Ctrl+N` to instantly create a new note.
  - `Ctrl+B`, `Ctrl+I`, `Ctrl+U` for quick text formatting.
- **Performance Focused**: Renders only the most recently active notes to keep startup times fast.

## Installation

Whisp is officially distributed through Flathub, making it easy to install on any Linux distribution.

```bash
flatpak install flathub io.github.tanaybhomia.Whisp
```

<details>
<summary>Installation using flakes (Home Manager)</summary>
To use this package in your configuration, you can import it as a flake input and either use the Home Manager module or install the package directly. Here is how you can do it:

### 1. Add Whisp as a Flake Input

Add this repository to the `inputs` section of your `flake.nix`:

```nix
inputs = {
  nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
  whisp.url = "github:tanaybhomia/Whisp";
};
```

---

### 2. Enable it in your configuration

You have two options depending on how you prefer to configure your system:

#### Option A: Using the Home Manager Module (Recommended)

Pass `inputs` to your module arguments, import the module in your Home Manager configuration, and enable/configure the program:

**In your `home.nix` or flake configuration:**

```nix
{ inputs, pkgs, ... }: {
  imports = [
    inputs.whisp.homeManagerModules.default
  ];

  # Optional: Use the stable version from nixpkgs instead of the latest flake version
  # package = pkgs.whisp;

  # Enable and configure Whisp declaratively
  programs.whisp = {
    enable = true;
    settings = {
      data_dir = "${config.home.homeDirectory}/.local/share/whisp/notes";
      font_name = "VictorMono Nerd Font Bold 12";
      paper_theme = "blank";
      confirm_delete = true;
      color_scheme = "light";
      startup_behavior = "last_note";
      run_in_background = true;
      run_on_startup = true;
      start_hidden = true;
      show_command_toasts = true;
      archive_days = 0;
      max_carousel_size = 10;
      start_in_slate_mode = true;
    };
  };
}
```

#### Option B: Installing the Package Directly

If you do not want to use the module, you can add the package directly to your configuration. You can choose to use the stable version from `nixpkgs` or the latest version from this flake:

**In your Home Manager or NixOS configuration:**

```nix
{ pkgs, inputs, ... }: {
  home.packages = [ # or environment.systemPackages for NixOS
    # Option 1: Use the latest flake version
    inputs.whisp.packages.${stdenv.hostPlatform.system}.default


    # Option 2: Use the stable version from nixpkgs (if available)
    # pkgs.whisp
  ];
}
```

</details>

## Contribution & Development

If you'd like to contribute to Whisp or build your own version, we have set up scripts to make local development frictionless. We kindly ask that all contributors adhere to our [Code of Conduct](CODE_OF_CONDUCT.md).

### Translations

Want to help translate Whisp into your native language? Whisp is [translated on the GNOME platform Damned Lies](https://l10n.gnome.org/module/whisp/), join your language team to start translating Whisp.

### Local Testing

You do not need to install the app or compile it with Meson just to test Python code changes. Run the following command in the project root to instantly launch the app from the source code:

```bash
./run.sh
```

*(Note: To test the Smart Paste OCR features locally outside of Flatpak, ensure `tesseract` (or `tesseract-ocr`), `python3-pillow`, and `pytesseract` are installed on your system).*

### Development Environment Setup

If you want to use the official Flathub release for your daily notes, but also want a separate development version of Whisp in your app launcher for testing, run:

```bash
./install-dev.sh
```

This script creates a separate "Whisp (Development)" entry in your GNOME app grid. It uses a custom development icon and saves your test notes to a completely isolated folder (`~/.local/share/Whisp/`), keeping your official Flatpak notes safe. Any code changes you make in your IDE will instantly be reflected the next time you click the Development app icon.

## Architecture

Whisp follows the GNOME Human Interface Guidelines (HIG). It uses `Adw.Carousel` for its swipeable interface and uses a custom `Gtk.TextView` wrapper to parse and format Markdown text.

## License

Whisp is free and open-source software licensed under the **GNU General Public License v3.0** (GPL-3.0). See the [LICENSE](LICENSE) file for more details.

## Inspiration

Whisp was heavily inspired by the core workflow and design philosophy of **[Anti Note on macos](https://antinote.io/)**. I loved the concept of a distraction-free, "anti-folder" scratchpad, but since it wasn't available on Linux, I built Whisp to bring that exact experience natively to the GNOME ecosystem!

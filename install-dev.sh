#!/usr/bin/env bash
# install-dev.sh - Installs the Development version of Whisp to your system launcher

# Ensure we're in the right directory
cd "$(dirname "$0")"

APP_DIR=$(pwd)
DESKTOP_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"
SERVICE_DIR="$HOME/.local/share/dbus-1/services"

# GNOME Shell only scans search-providers dirs listed in XDG_DATA_DIRS.
# The flatpak exports dir is the user-writable one among them.
FLATPAK_EXPORTS="$HOME/.local/share/flatpak/exports/share"
if [ -d "$FLATPAK_EXPORTS" ]; then
    PROVIDER_DIR="$FLATPAK_EXPORTS/gnome-shell/search-providers"
else
    PROVIDER_DIR="/usr/local/share/gnome-shell/search-providers"
fi

echo "Installing Whisp (Development)..."

# Create directories if they don't exist
mkdir -p "$DESKTOP_DIR"
mkdir -p "$ICON_DIR"
mkdir -p "$SERVICE_DIR"
mkdir -p "$PROVIDER_DIR" 2>/dev/null || echo "WARNING: cannot write to $PROVIDER_DIR, search provider will not be installed"

# Copy the development icon
if [ -f "data/icons/io.github.tanaybhomia.Whisp.Devel.svg" ]; then
    cp "data/icons/io.github.tanaybhomia.Whisp.Devel.svg" "$ICON_DIR/"
    echo "Copied development icon."
else
    echo "WARNING: Could not find data/icons/io.github.tanaybhomia.Whisp.Devel.svg"
fi

# Generate the .desktop file
cat <<EOF > "$DESKTOP_DIR/io.github.tanaybhomia.Whisp.Devel.desktop"
[Desktop Entry]
Name=Whisp (Development)
Comment=A minimalist, gesture-driven scratchpad for GNOME.
Exec=$APP_DIR/run.sh --dev
Icon=io.github.tanaybhomia.Whisp.Devel
Terminal=false
Type=Application
Categories=Utility;TextEditor;
StartupNotify=true
EOF

echo "Created launcher entry at $DESKTOP_DIR/io.github.tanaybhomia.Whisp.Devel.desktop"

# Clean up legacy production service override created by older dev scripts
rm -f "$SERVICE_DIR/io.github.tanaybhomia.Whisp.service"

# D-Bus service files so the dev app and its GNOME Shell search provider can be activated on demand
cat <<EOF > "$SERVICE_DIR/io.github.tanaybhomia.Whisp.Devel.service"
[D-BUS Service]
Name=io.github.tanaybhomia.Whisp.Devel
Exec=$APP_DIR/run.sh --dev
EOF

cat <<EOF > "$SERVICE_DIR/io.github.tanaybhomia.Whisp.SearchProvider.service"
[D-BUS Service]
Name=io.github.tanaybhomia.Whisp.SearchProvider
Exec=$APP_DIR/run.sh --search-provider
EOF

echo "Created D-Bus service files in $SERVICE_DIR"

# GNOME Shell search provider registration
cat <<EOF > "$PROVIDER_DIR/io.github.tanaybhomia.Whisp-search-provider.ini"
[Shell Search Provider]
DesktopId=io.github.tanaybhomia.Whisp.Devel.desktop
BusName=io.github.tanaybhomia.Whisp.SearchProvider
ObjectPath=/io/github/tanaybhomia/Whisp/SearchProvider
Version=2
EOF

echo "Created search provider registration at $PROVIDER_DIR/io.github.tanaybhomia.Whisp-search-provider.ini"

# Clean up the old (unscanned) location from earlier versions of this script
rm -f "$HOME/.local/share/gnome-shell/search-providers/io.github.tanaybhomia.Whisp-search-provider.ini"

# Refresh GNOME databases
update-desktop-database "$DESKTOP_DIR" 2>/dev/null
gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null

echo ""
echo "Done! You can now search for 'Whisp' in your app launcher."
echo "You will see two icons. The one with the dev icon will run your local codebase directly."
echo "Note: log out and back in for GNOME Shell to pick up the search provider."

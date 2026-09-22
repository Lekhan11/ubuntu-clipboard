#!/usr/bin/env bash
#
# Set up ubuntu-clipboard for the current user:
#   1. checks system dependencies
#   2. writes ~/.config/autostart/ubuntu-clipboard.desktop pointing at this checkout
#   3. registers the Super+Shift+V shortcut via GNOME settings
#   4. (with --start) launches the manager now, detached
#
# Usage: ./install.sh [--start]
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$REPO_DIR/clipboard.py"
DEST="$HOME/.config/autostart/ubuntu-clipboard.desktop"

fail() { printf 'error: %s\n' "$*" >&2; exit 1; }

# --- dependencies ----------------------------------------------------------
missing=""
if ! python3 -c 'import gi; gi.require_version("Gtk", "3.0"); from gi.repository import Gtk, Gdk, GdkPixbuf' 2>/dev/null; then
    missing="$missing python3-gi gir1.2-gtk-3.0 gir1.2-gdkpixbuf-2.0"
fi
if ! command -v ydotool >/dev/null 2>&1; then
    missing="$missing ydotool"
fi
if [ -n "$missing" ]; then
    echo "Missing dependencies:$missing"
    echo
    echo "Install them with:"
    echo "  sudo apt install$missing"
    echo
    echo "ydotool is optional: without it the item is still copied back to the"
    echo "clipboard, you just press Ctrl+V yourself."
    exit 1
fi
# --- autostart ---------------------------------------------------------------
mkdir -p "$(dirname "$DEST")"
cat > "$DEST" <<EOF
[Desktop Entry]
Type=Application
Name=Ubuntu Clipboard History
Comment=Windows-style clipboard history
Exec=/usr/bin/python3 $SCRIPT
Icon=edit-paste
Terminal=false
Categories=Utility;
X-GNOME-Autostart-enabled=true
EOF
echo "Wrote $DEST"

# --- shortcut ----------------------------------------------------------------
python3 "$SCRIPT" --install-shortcut

# --- start (optional) ----------------------------------------------------------
if [ "${1:-}" = "--start" ]; then
    python3 "$SCRIPT" --restart
    echo "Started the manager — press Super+Shift+V to open the history."
else
    echo "Done. It will start automatically at next login, or start it now with:"
    echo "  $0 --start"
fi

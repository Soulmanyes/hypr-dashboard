#!/usr/bin/env bash
# Adds Hyprland Dashboard to your app launcher (and removes it with --uninstall).
# The app runs from this folder, so keep it where it is or run this again after moving it.
set -euo pipefail

app_id="dev.hyprdashboard.HyprDashboard"
dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
apps="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
desktop="$apps/$app_id.desktop"

if [ "${1:-}" = "--uninstall" ]; then
	rm -f "$desktop"
	echo "Removed $desktop"
	echo "Your Hyprland config is unchanged. See README.md for removing the lines the app added."
	exit 0
fi

missing=()
command -v python3 >/dev/null || missing+=("python3")
python3 - <<'PY' 2>/dev/null || missing+=("PyGObject with GTK 4.10+ and libadwaita 1.6+")
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk
assert (Gtk.get_major_version(), Gtk.get_minor_version()) >= (4, 10)
assert (Adw.get_major_version(), Adw.get_minor_version()) >= (1, 6)
PY
command -v hyprctl >/dev/null || missing+=("hyprctl (Hyprland)")
if [ "${#missing[@]}" -gt 0 ]; then
	printf 'Missing: %s\n' "${missing[@]}" >&2
	echo "On Arch: sudo pacman -S python-gobject gtk4 libadwaita" >&2
	exit 1
fi

mkdir -p "$apps"
cat > "$desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Hyprland Dashboard
Comment=Edit Hyprland options, monitors, keybinds, rules, animations and startup
Exec=python3 "$dir/hypr-dashboard.py"
Icon=preferences-desktop
Categories=Settings;
StartupWMClass=$app_id
DESKTOP
chmod +x "$dir/hypr-dashboard.py" "$dir/restore.sh"
echo "Installed: $desktop"
echo "Open \"Hyprland Dashboard\" from your launcher. Keep $dir/restore.sh in mind (see README.md)."

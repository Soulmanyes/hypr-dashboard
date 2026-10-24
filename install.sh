#!/usr/bin/env bash
# Adds Hyprland Dashboard to your app launcher (and removes it with --uninstall).
# The app runs from this folder, so keep it where it is or run this again after moving it.
# Without cloning the repo first (clones to ~/hypr-dashboard, or updates the installed copy):
#   curl -fsSL https://raw.githubusercontent.com/Soulmanyes/hypr-dashboard/main/install.sh | bash
# The whole script is one { } block, so a download cut off midway runs nothing.
{
set -euo pipefail

app_id="dev.hyprdashboard.HyprDashboard"
dir="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
apps="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
desktop="$apps/$app_id.desktop"

# run through curl | bash: the repo isn't next to the script, so clone it (or update the
# installed copy, whose backups/ folder must stay with it) and run its installer
# (--uninstall only removes the launcher, so it needs no download)
if [ ! -f "$dir/hypr-dashboard.py" ] && [ "${1:-}" != "--uninstall" ]; then
	command -v git >/dev/null || { echo "Install git first." >&2; exit 1; }
	dir=""
	if [ -f "$desktop" ]; then
		dir="$(sed -n 's|^Exec=python3 "\(.*\)/hypr-dashboard.py"$|\1|p' "$desktop")"
	fi
	dir="${dir:-$HOME/hypr-dashboard}"
	if [ -d "$dir/.git" ]; then
		echo "Updating $dir"
		git -C "$dir" pull --ff-only
	elif [ -e "$dir" ]; then
		echo "$dir exists but isn't a clone of hypr-dashboard. Move it away and run this again." >&2
		exit 1
	else
		git clone https://github.com/Soulmanyes/hypr-dashboard "$dir"
	fi
	exec bash "$dir/install.sh" "$@" < /dev/tty
fi

if [ "${1:-}" = "--uninstall" ]; then
	rm -f "$desktop"
	echo "Removed $desktop"
	echo "Your Hyprland config is unchanged. See README.md for removing the lines the app added."
	exit 0
fi

missing=()
command -v python3 >/dev/null || missing+=("python3")
python3 - <<'PY' 2>/dev/null || missing+=("PyGObject with GTK 4.12+ and libadwaita 1.6+")
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk
assert (Gtk.get_major_version(), Gtk.get_minor_version()) >= (4, 12)
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
}

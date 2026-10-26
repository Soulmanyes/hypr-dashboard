#!/bin/sh
# Installs Hyprland Dashboard for the current user (and removes it with --uninstall):
# copies the app to ~/.local/share/hypr-dashboard and adds it to your app launcher.
# Config backups live in ~/.local/state/hypr-dashboard/backups, so reinstalling or
# updating never touches them. Run this again after changing the code in a clone.
# Without cloning the repo first (installs the latest release, or updates the installed copy):
#   curl -fsSL https://raw.githubusercontent.com/Soulmanyes/hypr-dashboard/main/install.sh | sh
# The whole script is one { } block, so a download cut off midway runs nothing.
{
set -eu

app_id="dev.hyprdashboard.HyprDashboard"
src="$(cd "$(dirname "$0")" && pwd)"
data="${XDG_DATA_HOME:-$HOME/.local/share}"
dest="$data/hypr-dashboard"
state="${XDG_STATE_HOME:-$HOME/.local/state}/hypr-dashboard"
apps="$data/applications"
desktop="$apps/$app_id.desktop"

# run through curl | sh: the app isn't next to the script, so download the latest
# release (main if there is none yet) and run its installer
if [ ! -f "$src/hypr-dashboard.py" ] && [ "${1:-}" != "--uninstall" ]; then
	command -v curl >/dev/null || { echo "Install curl first." >&2; exit 1; }
	tmp="$(mktemp -d)"
	trap 'rm -rf "$tmp"' EXIT
	echo "Downloading Hyprland Dashboard"
	# releases/latest redirects to .../tag/<tag>
	tag="$(curl -fsSLo /dev/null -w '%{url_effective}' \
		https://github.com/Soulmanyes/hypr-dashboard/releases/latest | sed -n 's|.*/tag/||p')"
	ref="${tag:+tags/$tag}"
	curl -fsSL "https://github.com/Soulmanyes/hypr-dashboard/archive/refs/${ref:-heads/main}.tar.gz" |
		tar -xz -C "$tmp" --strip-components=1
	sh "$tmp/install.sh" "$@" < /dev/null
	exit
fi

if [ "${1:-}" = "--uninstall" ]; then
	rm -rf "$dest" "$desktop"
	echo "Removed $dest and $desktop"
	echo "Your Hyprland config is unchanged, and its backups stay in $state/backups."
	echo "See README.md for removing the lines the app added."
	exit 0
fi

missing=""
command -v python3 >/dev/null || missing="$missing  python3
"
python3 - <<'PY' 2>/dev/null || missing="$missing  PyGObject with GTK 4.12+ and libadwaita 1.6+
"
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk
assert (Gtk.get_major_version(), Gtk.get_minor_version()) >= (4, 12)
assert (Adw.get_major_version(), Adw.get_minor_version()) >= (1, 6)
PY
command -v hyprctl >/dev/null || missing="$missing  hyprctl (Hyprland)
"
if [ -n "$missing" ]; then
	printf 'Missing:\n%s' "$missing" >&2
	echo "On Arch: sudo pacman -S python-gobject gtk4 libadwaita" >&2
	exit 1
fi

# Before 1.1.0 the app ran from a git clone (by default ~/hypr-dashboard) and kept its
# backups in a backups/ folder there. Copy them over; the clone itself stays as it is.
old=""
if [ -f "$desktop" ]; then
	old="$(sed -n 's|^Exec=python3 "\(.*\)/hypr-dashboard.py"$|\1|p' "$desktop")"
fi
if [ -n "$old" ] && [ "$old" != "$dest" ] && [ -d "$old/backups" ]; then
	mkdir -p "$state/backups"
	copied=0
	for b in "$old"/backups/hypr-* "$old/backups/.app-version"; do
		[ -e "$b" ] || continue
		[ -e "$state/backups/$(basename "$b")" ] && continue
		cp -a "$b" "$state/backups/"
		copied=$((copied + 1))
	done
	echo "Copied $copied config backups from $old/backups to $state/backups"
	if [ "$old" != "$src" ]; then
		echo "The app no longer runs from $old. Once you've checked the backups, you can delete that folder."
	fi
fi

if [ "$src" != "$dest" ]; then
	echo "Installing the app to $dest"
	mkdir -p "$dest"
	# old .py files would change the code fingerprint backup.py takes
	rm -rf "$dest"/*.py "$dest/__pycache__"
	cp "$src"/*.py "$src/install.sh" "$src/restore.sh" "$src/LICENSE" "$dest/"
fi
chmod +x "$dest/hypr-dashboard.py" "$dest/restore.sh" "$dest/install.sh"

mkdir -p "$apps"
cat > "$desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Hyprland Dashboard
Comment=Edit Hyprland options, monitors, keybinds, rules, animations and startup
Exec=python3 "$dest/hypr-dashboard.py"
Icon=preferences-desktop
Categories=Settings;
StartupWMClass=$app_id
DESKTOP
echo "Installed: $desktop"
echo "Open \"Hyprland Dashboard\" from your launcher. Keep $dest/restore.sh in mind (see README.md)."
}

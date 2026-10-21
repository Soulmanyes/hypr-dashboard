#!/usr/bin/env bash
# Put back a Hyprland config backup made by hypr-dashboard. Works from a TTY:
# needs only bash and coreutils, not the app or a running Hyprland.
#
#   restore.sh            pick a backup from a numbered list (newest first)
#   restore.sh 1          restore backup number 1 from that list
#   restore.sh --list     only show the list
#   restore.sh --last-apply   undo the files the last Apply changed
#
# Before anything is overwritten, the current config is copied to
# backups/hypr-<time>-before-restore, so a restore can itself be undone.
set -euo pipefail

app_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
store="$app_dir/backups"
hypr="${XDG_CONFIG_HOME:-$HOME/.config}/hypr"

die() { echo "restore.sh: $*" >&2; exit 1; }

save_current() {
	local dest="$store/hypr-$(date +%Y%m%d-%H%M%S)-before-restore" n=1
	while [ -e "$dest" ]; do  # two restores in the same second
		n=$((n + 1)); dest="${dest%-before-restore*}-before-restore-$n"
	done
	mkdir -p "$dest"
	# Everything except the config's own backups folder.
	(cd "$hypr" && find . -mindepth 1 -maxdepth 1 ! -name backups -exec cp -a {} "$dest/" \;)
	echo "Saved the current config to $dest"
}

reload_if_running() {
	if [ -n "${HYPRLAND_INSTANCE_SIGNATURE:-}" ] && command -v hyprctl >/dev/null; then
		hyprctl reload >/dev/null && echo "Hyprland reloaded."
	else
		echo "Hyprland isn't running here. Start it again (log in, or run Hyprland from the TTY)."
	fi
}

if [ "${1:-}" = "--last-apply" ]; then
	last="$(ls -d "$hypr"/backups/hypr-dashboard-apply-* 2>/dev/null | sort | tail -n 1)"
	[ -n "$last" ] || die "no Apply backups in $hypr/backups"
	echo "Undo the last Apply: copy these files back from $last"
	(cd "$last" && find . -type f | sed 's|^\./|  |')
	read -r -p "Go ahead? [y/N] " ok
	[ "$ok" = y ] || [ "$ok" = Y ] || { echo "Nothing changed."; exit 0; }
	save_current
	cp -a "$last/." "$hypr/"
	echo "Done."
	reload_if_running
	exit 0
fi

mapfile -t backups < <(ls -d "$store"/hypr-* 2>/dev/null | sort -r)
[ "${#backups[@]}" -gt 0 ] || die "no backups in $store"

echo "Hyprland config backups (newest first):"
for i in "${!backups[@]}"; do
	printf '  %2d  %s\n' "$((i + 1))" "$(basename "${backups[$i]}")"
done
[ "${1:-}" = "--list" ] && exit 0

choice="${1:-}"
if [ -z "$choice" ]; then
	read -r -p "Restore which one? [number, Enter to cancel] " choice
	[ -n "$choice" ] || { echo "Nothing changed."; exit 0; }
fi
[[ "$choice" =~ ^[0-9]+$ ]] && [ "$choice" -ge 1 ] && [ "$choice" -le "${#backups[@]}" ] \
	|| die "pick a number from 1 to ${#backups[@]}"
pick="${backups[$((choice - 1))]}"

echo
echo "This replaces everything in $hypr (except its backups folder)"
echo "with $(basename "$pick")."
read -r -p "Go ahead? [y/N] " ok
[ "$ok" = y ] || [ "$ok" = Y ] || { echo "Nothing changed."; exit 0; }

save_current
(cd "$hypr" && find . -mindepth 1 -maxdepth 1 ! -name backups -exec rm -rf {} +)
cp -a "$pick/." "$hypr/"
echo "Restored $(basename "$pick")."
reload_if_running

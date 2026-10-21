"""Talking to Hyprland and changing config files safely.

apply_files() is the only place the app writes Hyprland config: it backs up
every file it touches, writes them, reloads once and puts all of them back if
Hyprland reports an error or an option did not take the expected value.
"""
import json
import os
import shutil
import socket
import subprocess
import time

import luasrc

HYPR_DIR = luasrc.HYPR_DIR
BACKUPS = os.path.join(HYPR_DIR, "backups")
# Only these backup folders are pruned; older hand-made ones are kept.
BACKUP_PREFIX = "hypr-dashboard-apply-"
KEEP_BACKUPS = 20


def _socket_path():
	return os.path.join(os.environ["XDG_RUNTIME_DIR"], "hypr", os.environ["HYPRLAND_INSTANCE_SIGNATURE"], ".socket.sock")


def request(command):
	"""Sends one command over the Hyprland socket and returns the reply text."""
	with socket.socket(socket.AF_UNIX) as s:
		s.settimeout(5)
		s.connect(_socket_path())
		s.sendall(command.encode())
		reply = b""
		while chunk := s.recv(65536):
			reply += chunk
	return reply.decode()


def hyprctl_json(*args):
	out = subprocess.run(["hyprctl", *args, "-j"], capture_output=True, text=True, timeout=5).stdout
	return json.loads(out)


def get_option(name):
	"""Returns (type, value, set), or None if Hyprland can't report this option."""
	try:
		reply = json.loads(request(f"j/getoption {name}"))
	except ValueError:
		return None
	kind = next(k for k in reply if k not in ("option", "set"))
	return kind, reply[kind], reply["set"]


def config_errors():
	return [e for e in hyprctl_json("configerrors") if e.strip()]


def reload():
	subprocess.run(["hyprctl", "reload"], capture_output=True, timeout=10)


def _atomic_write(path, text):
	# Write through a symlink (stow and similar) instead of replacing the link with a file.
	path = os.path.realpath(path)
	os.makedirs(os.path.dirname(path), exist_ok=True)
	tmp = path + ".tmp"
	with open(tmp, "w") as f:
		f.write(text)
	os.replace(tmp, path)


def _read(path):
	try:
		with open(path) as f:
			return f.read()
	except FileNotFoundError:
		return None


def _prune_backups():
	old = sorted(d for d in os.listdir(BACKUPS) if d.startswith(BACKUP_PREFIX))
	for name in old[:-KEEP_BACKUPS]:
		shutil.rmtree(os.path.join(BACKUPS, name), ignore_errors=True)


# What the files held before the last successful apply_files, for undo().
last_before = {}


def apply_files(texts, verify=None):
	"""Writes {path: new text, or None to delete the file} and reloads Hyprland.

	verify() runs after a clean reload and returns a list of problems. If
	there are config errors or problems, every file is restored and Hyprland
	reloaded again. Returns the list of errors (empty on success).
	"""
	global last_before
	last_before = {}  # an apply that writes nothing leaves nothing to undo
	before = {path: _read(path) for path in texts}
	changed = {path: text for path, text in texts.items() if text != before[path]}
	if not changed:
		return []

	stamp = BACKUP_PREFIX + time.strftime("%Y%m%d-%H%M%S")
	backup = os.path.join(BACKUPS, stamp)
	n = 1
	while os.path.exists(backup):  # two applies in the same second
		n += 1
		backup = os.path.join(BACKUPS, f"{stamp}-{n}")
	os.makedirs(backup)
	for path in changed:
		if before[path] is not None:
			target = os.path.join(backup, os.path.relpath(path, HYPR_DIR))
			os.makedirs(os.path.dirname(target), exist_ok=True)
			shutil.copy2(path, target)

	try:
		for path, text in changed.items():
			_write_or_delete(path, text)
		reload()
		errors = config_errors()
		if not errors and verify:
			errors = verify()
	except Exception as e:  # noqa: BLE001 - always try to put the files back
		errors = [f"{type(e).__name__}: {e}"]

	if errors:
		for path in changed:
			_write_or_delete(path, before[path])
		reload()
	else:
		last_before = {path: before[path] for path in changed}
	_prune_backups()
	return errors


def undo():
	"""Puts back what the last successful apply_files changed. Returns config errors."""
	errors = apply_files(last_before) if last_before else []
	return errors


def _write_or_delete(path, text):
	if text is None:
		if os.path.exists(path):
			os.remove(path)
	else:
		_atomic_write(path, text)

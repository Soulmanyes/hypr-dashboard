"""A full copy of ~/.config/hypr in the app's own folder, made on the first
launch and on the first launch after the app's code changed.

The per-Apply backups in ~/.config/hypr/backups only hold the files one Apply
touched; this one is the whole config as it was before this version of the
app ever ran.
"""
import hashlib
import os
import shutil
import time

import hypr

APP_DIR = os.path.dirname(os.path.abspath(__file__))
BACKUP_DIR = os.path.join(APP_DIR, "backups")
MARKER = os.path.join(BACKUP_DIR, ".app-version")


def app_version():
	"""A fingerprint of the app's code: changes whenever any .py file does."""
	digest = hashlib.sha256()
	for name in sorted(os.listdir(APP_DIR)):
		if name.endswith(".py"):
			digest.update(name.encode())
			with open(os.path.join(APP_DIR, name), "rb") as f:
				digest.update(f.read())
	return digest.hexdigest()[:16]


def ensure():
	"""Makes the backup if this version hasn't made one yet. Returns its path, or None."""
	version = app_version()
	try:
		with open(MARKER) as f:
			if f.read().strip() == version:
				return None
	except FileNotFoundError:
		pass
	first = not os.path.exists(MARKER)
	stamp = time.strftime("%Y%m%d-%H%M%S")
	target = os.path.join(BACKUP_DIR, f"hypr-{stamp}-{'first-launch' if first else 'update'}")
	# The config's own backups folder is skipped: it's old copies, not config. Symlinks are
	# followed, so a stow-style setup is backed up as real files.
	shutil.copytree(hypr.HYPR_DIR, target, symlinks=False,
		ignore=shutil.ignore_patterns("backups", "*.tmp", "__pycache__"))
	with open(MARKER + ".tmp", "w") as f:
		f.write(version + "\n")
	os.replace(MARKER + ".tmp", MARKER)
	return target

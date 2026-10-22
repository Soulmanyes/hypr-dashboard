"""Installed apps by kind, from their .desktop files, to offer as values.

Only for a value that is itself an installed app of exactly one well-defined
kind (Categories=TerminalEmulator, WebBrowser, ...): then every other app of
that kind is offered. Variable names are never guessed from, so nothing is
offered for a value that isn't plainly an app.
"""
import configparser
import os
import shutil

# freedesktop "additional categories" that name what an app is for, not its toolkit or area
KINDS = {
	"TerminalEmulator": "terminals",
	"WebBrowser": "web browsers",
	"FileManager": "file managers",
	"TextEditor": "text editors",
	"Calculator": "calculators",
	"Email": "mail apps",
}
# Exec lines starting with these run something else: the real app isn't the first word
WRAPPERS = {"env", "sh", "bash", "flatpak", "sudo", "pkexec", "gtk-launch", "dbus-launch"}

_cache = None


def _dirs():
	home = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
	data = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
	return [os.path.join(d, "applications") for d in [home, *data.split(":")] if d]


def _entry(path):
	parser = configparser.RawConfigParser(interpolation=None, strict=False)
	parser.optionxform = str
	try:
		parser.read(path, encoding="utf-8")
		e = parser["Desktop Entry"]
	except (configparser.Error, KeyError, UnicodeDecodeError):
		return None
	if e.get("Type", "Application") != "Application" or e.get("NoDisplay") == "true" or e.get("Hidden") == "true":
		return None
	if e.get("Terminal") == "true":
		return None  # needs a terminal around it (vim, micro): not something to launch on its own
	words = e.get("Exec", "").split()
	if not words or os.path.basename(words[0]) in WRAPPERS:
		return None
	binary = shutil.which(words[0])
	if binary is None:
		return None
	name = os.path.basename(words[0])
	# Offer the short name when one is on PATH (/usr/bin/firefox starts /usr/lib/firefox/firefox), else the full path.
	command = name if shutil.which(name) else words[0]
	kinds = {c for c in e.get("Categories", "").split(";") if c in KINDS}
	return command, os.path.realpath(binary), kinds


def installed():
	"""[(command, real path, kinds)] for every launchable app; the first file of an id wins."""
	global _cache
	if _cache is None:
		seen, found = set(), []
		for d in _dirs():
			try:
				names = sorted(os.listdir(d))
			except OSError:
				continue
			for n in names:
				if n.endswith(".desktop") and n not in seen:
					seen.add(n)
					entry = _entry(os.path.join(d, n))
					if entry:
						found.append(entry)
		_cache = found
	return _cache


def alternatives(value):
	"""(kind label, [commands]) when value is an installed app of exactly one kind, else None."""
	if not isinstance(value, str) or not value or " " in value.strip():
		return None
	which = shutil.which(value)
	real = os.path.realpath(which) if which else None
	mine = [e for e in installed() if e[1] == real or os.path.basename(e[0]) == os.path.basename(value)]
	kinds = set().union(*(e[2] for e in mine)) if mine else set()
	if len(kinds) != 1:
		return None
	kind = kinds.pop()
	commands = sorted({e[0] for e in installed() if kind in e[2]})
	return KINDS[kind], commands

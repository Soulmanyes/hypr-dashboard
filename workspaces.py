"""Workspace strings the way Hyprland 0.56.2 reads them.

The app picks workspaces by number only; a label (default_name, rename) is cosmetic.
Other forms written by hand are shown and kept as they are.

"7" is workspace ID 7, "name:web" is the workspace named web and "special:x" a
special workspace (getWorkspaceIDNameFromString and matchesStaticSelector). A bare
word like "web" is neither: rules read it as a selector (r[1-5], m[DP-1], ...), so
the app never writes one for a name. Window rules may end in " silent".
"""
import re

SILENT = " silent"


def split(text):
	"""(kind, value, suffix): "999 silent" -> ("id", "999", " silent"), "name:web" -> ("name", "web", "")."""
	text = str(text).strip()
	suffix = SILENT if text.endswith(SILENT) and len(text) > len(SILENT) else ""
	head = text[:len(text) - len(suffix)].strip()
	if re.fullmatch(r"\d+", head) and int(head) >= 1:
		return "id", head, suffix
	if head.startswith("name:") and len(head) > 5:
		return "name", head[5:], suffix
	if head == "special" or head.startswith("special:"):
		return "special", head[8:], suffix
	return "other", text, ""


def valid(kind, value):
	value = value.strip()
	if kind == "id":
		return re.fullmatch(r"\d+", value) is not None and int(value) >= 1
	if kind == "special":
		return re.fullmatch(r"\S*", value) is not None
	return bool(value)


def describe(text, names=None):
	"""Plain words for a workspace string: "Workspace 999 · label “gaming”".

	names maps a number ("999") to the label a bar shows for it ("gaming").
	"""
	kind, value, suffix = split(text)
	if kind == "id":
		shown = (names or {}).get(value)
		out = f"Workspace {value}" + (f" · label “{shown}”" if shown and shown != value else "")
	elif kind == "name":
		out = f"Workspace labelled “{value}” (picked by label, written by hand)"
	elif kind == "special":
		out = f"Special workspace “{value}”" if value else "Special workspace"
	else:
		return f"Hand-written selector {value}" if value else "?"
	return out + (" · silent" if suffix else "")


def live_names():
	"""{"999": "gaming"} for open workspaces and workspace rules whose name differs from their ID."""
	import hypr
	names = {}
	try:
		for rule in hypr.hyprctl_json("workspacerules"):
			if rule.get("defaultName") and split(rule["workspaceString"])[0] == "id":
				names[rule["workspaceString"]] = rule["defaultName"]
		for ws in hypr.hyprctl_json("workspaces"):
			if ws["id"] > 0 and ws["name"] != str(ws["id"]):
				names[str(ws["id"])] = ws["name"]
	except Exception:  # noqa: BLE001 - labels only
		pass
	return names


def known():
	"""Workspace numbers to offer in menus: open workspaces and those with rules."""
	import hypr
	out = set()
	try:
		out |= {str(ws["id"]) for ws in hypr.hyprctl_json("workspaces") if ws["id"] > 0}
		out |= {r["workspaceString"] for r in hypr.hyprctl_json("workspacerules") if split(r["workspaceString"])[0] == "id"}
	except Exception:  # noqa: BLE001 - the menu is a convenience
		pass
	return sorted(out, key=int)

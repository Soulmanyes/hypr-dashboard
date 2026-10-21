"""Rule data for hypr-dashboard: load/save the JSON state and write dashboard.lua.

The JSON file is the app's source of truth. dashboard.lua is generated from it
on Apply (options.apply writes it together with any option changes), and
windowrules.lua reads that with require("config.dashboard").
"""
import json
import os
import re

import luasrc

HYPR_CONFIG = os.path.join(luasrc.HYPR_DIR, "config")
STATE_FILE = os.path.join(HYPR_CONFIG, "dashboard.json")
LUA_FILE = os.path.join(HYPR_CONFIG, "dashboard.lua")

MATCH_FIELDS = ["class", "title", "initial_class", "initial_title"]
# Yes/no window rule actions offered as switches, with their labels.
FLAGS = [
	("float", "Float"),
	("center", "Center"),
	("pin", "Pin (show on every workspace)"),
	("opaque", "Opaque"),
	("no_blur", "No blur"),
]

# A new user starts empty; the gaming list only means something with a gaming rule (see README).
DEFAULT_STATE = {
	"gaming_apps": [],
	"rules": [],
	"options": {},
}


# Appended to hyprland.lua by "Connect" on the Extra rules page when no loaded file
# reads dashboard.lua yet, so rules made in the app take effect.
RULES_HOOK = (
	"\n-- Window rules made in hypr-dashboard (Extra rules page).\n"
	'package.loaded["config.dashboard"] = nil\n'
	'for _, rule in ipairs(require("config.dashboard").rules) do hl.window_rule(rule) end\n'
)


def hooks():
	"""(rules_hooked, gaming_hooked): whether the loaded config reads dashboard.lua's
	rules, and whether it uses the gaming list (a hand-written gaming rule, see README)."""
	rules_hooked = gaming_hooked = False
	for path in luasrc.load_order():
		if path == LUA_FILE:
			continue
		with open(path) as f:
			code = luasrc._strip_comments(f.read())
		if re.search(r"""require\s*\(?\s*["']config\.dashboard["']""", code):
			rules_hooked = rules_hooked or ".rules" in code
			gaming_hooked = gaming_hooked or "gaming_apps" in code
	return rules_hooked, gaming_hooked


def new_rule():
	return {"name": "", "enabled": True, "match": {}, "workspace": "", "monitor": "", "flags": []}


def load_state():
	try:
		with open(STATE_FILE) as f:
			state = json.load(f)
	except FileNotFoundError:
		return json.loads(json.dumps(DEFAULT_STATE))
	state.setdefault("gaming_apps", [])
	state.setdefault("rules", [])
	state.setdefault("options", {})
	return state


def lua_string(value):
	return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def rule_to_lua(rule):
	match = ", ".join(f"{key} = {lua_string(value)}" for key, value in rule["match"].items() if value)
	parts = [f"match = {{ {match} }}"]
	if rule.get("workspace"):
		parts.append(f"workspace = {lua_string(rule['workspace'])}")
	if rule.get("monitor"):
		parts.append(f"monitor = {lua_string(rule['monitor'])}")
	parts += [f"{flag} = true" for flag in rule.get("flags", [])]
	# A newline in the name would end the comment and break the file.
	comment = "  -- " + " ".join(rule["name"].split()) if rule.get("name") else ""
	return f"\t\t{{ {', '.join(parts)} }},{comment}"


def to_lua(state):
	apps = "".join(f"\t\t{lua_string(app)},\n" for app in state["gaming_apps"])
	rules = "".join(rule_to_lua(r) + "\n" for r in state["rules"] if r.get("enabled", True) and any(r["match"].values()))
	return (
		"-- Written by hypr-dashboard (~/claude/hypr-dashboard). Edit it with the app,\n"
		"-- not by hand: the app rewrites this whole file when you press Apply.\n"
		"return {\n"
		f"\tgaming_apps = {{\n{apps}\t}},\n"
		f"\trules = {{\n{rules}\t}},\n"
		"}\n"
	)


def state_json(state):
	return json.dumps(state, indent="\t") + "\n"


def regex_error(pattern):
	"""Python's re is close enough to Hyprland's RE2 to catch typos."""
	try:
		re.compile(pattern.removeprefix("negative:"))
	except re.error as e:
		return str(e)
	return None


def field_matches(pattern, value):
	if not pattern:
		return True
	negative = pattern.startswith("negative:")
	try:
		found = re.search(pattern.removeprefix("negative:"), value or "") is not None
	except re.error:
		return False
	return found != negative


CLIENT_KEYS = {"class": "class", "title": "title", "initial_class": "initialClass", "initial_title": "initialTitle"}


def rule_matches(rule, client):
	if not any(rule["match"].values()):
		return False
	return all(field_matches(p, client.get(CLIENT_KEYS[k], "")) for k, p in rule["match"].items())


def gaming_regex(apps):
	return "^(" + "|".join(apps) + ")$"

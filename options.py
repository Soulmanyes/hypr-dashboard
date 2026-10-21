"""Every Hyprland option: where it is set, whether the app may change it, and how.

An option set by a plain literal in a hand-written file is changed in place in
the file that wins (luasrc keeps every other byte). An option set nowhere is
written to dashboard_options.lua, which hyprland.lua requires last; those
overrides live in dashboard.json under "options". Anything computed, generated
by another tool (Noctalia) or of a type without an editor yet is read-only.
"""
import math
import os
import re
from dataclasses import dataclass, field

import hypr
import luasrc
import rules

OVERRIDES_FILE = os.path.join(hypr.HYPR_DIR, "config", "dashboard_options.lua")
REQUIRE_LINE = 'require("config.dashboard_options")'
DASHBOARD = "hypr-dashboard"
luasrc.MANAGED[os.path.basename(OVERRIDES_FILE)] = DASHBOARD

EDITABLE_TYPES = {"bool", "int", "float", "str", "css"}
RESET = object()  # a change value meaning "back to the default"
# The table form to_lua writes for css values; plain enough to replace whole.
_CSS_TABLE = re.compile(r"\{\s*(?:(?:top|right|bottom|left)\s*=\s*-?\d+\s*,?\s*)*\}")


@dataclass
class Option:
	name: str  # hyprctl name, e.g. "general:border_size"
	description: str
	default: object
	min: object
	max: object
	choices: list  # [(label, int value)] for options with a fixed set of values
	type: str  # from getoption: bool, int, float, str, css, gradient, vec2, or "" if unreadable
	value: object
	set: bool
	sites: list = field(default_factory=list)  # luasrc.Site, load order, without our overrides
	override: object = None  # value from dashboard.json, if any
	unseen: bool = False  # Hyprland says it's set, but no file the app reads sets it

	@property
	def path(self):
		return self.name.replace(":", ".")

	@property
	def section(self):
		return self.name.split(":")[0]

	@property
	def winner(self):
		return self.sites[-1] if self.sites else None

	def source(self):
		"""Where the current value comes from, for the badge."""
		if self.override is not None:
			return "set by dashboard"
		site = self.winner
		if site is None:
			return "default"
		if site.managed:
			return site.managed
		return f"{os.path.relpath(site.file, hypr.HYPR_DIR)}:{site.line}"

	def locked(self):
		"""Why the app won't change this option, or "" if it can."""
		if self.type not in EDITABLE_TYPES:
			return f"{self.type or 'unreadable'} values can't be edited yet"
		if ".col." in "." + self.path:
			return "colours can't be edited yet"
		if self.override is not None:
			return ""
		if self.unseen:
			# An override would quietly beat whatever sets it (a file the app can't follow, or hyprctl keyword).
			return "set somewhere the app can't read (another file or hyprctl keyword)"
		for site in self.sites:
			where = f"{os.path.relpath(site.file, hypr.HYPR_DIR)}:{site.line}"
			if site.managed:
				return f"set by {site.managed} ({where})"
			if not site.literal and not (self.type == "css" and _CSS_TABLE.fullmatch(site.text)):
				return f"set via {' '.join(site.text.split())[:40]} in {where}"
		return ""

	def mode(self):
		"""How a change is written: "file" (edit the literal in place) or "override"."""
		return "override" if self.override is not None or not self.sites else "file"


def parse_live(kind, value):
	"""getoption value as the app stores it: css becomes [top, right, bottom, left]."""
	if kind == "css":
		parts = [int(p) for p in str(value).split()]
		return (parts * 4)[:4] if len(parts) == 1 else parts
	if kind == "float":
		return float(value)
	return value


def to_lua(kind, value):
	if kind == "bool":
		return "true" if value else "false"
	if kind == "int":
		return str(int(value))
	if kind == "float":
		text = repr(float(value))
		return text[:-2] if text.endswith(".0") else text
	if kind == "str":
		return luasrc.lua_string(value)
	if kind == "css":
		top, right, bottom, left = (int(v) for v in value)
		if top == right == bottom == left:
			return str(top)
		return f"{{ top = {top}, right = {right}, bottom = {bottom}, left = {left} }}"
	raise ValueError(f"can't write {kind} values")


def normalize(kind, value):
	"""Accepts one number for a css value, meaning the same on all four sides."""
	if kind == "css" and not isinstance(value, (list, tuple)):
		return [int(value)] * 4
	if kind == "css":
		return [int(v) for v in value]
	return value


def check_value(option, value):
	"""Returns an error message for a value Hyprland would accept but shouldn't get."""
	numbers = value if option.type == "css" else [value] if option.type in ("int", "float") else []
	for n in numbers:
		if isinstance(n, float) and not math.isfinite(n):
			return "not a number"
		if option.min is not None and n < option.min:
			return f"below the minimum {option.min}"
		if option.max is not None and n > option.max:
			return f"above the maximum {option.max}"
	if option.choices and value not in [v for _, v in option.choices]:
		return "not one of the allowed values"
	return None


def same_value(kind, a, b):
	if kind == "float":
		return abs(float(a) - float(b)) < 1e-4
	if kind == "css":
		return list(a) == list(b)
	return a == b


def load(state):
	"""Reads every option. Returns (options sorted by name, scanned files)."""
	descriptions = hypr.hyprctl_json("descriptions")
	known = {d["name"].replace(":", ".") for d in descriptions}
	files, by_path = luasrc.scan(known)
	overrides = state.get("options", {})
	result = []
	for d in descriptions:
		live = hypr.get_option(d["name"])
		kind, value, is_set = live if live else ("", None, False)
		if kind:
			value = parse_live(kind, value)
		choices = [(label, v) for item in d.get("map") or [] for label, v in item.items()]
		choices.sort(key=lambda c: c[1])
		sites = [s for s in by_path.get(d["name"].replace(":", "."), []) if s.managed != DASHBOARD]
		override = overrides.get(d["name"])
		result.append(Option(d["name"], d["description"], d.get("default"), d.get("min"), d.get("max"),
			choices, kind, value, is_set, sites, override, unseen=is_set and not sites and override is None))
	result.sort(key=lambda o: o.name)
	return result, files


def overrides_lua(overrides, by_name):
	"""dashboard_options.lua for {name: value}: one hl.config call with nested tables."""
	tree = {}
	for name, value in sorted(overrides.items()):
		*parents, leaf = name.replace(":", ".").split(".")
		node = tree
		for part in parents:
			node = node.setdefault(part, {})
		node[leaf] = to_lua(by_name[name].type, value)

	def emit(node, depth):
		pad = "\t" * depth
		lines = []
		for key, val in node.items():
			if isinstance(val, dict):
				lines += [f"{pad}{key} = {{", *emit(val, depth + 1), f"{pad}}},"]
			else:
				lines.append(f"{pad}{key} = {val},")
		return lines

	body = "\n".join(emit(tree, 1))
	return (
		"-- Written by hypr-dashboard (~/claude/hypr-dashboard). Edit it with the app,\n"
		"-- not by hand: the app rewrites this whole file when you press Apply.\n"
		"-- Only options that no other config file sets end up here.\n"
		f"hl.config({{\n{body}\n}})\n" if body else
		"-- Written by hypr-dashboard (~/claude/hypr-dashboard). No options set here right now.\n"
	)


def _with_require(text):
	"""hyprland.lua with the require for our overrides at the very end, after Noctalia."""
	if REQUIRE_LINE in luasrc._strip_comments(text):
		return text
	return text.rstrip("\n") + "\n\n-- Options set in hypr-dashboard. Keep this last so they apply.\n" + REQUIRE_LINE + "\n"


def plan(options, files, changes, state, edits):
	"""Adds the edits for {name: value or RESET} to edits; returns the new overrides.

	Raises ValueError for a change the app must not make.
	"""
	by_name = {o.name: o for o in options}
	overrides = dict(state.get("options", {}))
	for name, value in changes.items():
		option = by_name[name]
		reason = option.locked()
		if reason:
			raise ValueError(f"{name}: {reason}")
		if value is not RESET:
			value = normalize(option.type, value)
			problem = check_value(option, value)
			if problem:
				raise ValueError(f"{name}: {problem}")
		if option.mode() == "override":
			if value is RESET:
				overrides.pop(name, None)
			else:
				overrides[name] = value
		elif value is RESET:
			# Every site has to go, or an earlier one would show through.
			for site in option.sites:
				edits.remove(site.file, files[site.file].text, site.field_start, site.field_end)
		else:
			site = option.winner
			edits.replace(site.file, files[site.file].text, site.start, site.end, to_lua(option.type, value))
	return overrides


def verify_options(changes):
	problems = []
	for name, value in changes.items():
		live = hypr.get_option(name)
		if live is None:
			problems.append(f"{name}: can't read it back")
			continue
		kind, got, is_set = live
		if value is RESET:
			if is_set:
				problems.append(f"{name}: still set after reset")
		elif not same_value(kind, parse_live(kind, got), normalize(kind, value)):
			problems.append(f"{name}: Hyprland has {got}, expected {value}")
	return problems


class Prepared:
	"""An Apply worked out but not written: the new file texts, what the files
	held when they were worked out, and the checks to run after the reload."""

	def __init__(self, texts, verify, overrides):
		self.texts = texts
		self.verify = verify
		self.overrides = overrides
		self.before = {path: hypr._read(path) for path in texts}

	def changed(self):
		"""{path: (old text or None, new text or None)} for files that would change."""
		return {p: (self.before[p], t) for p, t in self.texts.items() if t != self.before[p]}


def prepare(state, options=None, files=None, changes=None, items=None):
	"""Works out every file an Apply would write (rules, option changes and item
	changes: objects with plan(edits) and verify(), such as items.Pending),
	without writing anything. Raises ValueError for a change the app refuses."""
	changes = changes or {}
	edits = luasrc.Edits()
	overrides = plan(options, files, changes, state, edits) if changes else state.get("options", {})
	items = [i for i in items or [] if i]
	for pending in items:
		pending.plan(edits)
	texts = edits.render()
	texts[rules.LUA_FILE] = rules.to_lua(state)

	if overrides or os.path.exists(OVERRIDES_FILE):
		texts[OVERRIDES_FILE] = overrides_lua(overrides, {o.name: o for o in options or []})
		if overrides:
			if luasrc.ENTRY not in texts:
				with open(luasrc.ENTRY) as f:
					texts[luasrc.ENTRY] = f.read()
			texts[luasrc.ENTRY] = _with_require(texts[luasrc.ENTRY])
	new_state = dict(state, options=overrides)
	texts[rules.STATE_FILE] = rules.state_json(new_state)

	def verify():
		problems = verify_options(changes)
		for pending in items:
			problems += pending.verify()
		return problems

	return Prepared(texts, verify, overrides)


def commit(state, prepared):
	"""Writes a Prepared Apply in one transaction. Returns config errors; on
	success state["options"] is updated (dashboard.json is written with the rest).
	Raises ValueError if a file changed after it was prepared (and reviewed)."""
	for path, old in prepared.before.items():
		if hypr._read(path) != old:
			raise ValueError(f"{os.path.relpath(path, hypr.HYPR_DIR)} changed after the review; press Refresh and try again")
	errors = hypr.apply_files(prepared.texts, prepared.verify)
	if not errors:
		state["options"] = prepared.overrides
	return errors


def apply(state, options=None, files=None, changes=None, items=None):
	"""prepare() and commit() in one go, without a review."""
	return commit(state, prepare(state, options, files, changes, items))

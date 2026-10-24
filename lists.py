"""Phase 3 lists: keybinds, window rules, workspace rules, animations and
curves, gestures and devices.

They are all top-level hl.NAME({...}) calls, so one generic editor covers
them: change, add or remove a literal field (path into nested tables such as
a window rule's match), replace a string argument (a bind's keys or command),
add a call after the last one of its kind, or delete one. Calls inside loops
or functions, and values that aren't literals or plain string concatenations,
are read-only.
"""
import os
import re
from dataclasses import dataclass

import hypr
import items
import luasrc
import rules
import workspaces

STUBS = "/usr/share/hypr/stubs/hl.meta.lua"
RESET = items.RESET
DEFAULT_FILES = {
	"bind": "binds.lua", "window_rule": "windowrules.lua", "workspace_rule": "workspaces.lua",
	"animation": "animations.lua", "curve": "animations.lua", "gesture": "inputs.lua", "device": "inputs.lua",
}
# hyprctl binds modmask bits
MODS = {"SHIFT": 1, "CAPS": 2, "CTRL": 4, "CONTROL": 4, "ALT": 8, "MOD2": 16, "MOD3": 32,
	"SUPER": 64, "WIN": 64, "LOGO": 64, "MOD4": 64, "MOD5": 128}
EXEC = re.compile(r"hl\.dsp\.exec_cmd\(\s*(.*?)\s*\)\s*", re.DOTALL)


def spec_types(class_name, path=STUBS):
	"""{field: lua type} for one ---@class in the Hyprland stubs, in file order."""
	fields = {}
	try:
		with open(path) as f:
			lines = f.read().splitlines()
	except OSError:
		return fields
	inside = False
	for line in lines:
		if line.startswith("---@class "):
			inside = line.split()[1] == class_name
		elif inside and line.startswith("---@field "):
			m = re.match(r"---@field (\w+)(\??) (.+)", line)
			if m:
				fields[m.group(1)] = m.group(3).strip()
		elif inside and not line.startswith("---"):
			inside = False
	return fields


def parse_keys(text):
	"""'SUPER + SHIFT + Right' -> (modmask, 'right'); None if a part is unknown."""
	parts = [p.strip() for p in text.split("+") if p.strip()]
	if not parts:
		return None
	mask = 0
	for p in parts[:-1]:
		if p.upper() not in MODS:
			return None
		mask |= MODS[p.upper()]
	return mask, parts[-1].lower()


@dataclass
class Value:
	"""What a field or argument holds, as far as the app can tell."""
	value: object = None
	kind: str = ""  # "literal", "concat" (names and strings joined by ..), or "" (read-only)
	parts: list = None


class Lists:
	def __init__(self, model):
		self.model = model
		calls = [c for c in model.calls if not c.disabled]
		by_fn = lambda fn: [c for c in calls if c.fn == fn]  # noqa: E731
		self.binds = by_fn("bind")
		# The app's own `for _, rule in ipairs(dashboard.rules)` loop belongs to the Extra rules page.
		self.window_rules = [c for c in by_fn("window_rule") if not (c.nested and c.args and c.args[0].text == "rule")]
		self.workspace_rules = by_fn("workspace_rule")
		self.curves = by_fn("curve")
		self.animations = by_fn("animation")
		self.gestures = by_fn("gesture")
		self.devices = by_fn("device")
		self.types = {
			"workspace_rule": spec_types("HL.WorkspaceRuleSpec"),
			"gesture": spec_types("HL.GestureSpec"),
			"device": spec_types("HL.DeviceSpec"),
			"bind_opts": spec_types("HL.BindOptions"),
		}
		try:
			self.live_binds = hypr.hyprctl_json("binds")
			self.live_animations = hypr.hyprctl_json("animations")
			self.live_devices = hypr.hyprctl_json("devices")
		except Exception:  # noqa: BLE001 - still show the config
			self.live_binds, self.live_animations, self.live_devices = [], [[], []], {}

	def src(self, call):
		return self.model.files[call.file]

	def resolve(self, call, text):
		"""The value of an expression in call's file: a literal, or names and strings joined by ..."""
		tokens = [t for t in luasrc.tokenize(text) if t.kind not in ("ws", "comment")]
		if luasrc._is_literal(tokens):
			return Value(luasrc._literal_value(tokens), "literal")
		parts = luasrc.concat_parts(text)
		if not parts:
			return Value()
		out = []
		for kind, part in parts:
			if kind == "str":
				out.append(part)
				continue
			g = self.src(call).locals.get(part) or next((v.glob for v in self.model.variables if v.name == part), None)
			if g is None or not g.literal or not isinstance(luasrc.literal_value(g), str):
				return Value()
			out.append(luasrc.literal_value(g))
		return Value("".join(out), "concat", parts)

	def table(self, call, path):
		"""The table at path (argument index, then keys) as an Arg, or None."""
		if not call.args or path[0] >= len(call.args):
			return None
		arg = call.args[path[0]]
		for key in path[1:]:
			if not arg or not arg.fields:
				return None
			site = next((f for f in arg.fields if f.name == key), None)
			arg = luasrc.sub_table(self.src(call), site) if site else None
		return arg if arg and arg.fields is not None else None

	def fields(self, call, path=(0,)):
		"""[(key, Site, Value)] for the table at path."""
		arg = self.table(call, path)
		return [(f.name, f, self.resolve(call, f.text) if f.literal or not f.text.startswith("{") else Value())
			for f in (arg.fields if arg else [])]

	def field(self, call, path):
		for key, site, value in self.fields(call, path[:-1]):
			if key == path[-1]:
				return site, value
		return None, Value()

	def gaming_name(self, call, name):
		"""Whether name is a local in call's file built from dashboard.gaming_apps (gamingApps)."""
		return re.search(r"^\s*local\s+" + re.escape(name) + r"\s*=[^\n]*\.gaming_apps\b",
			self.src(call).text, re.MULTILINE) is not None

	def window_rule_matches(self, call, client, gaming_apps=()):
		"""(answer, why) for whether a hand-written window rule matches a hyprctl client right
		now: answer is True, False or None (can't tell, why says which field). A field set to
		the gaming list's variable is checked against gaming_apps."""
		if call.nested:
			return None, "built in a loop or function"
		match = self.fields(call, (0, "match"))
		if not match:
			return None, "no plain match table"
		unsure = []
		for key, site, value in match:
			if not value.kind and self.gaming_name(call, site.text.strip()):
				value = Value(rules.gaming_regex(gaming_apps), "literal") if gaming_apps else value
			answer = rules.client_matches(key, value.value, client) if value.kind else None
			if answer is False:
				return False, ""
			if answer is None:
				unsure.append(key)
		return (None, "can't check " + ", ".join(unsure)) if unsure else (True, "")

	def exec_command(self, call):
		"""(Arg for the command expression, Value) if the bind's action is hl.dsp.exec_cmd(...)."""
		if len(call.args) < 2:
			return None, Value()
		action = call.args[1]
		m = EXEC.fullmatch(action.text)
		if not m or "(" in m.group(1).replace("hl.dsp.exec_cmd(", ""):
			return None, Value()
		start = action.start + m.start(1)
		inner = luasrc.Arg(start, start + len(m.group(1)), m.group(1), False)
		return inner, self.resolve(call, inner.text)

	def curve_points(self, call):
		"""[(start, end, value)] for the four numbers in a bezier curve's points, or None."""
		site, _ = self.field(call, (1, "points"))
		if site is None or not site.text.startswith("{"):
			return None
		tokens = [t for t in luasrc.tokenize(site.text) if t.kind not in ("ws", "comment")]
		out = []
		i = 0
		while i < len(tokens):
			tok = tokens[i]
			if tok.kind == "num" or (tok.text == "-" and i + 1 < len(tokens) and tokens[i + 1].kind == "num"):
				end_tok = tokens[i + 1] if tok.text == "-" else tok
				text = site.text[tok.start:end_tok.end]
				out.append((site.start + tok.start, site.start + end_tok.end, float(text)))
				i += 2 if tok.text == "-" else 1
			elif tok.text in ("{", "}", ","):
				i += 1
			else:
				return None
		return out if len(out) == 4 else None

	def bind_count(self, keys):
		parsed = parse_keys(keys)
		if not parsed:
			return 0
		return sum(1 for b in self.live_binds if b["modmask"] == parsed[0] and b["key"].lower() == parsed[1])

	def file_for(self, fn):
		top = [c for c in self.model.calls if c.fn == fn and not c.nested]
		return top[-1].file if top else luasrc.loaded_or_entry(os.path.join(items.CONFIG, DEFAULT_FILES[fn]))


def write_expr(value, old_parts, lookup):
	"""Lua for a new string value, keeping a `name .. "..."` prefix when the value still starts with it."""
	best = None  # the longest run of leading names whose text the new value still starts with
	names, prefix = [], ""
	for kind, part in old_parts or []:
		resolved = lookup(part) if kind == "name" else None
		if resolved is None:
			break
		names, prefix = names + [part], prefix + resolved
		if value.startswith(prefix):
			best = (list(names), prefix)
	if best:
		rest = value[len(best[1]):]
		return " .. ".join(best[0] + ([luasrc.lua_string(rest)] if rest else []))
	return luasrc.lua_string(value)


class Pending:
	def __init__(self, lists):
		self.lists = lists
		self.ops = {}
		self.specs = {}  # fields of calls added this session that stay editable, by add key
		self.next_add = 0

	def __bool__(self):
		return bool(self.ops)

	def drop(self, key):
		self.ops.pop(key, None)

	@staticmethod
	def call_key(call):
		return (call.file, call.start)

	def field(self, call, path, value):
		"""Sets (or with RESET removes) the field at path, e.g. (0, "match", "class")."""
		key = ("field", self.call_key(call), path)
		site, current = self.lists.field(call, path)
		if (value is RESET and site is None) or (site is not None and current.kind == "literal" and value == current.value):
			self.drop(key)
		else:
			self.ops[key] = ("field", call, path, value)

	def text_arg(self, call, arg, value, current):
		"""Replaces a string expression (bind keys, exec command) with value."""
		key = ("text", self.call_key(call), arg.start)
		if value == current.value:
			self.drop(key)
		else:
			self.ops[key] = ("text", call, arg, value, current)

	def span(self, call, start, end, value, current):
		"""Replaces one literal by position (bezier points have no keys)."""
		key = ("span", self.call_key(call), start)
		if value == current:
			self.drop(key)
		else:
			self.ops[key] = ("span", call, start, end, value)

	def pending_span(self, call, start):
		op = self.ops.get(("span", self.call_key(call), start))
		return op[4] if op else None

	def add(self, fn, text, label, key=None):
		"""Queues a new call; give a key to edit it before Apply (add again with the same key)."""
		self.ops[("add", fn, text if key is None else key)] = ("add", fn, text, label)

	def delete(self, call):
		self.ops[("delete", self.call_key(call))] = ("delete", call)

	def deleted(self, call):
		return ("delete", self.call_key(call)) in self.ops

	def pending_field(self, call, path):
		op = self.ops.get(("field", self.call_key(call), path))
		return op[3] if op else None

	def pending_text(self, call, arg):
		op = self.ops.get(("text", self.call_key(call), arg.start))
		return op[3] if op else None

	def describe(self):
		"""(what, change) per pending edit, for the Apply review."""
		lines = []
		deleted = {self.call_key(op[1]) for op in self.ops.values() if op[0] == "delete"}
		for op in self.ops.values():
			kind = op[0]
			if kind == "add":
				_, fn, _text, label = op
				lines.append((f"New {items.CALL_NAMES.get(fn, fn).lower()}", label))
				continue
			call = op[1]
			what = items.call_label(call)
			if call.fn == "workspace_rule":
				_s, ws = self.lists.field(call, (0, "workspace"))
				what = f"Workspace rule for {workspaces.describe(ws.value)}" if ws.kind else what
			if kind == "delete":
				lines.append((what, "remove"))
			elif self.call_key(call) in deleted:
				continue
			elif kind == "field":
				_, _call, path, value = op
				site, current = self.lists.field(call, path)
				old = current.value if site is not None and current.kind == "literal" else None
				name = ".".join(str(p) for p in path if not isinstance(p, int)) or "value"
				lines.append((f"{what} {name}", "remove" if value is RESET else items.change(old, value)))
			elif kind == "text":
				_, _call, _arg, value, current = op
				lines.append((what, items.change(current.value, value)))
			elif kind == "span":
				lines.append((what, items.change(None, op[4])))
		return lines

	# Planning ----------------------------------------------------------------

	def plan(self, edits):
		lists = self.lists
		deleted = {self.call_key(op[1]) for op in self.ops.values() if op[0] == "delete"}
		new_tables = {}  # fields for an argument table that doesn't exist yet, per call
		for op in self.ops.values():
			kind = op[0]
			if kind in ("field", "text", "span") and self.call_key(op[1]) in deleted:
				continue
			if kind == "field" and len(op[2]) == 2 and op[2][0] == len(op[1].args) and op[3] is not RESET:
				new_tables.setdefault(self.call_key(op[1]), (op[1], []))[1].append((op[2][1], op[3]))
				continue
			if kind in ("field", "text", "delete") and op[1].nested:
				raise ValueError(f"{items.rel(op[1].file)}:{op[1].line} is built in a loop or function; edit it by hand")
			if kind == "field":
				self._plan_field(edits, *op[1:])
			elif kind == "text":
				_, call, arg, value, current = op
				if current.kind not in ("literal", "concat"):
					raise ValueError(f"{items.rel(call.file)}:{call.line}: that value is computed; edit it by hand")
				if call.fn == "bind" and arg is call.args[0] and not parse_keys(value):
					raise ValueError(f"{items.rel(call.file)}:{call.line}: {value!r} isn't a key combination like SUPER + K")
				src = lists.src(call)
				lookup = lambda n, src=src: self._lookup(src, n)  # noqa: E731
				edits.replace(call.file, src.text, arg.start, arg.end, write_expr(value, current.parts, lookup))
			elif kind == "span":
				_, call, start, end, value = op
				if call.nested:
					raise ValueError(f"{items.rel(call.file)}:{call.line} is built in a loop or function; edit it by hand")
				edits.replace(call.file, lists.src(call).text, start, end, items.to_lua(value))
			elif kind == "delete":
				call = op[1]
				edits.remove(call.file, lists.src(call).text, call.start, call.end)
			elif kind == "add":
				_, fn, text, _label = op
				self._plan_add(edits, fn, text)

		for call, pairs in new_tables.values():
			src = lists.src(call)
			# A bind's options table (third argument): add it before the closing ")".
			close = src.text.rfind(")", call.start, call.end)
			body = ", ".join(f"{k} = {items.to_lua(v)}" for k, v in pairs)
			edits.insert(call.file, src.text, close, f", {{ {body} }}")

	def _lookup(self, src, name):
		g = src.locals.get(name) or next((v.glob for v in self.lists.model.variables if v.name == name), None)
		if g is None or not g.literal:
			return None
		value = luasrc.literal_value(g)
		return value if isinstance(value, str) else None

	def _plan_field(self, edits, call, path, value):
		lists = self.lists
		src = lists.src(call)
		site, current = lists.field(call, path)
		if site is not None and value is not RESET and current.kind != "literal":
			raise ValueError(f"{items.rel(call.file)}:{site.line} {path[-1]}: computed value; edit it by hand")
		if value is RESET:
			if site is not None:
				# A comma left before "}" is fine in Lua; new fields attach to a kept one.
				edits.remove(call.file, src.text, site.field_start, site.field_end)
			return
		lua = items.to_lua(value)
		if site is not None:
			edits.replace(call.file, src.text, site.start, site.end, lua)
			return
		table = lists.table(call, path[:-1])
		if table is not None:
			self._insert_field(edits, src, table, path[-1], lua, self._removed(call, path[:-1]))
			return
		if len(path) == 3:
			parent = lists.table(call, path[:1])
			if parent is not None:
				self._insert_field(edits, src, parent, path[1], f"{{ {path[2]} = {lua} }}", self._removed(call, path[:1]))
				return
		raise ValueError(f"{items.rel(call.file)}:{call.line}: can't add {'.'.join(map(str, path[1:]))} there")

	def _removed(self, call, table_path):
		"""Keys of the table at table_path that this plan removes."""
		return {op[2][-1] for op in self.ops.values() if op[0] == "field" and op[1] is call
			and op[2][:-1] == table_path and op[3] is RESET}

	@staticmethod
	def _insert_field(edits, src, table, key, lua, removed=()):
		base = src.text
		kept = [f for f in table.fields if f.name not in removed]
		if not kept:
			# Nothing left to attach to: put it right after the opening brace.
			edits.insert(src.path, base, table.start + 1, f" {key} = {lua},")
			return
		last = kept[-1]
		comma = "" if base[last.field_end - 1] == "," else ","
		if "\n" not in base[table.start:table.close]:
			edits.insert(src.path, base, last.field_end, f"{comma} {key} = {lua}")
			return
		line_start = base.rfind("\n", 0, last.field_start) + 1
		indent = re.match(r"[ \t]*", base[line_start:]).group(0)
		edits.insert(src.path, base, last.field_end, f"{comma}\n{indent}{key} = {lua},")

	def _plan_add(self, edits, fn, text):
		lists = self.lists
		path = lists.file_for(fn)
		top = [c for c in lists.model.calls if c.fn == fn and not c.nested and c.file == path]
		base = lists.model.files[path].text if path in lists.model.files else (hypr._read(path) or "")
		if top:
			edits.insert(path, base, top[-1].end, "\n" + text)
		else:
			sep = "" if not base or base.endswith("\n") else "\n"
			edits.insert(path, base, len(base), sep + text + "\n")

	# Verify --------------------------------------------------------------------

	def verify(self):
		problems = []
		binds_changed = any(op[0] in ("add", "delete") and (op[1] == "bind" or getattr(op[1], "fn", "") == "bind")
			for op in self.ops.values())
		if binds_changed:
			live = hypr.hyprctl_json("binds")
			added = sum(1 for op in self.ops.values() if op[0] == "add" and op[1] == "bind")
			removed = sum(1 for op in self.ops.values() if op[0] == "delete" and op[1].fn == "bind")
			expected = len(self.lists.live_binds) + added - removed
			if len(live) != expected:
				problems.append(f"Hyprland has {len(live)} keybinds, expected {expected}")
		anims = [op for op in self.ops.values() if op[0] == "field" and op[1].fn == "animation"]
		if anims:
			live = {a["name"]: a for a in hypr.hyprctl_json("animations")[0]}
			for _, call, path, value in anims:
				leaf_site, leaf = self.lists.field(call, (0, "leaf"))
				a = live.get(leaf.value)
				if a is None or value is RESET:
					continue
				got = {"enabled": a["enabled"], "speed": a["speed"], "style": a["style"], "bezier": a["bezier"],
					"spring": a["bezier"].removeprefix("spring:")}.get(path[-1])
				if got is not None and got != value and not (isinstance(value, (int, float)) and abs(float(got) - value) < 1e-3):
					problems.append(f"animation {leaf.value} {path[-1]}: Hyprland has {got}, expected {value}")
		return problems

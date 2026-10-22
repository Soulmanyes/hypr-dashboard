"""Monitors, variables, environment and autostart: the hand-written statements
behind them, and the edits that change them.

Only top-level statements with literal values are editable: hl.monitor({...})
fields, NAME = "value" globals, hl.env("K", "V") and hl.exec_cmd("...")
directly inside hl.on("hyprland.start", ...). Everything else is shown
read-only. New statements go next to the existing ones of the same kind.
"""
import os
import re
from dataclasses import dataclass, field

import hypr
import luasrc

CONFIG = os.path.join(hypr.HYPR_DIR, "config")
MONITORS_FILE = os.path.join(CONFIG, "monitors.lua")
ENV_FILE = os.path.join(CONFIG, "environment.lua")
AUTOSTART_FILE = os.path.join(CONFIG, "autostart.lua")
RESET = object()

MONITOR_KEYS = ["mode", "position", "scale", "transform", "vrr", "disabled"]
TRANSFORMS = ["normal", "90°", "180°", "270°", "flipped", "flipped 90°", "flipped 180°", "flipped 270°"]
VRR = ["off", "on", "fullscreen only", "fullscreen games only"]
_COLOUR = re.compile(r"rgba?\(([0-9a-fA-F]{6}|[0-9a-fA-F]{8})\)")


def rel(path):
	return os.path.relpath(path, hypr.HYPR_DIR)


def say(value):
	"""A value as the Apply review shows it."""
	if value is RESET:
		return "default"
	if isinstance(value, bool):
		return "on" if value else "off"
	if isinstance(value, (list, tuple)):
		return " ".join(say(v) for v in value)
	if isinstance(value, float):
		return f"{value:g}"
	return "(empty)" if value in (None, "") else str(value)


def literal_or_none(arg_or_site):
	"""The literal value of an argument, field or global, or None if it's computed."""
	try:
		return luasrc.literal_value(arg_or_site)
	except Exception:  # noqa: BLE001 - only used to show the old value
		return None


CALL_NAMES = {"exec_cmd": "Startup command", "env": "Environment variable", "monitor": "Monitor", "bind": "Bind",
	"window_rule": "Window rule", "workspace_rule": "Workspace rule", "curve": "Curve", "animation": "Animation",
	"gesture": "Gesture", "device": "Device"}


def call_label(call):
	"""A call as the Apply review names it: its kind and first literal argument, else where it is."""
	kind = CALL_NAMES.get(call.fn, call.fn)
	first = literal_or_none(call.args[0]) if call.args else None
	if isinstance(first, (str, int, float)) and not isinstance(first, bool) and str(first):
		return f"{kind} {first}"
	return f"{kind} ({rel(call.file)}:{call.line})"


def change(old, new):
	return f"{say(old)} → {say(new)}" if old is not None else say(new)


def to_lua(value):
	if isinstance(value, bool):
		return "true" if value else "false"
	if isinstance(value, (int, float)):
		text = repr(value)
		return text
	return luasrc.lua_string(value)


def parse_mode(text):
	"""'2560x1440@74.97Hz' -> (2560, 1440, 74.97); refresh is None when not given."""
	m = re.fullmatch(r"\s*(\d+)x(\d+)(?:@([\d.]+)(?:Hz)?)?\s*", text or "")
	if not m:
		return None
	return int(m.group(1)), int(m.group(2)), float(m.group(3)) if m.group(3) else None


def same_mode(a, b):
	pa, pb = parse_mode(a), parse_mode(b)
	if not pa or not pb or pa[:2] != pb[:2]:
		return False
	return pa[2] is None or pb[2] is None or abs(pa[2] - pb[2]) < 0.1


@dataclass
class Monitor:
	output: str
	live: dict = None  # from hyprctl monitors all -j, None if not connected
	call: luasrc.Call = None  # its hl.monitor statement, None if not configured
	readonly: str = ""

	@property
	def fields(self):
		return {f.name: f for f in self.call.args[0].fields} if self.call else {}

	def value(self, key):
		f = self.fields.get(key)
		return luasrc.literal_value(f) if f and f.literal else None

	def modes(self):
		modes = [m.removesuffix("Hz") for m in (self.live or {}).get("availableModes", [])]
		current = self.value("mode")
		if current and not any(same_mode(current, m) for m in modes):
			modes.append(current)
		return modes


@dataclass
class Variable:
	glob: luasrc.Global
	uses: list = field(default_factory=list)  # "file:line"

	@property
	def name(self):
		return self.glob.name

	@property
	def value(self):
		return luasrc.literal_value(self.glob) if self.glob.literal else None

	@property
	def ref(self):
		return self.glob.ref

	def is_colour(self):
		return isinstance(self.value, str) and bool(_COLOUR.fullmatch(self.value))


class Model:
	"""Everything phase 2 shows, read from the loaded config files."""

	def __init__(self):
		self.files = {}
		self.calls = []
		self.globals = []
		self.handlers = []
		for path in luasrc.load_order():
			src, calls, globals_, handlers = luasrc.scan_items(path)
			self.files[path] = src
			self.calls += calls
			self.globals += globals_
			self.handlers += handlers
		self.monitors = self._monitors()
		self.variables = self._variables()
		self.env = [c for c in self.calls if c.fn == "env"]
		self.autostart = [c for c in self.calls if c.fn == "exec_cmd" and c.event == "hyprland.start"]

	def text(self, path):
		return self.files[path].text

	def _monitors(self):
		try:
			live = {m["name"]: m for m in hypr.hyprctl_json("monitors", "all")}
		except Exception:  # noqa: BLE001 - still show the config without Hyprland
			live = {}
		result = {}
		for call in (c for c in self.calls if c.fn == "monitor"):
			arg = call.args[0] if call.args else None
			out = next((f for f in (arg.fields or []) if f.name == "output"), None) if arg else None
			if not out or not out.literal:
				continue  # can't tell which monitor; binds/rules tabs will list these later
			name = luasrc.literal_value(out)
			readonly = ""
			if call.nested:
				readonly = f"set inside a block in {rel(call.file)}:{call.line}"
			elif name in result:
				readonly = f"configured twice; {rel(call.file)}:{call.line} wins"
			result[name] = Monitor(name, live.get(name), call, readonly)
		for name, m in live.items():
			if name not in result:
				result[name] = Monitor(name, m)
			else:
				result[name].live = m
		return sorted(result.values(), key=lambda m: (m.live is None, (m.live or {}).get("x", 0)))

	def _variables(self):
		names = {g.name for g in self.globals}
		uses = {n: [] for n in names}
		for path, src in self.files.items():
			for t in src.tokens:
				if t.kind == "name" and t.text in names:
					line = src.text.count("\n", 0, t.start) + 1
					if not any(g.file == path and g.line == line and g.name == t.text for g in self.globals):
						uses[t.text].append(f"{rel(path)}:{line}")
		return [Variable(g, uses[g.name]) for g in self.globals]


class Pending:
	"""Changes made in the UI but not applied yet, keyed so a later edit of the
	same thing replaces an earlier one."""

	def __init__(self, model):
		self.model = model
		self.ops = {}

	def __bool__(self):
		return bool(self.ops)

	def set(self, key, op):
		self.ops[key] = op

	def drop(self, key):
		self.ops.pop(key, None)

	# UI-facing helpers ------------------------------------------------------

	def monitor_field(self, monitor, key, value):
		self.set(("monitor", monitor.output, key), ("monitor_field", monitor, key, value))

	def add_monitor(self, monitor):
		self.set(("monitor", monitor.output, "add"), ("add_monitor", monitor))

	def variable(self, var, value):
		if value == var.value:
			self.drop(("var", var.name))
		else:
			self.set(("var", var.name), ("arg_like", var.glob, value))

	def variable_ref(self, var, target):
		"""Points a variable at another variable (target is its name) or, with
		target ("text", value), gives it a plain value."""
		if target == var.ref:
			self.drop(("var", var.name))
		else:
			self.set(("var", var.name), ("var_ref", var.glob, target))

	def call_arg(self, call, index, value):
		key = ("arg", call.file, call.start, index)
		if value == luasrc.literal_value(call.args[index]):
			self.drop(key)
		else:
			self.set(key, ("arg", call, index, value))

	def delete(self, call):
		self.set(("delete", call.file, call.start), ("delete", call))

	def toggle(self, call, enabled):
		key = ("toggle", call.file, call.start)
		if enabled != call.disabled:
			self.drop(key)  # back to how it is in the file
		else:
			self.set(key, ("toggle", call, enabled))

	def add_env(self, name, value):
		self.set(("add_env", name), ("add_env", name, value))

	def add_exec(self, command):
		self.set(("add_exec", command), ("add_exec", command))

	def describe(self):
		"""(what, change) per pending edit, in the words the pages use, for the Apply review."""
		lines = []
		deleted = {(op[1].file, op[1].start) for op in self.ops.values() if op[0] == "delete"}
		for op in self.ops.values():
			kind = op[0]
			if kind in ("arg", "toggle") and (op[1].file, op[1].start) in deleted:
				continue
			if kind == "monitor_field":
				_, monitor, key, value = op
				f = monitor.fields.get(key)
				old = literal_or_none(f) if f is not None and f.literal else None
				lines.append((f"Monitor {monitor.output}: {key}", change(old, value)))
			elif kind == "add_monitor":
				lines.append((f"Monitor {op[1].output}", "add its current setup to the config"))
			elif kind in ("arg_like", "var_ref"):
				_, glob, value = op
				new = value[1] if kind == "var_ref" and isinstance(value, tuple) else value
				lines.append((f"Variable {glob.name}", change(literal_or_none(glob) if glob.literal else None, new)))
			elif kind == "arg":
				_, call, index, value = op
				lines.append((call_label(call), change(literal_or_none(call.args[index]), value)))
			elif kind == "delete":
				call = op[1]
				lines.append((call_label(call), "remove"))
			elif kind == "toggle":
				_, call, enabled = op
				lines.append((call_label(call), "turn on" if enabled else "turn off"))
			elif kind == "add_env":
				lines.append((f"Environment variable {op[1]}", f"add: {op[2]}"))
			elif kind == "add_exec":
				lines.append(("Startup command", f"add: {op[1]}"))
		return lines

	def touches_monitors(self):
		return any(op[0] in ("monitor_field", "add_monitor") for op in self.ops.values())

	# Planning ----------------------------------------------------------------

	def plan(self, edits):
		m = self.model
		deleted = {(op[1].file, op[1].start) for op in self.ops.values() if op[0] == "delete"}
		for op in self.ops.values():
			kind = op[0]
			if kind == "monitor_field":
				_, monitor, key, value = op
				if monitor.readonly or not monitor.call:
					raise ValueError(f"{monitor.output}: {monitor.readonly or 'not configured'}")
				call = monitor.call
				base = m.text(call.file)
				f = monitor.fields.get(key)
				if f is not None and not f.literal:
					raise ValueError(f"{monitor.output} {key}: computed in {rel(call.file)}:{call.line}")
				if value is RESET:
					if f is not None:
						edits.remove(call.file, base, f.field_start, f.field_end)
				elif f is not None:
					edits.replace(call.file, base, f.start, f.end, to_lua(value))
				else:
					self._insert_field(edits, call, key, value)
			elif kind == "add_monitor":
				monitor = op[1]
				live = monitor.live
				now = f"{live['width']}x{live['height']}@{live['refreshRate']}"
				# Hyprland's own spelling of the mode (2560x1440@200.00), not 199.999 from refreshRate.
				mode = next((m for m in monitor.modes() if same_mode(m, now)), f"{live['width']}x{live['height']}@{live['refreshRate']:.2f}")
				spec = {
					"output": monitor.output,
					"mode": mode,
					"position": f"{live['x']}x{live['y']}",
					"scale": float(live["scale"]),
				}
				self._append(edits, self._file_of("monitor", MONITORS_FILE), "\n" + monitor_lua(spec))
			elif kind == "arg_like":
				_, glob, value = op
				if not glob.literal:
					raise ValueError(f"{glob.name} is computed; edit {rel(glob.file)}:{glob.line} by hand")
				edits.replace(glob.file, m.text(glob.file), glob.start, glob.end, to_lua(value))
			elif kind == "var_ref":
				_, glob, target = op
				if not (glob.literal or glob.ref):
					raise ValueError(f"{glob.name} is computed; edit {rel(glob.file)}:{glob.line} by hand")
				names = {g.name for g in m.globals if g.literal}
				if isinstance(target, tuple):
					lua = to_lua(target[1])
				elif target in names:
					lua = target
				else:
					raise ValueError(f"{glob.name}: there's no variable called {target}")
				edits.replace(glob.file, m.text(glob.file), glob.start, glob.end, lua)
			elif kind == "arg":
				_, call, index, value = op
				if (call.file, call.start) in deleted:
					continue
				arg = call.args[index]
				if not arg.literal or call.nested:
					raise ValueError(f"{rel(call.file)}:{call.line} is computed; edit it by hand")
				edits.replace(call.file, m.text(call.file), arg.start, arg.end, to_lua(value))
			elif kind == "delete":
				call = op[1]
				edits.remove(call.file, m.text(call.file), call.start, call.end)
			elif kind == "toggle":
				_, call, enabled = op
				if (call.file, call.start) in deleted:
					continue
				base = m.text(call.file)
				if enabled:
					edits.replace(call.file, base, call.start, call.end, base[call.start + len(luasrc.DISABLED_MARK):call.end])
				else:
					edits.replace(call.file, base, call.start, call.end, luasrc.DISABLED_MARK + base[call.start:call.end])
			elif kind == "add_env":
				_, name, value = op
				self._add_env(edits, name, value)
			elif kind == "add_exec":
				self._add_exec(edits, op[1])

	def _file_of(self, fn, default):
		calls = [c for c in self.model.calls if c.fn == fn and not c.nested]
		return calls[-1].file if calls else luasrc.loaded_or_entry(default)

	def _base(self, path):
		if path in self.model.files:
			return self.model.text(path)
		return hypr._read(path) or ""

	def _append(self, edits, path, text):
		base = self._base(path)
		sep = "" if not base or base.endswith("\n") else "\n"
		edits.insert(path, base, len(base), sep + text)

	def _insert_field(self, edits, call, key, value):
		arg = call.args[0]
		base = self.model.text(call.file)
		if not arg.fields:
			edits.replace(call.file, base, arg.start, arg.close + 1, f"{{ {key} = {to_lua(value)} }}")
			return
		last = arg.fields[-1]
		if "\n" not in base[arg.start:arg.close]:  # one-line table: stay on that line
			comma = "" if base[last.field_end - 1] == "," else ","
			edits.insert(call.file, base, last.field_end, f"{comma} {key} = {to_lua(value)}")
			return
		line_start = base.rfind("\n", 0, last.field_start) + 1
		indent = re.match(r"[ \t]*", base[line_start:]).group(0)
		comma = "" if base[last.field_end - 1] == "," else ","
		edits.insert(call.file, base, last.field_end, f"{comma}\n{indent}{key} = {to_lua(value)},")

	def _add_env(self, edits, name, value):
		top = [c for c in self.model.env if not c.nested]
		line = f'hl.env({luasrc.lua_string(name)}, {luasrc.lua_string(value)})'
		if top:
			last = top[-1]
			base = self.model.text(last.file)
			edits.insert(last.file, base, last.end, "\n" + line)
		else:
			self._append(edits, luasrc.loaded_or_entry(ENV_FILE), line + "\n")

	def _add_exec(self, edits, command):
		line = f"hl.exec_cmd({luasrc.lua_string(command)})"
		handlers = [h for h in self.model.handlers if h.event == "hyprland.start"]
		if not handlers:
			self._append(edits, luasrc.loaded_or_entry(AUTOSTART_FILE),
				f'\nhl.on("hyprland.start", function ()\n\t{line}\nend)\n')
			return
		h = handlers[0]
		base = self.model.text(h.file)
		line_start = base.rfind("\n", 0, h.end) + 1
		inside = [c for c in self.model.autostart if c.file == h.file and c.start < h.end]
		if inside:
			ref_start = base.rfind("\n", 0, inside[-1].start) + 1
			indent = re.match(r"[ \t]*", base[ref_start:]).group(0)
		else:
			indent = "\t"
		if base[line_start:h.end].strip():  # "end" shares a line with code: put ours on its own line
			edits.insert(h.file, base, h.end, f"\n{indent}{line}\n")
		else:
			edits.insert(h.file, base, line_start, f"{indent}{line}\n")

	# Verify --------------------------------------------------------------------

	def verify(self):
		"""Checks changed monitors against what Hyprland reports after the reload."""
		changed = {op[1].output for op in self.ops.values() if op[0] == "monitor_field"}
		if not changed:
			return []
		live = {m["name"]: m for m in hypr.hyprctl_json("monitors", "all")}
		problems = []
		for op in self.ops.values():
			if op[0] != "monitor_field":
				continue
			_, monitor, key, value = op
			m = live.get(monitor.output)
			if m is None or value is RESET:
				continue
			if key == "mode" and not same_mode(value, f"{m['width']}x{m['height']}@{m['refreshRate']}"):
				problems.append(f"{monitor.output}: mode is {m['width']}x{m['height']}@{m['refreshRate']:.2f}, expected {value}")
			elif key == "position" and value != "auto" and value != f"{m['x']}x{m['y']}":
				problems.append(f"{monitor.output}: position is {m['x']}x{m['y']}, expected {value}")
			elif key == "scale" and abs(float(m["scale"]) - float(value)) > 0.01:
				problems.append(f"{monitor.output}: scale is {m['scale']}, expected {value}")
			elif key == "transform" and int(m["transform"]) != int(value):
				problems.append(f"{monitor.output}: transform is {m['transform']}, expected {value}")
			elif key == "disabled" and bool(m["disabled"]) != bool(value):
				problems.append(f"{monitor.output}: disabled is {m['disabled']}, expected {value}")
		return problems


def monitor_lua(spec):
	width = max(len(k) for k in spec)
	body = "".join(f"    {k.ljust(width)} = {to_lua(v)},\n" for k, v in spec.items())
	return f"hl.monitor({{\n{body}}})\n"

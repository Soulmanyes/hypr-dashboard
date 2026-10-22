"""Monitors, Variables and Startup tabs, built from an items.Model.

Edits are recorded in an items.Pending; the window's Apply writes them.
"""
import os
import subprocess

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

import apps  # noqa: E402
import items  # noqa: E402
import luasrc  # noqa: E402
from lists_page import choice_entry  # noqa: E402

NOT_SET = "not set"


def esc(text):
	return GLib.markup_escape_text(str(text))


def note(text):
	return Gtk.Label(label=text, css_classes=["caption", "dim-label"], valign=Gtk.Align.CENTER)


def flat_button(label, callback, tooltip=None, css=()):
	button = Gtk.Button(label=label, valign=Gtk.Align.CENTER, css_classes=["flat", *css], tooltip_text=tooltip)
	button.connect("clicked", lambda _b: callback())
	return button


class ItemsPages:
	"""Owns the three pages; rebuild() redraws them from the model and pending changes."""

	def __init__(self, on_change):
		self.on_change = on_change
		self.monitors_page = Adw.PreferencesPage()
		self.variables_page = Adw.PreferencesPage()
		self.startup_page = Adw.PreferencesPage()
		self.groups = {}
		self.after_rebuild = lambda: None  # the window sets this to re-apply page searches
		self.model = None
		self.pending = None
		self.options = []

	def load(self, options=()):
		self.model = items.Model()
		self.pending = items.Pending(self.model)
		self.options = list(options)
		self.rebuild()

	def changed(self, rebuild=False):
		if rebuild:
			self.rebuild()
		self.on_change()

	def rebuild(self):
		for page, build in ((self.monitors_page, self.build_monitors), (self.variables_page, self.build_variables),
				(self.startup_page, self.build_startup)):
			for group in self.groups.get(page, []):
				page.remove(group)
			self.groups[page] = build()
			for group in self.groups[page]:
				page.add(group)
		self.after_rebuild()

	# Monitors ------------------------------------------------------------------

	def build_monitors(self):
		groups = [self.layout_group()]
		for monitor in self.model.monitors:
			groups.append(self.monitor_group(monitor))
		return groups

	def pending_monitor(self, monitor, key):
		op = self.pending.ops.get(("monitor", monitor.output, key))
		return op[3] if op else None

	def monitor_position(self, monitor):
		"""(x, y, width, height) as it will be after Apply, for the layout drawing."""
		live = monitor.live
		pos = self.pending_monitor(monitor, "position") or monitor.value("position")
		mode = self.pending_monitor(monitor, "mode") or monitor.value("mode")
		x, y = live["x"], live["y"]
		if isinstance(pos, str) and "x" in pos:
			try:
				x, y = (int(v) for v in pos.split("x"))
			except ValueError:
				pass
		parsed = items.parse_mode(mode) if isinstance(mode, str) else None
		w, h = (parsed[0], parsed[1]) if parsed else (live["width"], live["height"])
		scale = self.pending_monitor(monitor, "scale") or monitor.value("scale") or live["scale"]
		if self.pending_monitor(monitor, "transform") in (1, 3, 5, 7) or (
				self.pending_monitor(monitor, "transform") is None and (monitor.value("transform") or 0) in (1, 3, 5, 7)):
			w, h = h, w
		return x, y, w / float(scale), h / float(scale)

	def layout_group(self):
		group = Adw.PreferencesGroup(title="Layout", description="How the connected monitors will sit after Apply.")
		area = Gtk.DrawingArea(content_height=170, hexpand=True)
		connected = [m for m in self.model.monitors if m.live and not m.live.get("disabled")]

		def draw(_area, cr, width, height):
			rects = [(m, *self.monitor_position(m)) for m in connected]
			if not rects:
				return
			left = min(r[1] for r in rects)
			top = min(r[2] for r in rects)
			right = max(r[1] + r[3] for r in rects)
			bottom = max(r[2] + r[4] for r in rects)
			scale = min((width - 20) / max(right - left, 1), (height - 20) / max(bottom - top, 1))
			ox = (width - (right - left) * scale) / 2
			oy = (height - (bottom - top) * scale) / 2
			fg = area.get_color()
			for m, x, y, w, h in rects:
				rx, ry, rw, rh = ox + (x - left) * scale, oy + (y - top) * scale, w * scale, h * scale
				cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.12)
				cr.rectangle(rx + 2, ry + 2, rw - 4, rh - 4)
				cr.fill_preserve()
				cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.6)
				cr.set_line_width(1.5)
				cr.stroke()
				cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.9)
				cr.select_font_face("sans")
				cr.set_font_size(13)
				ext = cr.text_extents(m.output)
				cr.move_to(rx + (rw - ext.width) / 2, ry + rh / 2 + ext.height / 2)
				cr.show_text(m.output)

		area.set_draw_func(draw)
		self.layout_area = area
		frame = Gtk.Frame(child=area, css_classes=["view"])
		group.add(frame)
		return group

	def monitor_group(self, monitor):
		live = monitor.live
		title = monitor.output + (f" · {live['description']}" if live else "")
		if live:
			desc = f"Now {live['width']}x{live['height']}@{live['refreshRate']:.2f} at {live['x']}x{live['y']}, scale {live['scale']:g}"
			if live.get("disabled"):
				desc = "Connected, disabled"
		else:
			desc = "Not connected"
		if monitor.call:
			desc += f" · {items.rel(monitor.call.file)}:{monitor.call.line}"
		group = Adw.PreferencesGroup(title=esc(title), description=esc(desc))

		if not monitor.call:
			if ("monitor", monitor.output, "add") in self.pending.ops:
				group.add(Adw.ActionRow(title="Added to the config on Apply, with its current settings"))
				group.set_header_suffix(flat_button("Undo", lambda: (self.pending.drop(("monitor", monitor.output, "add")),
					self.changed(True))))
			elif live:
				group.add(Adw.ActionRow(title="Not in the config", subtitle="Hyprland picks its settings itself."))
				group.set_header_suffix(flat_button("Add to config", lambda: (self.pending.add_monitor(monitor),
					self.changed(True)), css=["suggested-action"]))
			return group

		if monitor.readonly:
			group.add(Adw.ActionRow(title=esc(monitor.call.args[0].text if monitor.call.args else ""),
				subtitle=esc(monitor.readonly)))
			return group

		fields = monitor.fields

		def current(key):
			p = self.pending_monitor(monitor, key)
			if p is items.RESET:
				return None
			return p if p is not None else monitor.value(key)

		def locked_row(key, title):
			f = fields.get(key)
			if f is not None and not f.literal:
				row = Adw.ActionRow(title=title, subtitle=esc(f"computed: {f.text}"))
				return row
			return None

		def set_field(key, value):
			if value is items.RESET and key not in fields:
				self.pending.drop(("monitor", monitor.output, key))
			elif value is not items.RESET and key in fields and fields[key].literal and value == monitor.value(key):
				self.pending.drop(("monitor", monitor.output, key))
			else:
				self.pending.monitor_field(monitor, key, value)
			self.layout_area.queue_draw()
			self.changed()

		# Mode
		row = locked_row("mode", "Mode")
		if row is None:
			modes = monitor.modes()
			mode = current("mode")
			labels = [NOT_SET] + modes
			selected = next((i + 1 for i, m in enumerate(modes) if mode and items.same_mode(mode, m)), 0)
			if mode and not selected:
				labels.append(mode)
				modes.append(mode)
				selected = len(labels) - 1
			row = Adw.ComboRow(title="Mode", subtitle="resolution@refresh rate", model=Gtk.StringList.new(labels),
				selected=selected)
			row.connect("notify::selected", lambda r, _p: set_field("mode",
				items.RESET if r.get_selected() == 0 else
				(monitor.value("mode") if items.same_mode(monitor.value("mode"), modes[r.get_selected() - 1])
					else modes[r.get_selected() - 1])))
		group.add(row)

		# Position
		row = locked_row("position", "Position")
		if row is None:
			pos = current("position")
			row = Adw.ActionRow(title="Position", subtitle="x and y in pixels, top-left corner")
			try:
				x, y = (int(v) for v in pos.split("x"))
			except (AttributeError, ValueError):
				x, y = (live["x"], live["y"]) if live else (0, 0)
				if pos:
					row.set_subtitle(esc(f"currently {pos!r}; changing it writes x and y"))
			spins = []
			for v, tip in ((x, "x"), (y, "y")):
				spin = Gtk.SpinButton(adjustment=Gtk.Adjustment(lower=-20000, upper=20000, step_increment=10,
					page_increment=100), valign=Gtk.Align.CENTER, tooltip_text=tip, width_chars=6)
				spin.set_value(v)
				spins.append(spin)
				row.add_suffix(spin)
			for spin in spins:
				spin.connect("value-changed", lambda _s: set_field("position",
					f"{int(spins[0].get_value())}x{int(spins[1].get_value())}"))
		group.add(row)

		# Scale
		row = locked_row("scale", "Scale")
		if row is None:
			scale = current("scale")
			row = Adw.SpinRow(title="Scale", subtitle="1 = no scaling", digits=2,
				adjustment=Gtk.Adjustment(lower=0.25, upper=4, step_increment=0.05, page_increment=0.25))
			row.set_value(float(scale) if isinstance(scale, (int, float)) else float(live["scale"]) if live else 1.0)
			row.connect("notify::value", lambda r, _p: set_field("scale", round(r.get_value(), 3)))
		group.add(row)

		# Transform, VRR
		for key, title, labels in (("transform", "Rotation", items.TRANSFORMS), ("vrr", "Adaptive sync (VRR)", items.VRR)):
			row = locked_row(key, title)
			if row is None:
				value = current(key)
				value = int(value) if isinstance(value, (bool, int)) else None
				row = Adw.ComboRow(title=title, model=Gtk.StringList.new([NOT_SET] + labels),
					selected=0 if value is None or value >= len(labels) else value + 1)
				if key == "vrr":
					row.set_subtitle("not set = the misc vrr option")
				row.connect("notify::selected", lambda r, _p, key=key: set_field(key,
					items.RESET if r.get_selected() == 0 else r.get_selected() - 1))
			group.add(row)

		row = locked_row("disabled", "Disabled")
		if row is None:
			row = Adw.SwitchRow(title="Disabled", subtitle="turn this monitor off", active=bool(current("disabled")))
			row.connect("notify::active", lambda r, _p: set_field("disabled", True if r.get_active() else items.RESET))
		group.add(row)
		return group

	# Variables -----------------------------------------------------------------

	def overridden_uses(self, var):
		"""Uses of a variable that feed options another file overrides (Noctalia's colours)."""
		lost = []
		for option in self.options:
			winner = option.winner
			for site in option.sites[:-1]:
				where = f"{items.rel(site.file)}:{site.line}"
				if where in var.uses and winner and winner.managed:
					lost.append(where)
		return lost

	def build_variables(self):
		by_file = {}
		for var in self.model.variables:
			by_file.setdefault(var.glob.file, []).append(var)
		connected = {m.output for m in self.model.monitors if m.live}
		groups = []
		for path, variables in by_file.items():
			group = Adw.PreferencesGroup(title=esc(items.rel(path)),
				description="Lua globals the rest of the config uses. Changing one here changes every place that uses it.")
			for var in variables:
				group.add(self.variable_row(var, connected))
			groups.append(group)
		if not groups:
			groups.append(Adw.PreferencesGroup(title="No variables found"))
		return groups

	def variable_row(self, var, connected):
		uses = var.uses
		subtitle = ("used in " + ", ".join(uses[:4]) + (f" and {len(uses) - 4} more" if len(uses) > 4 else "")) if uses \
			else "not used anywhere"
		lost = self.overridden_uses(var)
		if lost and len(lost) == len(uses):
			subtitle += " · all overridden by the Noctalia theme, so changing it shows nowhere"
		elif lost:
			subtitle += f" · {len(lost)} of these are overridden by the Noctalia theme"
		value = var.value
		op = self.pending.ops.get(("var", var.name))
		shown = op[2] if op else value
		if "MONITOR" in var.name and isinstance(shown, str) and connected and shown not in connected:
			subtitle += f" · no monitor called {shown} is connected"
		row = Adw.ActionRow(title=esc(var.name), subtitle=esc(subtitle))
		if var.ref:
			return self.ref_row(row, var, connected)
		if not var.glob.literal:
			row.add_suffix(Gtk.Label(label=var.glob.text, css_classes=["dim-label"], valign=Gtk.Align.CENTER))
			row.add_prefix(Gtk.Image(icon_name="changes-prevent-symbolic", css_classes=["dim-label"]))
			row.set_tooltip_text(f"computed; edit {items.rel(var.glob.file)}:{var.glob.line} by hand")
			return row

		if isinstance(value, bool):
			switch = Gtk.Switch(active=shown, valign=Gtk.Align.CENTER)
			switch.connect("notify::active", lambda s, _p: (self.pending.variable(var, s.get_active()), self.changed()))
			row.add_suffix(switch)
		elif isinstance(value, (int, float)):
			spin = Gtk.SpinButton(adjustment=Gtk.Adjustment(lower=-1e6, upper=1e6, step_increment=1, page_increment=10),
				digits=0 if isinstance(value, int) else 3, valign=Gtk.Align.CENTER)
			spin.set_value(shown)
			spin.connect("value-changed", lambda s: (self.pending.variable(var,
				int(s.get_value()) if isinstance(value, int) else s.get_value()), self.changed()))
			row.add_suffix(spin)
		elif not var.is_colour() and (offer := apps.alternatives(value)):
			# The value is an installed app of one kind (a terminal, a browser): offer the others.
			kind, commands = offer
			box = choice_entry(shown, commands, lambda text: (self.pending.variable(var, text), self.changed()))
			box.get_last_child().set_tooltip_text(f"Installed {kind}")
			row.add_suffix(box)
		else:
			entry = Gtk.Entry(text=shown, valign=Gtk.Align.CENTER, width_chars=22)
			if var.is_colour():
				colour = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=True), valign=Gtk.Align.CENTER)
				colour.set_rgba(rgba_from_hypr(shown))
				syncing = []

				def picked(button, _p):
					if syncing:
						return
					syncing.append(1)
					entry.set_text(hypr_from_rgba(button.get_rgba()))
					syncing.clear()
				colour.connect("notify::rgba", picked)

				def typed(e):
					parsed = rgba_from_hypr(e.get_text(), None)
					if parsed and not syncing:
						syncing.append(1)
						colour.set_rgba(parsed)
						syncing.clear()
				entry.connect("changed", typed)
				row.add_suffix(colour)
			entry.connect("changed", lambda e: (self.pending.variable(var, e.get_text()), self.changed()))
			row.add_suffix(entry)
		return row

	def ref_row(self, row, var, connected):
		"""A variable that names another variable: pick which one, or a plain value."""
		current_target = next((v for v in self.model.variables if v.name == var.ref), None)
		monitorish = "MONITOR" in var.name

		def fits(v):
			"""Only variables of the same kind as the current one: monitors, colours, or same file."""
			if v.name == var.name or not v.glob.literal or not isinstance(v.value, str):
				return False
			if monitorish:
				return "MONITOR" in v.name
			if current_target is not None and current_target.is_colour():
				return v.is_colour()
			return current_target is not None and v.glob.file == current_target.glob.file
		targets = [v for v in self.model.variables if fits(v)]
		op = self.pending.ops.get(("var", var.name))
		current = op[2] if op else var.ref
		if monitorish and connected:
			# One choice per connected screen: through the variable that holds it, else by its name.
			# A variable naming a screen that isn't connected only shows while it's the one chosen.
			choices = []
			for m in self.model.monitors:
				if not m.live:
					continue
				holder = next((v for v in targets if v.value == m.output), None)
				desc = m.live.get("description", "")
				if holder:
					choices.append((holder.name, f"{m.output} · {desc}  (via {holder.name})"))
				else:
					choices.append((("text", m.output), f"{m.output} · {desc}"))
			for v in targets:
				if v.name == current and v.value not in connected:
					choices.insert(0, (v.name, f"{v.name} = {v.value}  (not connected)"))
		else:
			choices = [(v.name, f"{v.name}  ({v.value})") for v in targets]
		if var.ref not in [c for c, _ in choices] and var.ref == current:
			choices.insert(0, (var.ref, f"{var.ref}  (not found)"))
		keys = [c for c, _ in choices]
		drop = Gtk.DropDown(model=Gtk.StringList.new([label for _, label in choices]), valign=Gtk.Align.CENTER,
			selected=keys.index(current) if current in keys else 0)
		drop.connect("notify::selected", lambda d, _p: (self.pending.variable_ref(var, keys[d.get_selected()]),
			self.changed()))
		target = next((v for v in self.model.variables if v.name == var.ref), None)
		if target is not None:
			row.set_subtitle(esc(f"= {var.ref} = {target.value} · " + row.get_subtitle()))
		row.add_suffix(drop)
		return row

	# Startup -------------------------------------------------------------------

	def build_startup(self):
		return [self.env_group(), self.autostart_group()]

	def deleted(self, call):
		return ("delete", call.file, call.start) in self.pending.ops

	def deleted_row(self, title, call):
		row = Adw.ActionRow(title=esc(title), subtitle="removed on Apply", css_classes=["dim-label"])
		row.add_suffix(flat_button("Undo", lambda: (self.pending.drop(("delete", call.file, call.start)),
			self.changed(True))))
		return row

	def env_group(self):
		group = Adw.PreferencesGroup(title="Environment variables",
			description="Set for programs Hyprland starts after Apply. Removing one only takes effect after you log in again.")
		for call in self.model.env:
			literal = len(call.args) == 2 and all(a.literal for a in call.args) and not call.nested
			name = luasrc.literal_value(call.args[0]) if call.args and call.args[0].literal else call.args[0].text if call.args else "?"
			where = f"{items.rel(call.file)}:{call.line}"
			if self.deleted(call):
				group.add(self.deleted_row(name, call))
				continue
			if not literal:
				row = Adw.ActionRow(title=esc(name), subtitle=esc(f"computed in {where}: {' '.join(call.args[-1].text.split())}"),
					tooltip_text="edit this by hand")
				row.add_prefix(Gtk.Image(icon_name="changes-prevent-symbolic", css_classes=["dim-label"]))
				group.add(row)
				continue
			op = self.pending.ops.get(("arg", call.file, call.start, 1))
			row = Adw.ActionRow(title=esc(name), subtitle=esc(where))
			entry = Gtk.Entry(text=op[3] if op else luasrc.literal_value(call.args[1]), valign=Gtk.Align.CENTER, width_chars=24)
			entry.connect("changed", lambda e, call=call: (self.pending.call_arg(call, 1, e.get_text()), self.changed()))
			row.add_suffix(entry)
			row.add_suffix(flat_button("Remove", lambda call=call: (self.pending.delete(call), self.changed(True))))
			group.add(row)
		for key, op in self.pending.ops.items():
			if op[0] == "add_env":
				row = Adw.ActionRow(title=esc(op[1]), subtitle=esc(f"= {op[2]} · added on Apply"))
				row.add_suffix(flat_button("Undo", lambda key=key: (self.pending.drop(key), self.changed(True))))
				group.add(row)
		group.add(self.add_env_row())
		return group

	def add_env_row(self):
		row = Adw.ActionRow(title="Add a variable")
		name = Gtk.Entry(placeholder_text="NAME", valign=Gtk.Align.CENTER, width_chars=14)
		value = Gtk.Entry(placeholder_text="value", valign=Gtk.Align.CENTER, width_chars=18)

		def add():
			key = name.get_text().strip()
			if not key or not key.replace("_", "a").isalnum() or key[0].isdigit():
				name.add_css_class("error")
				return
			self.pending.add_env(key, value.get_text())
			self.changed(True)
		value.connect("activate", lambda _e: add())
		row.add_suffix(name)
		row.add_suffix(value)
		row.add_suffix(flat_button("Add", add))
		return row

	def autostart_group(self):
		group = Adw.PreferencesGroup(title="Autostart",
			description="Runs once when Hyprland starts (when you log in). Apply does not run these; use Run now to try one.")
		for call in self.model.autostart:
			command = luasrc.literal_value(call.args[0]) if call.args and call.args[0].literal else None
			where = f"{items.rel(call.file)}:{call.line}"
			if self.deleted(call):
				group.add(self.deleted_row(command or call.args[0].text, call))
				continue
			if command is None:
				row = Adw.ActionRow(title=esc(call.args[0].text if call.args else "?"), subtitle=esc(f"computed in {where}"))
				group.add(row)
				continue
			op = self.pending.ops.get(("arg", call.file, call.start, 0))
			toggle = self.pending.ops.get(("toggle", call.file, call.start))
			enabled = toggle[2] if toggle else not call.disabled
			entry = row = Adw.EntryRow(title=esc(where + ("" if enabled else " · off")), text=op[3] if op else command)
			entry.connect("changed", lambda e, call=call: (self.pending.call_arg(call, 0, e.get_text()), self.changed()))
			switch = Gtk.Switch(active=enabled, valign=Gtk.Align.CENTER, tooltip_text="Run at startup")
			switch.connect("notify::active", lambda s, _p, call=call: (self.pending.toggle(call, s.get_active()), self.changed()))
			row.add_suffix(switch)
			row.add_suffix(flat_button("Run now", lambda e=entry: self.run_now(e.get_text(), e.get_root()),
				tooltip="Start it once now, through Hyprland"))
			row.add_suffix(flat_button("Remove", lambda call=call: (self.pending.delete(call), self.changed(True))))
			group.add(row)
		for key, op in self.pending.ops.items():
			if op[0] == "add_exec":
				row = Adw.ActionRow(title=esc(op[1]), subtitle="added on Apply")
				row.add_suffix(flat_button("Undo", lambda key=key: (self.pending.drop(key), self.changed(True))))
				group.add(row)
		add = Adw.EntryRow(title="Add a command", show_apply_button=True)
		add.connect("apply", lambda e: e.get_text().strip() and (self.pending.add_exec(e.get_text().strip()),
			self.changed(True)))
		group.add(add)
		return group

	def run_now(self, command, parent=None):
		"""Starts the command once through Hyprland; asks first if it already seems to be running."""
		def run():
			subprocess.run(["hyprctl", "dispatch", f"hl.dsp.exec_cmd({luasrc.lua_string(command)})"],
				capture_output=True, timeout=5)
		words = command.split()
		program = os.path.basename(words[0]) if words else ""
		running = program and subprocess.run(["pgrep", "-x", program[:15]], capture_output=True).returncode == 0
		if not running or parent is None:
			run()
			return
		dialog = Adw.AlertDialog(heading=f"{program} is already running",
			body="Running it again may start a second copy.")
		dialog.add_response("cancel", "Cancel")
		dialog.add_response("run", "Run anyway")
		dialog.set_response_appearance("run", Adw.ResponseAppearance.DESTRUCTIVE)
		dialog.connect("response", lambda _d, r: run() if r == "run" else None)
		dialog.present(parent)


def rgba_from_hypr(text, default=...):
	"""'rgba(82dcccff)' or 'rgb(82dccc)' as a Gdk.RGBA."""
	m = items._COLOUR.fullmatch(text or "")
	if not m:
		return Gdk.RGBA() if default is ... else default
	hexa = m.group(1) + ("ff" if len(m.group(1)) == 6 else "")
	r, g, b, a = (int(hexa[i:i + 2], 16) / 255 for i in range(0, 8, 2))
	colour = Gdk.RGBA()
	colour.red, colour.green, colour.blue, colour.alpha = r, g, b, a
	return colour


def hypr_from_rgba(colour):
	parts = (colour.red, colour.green, colour.blue, colour.alpha)
	return "rgba(" + "".join(f"{round(v * 255):02x}" for v in parts) + ")"

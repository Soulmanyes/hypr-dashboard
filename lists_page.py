"""Workspaces, Keybinds, Window rules, Animations and Gestures & devices pages.

Built from a lists.Lists; edits go into a lists.Pending that the window's
Apply writes together with everything else.
"""
import re
import subprocess

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

import hypr  # noqa: E402
import items  # noqa: E402
import lists  # noqa: E402
import luasrc  # noqa: E402
import workspaces  # noqa: E402

RESET = lists.RESET
MATCH_KEYS = ["class", "title", "initial_class", "initial_title", "tag", "xwayland", "float", "fullscreen",
	"pin", "focus", "group", "modal", "workspace", "content", "xdg_tag"]
DIRECTIONS = ["horizontal", "vertical", "left", "right", "up", "down", "swipe", "pinch", "pinchin", "pinchout"]
GESTURE_ACTIONS = ["workspace", "move", "resize", "special", "close", "fullscreen", "float", "dispatcher", "cursorZoom"]
GESTURE_FINGERS = (2, 5)
# Editors shared by "Add a gesture" and the rows of existing gestures: a (min, max) or a menu of values.
GESTURE_EDITORS = {"fingers": GESTURE_FINGERS, "direction": DIRECTIONS, "action": GESTURE_ACTIONS}
SLIDE = ["slide", "slide top", "slide bottom", "slide left", "slide right"]
# Styles Hyprland accepts per animation family; a number after popin/slidefade is a percentage.
ANIMATION_STYLES = {
	"windows": SLIDE + ["popin", "popin 80%", "gnomed"],
	"layers": SLIDE + ["popin", "popin 80%", "fade"],
	"workspaces": ["slide", "slidevert", "fade", "slidefade", "slidefade 20%", "slidefadevert", "slidefadevert 20%"],
	"borderangle": ["once", "loop"],
}
WS_FIELD = {"workspace": "workspace"}
# Shown on every workspace rule, set or not, in this order.
WS_COMMON = ("default_name", "monitor", "default", "persistent")
WS_RULE_TITLES = {"workspace": "Workspace number", "default_name": "Label in your bar (default_name)",
	"monitor": "Monitor (monitor)", "default": "Opens first on its monitor (default)",
	"persistent": "Kept even when empty (persistent)"}
WS_RULE_NOTES = {"default_name": "Only cosmetic: shown instead of the number. Set when the workspace is created; "
	"relabel an open one under Open workspaces",
	"monitor": "A port name (DP-1) or desc:… (survives port changes)",
	"default": "The workspace its monitor shows first", "persistent": "Stays even with no windows on it"}
SECTION = re.compile(r"^-{3,}\s*([^-].*?)\s*-{3,}\s*$")
NOTE = re.compile(r"^--\s+(\S.*)$")


def esc(text):
	return GLib.markup_escape_text(str(text))


def button(label, callback, tooltip=None, css=("flat",)):
	b = Gtk.Button(label=label, valign=Gtk.Align.CENTER, css_classes=list(css), tooltip_text=tooltip)
	b.connect("clicked", lambda _b: callback())
	return b


def lock(row, reason):
	row.add_prefix(Gtk.Image(icon_name="changes-prevent-symbolic", css_classes=["dim-label"]))
	row.set_tooltip_text(reason)
	return row


def parse_value(text, type_hint=""):
	"""Text typed into an "add field" box as a Lua value, guided by the stub type."""
	text = text.strip()
	hint = type_hint.split("|")
	if text in ("true", "false") and ("boolean" in hint or not type_hint or text == "true" or text == "false"):
		return text == "true"
	if "string" not in hint or not type_hint:
		try:
			return int(text)
		except ValueError:
			try:
				return float(text)
			except ValueError:
				pass
	return text


def animation_styles(leaf):
	"""The style choices for an animation leaf, or [] when it takes no style."""
	if leaf.startswith("specialWorkspace"):
		return ANIMATION_STYLES["workspaces"]
	for family, styles in ANIMATION_STYLES.items():
		if leaf.startswith(family):
			return styles
	return []


def choice_entry(text, choices, on_change, placeholder=""):
	"""An entry with a menu of known values; typing anything else still works."""
	entry = Gtk.Entry(text=text, placeholder_text=placeholder, valign=Gtk.Align.CENTER, width_chars=16)
	entry.connect("changed", lambda e: on_change(e.get_text()))
	listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["menu"])
	popover = Gtk.Popover(child=Gtk.ScrolledWindow(child=listbox, propagate_natural_height=True,
		max_content_height=320, hscrollbar_policy=Gtk.PolicyType.NEVER))
	for choice in choices:
		listbox.append(Gtk.Label(label=choice, xalign=0, margin_start=6, margin_end=6, margin_top=4, margin_bottom=4))

	def pick(_box, row):
		entry.set_text(choices[row.get_index()])
		popover.popdown()
	listbox.connect("row-activated", pick)
	menu = Gtk.MenuButton(icon_name="pan-down-symbolic", popover=popover, valign=Gtk.Align.CENTER,
		tooltip_text="Pick a value")
	box = Gtk.Box(css_classes=["linked"], valign=Gtk.Align.CENTER)
	box.append(entry)
	box.append(menu)
	box.entry = entry
	return box


HAND_WRITTEN = "Written by hand in your config, and kept as it is. Rules made in the app pick a workspace by number."


def workspace_editor(text, on_change, choices=(), names=None, allow_none=False, show_label=True):
	"""A workspace number box, with the label the bar shows for that number beside it.

	on_change gets the workspace string ("7", "" for none when allow_none) while it is valid;
	box.current() gives that string, or None when the box holds something invalid. A
	hand-written target that isn't a number ("name:web", "r[1-5]") is shown, not edited.
	"""
	kind, value, suffix = workspaces.split(text) if text else ("id", "", "")
	box = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
	if text and kind != "id":
		box.append(Gtk.Label(label=workspaces.describe(text), css_classes=["dim-label"], tooltip_text=HAND_WRITTEN,
			ellipsize=3, max_width_chars=40))
		box.entry = None
		box.current = lambda: text
		return box
	shown = Gtk.Label(css_classes=["dim-label"], ellipsize=3, max_width_chars=24)
	entry = Gtk.Entry(text=value, placeholder_text="none" if allow_none else "number", width_chars=8,
		input_purpose=Gtk.InputPurpose.DIGITS, valign=Gtk.Align.CENTER)
	linked = Gtk.Box(css_classes=["linked"], valign=Gtk.Align.CENTER)
	linked.append(entry)
	box.append(shown)
	box.append(linked)
	box.entry = entry

	def current():
		number = entry.get_text().strip()
		if not number:
			return "" if allow_none else None
		return number + suffix if workspaces.valid("id", number) else None
	box.current = current

	def show():
		number = entry.get_text().strip()
		bad = current() is None and bool(number)
		if bad:
			entry.add_css_class("error")
		else:
			entry.remove_css_class("error")
		entry.set_tooltip_text("A workspace number is a whole number, 1 or more" if bad else None)
		label = (names or {}).get(number)
		shown.set_text(f"label “{label}”" if show_label and label and not bad else "")

	def update(*_):
		show()
		if current() is not None:
			on_change(current())
	show()
	entry.connect("changed", update)
	if choices:
		listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["menu"])
		for choice in choices:
			listbox.append(Gtk.Label(label=workspaces.describe(choice, names), xalign=0, margin_start=6, margin_end=6,
				margin_top=4, margin_bottom=4))
		popover = Gtk.Popover(child=Gtk.ScrolledWindow(child=listbox, propagate_natural_height=True,
			max_content_height=320, hscrollbar_policy=Gtk.PolicyType.NEVER))

		def pick(_box, row):
			popover.popdown()
			entry.set_text(choices[row.get_index()])
		listbox.connect("row-activated", pick)
		linked.append(Gtk.MenuButton(icon_name="pan-down-symbolic", popover=popover, valign=Gtk.Align.CENTER,
			tooltip_text="Pick a workspace that's open or has a rule"))
	return box


def ws_rule_text(spec):
	"""hl.workspace_rule(...) for a new rule: {"workspace": "7", "monitor": ..., "default_name": ...}."""
	fields = ", ".join(f"{k} = {items.to_lua(spec[k])}" for k in ("workspace", "monitor", "default_name", "default",
		"persistent") if spec.get(k))
	return f"hl.workspace_rule({{ {fields} }})"


def decimals(value):
	"""Enough digits to show a float from the file exactly (15.8273644 needs 7)."""
	text = repr(float(value))
	return max(3, len(text.split(".")[1]) if "." in text and "e" not in text else 3)


def short(text, n=70):
	if isinstance(text, bool):
		text = "true" if text else "false"
	text = " ".join(str(text).split())
	return text if len(text) <= n else text[:n - 1] + "…"


class ListsPages:
	def __init__(self, on_change):
		self.on_change = on_change
		self.workspaces_page = Adw.PreferencesPage()
		self.binds_page = Adw.PreferencesPage()
		self.windowrules_page = Adw.PreferencesPage()
		self.animations_page = Adw.PreferencesPage()
		self.input_page = Adw.PreferencesPage()
		self.groups = {}
		self.after_rebuild = lambda: None  # the window sets this to re-apply page searches
		self.on_open_window = None  # the window sets this: "Rules for an open window…"
		self.windowrule_rows = []  # [(call, row)] on the Window rules page, for jumping to a rule
		self.lists = None
		self.pending = None
		self.monitors = []
		self.ws_names = {}  # {"999": "gaming"}, for labels
		self.ws_choices = []  # workspace strings offered in menus

	def load(self, model):
		self.lists = lists.Lists(model)
		self.pending = lists.Pending(self.lists)
		self.monitors = [m for m in model.monitors if m.live]
		self.rebuild()

	def changed(self, rebuild=False):
		if rebuild:
			self.rebuild()
		self.on_change()

	def rebuild(self):
		self.ws_names = workspaces.live_names()
		self.ws_choices = workspaces.known()
		for page, build in ((self.workspaces_page, self.build_workspaces), (self.binds_page, self.build_binds),
				(self.windowrules_page, self.build_windowrules), (self.animations_page, self.build_animations),
				(self.input_page, self.build_input)):
			for group in self.groups.get(page, []):
				page.remove(group)
			self.groups[page] = build()
			for group in self.groups[page]:
				page.add(group)
		self.after_rebuild()

	# Shared pieces -----------------------------------------------------------

	def where(self, call):
		return f"{items.rel(call.file)}:{call.line}"

	def deleted_row(self, title, call):
		row = Adw.ActionRow(title=esc(title), subtitle="removed on Apply", css_classes=["dim-label"])
		row.add_suffix(button("Undo", lambda: (self.pending.drop(("delete", self.pending.call_key(call))),
			self.changed(True))))
		return row

	def added_rows(self, group, fn):
		for key, op in self.pending.ops.items():
			if op[0] == "add" and op[1] == fn:
				row = Adw.ActionRow(title=esc(op[3]), subtitle="added on Apply")
				row.add_suffix(button("Undo", lambda key=key: (self.pending.drop(key), self.changed(True))))
				group.add(row)

	def value_widget(self, call, path, current, type_hint="", editor=None):
		"""An editor for one literal field; None if it isn't a plain literal.

		editor is an optional (min, max) for numbers, a list of values offered in a menu for text,
		or "workspace" for a workspace string (ID number or name:NAME).
		"""
		pending = self.pending.pending_field(call, path)
		value = current.value if pending is None or pending is RESET else pending
		on = lambda v: (self.pending.field(call, path, v), self.changed())  # noqa: E731
		if isinstance(value, bool):
			w = Gtk.Switch(active=value, valign=Gtk.Align.CENTER)
			w.connect("notify::active", lambda s, _p: on(s.get_active()))
		elif isinstance(value, (int, float)):
			is_int = isinstance(value, int) and "number" not in type_hint
			lo, hi = editor if isinstance(editor, tuple) else (-1e6, 1e6)
			w = Gtk.SpinButton(adjustment=Gtk.Adjustment(lower=lo, upper=hi, step_increment=1 if is_int else 0.05,
				page_increment=10), digits=0 if is_int else decimals(value), valign=Gtk.Align.CENTER, width_chars=9)
			w.set_value(value)
			w.connect("value-changed", lambda s: on(int(s.get_value()) if is_int else round(s.get_value(), 10)))
		elif isinstance(value, str) and editor == "workspace":
			w = workspace_editor(value, on, self.ws_choices, self.ws_names)
		elif isinstance(value, str) and isinstance(editor, list):
			w = choice_entry(value, editor, on)
		elif isinstance(value, str):
			w = Gtk.Entry(text=value, valign=Gtk.Align.CENTER, width_chars=24)
			w.connect("changed", lambda e: on(e.get_text()))
		else:
			return None
		return w

	def style_widget(self, call, current, styles):
		"""Animation style: pick from the menu or type one; clearing a style that wasn't set drops the edit."""
		pending = self.pending.pending_field(call, (0, "style"))
		text = pending if isinstance(pending, str) else current or ""

		def on(value):
			value = value.strip()
			if not value:
				if current is None:
					self.pending.drop(("field", self.pending.call_key(call), (0, "style")))
				else:
					self.pending.field(call, (0, "style"), RESET)  # an empty style is an error; drop the field
			else:
				self.pending.field(call, (0, "style"), value)
			self.changed()
		return choice_entry(text, styles, on, placeholder="default")

	def field_rows(self, parent, call, path, types=None, skip=(), title_prefix="", editors=None, titles=None,
			notes=None, only=None):
		"""Rows for every field of the table at path, each with an editor and Remove.

		editors maps a key to the value_widget editor (range, menu or "workspace") for that field;
		titles and notes replace a key's title and its subtitle (the stub type); only limits the rows
		to those keys.
		"""
		editors = editors or {}
		types = types or {}
		titles = titles or {}
		notes = notes or {}
		for key, site, current in self.lists.fields(call, path):
			if key in skip or (only is not None and key not in only):
				continue
			full = path + (key,)
			removed = self.pending.pending_field(call, full) is RESET
			row = Adw.ActionRow(title=esc(titles.get(key, title_prefix + key)), subtitle=esc(notes.get(key, types.get(key, ""))))
			if removed:
				row.set_subtitle("removed on Apply")
				row.add_css_class("dim-label")
				row.add_suffix(button("Undo", lambda full=full: (self.pending.drop(("field", self.pending.call_key(call), full)),
					self.changed(True))))
			elif current.kind == "literal" and not call.nested:
				widget = self.value_widget(call, full, current, types.get(key, ""), editors.get(key))
				row.add_suffix(widget)
				row.add_suffix(button("Remove", lambda full=full: (self.pending.field(call, full, RESET), self.changed(True))))
			else:
				shown = site.text if current.kind in ("", "literal") else f"{site.text} = {short(current.value, 40)}"
				row.add_suffix(Gtk.Label(label=short(shown, 60), css_classes=["dim-label"], valign=Gtk.Align.CENTER,
					ellipsize=3, max_width_chars=40))
				lock(row, "computed or a table; edit it by hand")
			if editors.get(key) == "workspace" and current.kind and not removed:
				row.set_subtitle(esc(workspaces.describe(current.value, self.ws_names)))
			parent.add_row(row)
		for key, op in self.pending.ops.items():
			if op[0] == "field" and op[1] is call and op[2][:-1] == path and op[3] is not RESET \
					and self.lists.field(call, op[2])[0] is None and op[2][-1] not in skip \
					and (only is None or op[2][-1] in only):
				row = Adw.ActionRow(title=esc(titles.get(op[2][-1], title_prefix + op[2][-1])),
					subtitle=esc(f"= {op[3]!r} · added on Apply"))
				row.add_suffix(button("Undo", lambda key=key: (self.pending.drop(key), self.changed(True))))
				parent.add_row(row)

	def add_field_row(self, parent, call, path, types=None, keys=None, title="Add a field"):
		"""Key (from the stub type list, or typed) plus value, then Add."""
		types = types or {}
		present = {k for k, _s, _v in self.lists.fields(call, path)}
		choices = [k for k in (keys or list(types)) if k not in present]
		row = Adw.ActionRow(title=title)
		if choices:
			key_box = Gtk.DropDown(model=Gtk.StringList.new(choices), valign=Gtk.Align.CENTER, enable_search=True)
			get_key = lambda: choices[key_box.get_selected()]  # noqa: E731
		else:
			key_box = Gtk.Entry(placeholder_text="name", valign=Gtk.Align.CENTER, width_chars=14)
			get_key = lambda: key_box.get_text().strip()  # noqa: E731
		value = Gtk.Entry(placeholder_text="value", valign=Gtk.Align.CENTER, width_chars=16)

		def add():
			key = get_key()
			if not re.fullmatch(r"[A-Za-z_]\w*", key or "") or not value.get_text().strip():
				value.add_css_class("error")
				return
			self.pending.field(call, path + (key,), parse_value(value.get_text(), types.get(key, "")))
			self.changed(True)
		value.connect("activate", lambda _e: add())
		row.add_suffix(key_box)
		row.add_suffix(value)
		row.add_suffix(button("Add", add))
		parent.add_row(row)

	def call_expander(self, call, title, subtitle):
		row = Adw.ExpanderRow(title=esc(title), subtitle=esc(subtitle))
		if call.nested:
			lock(row, "built in a loop or function; edit it by hand")
		else:
			row.add_suffix(button("Remove", lambda: (self.pending.delete(call), self.changed(True))))
		return row

	# Workspaces --------------------------------------------------------------

	def ws_rule_title(self, call):
		"""'Workspace ID 999 · named “gaming”', from pending values where there are any."""
		def value(key):
			pending = self.pending.pending_field(call, (0, key))
			_s, cur = self.lists.field(call, (0, key))
			return pending if isinstance(pending, str) else cur.value if cur.kind else ""
		ws = value("workspace")
		return workspaces.describe(ws, {ws: value("default_name")}) if ws else "Workspace ?"

	def monitor_values(self):
		return [f"desc:{m.live['description']}" for m in self.monitors] + [m.output for m in self.monitors]

	def build_workspaces(self):
		types = self.lists.types["workspace_rule"]
		monitors = self.monitor_values()
		group = Adw.PreferencesGroup(title="Workspace rules",
			description="Every workspace has a number. Rules and keybinds (Super+1) find a workspace by its "
			"number, so it keeps working whatever the workspace is called. A label is only cosmetic: the text "
			"your bar shows instead of the number.")
		for call in self.lists.workspace_rules:
			ws_site, ws = self.lists.field(call, (0, "workspace"))
			title = self.ws_rule_title(call)
			if self.pending.deleted(call):
				group.add(self.deleted_row(title, call))
				continue
			row = self.call_expander(call, title, self.ws_rule_summary(call))
			editors = {"monitor": monitors, "workspace": "workspace"}
			if call.nested:
				self.field_rows(row, call, (0,), types, editors=editors, titles=WS_RULE_TITLES, notes=WS_RULE_NOTES)
				group.add(row)
				continue
			# The same rows for every rule, set or not, in the same order; the rest below.
			present = {k for k, _s, _v in self.lists.fields(call)}
			if ws.kind == "literal":
				row.add_row(self.ws_rule_workspace_row(call, row, ws))
			else:
				self.field_rows(row, call, (0,), types, editors=editors, titles=WS_RULE_TITLES, only=("workspace",))
			for key in WS_COMMON:
				site, current = self.lists.field(call, (0, key))
				if site is None or current.kind == "literal":
					row.add_row(self.ws_field_row(call, key, current if site else None, monitors, row))
				else:
					self.field_rows(row, call, (0,), types, editors=editors, titles=WS_RULE_TITLES, notes=WS_RULE_NOTES,
						only=(key,))
			self.field_rows(row, call, (0,), types, skip=("workspace",) + WS_COMMON, editors=editors,
				titles=WS_RULE_TITLES, notes=WS_RULE_NOTES)
			self.add_field_row(row, call, (0,), types, keys=[k for k in types if k not in WS_COMMON + ("workspace",)],
				title="Add another field")
			group.add(row)
		for key, op in self.pending.ops.items():
			if op[0] == "add" and op[1] == "workspace_rule":
				group.add(self.new_ws_rule_row(key, monitors))
		group.add(self.add_ws_rule_row())
		return [group, self.open_workspaces_group(), self.monitor_names_group()]

	def ws_rule_summary(self, call):
		"""The same facts for every rule: 'monitor DP-1 · opens first · kept when empty · file:line'."""
		values = {k: (s, v) for k, s, v in self.lists.fields(call)}

		def value(key):
			site, v = values.get(key, (None, lists.Value()))
			return v.value if v.kind else site.text if site else None
		monitor = value("monitor")
		port = next((m.output for m in self.monitors if isinstance(monitor, str) and monitor.startswith("desc:")
			and m.live["description"].startswith(monitor[5:].strip())), None)
		parts = [f"monitor {port or short(monitor, 30)}" if monitor else "any monitor",
			"opens first" if value("default") is True else None,
			"kept when empty" if value("persistent") is True else None]
		extra = [k for k in values if k not in WS_COMMON + ("workspace",)]
		if extra:
			parts.append("also " + ", ".join(extra))
		return " · ".join(p for p in parts + [self.where(call)] if p)

	def ws_field_row(self, call, key, current, monitors, expander):
		"""One of the fields every rule shows, set or not. Empty text or a switch turned off
		removes the field (Hyprland's default); filling one in adds it on Apply."""
		path = (0, key)
		pending = self.pending.pending_field(call, path)
		if pending is None and current is not None:
			pending = current.value
		elif pending is RESET:
			pending = None
		row = Adw.ActionRow(title=esc(WS_RULE_TITLES[key]), subtitle=esc(WS_RULE_NOTES[key]))

		def set_value(value):
			if value == "" or (value is False and current is None):
				self.pending.field(call, path, RESET)  # dropped when the rule doesn't set it
			else:
				self.pending.field(call, path, value)  # a switch the file sets stays written, as false
			expander.set_title(esc(self.ws_rule_title(call)))
			self.changed()
		if key in ("default", "persistent"):
			widget = Gtk.Switch(active=pending is True, valign=Gtk.Align.CENTER)
			widget.connect("notify::active", lambda s, _p: set_value(s.get_active()))
		elif key == "monitor":
			widget = choice_entry(pending if isinstance(pending, str) else "", monitors, lambda t: set_value(t.strip()),
				placeholder="any monitor")
		else:
			widget = Gtk.Entry(text=pending if isinstance(pending, str) else "", placeholder_text="none",
				valign=Gtk.Align.CENTER, width_chars=16)
			widget.connect("changed", lambda e: set_value(e.get_text().strip()))
		row.add_suffix(widget)
		return row

	def ws_rule_workspace_row(self, call, expander, ws):
		pending = self.pending.pending_field(call, (0, "workspace"))

		def on(text):
			self.pending.field(call, (0, "workspace"), text)
			expander.set_title(esc(self.ws_rule_title(call)))
			self.changed()
		row = Adw.ActionRow(title=WS_RULE_TITLES["workspace"], subtitle="The workspace this rule is for")
		row.add_suffix(workspace_editor(pending if isinstance(pending, str) else ws.value, on,
			self.ws_choices, self.ws_names, show_label=False))
		return row

	def add_ws_rule_row(self):
		row = Adw.ActionRow(title="Add a rule for workspace number", subtitle="Set its label and monitor after adding")
		editor = workspace_editor("", lambda _t: None, self.ws_choices, self.ws_names)

		def add():
			ws = editor.current()
			if not ws:
				editor.entry.add_css_class("error")
				return
			self.pending.next_add += 1
			key = ("workspace_rule", self.pending.next_add)
			self.pending.specs[key] = {"workspace": ws}
			self.pending.add("workspace_rule", ws_rule_text(self.pending.specs[key]), workspaces.describe(ws), key=key)
			self.changed(True)
		editor.entry.connect("activate", lambda _e: add())
		row.add_suffix(editor)
		row.add_suffix(button("Add", add))
		return row

	def new_ws_rule_row(self, key, monitors):
		"""A rule added in this session: its workspace, name and monitor stay editable until Apply."""
		spec = self.pending.specs[key[2]]
		row = Adw.ExpanderRow(title=esc(self.new_ws_rule_title(spec)), subtitle="added on Apply")
		row.add_suffix(button("Undo", lambda: (self.pending.drop(key), self.changed(True))))

		def changed():
			title = self.new_ws_rule_title(spec)
			self.pending.add("workspace_rule", ws_rule_text(spec), title, key=key[2])
			row.set_title(esc(title))
			self.changed()

		def set_field(field, value):
			if value.strip():
				spec[field] = value.strip()
			else:
				spec.pop(field, None)
			changed()
		which = Adw.ActionRow(title=WS_RULE_TITLES["workspace"], subtitle="The workspace this rule is for")
		which.add_suffix(workspace_editor(spec["workspace"], lambda t: set_field("workspace", t),
			self.ws_choices, self.ws_names, show_label=False))
		row.add_row(which)
		name = Adw.ActionRow(title=WS_RULE_TITLES["default_name"], subtitle=WS_RULE_NOTES["default_name"])
		name_entry = Gtk.Entry(text=spec.get("default_name", ""), placeholder_text="none", valign=Gtk.Align.CENTER,
			width_chars=16)
		name_entry.connect("changed", lambda e: set_field("default_name", e.get_text()))
		name.add_suffix(name_entry)
		row.add_row(name)
		monitor = Adw.ActionRow(title=WS_RULE_TITLES["monitor"], subtitle=WS_RULE_NOTES["monitor"])
		monitor.add_suffix(choice_entry(spec.get("monitor", ""), monitors, lambda t: set_field("monitor", t),
			placeholder="any"))
		row.add_row(monitor)
		for flag in ("default", "persistent"):
			flag_row = Adw.ActionRow(title=WS_RULE_TITLES[flag], subtitle=WS_RULE_NOTES[flag])
			switch = Gtk.Switch(active=bool(spec.get(flag)), valign=Gtk.Align.CENTER)
			switch.connect("notify::active", lambda s, _p, flag=flag: (spec.__setitem__(flag, True) if s.get_active()
				else spec.pop(flag, None), changed()))
			flag_row.add_suffix(switch)
			row.add_row(flag_row)
		row.add_row(Adw.ActionRow(title="More fields", subtitle="Gaps, borders, layout and the rest after Apply",
			css_classes=["dim-label"]))
		return row

	@staticmethod
	def new_ws_rule_title(spec):
		return workspaces.describe(spec["workspace"], {spec["workspace"]: spec.get("default_name")})

	def open_workspaces_group(self):
		group = Adw.PreferencesGroup(title="Open workspaces",
			description="Change the label or number of a workspace that is open right now. This happens at "
			"once and is not saved: when the workspace is made again it gets the label from its rule above.")
		try:
			live = sorted(hypr.hyprctl_json("workspaces"), key=lambda w: (w["id"] < 0, w["id"]))
		except Exception:  # noqa: BLE001 - Hyprland not answering
			live = []
		for ws in live:
			if ws["name"].startswith("special"):
				continue
			numbered = ws["id"] > 0
			selector = str(ws["id"]) if numbered else "name:" + ws["name"]
			labelled = not numbered or ws["name"] != str(ws["id"])
			title = f"Workspace {ws['id']}" if numbered else f"Workspace labelled “{ws['name']}” (no number)"
			windows = f"{ws['windows']} window{'s' if ws['windows'] != 1 else ''}"
			label_text = f"label “{ws['name']}”" if numbered and labelled else "no label" if numbered else ""
			row = Adw.ExpanderRow(title=esc(title), subtitle=esc(" · ".join(x for x in (label_text, ws["monitor"], windows) if x)))
			name_row = Adw.ActionRow(title="Label in your bar",
				subtitle="Leave empty to show just the number" if numbered else "Only cosmetic")
			name_entry = Gtk.Entry(text=ws["name"] if labelled else "", placeholder_text=str(ws["id"]) if numbered else "",
				valign=Gtk.Align.CENTER, width_chars=16)
			name_row.add_suffix(name_entry)
			name_row.add_suffix(button("Set label now", lambda e=name_entry, r=name_row, sel=selector, n=numbered, i=ws["id"]:
				self.rename_workspace(sel, e.get_text().strip() or (str(i) if n else ""), r)))
			row.add_row(name_row)
			if numbered:
				id_row = Adw.ActionRow(title="Number",
					subtitle="Must be free. Rules and keybinds for the old number won't reach it any more")
				spin = Gtk.SpinButton(adjustment=Gtk.Adjustment(lower=1, upper=2147483647, step_increment=1,
					page_increment=10), valign=Gtk.Align.CENTER, width_chars=9)
				spin.set_value(ws["id"])
				id_row.add_suffix(spin)
				id_row.add_suffix(button("Change number now", lambda s=spin, r=id_row, sel=selector:
					self.change_workspace_id(sel, int(s.get_value()), r)))
				row.add_row(id_row)
			group.add(row)
		return group

	def run_dispatch(self, expr, row):
		"""Runs one hl.dsp call; a refusal shows on the row, success rebuilds the pages."""
		out = subprocess.run(["hyprctl", "dispatch", expr], capture_output=True, text=True, timeout=5)
		text = (out.stdout + out.stderr).strip()
		if out.returncode != 0 or text != "ok":
			reason = text.rsplit(": ", 1)[-1] if text else f"exit code {out.returncode}"  # drop "warning: =[C]:-1: "
			row.set_subtitle(esc(f"Hyprland said: {reason}"))
			row.add_css_class("error")
			return
		self.rebuild()

	def rename_workspace(self, selector, name, row):
		if not name:
			row.add_css_class("error")
			return
		self.run_dispatch(f"hl.dsp.workspace.rename({{ workspace = {luasrc.lua_string(selector)}, "
			f"name = {luasrc.lua_string(name)} }})", row)

	def change_workspace_id(self, selector, new_id, row):
		self.run_dispatch(f"hl.dsp.workspace.change_id({{ workspace = {luasrc.lua_string(selector)}, id = {new_id} }})",
			row)

	def monitor_names_group(self):
		monitors = Adw.PreferencesGroup(title="Monitor names for rules",
			description="Use these as monitor values; desc: names survive port changes.")
		for m in self.monitors:
			monitors.add(Adw.ActionRow(title=esc(f"desc:{m.live['description']}"), subtitle=esc(m.output),
				css_classes=["property"]))
		return monitors

	# Keybinds ----------------------------------------------------------------

	def bind_sections(self):
		"""{call: (section, note)} from the comment lines above each bind."""
		out = {}
		for call in self.lists.binds:
			text = self.lists.src(call).text
			section, note = "Other", ""
			for line in reversed(text[:call.start].splitlines()):
				line = line.strip()
				m = SECTION.match(line)
				if m:
					section = m.group(1).title()
					break
				n = NOTE.match(line)
				if n and not note and not line.startswith("---"):
					note = n.group(1)
			out[id(call)] = (section, note)
		return out

	def build_binds(self):
		sections = self.bind_sections()
		groups = {}
		order = []
		for call in self.lists.binds:
			section, note = sections[id(call)]
			if section not in groups:
				groups[section] = Adw.PreferencesGroup(title=esc(section))
				order.append(section)
			groups[section].add(self.bind_row(call, note))
		add_group = Adw.PreferencesGroup(title="Add a keybind",
			description="Runs a command. Keys look like SUPER + SHIFT + K; new binds go at the end of binds.lua.")
		self.added_rows(add_group, "bind")
		add_group.add(self.add_bind_row())
		top = Adw.PreferencesGroup(description="Binds built in loops (workspace numbers) are read-only. "
			"\"Bound N times\" counts what Hyprland has registered for that key; it doesn't say which one runs.")
		return [top] + [groups[s] for s in order] + [add_group]

	def bind_row(self, call, note):
		keys_arg = call.args[0] if call.args else None
		keys = self.lists.resolve(call, keys_arg.text) if keys_arg else lists.Value()
		inner, command = self.lists.exec_command(call)
		action_text = short(call.args[1].text if len(call.args) > 1 else "", 60)
		label = keys.value if keys.kind else short(keys_arg.text if keys_arg else "?", 40)
		subtitle = " · ".join(x for x in (note, self.where(call)) if x)
		if self.pending.deleted(call):
			return self.deleted_row(f"{label}: {action_text}", call)
		if call.nested or not keys.kind:
			row = Adw.ActionRow(title=esc(label), subtitle=esc(f"{action_text} · {subtitle}"))
			return lock(row, "built in a loop or computed; edit it by hand")

		row = Adw.ExpanderRow(subtitle=esc(subtitle))
		pending_keys = self.pending.pending_text(call, keys_arg)
		shown_keys = pending_keys if pending_keys is not None else keys.value
		count = self.lists.bind_count(keys.value)
		row.set_title(esc(f"{shown_keys}  →  {command.value if command.kind else action_text}"))
		if count > 1:
			row.add_suffix(Gtk.Label(label=f"bound {count} times", css_classes=["caption", "warning"],
				valign=Gtk.Align.CENTER))
		row.add_suffix(button("Remove", lambda: (self.pending.delete(call), self.changed(True))))

		keys_row = Adw.EntryRow(title="Keys", text=shown_keys)
		keys_row.connect("changed", lambda e: (self.pending.text_arg(call, keys_arg, e.get_text(), keys),
			row.set_title(esc(f"{e.get_text()}  →  {command.value if command.kind else action_text}")),
			self.check_keys(e), self.changed()))
		row.add_row(keys_row)
		if command.kind:
			pending_cmd = self.pending.pending_text(call, inner)
			cmd_row = Adw.EntryRow(title="Command", text=pending_cmd if pending_cmd is not None else command.value)
			cmd_row.connect("changed", lambda e: (self.pending.text_arg(call, inner, e.get_text(), command), self.changed()))
			row.add_row(cmd_row)
		else:
			row.add_row(lock(Adw.ActionRow(title="Action", subtitle=esc(action_text)), "only commands can be edited here"))
		opts = self.lists.types["bind_opts"]
		for key in ("locked", "repeating", "release"):
			_site, current = self.lists.field(call, (2, key))
			pending = self.pending.pending_field(call, (2, key))
			active = bool(current.value) if pending is None else pending is not RESET and bool(pending)
			switch = Adw.SwitchRow(title=key, subtitle={"locked": "works on the lock screen too",
				"repeating": "repeats while held", "release": "fires on key release"}[key], active=active)
			if current.kind not in ("", "literal"):
				switch.set_sensitive(False)
			switch.connect("notify::active", lambda s, _p, key=key: (self.pending.field(call, (2, key),
				True if s.get_active() else RESET), self.changed()))
			row.add_row(switch)
		del opts
		return row

	@staticmethod
	def check_keys(entry):
		if lists.parse_keys(entry.get_text()):
			entry.remove_css_class("error")
		else:
			entry.add_css_class("error")

	def add_bind_row(self):
		row = Adw.ActionRow(title="New keybind")
		keys = Gtk.Entry(placeholder_text="SUPER + K", valign=Gtk.Align.CENTER, width_chars=16)
		command = Gtk.Entry(placeholder_text="command to run", valign=Gtk.Align.CENTER, width_chars=26)
		keys.connect("changed", lambda e: self.check_keys(e))

		def add():
			k, c = keys.get_text().strip(), command.get_text().strip()
			if not lists.parse_keys(k) or not c:
				(keys if not lists.parse_keys(k) else command).add_css_class("error")
				return
			binds_src = self.lists.model.files.get(self.lists.file_for("bind"))
			lookup = (lambda n: self.pending._lookup(binds_src, n)) if binds_src else (lambda n: None)
			parts = [("name", "mainMod")] if binds_src and "mainMod" in binds_src.locals else None
			key_lua = lists.write_expr(k, parts, lookup)
			self.pending.add("bind", f"hl.bind({key_lua}, hl.dsp.exec_cmd({items.to_lua(c)}))", f"{k}  →  {c}")
			self.changed(True)
		command.connect("activate", lambda _e: add())
		row.add_suffix(keys)
		row.add_suffix(command)
		row.add_suffix(button("Add", add))
		return row

	# Window rules ------------------------------------------------------------

	def build_windowrules(self):
		group = Adw.PreferencesGroup(title="Your window rules",
			description="The hand-written rules in windowrules.lua, in the order Hyprland applies them. "
			"Rules built in loops are read-only. Add new rules on the Extra rules page.")
		if self.on_open_window:
			group.set_header_suffix(button("Rules for an open window…", self.on_open_window, css=()))
		self.windowrule_rows = []
		for call in self.lists.window_rules:
			fields = {k: (s, v) for k, s, v in self.lists.fields(call)}
			name = fields.get("name", (None, lists.Value()))[1]
			match = self.lists.fields(call, (0, "match"))
			summary = ", ".join(f"{k} {short(v.value if v.kind else s.text, 40)}" for k, s, v in match) \
				or short(call.args[0].text if call.args else "", 50)
			title = name.value if name.kind else summary
			if self.pending.deleted(call):
				group.add(self.deleted_row(title, call))
				continue
			effects = [k for k in fields if k not in ("match", "name")]
			row = self.call_expander(call, title, f"{', '.join(effects) or 'no effects'} · {self.where(call)}")
			if call.nested:
				row.add_row(Adw.ActionRow(title=esc(short(call.args[0].text if call.args else "", 90)), title_lines=3))
			else:
				self.field_rows(row, call, (0, "match"), title_prefix="match ", editors=WS_FIELD)
				if self.lists.table(call, (0, "match")) is not None:
					self.add_field_row(row, call, (0, "match"), keys=MATCH_KEYS, title="Add a match")
				self.field_rows(row, call, (0,), skip=("match",), editors={**WS_FIELD, "monitor": self.monitor_values()},
					notes={"monitor": WS_RULE_NOTES["monitor"]})
				self.add_field_row(row, call, (0,), title="Add an effect (e.g. float = true)")
			self.windowrule_rows.append((call, row))
			group.add(row)
		return [group]

	# Animations --------------------------------------------------------------

	def curve_names(self):
		names = []
		for call in self.lists.curves:
			if call.args and call.args[0].literal:
				names.append(lists.luasrc.literal_value(call.args[0]))
		return names

	def build_animations(self):
		curves = Adw.PreferencesGroup(title="Curves", description="Bezier curves have two control points; "
			"springs have mass, stiffness and dampening.")
		for call in self.lists.curves:
			name = lists.luasrc.literal_value(call.args[0]) if call.args and call.args[0].literal else "?"
			_s, kind = self.lists.field(call, (1, "type"))
			if self.pending.deleted(call):
				curves.add(self.deleted_row(name, call))
				continue
			row = self.call_expander(call, name, f"{kind.value if kind.kind else '?'} · {self.where(call)}")
			points = self.lists.curve_points(call) if kind.value == "bezier" else None
			if points:
				labels = ["x1", "y1", "x2", "y2"]
				for (start, end, value), label in zip(points, labels):
					shown = self.pending.pending_span(call, start)
					spin = Adw.SpinRow(title=label, digits=decimals(value), subtitle="0 to 1" if label[0] == "x" else "",
						adjustment=Gtk.Adjustment(lower=0 if label[0] == "x" else -2, upper=1 if label[0] == "x" else 3,
							step_increment=0.01, page_increment=0.1))
					spin.set_value(shown if shown is not None else value)
					spin.connect("notify::value", lambda s, _p, start=start, end=end, value=value: (
						self.pending.span(call, start, end, round(s.get_value(), 4), value), self.changed()))
					row.add_row(spin)
			elif kind.value == "spring":
				self.field_rows(row, call, (1,), skip=("type",))
			else:
				self.field_rows(row, call, (1,))
			curves.add(row)
		self.added_rows(curves, "curve")
		add = Adw.EntryRow(title="Add a bezier curve (name)", show_apply_button=True)

		def add_curve(e):
			name = e.get_text().strip()
			if re.fullmatch(r"[A-Za-z_]\w*", name):
				self.pending.add("curve", f'hl.curve({items.to_lua(name)}, {{ type = "bezier", points = {{ {{0.25, 0.1}}, {{0.25, 1}} }} }})',
					f"curve {name}")
				self.changed(True)
		add.connect("apply", add_curve)
		curves.add(add)

		anims = Adw.PreferencesGroup(title="Animations", description="Leaves you haven't set inherit from their parent "
			"(global → windows → windowsIn ...).")
		names = self.curve_names()
		curve_kind = {}
		for call in self.lists.curves:
			_s, kind = self.lists.field(call, (1, "type"))
			if call.args and call.args[0].literal:
				curve_kind[lists.luasrc.literal_value(call.args[0])] = kind.value
		for call in self.lists.animations:
			_s, leaf = self.lists.field(call, (0, "leaf"))
			name = leaf.value if leaf.kind else "?"
			if self.pending.deleted(call):
				anims.add(self.deleted_row(name, call))
				continue
			fields = {k: v for k, _s, v in self.lists.fields(call)}
			curve_key = "spring" if "spring" in fields else "bezier"
			curve = fields.get(curve_key, lists.Value())
			bits = [f"{k} {short(v.value)}" for k, v in fields.items() if k != "leaf" and v.kind]
			row = self.call_expander(call, name, f"{', '.join(bits)} · {self.where(call)}")
			if call.nested:
				anims.add(row)
				continue
			styles = animation_styles(name)
			for key in ("enabled", "speed", "style"):
				if key in fields and fields[key].kind == "literal":
					r = Adw.ActionRow(title=key, subtitle={"speed": "in tenths of a second (ds)"}.get(key, ""))
					if key == "style" and styles and isinstance(fields[key].value, str):
						r.add_suffix(self.style_widget(call, fields[key].value, styles))
					else:
						r.add_suffix(self.value_widget(call, (0, key), fields[key], "number" if key == "speed" else ""))
					row.add_row(r)
				elif key == "style" and key not in fields and styles:
					r = Adw.ActionRow(title=key, subtitle="not set: Hyprland's default for this animation")
					r.add_suffix(self.style_widget(call, None, styles))
					row.add_row(r)
			if curve.kind == "literal" and names:
				options = names if curve.value in names else names + [curve.value]
				pending_curve = self.pending.pending_field(call, (0, curve_key))
				for other in ("bezier", "spring"):
					p = self.pending.pending_field(call, (0, other))
					if p not in (None, RESET):
						pending_curve = p
				current = pending_curve if pending_curve not in (None, RESET) else curve.value
				drop = Adw.ComboRow(title="Curve", model=Gtk.StringList.new(options),
					selected=options.index(current) if current in options else 0)

				def pick(r, _p, call=call, curve_key=curve_key, options=options):
					chosen = options[r.get_selected()]
					want = "spring" if curve_kind.get(chosen) == "spring" else "bezier"
					other = "bezier" if want == "spring" else "spring"
					self.pending.drop(("field", self.pending.call_key(call), (0, other)))
					self.pending.drop(("field", self.pending.call_key(call), (0, want)))
					if want != curve_key:
						self.pending.field(call, (0, curve_key), RESET)
					self.pending.field(call, (0, want), chosen)
					self.changed()
				drop.connect("notify::selected", pick)
				row.add_row(drop)
			anims.add(row)
		self.added_rows(anims, "animation")
		configured = {self.lists.field(c, (0, "leaf"))[1].value for c in self.lists.animations}
		leaves = sorted(a["name"] for a in self.lists.live_animations[0]
			if not a["name"].startswith("__") and a["name"] not in configured)
		if leaves and names:
			row = Adw.ActionRow(title="Add an animation")
			leaf_box = Gtk.DropDown(model=Gtk.StringList.new(leaves), valign=Gtk.Align.CENTER, enable_search=True)
			curve_box = Gtk.DropDown(model=Gtk.StringList.new(names), valign=Gtk.Align.CENTER)

			def add_anim():
				leaf, curve = leaves[leaf_box.get_selected()], names[curve_box.get_selected()]
				key = "spring" if curve_kind.get(curve) == "spring" else "bezier"
				self.pending.add("animation", f"hl.animation({{ leaf = {items.to_lua(leaf)}, enabled = true, speed = 3, "
					f"{key} = {items.to_lua(curve)} }})", f"animation {leaf}")
				self.changed(True)
			row.add_suffix(leaf_box)
			row.add_suffix(curve_box)
			row.add_suffix(button("Add", add_anim))
			anims.add(row)
		return [curves, anims]

	# Gestures & devices ------------------------------------------------------

	def build_input(self):
		gtypes = self.lists.types["gesture"]
		gestures = Adw.PreferencesGroup(title="Touchpad gestures")
		for call in self.lists.gestures:
			f = {k: v for k, _s, v in self.lists.fields(call)}
			title = " ".join(str(f[k].value) for k in ("fingers", "direction") if k in f and f[k].kind)
			action = f.get("action", lists.Value())
			title = f"{title} fingers → {action.value if action.kind else 'function'}"
			if self.pending.deleted(call):
				gestures.add(self.deleted_row(title, call))
				continue
			row = self.call_expander(call, title, self.where(call))
			self.field_rows(row, call, (0,), gtypes, editors=GESTURE_EDITORS)
			if not call.nested:
				self.add_field_row(row, call, (0,), gtypes)
			gestures.add(row)
		self.added_rows(gestures, "gesture")
		row = Adw.ExpanderRow(title="Add a gesture", subtitle="Same fields as the gestures above")
		lo, hi = GESTURE_FINGERS
		fingers = Gtk.SpinButton(adjustment=Gtk.Adjustment(lower=lo, upper=hi, step_increment=1, page_increment=10, value=4),
			valign=Gtk.Align.CENTER, width_chars=9)
		direction_box = choice_entry("", DIRECTIONS, lambda _t: None, placeholder="direction, e.g. horizontal")
		action_box = choice_entry("", GESTURE_ACTIONS, lambda _t: None, placeholder="action, e.g. workspace")
		direction, action = direction_box.entry, action_box.entry

		def add_gesture():
			d, a = direction.get_text().strip(), action.get_text().strip()
			for entry, text in ((direction, d), (action, a)):
				entry.remove_css_class("error")
				if not text:
					entry.add_css_class("error")
			if not d or not a:
				return
			n = int(fingers.get_value())
			self.pending.add("gesture", f"hl.gesture({{ fingers = {n}, direction = {items.to_lua(d)}, action = {items.to_lua(a)} }})",
				f"{n} {d} fingers → {a}")
			self.changed(True)
		for key, w in (("fingers", fingers), ("direction", direction_box), ("action", action_box)):
			field = Adw.ActionRow(title=key, subtitle=esc(gtypes.get(key, "")))
			field.add_suffix(w)
			row.add_row(field)
		row.add_suffix(button("Add", add_gesture))
		gestures.add(row)

		dtypes = self.lists.types["device"]
		devices = Adw.PreferencesGroup(title="Per-device settings",
			description="Settings for one device instead of all of them (input options apply to all).")
		configured = set()
		for call in self.lists.devices:
			_s, name = self.lists.field(call, (0, "name"))
			configured.add(name.value)
			if self.pending.deleted(call):
				devices.add(self.deleted_row(name.value or "?", call))
				continue
			row = self.call_expander(call, name.value if name.kind else "?", self.where(call))
			self.field_rows(row, call, (0,), dtypes, skip=("name",))
			if not call.nested:
				self.add_field_row(row, call, (0,), dtypes)
			devices.add(row)
		self.added_rows(devices, "device")
		live = sorted({d["name"] for kind in ("mice", "keyboards", "tablets", "touch") for d in self.lists.live_devices.get(kind, [])}
			- configured)
		if live:
			row = Adw.ActionRow(title="Add settings for a device")
			box = Gtk.DropDown(model=Gtk.StringList.new(live), valign=Gtk.Align.CENTER, enable_search=True)

			def add_device():
				name = live[box.get_selected()]
				self.pending.add("device", f"hl.device({{ name = {items.to_lua(name)} }})", name)
				self.changed(True)
			row.add_suffix(box)
			row.add_suffix(button("Add", add_device))
			devices.add(row)
		return [gestures, devices]

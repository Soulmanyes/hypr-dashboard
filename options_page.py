"""The Options tab: every Hyprland option, by section, with a search box.

Changes are only collected here (self.changes); the window's Apply button
writes them through options.apply together with the rules.
"""
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

import options  # noqa: E402

INT_RANGE = (-(2 ** 31), 2 ** 31 - 1)
FLOAT_RANGE = (-1e6, 1e6)
SEARCH_LIMIT = 80
CSS_SIDES = ("top", "right", "bottom", "left")


def pretty(name):
	return name.rsplit(":", 1)[-1].replace("_", " ").replace(".", " ")


def shown_value(option):
	if option.type == "css":
		return " ".join(str(v) for v in option.value)
	if option.type == "bool":
		return "on" if option.value else "off"
	if option.choices:
		return next((label for label, v in option.choices if v == option.value), str(option.value))
	if option.type == "float":
		return options.to_lua("float", option.value)
	return str(option.value) if option.value not in (None, "") else "(empty)"


def float_digits(value):
	"""Enough decimals to show the value exactly (getoption gives at most 6)."""
	text = f"{value:.6f}".rstrip("0")
	return max(3, len(text.split(".")[1]))


class OptionsPage(Gtk.Box):
	def __init__(self, on_change):
		super().__init__(orientation=Gtk.Orientation.HORIZONTAL)
		self.on_change = on_change
		self.options = []
		self.files = {}
		self.changes = {}
		self.section = "general"

		side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, width_request=210)
		self.search = Gtk.SearchEntry(placeholder_text="Search options", margin_start=8, margin_end=8,
			margin_top=8, margin_bottom=8)
		self.search.connect("search-changed", lambda _e: self.show_rows())
		side.append(self.search)
		self.sections = Gtk.ListBox(css_classes=["navigation-sidebar"])
		self.sections.connect("row-selected", self.on_section)
		side.append(Gtk.ScrolledWindow(child=self.sections, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER))
		self.append(side)
		self.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

		self.page = Adw.PreferencesPage(hexpand=True)
		self.groups = []
		self.append(self.page)

	# Data ------------------------------------------------------------------

	def load(self, state, keep_changes=False):
		old = self.changes if keep_changes else {}
		self.options, self.files = options.load(state)
		self.by_name = {o.name: o for o in self.options}
		# A kept change is dropped if the option became read-only meanwhile.
		self.changes = {n: v for n, v in old.items() if n in self.by_name and not self.by_name[n].locked()}
		counts = {}
		for o in self.options:
			counts[o.section] = counts.get(o.section, 0) + 1
		self.section_names = sorted(counts)
		self._filling = True
		while row := self.sections.get_row_at_index(0):
			self.sections.remove(row)
		for name in self.section_names:
			box = Gtk.Box(spacing=6)
			box.append(Gtk.Label(label=name, xalign=0, hexpand=True))
			box.append(Gtk.Label(label=str(counts[name]), css_classes=["dim-label", "caption"]))
			self.sections.append(Gtk.ListBoxRow(child=box, name=name))
		self._filling = False
		if self.section not in self.section_names:
			self.section = self.section_names[0]
		self.sections.select_row(self.sections.get_row_at_index(self.section_names.index(self.section)))
		self.show_rows()

	def show_section(self, name):
		if name in self.section_names:
			self.sections.select_row(self.sections.get_row_at_index(self.section_names.index(name)))

	def on_section(self, _box, row):
		if row is None or self._filling:
			return
		self.section = row.get_name()
		if self.search.get_text():
			self.search.set_text("")  # show_rows runs from search-changed
		else:
			self.show_rows()

	def set_change(self, option, value):
		"""Records a pending change, or drops it when the value is back to what Hyprland has."""
		if value is not options.RESET and options.same_value(option.type, option.value, value):
			self.changes.pop(option.name, None)
		else:
			self.changes[option.name] = value
		self.on_change()

	# Rows ------------------------------------------------------------------

	def show_rows(self):
		for group in self.groups:
			self.page.remove(group)
		self.groups = []
		query = self.search.get_text().strip().lower()
		if query:
			shown = [o for o in self.options if query in o.name.lower() or query in o.description.lower()]
			title = f"{len(shown)} matching"
			if len(shown) > SEARCH_LIMIT:
				title += f", showing the first {SEARCH_LIMIT}"
				shown = shown[:SEARCH_LIMIT]
			groups = [(title, shown)] if shown else [("Nothing matches", [])]
		else:
			by_parent = {}
			for o in self.options:
				if o.section == self.section:
					by_parent.setdefault(o.name.rsplit(":", 1)[0], []).append(o)
			groups = sorted(by_parent.items(), key=lambda kv: (kv[0].count(":"), kv[0]))
		for title, opts in groups:
			group = Adw.PreferencesGroup(title=GLib.markup_escape_text(title.replace(":", " › ")))
			for o in opts:
				group.add(self.row(o, full_name=bool(query)))
			self.page.add(group)
			self.groups.append(group)
		self.page.scroll_to_top()

	def row(self, option, full_name=False):
		title = GLib.markup_escape_text(option.name if full_name else pretty(option.name))
		subtitle = GLib.markup_escape_text(option.description)
		locked = option.locked()
		pending = self.changes.get(option.name)
		value = option.value if pending is None or pending is options.RESET else pending

		if locked:
			row = Adw.ActionRow(title=title, subtitle=subtitle, tooltip_text=locked)
			row.add_suffix(Gtk.Label(label=shown_value(option), css_classes=["dim-label"],
				ellipsize=3, max_width_chars=24, valign=Gtk.Align.CENTER))
			row.add_prefix(Gtk.Image(icon_name="changes-prevent-symbolic", css_classes=["dim-label"]))
			self.add_badge(row, option)
			return row

		if option.type == "bool":
			row = Adw.SwitchRow(title=title, subtitle=subtitle, active=bool(value))
			row.connect("notify::active", lambda r, _p: self.set_change(option, r.get_active()))
		elif option.choices:
			labels = [label for label, _ in option.choices]
			values = [v for _, v in option.choices]
			if value not in values:
				labels.append(str(value))
				values.append(value)
			row = Adw.ComboRow(title=title, subtitle=subtitle, model=Gtk.StringList.new(labels),
				selected=values.index(value))
			row.connect("notify::selected", lambda r, _p: self.set_change(option, values[r.get_selected()]))
		elif option.type in ("int", "float"):
			low, high = INT_RANGE if option.type == "int" else FLOAT_RANGE
			# Widen the range to the live value so set_value never clamps it.
			low = min(option.min if option.min is not None else low, value)
			high = max(option.max if option.max is not None else high, value)
			span = high - low
			step = 1 if option.type == "int" else (0.05 if span <= 2 else 0.1 if span <= 20 else 1)
			adjustment = Gtk.Adjustment(lower=low, upper=high, step_increment=step, page_increment=step * 10)
			row = Adw.SpinRow(title=title, subtitle=subtitle, adjustment=adjustment,
				digits=0 if option.type == "int" else float_digits(value))
			row.set_value(value)
			row.connect("notify::value", lambda r, _p: self.set_change(option,
				int(r.get_value()) if option.type == "int" else round(r.get_value(), 6)))
		elif option.type == "css":
			row = Adw.ExpanderRow(title=title, subtitle=subtitle)
			spins = []
			for side, v in zip(CSS_SIDES, value):
				spin = Adw.SpinRow(title=side, adjustment=Gtk.Adjustment(lower=min(0, v), upper=max(500, v), step_increment=1,
					page_increment=10))
				spin.set_value(v)
				spins.append(spin)
				row.add_row(spin)
			for spin in spins:
				spin.connect("notify::value", lambda _s, _p: self.set_change(option, [int(s.get_value()) for s in spins]))
			row.add_suffix(Gtk.Label(label=" ".join(str(v) for v in value), valign=Gtk.Align.CENTER))
		else:  # str
			row = Adw.ActionRow(title=title, subtitle=subtitle)
			entry = Gtk.Entry(text=value or "", valign=Gtk.Align.CENTER, width_chars=18)
			entry.connect("changed", lambda e: self.set_change(option, e.get_text()))
			row.add_suffix(entry)

		self.add_badge(row, option)
		return row

	def add_badge(self, row, option):
		pending = self.changes.get(option.name)
		if pending is options.RESET:
			text, css = "reset on Apply", ["accent"]
		else:
			text, css = option.source(), []
		badge = Gtk.Label(label=text, css_classes=["caption", "dim-label", *css], valign=Gtk.Align.CENTER)
		row.add_suffix(badge)
		if option.set and not option.locked() and pending is not options.RESET:
			reset = Gtk.Button(label="Reset", valign=Gtk.Align.CENTER, css_classes=["flat", "caption"],
				tooltip_text=f"Reset to default ({option.default})")
			reset.connect("clicked", lambda _b: (self.set_change(option, options.RESET), self.show_rows()))
			row.add_suffix(reset)

"""The Options tab: every Hyprland option, by section, with a search box.

Changes are only collected here (self.changes); the window's Apply button
writes them through options.apply together with the rules.
"""
from dataclasses import replace

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


def typed(spin):
	"""The number in a spin row's text as typed, before Enter commits it, or None."""
	try:
		return float(spin.get_text().strip().replace(",", "."))
	except ValueError:
		return None


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
		self.views = {}  # option name -> [(row, sync)] for every row showing it right now
		self._syncing = False  # set while sync() moves widgets, so that isn't taken as an edit
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
		self.rows = {}  # option name -> its row on the page right now, for jumping to it
		self._shown = None  # (query, section) the rows were built for
		self.append(self.page)

	# Data ------------------------------------------------------------------

	def load(self, state, keep_changes=False):
		old = self.changes if keep_changes else {}
		self.options, self.files = options.load(state)
		self.by_name = {o.name: o for o in self.options}
		# A kept change is dropped if the option became read-only meanwhile.
		# A kept change is dropped too once Hyprland already has that value.
		self.changes = {n: v for n, v in old.items() if n in self.by_name and not self.by_name[n].locked()
			and (v is options.RESET or not options.same_value(self.by_name[n].type, self.by_name[n].value, v))}
		self.views = {}
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
		self.show_rows(force=True)

	def show_section(self, name):
		if name in self.section_names:
			self.sections.select_row(self.sections.get_row_at_index(self.section_names.index(name)))

	def reveal(self, name):
		"""Shows the section holding option name with no search; returns its row (or None)."""
		option = self.by_name.get(name)
		if option is None:
			return None
		self.section = option.section
		self.search.set_text("")  # its delayed search-changed finds the rows already built
		self._filling = True
		self.show_section(option.section)
		self._filling = False
		self.show_rows()
		return self.rows.get(name)

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
		self.sync(option.name)
		self.on_change()

	def sync(self, name):
		"""Brings every row showing option name up to date with its value and pending change."""
		alive = [(row, sync) for row, sync in self.views.get(name, []) if row.get_root() is not None]
		self.views[name] = alive
		self._syncing = True
		try:
			for _row, sync in alive:
				sync()
		finally:
			self._syncing = False

	# Rows ------------------------------------------------------------------

	def show_rows(self, force=False):
		query = self.search.get_text().strip().lower()
		if not force and self.groups and self._shown == (query, self.section):
			return  # already showing these (a search box clearing late, or a repeat)
		self._shown = (query, self.section)
		for group in self.groups:
			self.page.remove(group)
		self.groups = []
		self.rows = {}
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
				self.rows[o.name] = self.row(o, full_name=bool(query))
				group.add(self.rows[o.name])
			self.page.add(group)
			self.groups.append(group)
		self.page.scroll_to_top()

	def shown(self, option):
		"""What a row shows: the pending value, the default for a pending reset, else Hyprland's value."""
		pending = self.changes.get(option.name)
		if pending is options.RESET:
			after, _where = options.reset_value(option)
			return option.value if after is None else after
		return option.value if pending is None else pending

	def row(self, option, full_name=False):
		"""A row for option. Several rows can show one option (here and in Search everything);
		every change re-syncs all of them, so none keeps an old value."""
		title = GLib.markup_escape_text(option.name if full_name else pretty(option.name))
		subtitle = GLib.markup_escape_text(option.description)
		locked = option.locked()
		value = self.shown(option)

		if locked:
			row = Adw.ActionRow(title=title, subtitle=subtitle, tooltip_text=locked)
			row.add_suffix(Gtk.Label(label=shown_value(option), css_classes=["dim-label"],
				ellipsize=3, max_width_chars=24, valign=Gtk.Align.CENTER))
			row.add_prefix(Gtk.Image(icon_name="changes-prevent-symbolic", css_classes=["dim-label"]))
			self.add_badge(row, option)
			return row

		def changed(new):
			if not self._syncing:
				self.set_change(option, new)

		default = options.default_value(option)
		if option.type == "bool":
			row = Adw.SwitchRow(title=title, subtitle=subtitle, active=bool(value))
			row.connect("notify::active", lambda r, _p: changed(r.get_active()))
			show = lambda v: row.set_active(bool(v))  # noqa: E731
		elif option.choices:
			labels = [label for label, _ in option.choices]
			values = [v for _, v in option.choices]
			for extra in (option.value, value, default):
				if extra is not None and extra not in values:
					labels.append(str(extra))
					values.append(extra)
			row = Adw.ComboRow(title=title, subtitle=subtitle, model=Gtk.StringList.new(labels),
				selected=values.index(value))
			row.connect("notify::selected", lambda r, _p: changed(values[r.get_selected()]))
			show = lambda v: row.set_selected(values.index(v))  # noqa: E731
		elif option.type in ("int", "float"):
			low, high = INT_RANGE if option.type == "int" else FLOAT_RANGE
			# Widen the range to the live, shown and default values so set_value never clamps them.
			known = [v for v in (option.value, value, default) if v is not None]
			low = min(option.min if option.min is not None else low, *known)
			high = max(option.max if option.max is not None else high, *known)
			span = high - low
			step = 1 if option.type == "int" else (0.05 if span <= 2 else 0.1 if span <= 20 else 1)
			adjustment = Gtk.Adjustment(lower=low, upper=high, step_increment=step, page_increment=step * 10)
			row = Adw.SpinRow(title=title, subtitle=subtitle, adjustment=adjustment,
				digits=0 if option.type == "int" else max(float_digits(v) for v in known))
			row.set_value(value)
			number = (lambda n: int(n)) if option.type == "int" else (lambda n: round(n, 6))
			row.connect("notify::value", lambda r, _p: changed(number(r.get_value())))
			# Typing only commits on Enter or leaving the box; count it as a change right away,
			# or Apply stays off while the box already shows the new number.
			row.connect("changed", lambda r: None if typed(r) is None else changed(number(typed(r))))

			def show(v):
				if typed(row) is None or not options.same_value(option.type, typed(row), v):
					row.set_value(v)
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
				spin.connect("notify::value", lambda _s, _p: changed([int(s.get_value()) for s in spins]))
				spin.connect("changed", lambda _s: changed([int(typed(s)) if typed(s) is not None else int(s.get_value())
					for s in spins]))
			summary = Gtk.Label(label=" ".join(str(v) for v in value), valign=Gtk.Align.CENTER)
			row.add_suffix(summary)

			def show(v):
				for spin, side in zip(spins, v):
					spin.get_adjustment().set_lower(min(spin.get_adjustment().get_lower(), side))
					spin.get_adjustment().set_upper(max(spin.get_adjustment().get_upper(), side))
					if typed(spin) != side:
						spin.set_value(side)
				summary.set_label(" ".join(str(s) for s in v))
		else:  # str
			row = Adw.ActionRow(title=title, subtitle=subtitle)
			entry = Gtk.Entry(text=value or "", valign=Gtk.Align.CENTER, width_chars=18)
			entry.connect("changed", lambda e: changed(e.get_text()))
			row.add_suffix(entry)

			def show(v):
				if entry.get_text() != (v or ""):  # don't move the cursor of the entry being typed in
					entry.set_text(v or "")

		update_badge = self.add_badge(row, option)

		def sync():
			show(self.shown(option))
			update_badge()

		self.views.setdefault(option.name, []).append((row, sync))
		return row

	def add_badge(self, row, option):
		"""Adds the source badge and Reset button; returns a function that brings both up to date."""
		badge = Gtk.Label(css_classes=["caption", "dim-label"], valign=Gtk.Align.CENTER, wrap=True,
			max_width_chars=16, justify=Gtk.Justification.RIGHT, xalign=1)
		row.add_suffix(badge)
		reset = None
		if option.set and not option.locked():
			after, where = options.reset_value(option)
			if after is None:
				after = option.default if where == "default" else "its value there"
			else:
				after = shown_value(replace(option, value=after))
			reset = Gtk.Button(label="Reset", valign=Gtk.Align.CENTER, css_classes=["flat", "caption"],
				tooltip_text=f"Remove the dashboard's value, so {where} applies ({after})" if where != "default"
				else f"Remove it from the config, so Hyprland uses its default ({after})")
			reset.connect("clicked", lambda _b: self.set_change(option, options.RESET))
			row.add_suffix(reset)

		def update():
			pending = self.changes.get(option.name)
			if pending is options.RESET:
				after, where = options.reset_value(option)
				if where == "default":
					text, accent = "reset to default on Apply", "accent"
					tip = "Apply removes it from the config; " + ("the value shown is the default" if after is not None
						else f"the value shown is the current one, the default is {option.default}")
				else:
					text, accent = f"reset to {where} on Apply", "accent"
					tip = f"Apply removes the dashboard's value, so {where} applies; " + (
						"the value shown is that one" if after is not None else "the value shown is the current one")
			elif pending is not None and (problem := options.check_value(option, options.normalize(option.type, pending))):
				text, accent, tip = f"can't apply: {problem}", "error", "Apply refuses this value; Hyprland keeps " + shown_value(option)
			elif pending is not None:
				text, accent, tip = "changed, not applied", "accent", f"Hyprland has {shown_value(option)} until you press Apply"
			else:
				text, accent, tip = option.source(), None, None
			badge.set_label(text)
			badge.set_tooltip_text(tip)
			for css in ("accent", "error"):
				if css == accent:
					badge.add_css_class(css)
				else:
					badge.remove_css_class(css)
			if reset is not None:
				reset.set_visible(pending is not options.RESET)

		update()
		return update

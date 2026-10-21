#!/usr/bin/env python3
"""hypr-dashboard: edit Hyprland settings in a GUI: options, monitors, keybinds,
window and workspace rules, animations, input devices, variables and startup.

The app owns dashboard.json / dashboard.lua (gaming apps and extra rules, see
rules.py) and dashboard_options.lua. In the hand-written files it only changes
literal values in top-level calls, and adds or removes whole calls (see
options.py, items.py, lists.py); anything built in loops or functions is shown
read-only.

Run with --page NAME (options, monitors, workspaces, binds, windowrules, rules,
gaming, animations, input, variables, startup) to open on that page: options/SECTION
for one options section, NAME?TEXT to search that page, search?TEXT to search
every page. Used for screenshots.
"""
import json
import os
import re
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

import backup  # noqa: E402
import hypr  # noqa: E402
import luasrc  # noqa: E402
import options  # noqa: E402
import review  # noqa: E402
import rules  # noqa: E402
from items_page import ItemsPages  # noqa: E402
from lists_page import ListsPages  # noqa: E402
from options_page import OptionsPage  # noqa: E402
from search import ResultsPage, SearchablePage, matches  # noqa: E402

NONE = "(none)"
APP_ID = "dev.hyprdashboard.HyprDashboard"
VERSION = "1.0.0"
TESTED_HYPRLAND = "0.56"


class Dashboard(Adw.ApplicationWindow):
	def __init__(self, app, page=None):
		super().__init__(application=app, title="Hyprland Dashboard", default_width=1100, default_height=780)
		problem = startup_problem()
		if problem:
			self.show_problem(*problem)
			return
		self.state = rules.load_state()
		self.saved = json.dumps(self.state)
		self.backup_error = ""
		self.rules_hooked, gaming_hooked = rules.hooks()
		self.clients = []
		self.monitors = []
		self.workspaces = []
		self.page_groups = {}
		self.refresh_hyprland()

		self.toasts = Adw.ToastOverlay()
		stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, hexpand=True)
		self.stack = stack
		self.gaming_page = Adw.PreferencesPage()
		self.rules_page = Adw.PreferencesPage()
		self.options_page = OptionsPage(self.mark_changed)
		self.items = ItemsPages(self.mark_changed)
		self.lists = ListsPages(self.mark_changed)
		self.items.after_rebuild = self.lists.after_rebuild = self.refilter
		pages = [
			("options", "Options", "preferences-system-symbolic", self.options_page),
			("monitors", "Monitors", "video-display-symbolic", self.items.monitors_page),
			("workspaces", "Workspaces", "view-grid-symbolic", self.lists.workspaces_page),
			("binds", "Keybinds", "input-keyboard-symbolic", self.lists.binds_page),
			("windowrules", "Window rules", "view-list-symbolic", self.lists.windowrules_page),
			("rules", "Extra rules", "list-add-symbolic", self.rules_page),
			("gaming", "Gaming apps", "input-gaming-symbolic", self.gaming_page),
			("animations", "Animations", "media-playback-start-symbolic", self.lists.animations_page),
			("input", "Gestures & devices", "input-touchpad-symbolic", self.lists.input_page),
			("variables", "Variables", "accessories-text-editor-symbolic", self.items.variables_page),
			("startup", "Startup", "system-run-symbolic", self.items.startup_page),
		]
		self.page_names = [name for name, *_ in pages]
		self.page_titles = {name: title for name, title, *_ in pages}
		self.searchables = {}
		nav = Gtk.ListBox(css_classes=["navigation-sidebar"])
		for name, title, icon, widget in pages:
			if name != "options":  # the Options page has its own search
				widget = self.searchables.setdefault(name, SearchablePage(widget, f"Search {title.lower()}"))
			stack.add_named(widget, name)
			box = Gtk.Box(spacing=10, margin_start=4, margin_top=2, margin_bottom=2)
			box.append(Gtk.Image(icon_name=icon))
			box.append(Gtk.Label(label=title, xalign=0))
			nav.append(Gtk.ListBoxRow(child=box, name=name))
		self.results = ResultsPage(self.open_page)
		stack.add_named(self.results, "search")
		nav.connect("row-selected", self.on_nav)
		self.nav = nav
		self.global_search = Gtk.SearchEntry(placeholder_text="Search everything", margin_start=8, margin_end=8,
			margin_top=8, margin_bottom=4)
		self.global_search.connect("search-changed", self.on_global_search)
		side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, width_request=200)
		side.append(self.global_search)
		side.append(Gtk.ScrolledWindow(child=nav, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER))
		body = Gtk.Box()
		body.append(side)
		body.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
		body.append(stack)

		header = Adw.HeaderBar(title_widget=Adw.WindowTitle(title="Hyprland Dashboard"))
		self.apply_button = Gtk.Button(label="Apply", css_classes=["suggested-action"], sensitive=False)
		self.apply_button.connect("clicked", self.on_apply)
		header.pack_end(self.apply_button)
		refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Refresh open windows and option values")
		refresh.connect("clicked", self.on_refresh)
		header.pack_start(refresh)

		view = Adw.ToolbarView(content=body)
		view.add_top_bar(header)
		self.toasts.set_child(view)
		self.set_content(self.toasts)
		self.load_options()
		self.load_items()
		self.rebuild()
		nav.select_row(nav.get_row_at_index(0))
		self.make_backup()
		self.check_version()
		# The gaming list only does something with a hand-written gaming rule that reads it.
		if not gaming_hooked and not self.state["gaming_apps"]:
			nav.get_row_at_index(self.page_names.index("gaming")).set_visible(False)
		if page:
			name, _, section = page.partition("/")
			name, _, query = name.partition("?")
			if name in self.page_names:
				nav.select_row(nav.get_row_at_index(self.page_names.index(name)))
			if section:
				self.options_page.show_section(section)
			if query and name == "search":
				self.global_search.set_text(query)
			elif query and name == "options":
				self.options_page.search.set_text(query)
			elif query and name in self.searchables:
				self.searchables[name].entry.set_text(query)

	def show_problem(self, title, body):
		"""Shown instead of the pages when the app can't work on this setup."""
		status = Adw.StatusPage(icon_name="dialog-warning-symbolic", title=title, description=body)
		view = Adw.ToolbarView(content=status)
		view.add_top_bar(Adw.HeaderBar())
		self.set_content(view)

	def check_version(self):
		try:
			version = hypr.hyprctl_json("version").get("version", "")
		except Exception:  # noqa: BLE001
			return
		if not version.startswith(TESTED_HYPRLAND + "."):
			self.toasts.add_toast(Adw.Toast(title=f"Made for Hyprland {TESTED_HYPRLAND}; you have {version}. "
				"Options or call formats may differ, so check changes after Apply.", timeout=10))

	def make_backup(self):
		"""Copies ~/.config/hypr into the app folder on first launch and after an update.

		If that fails, Apply stays off: the app never writes config it hasn't backed up.
		"""
		self.backup_error = ""
		try:
			path = backup.ensure()
		except Exception as e:  # noqa: BLE001
			self.backup_error = str(e)
			self.toasts.add_toast(Adw.Toast(title=f"Couldn't back up your config, so Apply is off: {e}", timeout=0))
			return
		if path:
			name = os.path.relpath(path, backup.APP_DIR)
			self.toasts.add_toast(Adw.Toast(title=f"Config backed up to {name}. restore.sh puts it back, even from a TTY.",
				timeout=12))

	# Search --------------------------------------------------------------

	def on_nav(self, _nav, row):
		if row is None:
			return
		if self.global_search.get_text():
			self.global_search.set_text("")  # picking a page ends the overall search
		self.stack.set_visible_child_name(row.get_name())

	def on_global_search(self, entry):
		query = entry.get_text().strip()
		if not query:
			row = self.nav.get_selected_row()
			self.stack.set_visible_child_name(row.get_name() if row else "options")
			return
		options = [(GLib.markup_escape_text(o.name), o.description) for o in self.options_page.options
			if matches(f"{o.name} {o.name.replace('_', ' ')} {o.description}", query)]
		sources = [("options", "Options", options)]
		sources += [(name, self.page_titles[name], page.results(query)) for name, page in self.searchables.items()]
		self.results.show(query, sources)
		self.stack.set_visible_child_name("search")

	def open_page(self, name, query):
		"""Opens a page from the overall search, filtered by the same words."""
		self.global_search.set_text("")
		self.nav.select_row(self.nav.get_row_at_index(self.page_names.index(name)))
		if name == "options":
			self.options_page.search.set_text(query)
		else:
			self.searchables[name].entry.set_text(query)

	def refilter(self):
		"""Rows are rebuilt after edits; filter them again."""
		for page in self.searchables.values():
			if page.entry.get_text():
				page.refilter()

	# Hyprland state -----------------------------------------------------

	def refresh_hyprland(self):
		try:
			self.clients = hypr.hyprctl_json("clients")
			self.monitors = hypr.hyprctl_json("monitors")
			ids = {str(w["id"]) for w in hypr.hyprctl_json("workspaces") if w["id"] > 0}
			ids |= {r["workspaceString"] for r in hypr.hyprctl_json("workspacerules")}
			self.workspaces = sorted(ids, key=lambda w: (not w.isdigit(), int(w) if w.isdigit() else 0, w))
		except Exception as e:  # noqa: BLE001 - keep the app usable without Hyprland
			print("hyprctl failed:", e, file=sys.stderr)

	def window_label(self, client):
		return f"{client['class']} — {client['title']}" if client["title"] else client["class"]

	def matching(self, test):
		names = [self.window_label(c) for c in self.clients if test(c)]
		return ", ".join(names) if names else "no open window"

	def load_options(self, keep_changes=False):
		try:
			self.options_page.load(self.state, keep_changes)
		except Exception as e:  # noqa: BLE001 - the other tabs still work
			print("reading options failed:", e, file=sys.stderr)

	def load_items(self):
		try:
			self.items.load(self.options_page.options)
			self.lists.load(self.items.model)
		except Exception as e:  # noqa: BLE001 - the other tabs still work
			import traceback
			traceback.print_exc()
			print("reading the list pages failed:", e, file=sys.stderr)

	def on_refresh(self, _button):
		"""Re-reads open windows, option values and the other tabs; unapplied edits are kept."""
		self.refresh_hyprland()
		self.load_options(keep_changes=True)
		if self.items.pending or self.lists.pending:
			# Pending edits point into the files as they were read; keep that view.
			self.toasts.add_toast(Adw.Toast(title="Only option values re-read: apply your other changes first"))
		else:
			self.load_items()
		self.rebuild()

	def mark_changed(self):
		pending = json.dumps(self.state) != self.saved or bool(self.options_page.changes) or bool(self.items.pending) \
			or bool(self.lists.pending)
		self.apply_button.set_sensitive(pending)

	# Pages ---------------------------------------------------------------

	def rebuild(self):
		for page, build in ((self.gaming_page, self.build_gaming), (self.rules_page, self.build_rules)):
			for group in self.page_groups.get(page, []):
				page.remove(group)
			self.page_groups[page] = build(page)
		self.mark_changed()
		if hasattr(self, "searchables"):
			self.refilter()

	def build_gaming(self, page):
		apps = self.state["gaming_apps"]
		group = Adw.PreferencesGroup(
			title="Gaming apps",
			description="Windows whose class matches one of these go to the gaming workspace "
			"and render opaque without blur. Each entry is a regex.")
		pick = Gtk.Button(label="Add from open window…", valign=Gtk.Align.CENTER)
		pick.connect("clicked", lambda _b: self.pick_window(self.add_gaming_app))
		group.set_header_suffix(pick)

		for i, app in enumerate(apps):
			row = Adw.ActionRow(title=GLib.markup_escape_text(app), subtitle=self.matching(
				lambda c, a=app: rules.field_matches(rules.gaming_regex([a]), c["class"])))
			delete = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"])
			delete.connect("clicked", lambda _b, i=i: (apps.pop(i), self.rebuild()))
			row.add_suffix(delete)
			group.add(row)

		entry = Adw.EntryRow(title="Add a class (regex)", show_apply_button=True)
		entry.connect("apply", lambda e: self.add_gaming_app(e.get_text()))
		group.add(entry)
		page.add(group)

		preview = Adw.PreferencesGroup(title="Matching right now")
		preview.add(Adw.ActionRow(title=self.matching(
			lambda c: rules.field_matches(rules.gaming_regex(apps), c["class"])) if apps else "no apps listed",
			title_lines=0))
		page.add(preview)
		return [group, preview]

	def add_gaming_app(self, text):
		text = text.strip()
		if not text:
			return
		error = rules.regex_error(text)
		if error:
			self.toasts.add_toast(Adw.Toast(title=f"Not a valid regex: {error}"))
			return
		if text not in self.state["gaming_apps"]:
			self.state["gaming_apps"].append(text)
		self.rebuild()

	def build_rules(self, page):
		group = Adw.PreferencesGroup(
			title="Window rules",
			description="Rules made in this app (kept in dashboard.lua). Your hand-written rules from "
			"windowrules.lua are on the Window rules page.")
		add = Gtk.Button(label="Add rule", valign=Gtk.Align.CENTER, css_classes=["suggested-action"])
		add.connect("clicked", self.on_add_rule)
		group.set_header_suffix(add)
		groups = []
		if not self.rules_hooked:
			hook = Adw.PreferencesGroup(title="Not connected yet",
				description="Nothing in your config reads these rules, so they would do nothing. Connect adds "
				"three lines to the end of hyprland.lua that load them (backed up like any Apply).")
			connect = Gtk.Button(label="Connect", valign=Gtk.Align.CENTER, css_classes=["suggested-action"])
			connect.connect("clicked", self.on_connect_rules)
			hook.set_header_suffix(connect)
			page.add(hook)
			groups.append(hook)
		if not self.state["rules"]:
			group.add(Adw.ActionRow(title="No rules yet", subtitle="Add one, then pick a window to match."))
		for rule in self.state["rules"]:
			group.add(self.rule_row(rule))
		page.add(group)
		return groups + [group]

	def rule_summary(self, rule):
		what = rule["name"] or next((v for v in rule["match"].values() if v), "New rule")
		actions = []
		if rule["workspace"]:
			actions.append(f"workspace {rule['workspace']}")
		if rule["monitor"]:
			actions.append(self.monitor_label(rule["monitor"]))
		actions += [label.split(" (")[0].lower() for flag, label in rules.FLAGS if flag in rule["flags"]]
		return GLib.markup_escape_text(what), GLib.markup_escape_text(" · ".join(actions) or "does nothing yet")

	def rule_row(self, rule):
		title, subtitle = self.rule_summary(rule)
		row = Adw.ExpanderRow(title=title, subtitle=subtitle, show_enable_switch=True,
			enable_expansion=rule.get("enabled", True), expanded=not any(rule["match"].values()))

		def changed(*_):
			row.set_title(self.rule_summary(rule)[0])
			row.set_subtitle(self.rule_summary(rule)[1])
			preview.set_title(self.matching(lambda c: rules.rule_matches(rule, c)))
			self.mark_changed()

		def toggled(r, _pspec):
			rule["enabled"] = r.get_enable_expansion()
			self.mark_changed()
		row.connect("notify::enable-expansion", toggled)

		name = Adw.EntryRow(title="Name (optional)", text=rule["name"])
		name.connect("changed", lambda e: (rule.__setitem__("name", e.get_text()), changed()))
		row.add_row(name)

		for field in rules.MATCH_FIELDS:
			entry = Adw.EntryRow(title=f"Match {field.replace('_', ' ')} (regex)", text=rule["match"].get(field, ""))
			if field == "class":
				pick = Gtk.Button(icon_name="find-location-symbolic", tooltip_text="Pick an open window",
					valign=Gtk.Align.CENTER, css_classes=["flat"])
				pick.connect("clicked", lambda _b, e=entry: self.pick_window(
					lambda cls, e=e: e.set_text("^(" + re.escape(cls) + ")$")))
				entry.add_suffix(pick)

			def on_text(e, field=field):
				text = e.get_text()
				if text:
					rule["match"][field] = text
				else:
					rule["match"].pop(field, None)
				if rules.regex_error(text):
					e.add_css_class("error")
				else:
					e.remove_css_class("error")
				changed()
			entry.connect("changed", on_text)
			row.add_row(entry)

		workspace = self.combo("Move to workspace", [NONE] + self.workspaces, rule["workspace"] or NONE,
			lambda value: (rule.__setitem__("workspace", "" if value == NONE else value), changed()))
		row.add_row(workspace)

		monitor_values = [NONE] + [f"desc:{m['description']}" for m in self.monitors]
		if rule["monitor"] and rule["monitor"] not in monitor_values:
			monitor_values.append(rule["monitor"])
		monitor = self.combo("Move to monitor", monitor_values, rule["monitor"] or NONE,
			lambda value: (rule.__setitem__("monitor", "" if value == NONE else value), changed()),
			label=self.monitor_label)
		row.add_row(monitor)

		for flag, label in rules.FLAGS:
			switch = Adw.SwitchRow(title=label, active=flag in rule["flags"])

			def on_switch(s, _pspec, flag=flag):
				if s.get_active() and flag not in rule["flags"]:
					rule["flags"].append(flag)
				elif not s.get_active() and flag in rule["flags"]:
					rule["flags"].remove(flag)
				changed()
			switch.connect("notify::active", on_switch)
			row.add_row(switch)

		preview = Adw.ActionRow(title=self.matching(lambda c: rules.rule_matches(rule, c)),
			subtitle="Matching right now", title_lines=0, css_classes=["property"])
		row.add_row(preview)

		delete = Adw.ButtonRow(title="Delete rule", css_classes=["destructive-action"])
		delete.connect("activated", lambda _r: (self.state["rules"].remove(rule), self.rebuild()))
		row.add_row(delete)
		return row

	def monitor_label(self, value):
		if value == NONE:
			return value
		for m in self.monitors:
			if value == f"desc:{m['description']}":
				return f"{m['name']} · {m['description']}"
		return value

	def combo(self, title, values, current, on_change, label=str):
		model = Gtk.StringList.new([label(v) for v in values])
		row = Adw.ComboRow(title=title, model=model, selected=values.index(current) if current in values else 0)
		row.connect("notify::selected", lambda r, _p: on_change(values[r.get_selected()]))
		return row

	def on_connect_rules(self, _button):
		"""Appends the rules hook to the entry file in one checked, backed-up Apply."""
		with open(luasrc.ENTRY) as f:
			entry = f.read()
		texts = {rules.LUA_FILE: rules.to_lua(self.state), luasrc.ENTRY: entry.rstrip("\n") + "\n" + rules.RULES_HOOK}
		before = {path: hypr._read(path) for path in texts}

		def write():
			if any(hypr._read(path) != old for path, old in before.items()):
				self.toasts.add_toast(Adw.Toast(title="A file changed after the review; press Connect again"))
				return
			errors = hypr.apply_files(texts)
			if errors:
				self.toasts.add_toast(Adw.Toast(title=f"Hyprland rejected it, nothing changed: {errors[0]}", timeout=8))
				return
			self.rules_hooked = True
			self.rebuild()
			self.toasts.add_toast(Adw.Toast(title="Connected: rules made here now apply."))
		changed = {p: (before[p], t) for p, t in texts.items() if t != before[p]}
		review.show(self, changed, write, title="Connect extra rules")

	def on_add_rule(self, _button):
		self.state["rules"].append(rules.new_rule())
		self.rebuild()

	# Window picker -----------------------------------------------------------

	def pick_window(self, on_pick):
		self.refresh_hyprland()
		dialog = Adw.Dialog(title="Pick a window", content_width=520, content_height=480)
		listbox = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
		for client in sorted(self.clients, key=lambda c: c["class"].lower()):
			if not client["class"]:
				continue
			row = Adw.ActionRow(title=GLib.markup_escape_text(client["class"]),
				subtitle=GLib.markup_escape_text(client["title"]), activatable=True)
			row.connect("activated", lambda _r, c=client: (dialog.close(), on_pick(c["class"])))
			listbox.append(row)
		scroll = Gtk.ScrolledWindow(child=Adw.Clamp(child=listbox, margin_top=12, margin_bottom=12,
			margin_start=12, margin_end=12), vexpand=True)
		view = Adw.ToolbarView(content=scroll)
		view.add_top_bar(Adw.HeaderBar())
		dialog.set_child(view)
		dialog.present(self)

	# Apply -------------------------------------------------------------------

	def on_apply(self, _button):
		if self.backup_error:
			self.toasts.add_toast(Adw.Toast(title=f"Apply is off until the config backup works: {self.backup_error}"))
			return
		bad = [p for p in self.state["gaming_apps"] if rules.regex_error(p)]
		bad += [p for r in self.state["rules"] for p in r["match"].values() if rules.regex_error(p)]
		if bad:
			self.toasts.add_toast(Adw.Toast(title=f"Fix the invalid regex first: {bad[0]}"))
			return
		try:
			page = self.options_page
			prepared = options.prepare(self.state, page.options, page.files, page.changes,
				[self.items.pending, self.lists.pending])
		except ValueError as e:
			self.toasts.add_toast(Adw.Toast(title=str(e), timeout=8))
			return
		except Exception as e:  # noqa: BLE001
			self.toasts.add_toast(Adw.Toast(title=f"Apply failed: {e}"))
			return
		changed = prepared.changed()
		if not changed:
			self.toasts.add_toast(Adw.Toast(title="Nothing to change in any file."))
			return
		review.show(self, changed, lambda: self.commit(prepared))

	def commit(self, prepared):
		"""Writes a reviewed Apply."""
		monitors_changed = self.items.pending.touches_monitors()
		try:
			errors = options.commit(self.state, prepared)
		except ValueError as e:
			self.toasts.add_toast(Adw.Toast(title=str(e), timeout=8))
			return
		except Exception as e:  # noqa: BLE001
			self.toasts.add_toast(Adw.Toast(title=f"Apply failed: {e}"))
			return
		if errors:
			self.toasts.add_toast(Adw.Toast(title=f"Hyprland rejected it, nothing changed: {errors[0]}", timeout=8))
			return
		self.after_apply()
		if monitors_changed and hypr.last_before:
			self.confirm_monitors()
		else:
			self.toasts.add_toast(Adw.Toast(title="Applied."))

	def after_apply(self):
		self.state = rules.load_state()
		self.saved = json.dumps(self.state)
		self.refresh_hyprland()
		self.load_options()
		self.load_items()
		self.rebuild()

	def confirm_monitors(self, seconds=15):
		"""Asks to keep new monitor settings; undoes the whole Apply when the time runs out.

		The timer runs in the app, so a screen that went black still comes back.
		"""
		left = [seconds]
		dialog = Adw.AlertDialog(heading="Keep these display settings?",
			body=f"Going back to the previous settings in {seconds} seconds.")
		dialog.add_response("revert", "Revert")
		dialog.add_response("keep", "Keep")
		dialog.set_response_appearance("keep", Adw.ResponseAppearance.SUGGESTED)
		dialog.set_default_response("keep")
		dialog.set_close_response("revert")
		done = []

		def finish(keep, from_timer=False):
			if done:
				return
			done.append(1)
			if not from_timer:
				GLib.source_remove(timer)
			dialog.close()
			if keep:
				self.toasts.add_toast(Adw.Toast(title="Applied."))
				return
			errors = hypr.undo()
			self.after_apply()
			self.toasts.add_toast(Adw.Toast(title="Went back to the previous settings." if not errors
				else f"Going back failed: {errors[0]}", timeout=8))

		def tick():
			left[0] -= 1
			if left[0] <= 0:
				finish(False, from_timer=True)
				return False
			dialog.set_body(f"Going back to the previous settings in {left[0]} seconds.")
			return True

		timer = GLib.timeout_add_seconds(1, tick)
		dialog.connect("response", lambda _d, response: finish(response == "keep"))
		dialog.present(self)


def startup_problem():
	"""(title, text) when the app can't work here, else None."""
	if not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
		return ("Hyprland isn't running", "Start this app from inside a Hyprland session. "
			f"To put back a config backup from a TTY, run {backup.APP_DIR}/restore.sh")
	if not os.path.exists(luasrc.ENTRY):
		conf = os.path.join(luasrc.HYPR_DIR, "hyprland.conf")
		extra = " Your config is hyprland.conf (the older format), which this app can't edit." if os.path.exists(conf) else ""
		return ("No Lua config found", f"This app edits Hyprland's Lua config, {luasrc.ENTRY}.{extra}")
	try:
		hypr.hyprctl_json("version")
	except Exception as e:  # noqa: BLE001
		return ("Can't talk to Hyprland", f"hyprctl failed: {e}")
	return None


class App(Adw.Application):
	def __init__(self, page=None):
		# A --page launch (screenshots, tests) gets its own process instead of
		# raising a window that is already open.
		flags = Gio.ApplicationFlags.NON_UNIQUE if page else Gio.ApplicationFlags.DEFAULT_FLAGS
		super().__init__(application_id=APP_ID, flags=flags)
		self.page = page

	def do_activate(self):
		window = self.props.active_window or Dashboard(self, self.page)
		window.present()


if __name__ == "__main__":
	page = sys.argv[sys.argv.index("--page") + 1] if "--page" in sys.argv[:-1] else None
	sys.exit(App(page).run(sys.argv[:1]))

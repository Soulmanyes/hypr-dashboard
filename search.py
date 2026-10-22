"""Search: a filter box on each page, and one search over every page.

A row matches when the query appears in any text it shows (titles,
subtitles, labels, entry contents), including the rows inside an expander.
"""
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, GLib, Graphene, Gtk  # noqa: E402

RESULT_LIMIT = 150


def _children(widget):
	child = widget.get_first_child()
	while child:
		yield child
		child = child.get_next_sibling()


def _walk(widget):
	yield widget
	for child in _children(widget):
		if not isinstance(child, Gtk.Popover):  # dropdown lists aren't what the row shows
			yield from _walk(child)


def top_rows(widget):
	"""Rows inside widget that aren't inside another row."""
	for child in _children(widget):
		if isinstance(child, Adw.PreferencesRow):
			yield child
		else:
			yield from top_rows(child)


def groups(page):
	return [w for w in _walk(page) if isinstance(w, Adw.PreferencesGroup)]


def texts(widget):
	"""Every piece of text a widget shows."""
	out = []
	for w in _walk(widget):
		if isinstance(w, Gtk.Label):
			out.append(w.get_text())
		elif isinstance(w, Gtk.Editable) and not isinstance(w, Gtk.Text):
			out.append(w.get_text())
	return " ".join(t for t in out if t)


def matches(text, query):
	"""Every word of the query appears somewhere in text (case-insensitive)."""
	text = text.lower()
	return all(word in text for word in query.lower().split())


def row_title(row):
	labels = [w.get_text() for w in _walk(row) if isinstance(w, Gtk.Label) and w.get_text()]
	title = row.get_title() if row.get_title() else (labels[0] if labels else "")
	if isinstance(row, Adw.EntryRow) and row.get_text():
		title = f"{row.get_title()}: {row.get_text()}" if row.get_title() else row.get_text()
	return GLib.markup_escape_text(_plain(title))


def _plain(markup):
	"""Row titles are set as escaped markup; get the plain text back."""
	try:
		return Gtk.Label(label=markup, use_markup=True).get_text()
	except Exception:  # noqa: BLE001
		return markup


class SearchablePage(Gtk.Box):
	"""A PreferencesPage with a filter box above it."""

	def __init__(self, page, placeholder):
		super().__init__(orientation=Gtk.Orientation.VERTICAL)
		self.page = page
		self.entry = Gtk.SearchEntry(placeholder_text=placeholder, hexpand=True)
		self.entry.connect("search-changed", lambda _e: self.refilter())
		clamp = Adw.Clamp(child=self.entry, maximum_size=576, margin_top=10, margin_bottom=2, margin_start=12,
			margin_end=12)
		self.append(clamp)
		page.set_vexpand(True)
		self.append(page)

	def refilter(self):
		query = self.entry.get_text().strip()
		for group in groups(self.page):
			rows = list(top_rows(group))
			group_hit = bool(query) and matches(f"{group.get_title()} {group.get_description() or ''}", query)
			shown = 0
			for row in rows:
				visible = not query or group_hit or matches(texts(row), query)
				row.set_visible(visible)
				if visible and query and isinstance(row, Adw.ExpanderRow) and not group_hit \
						and not matches(f"{_plain(row.get_title())} {_plain(row.get_subtitle() or '')}", query):
					row.set_expanded(True)  # the hit is inside: show it
				shown += visible
			group.set_visible(not query or shown > 0 or (group_hit and not rows))

	def results(self, query):
		"""(title, subtitle, row) for every row on this page that matches."""
		out = []
		for group in groups(self.page):
			group_title = _plain(group.get_title() or "")
			for row in top_rows(group):
				if matches(f"{group_title} {texts(row)}", query):
					subtitle = _plain(row.get_subtitle() or "") if hasattr(row, "get_subtitle") else ""
					out.append((row_title(row), " · ".join(x for x in (group_title, subtitle) if x), row))
		return out

	def show_all(self):
		"""Clears the filter right away (not after the search box's delay)."""
		self.entry.set_text("")
		self.refilter()


class ResultsPage(Adw.PreferencesPage):
	"""Matches from every page, grouped by page.

	Options come as their real controls (change them right here); every result
	has a jump that opens the page at that setting and highlights it.
	"""

	def __init__(self):
		super().__init__()
		self.shown = []

	def show(self, query, sources):
		"""sources: [(page title, [hit])], a hit being (title, subtitle, jump, live row or None)."""
		for group in self.shown:
			self.remove(group)
		self.shown = []
		total = 0
		for page_title, hits in sources:
			if not hits:
				continue
			group = Adw.PreferencesGroup(title=GLib.markup_escape_text(f"{page_title} ({len(hits)})"))
			for title, subtitle, jump, live in hits[:max(0, RESULT_LIMIT - total)]:
				if live is not None:
					go = Gtk.Button(icon_name="find-location-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"],
						tooltip_text="Show where it is")
					go.connect("clicked", lambda _b, jump=jump: jump())
					live.add_suffix(go)
					group.add(live)
					continue
				row = Adw.ActionRow(title=title or "(untitled)", subtitle=GLib.markup_escape_text(subtitle),
					activatable=True, subtitle_lines=2, tooltip_text="Show where it is")
				row.add_suffix(Gtk.Image(icon_name="find-location-symbolic"))
				row.connect("activated", lambda _r, jump=jump: jump())
				group.add(row)
			total += len(hits)
			self.add(group)
			self.shown.append(group)
		if not self.shown:
			group = Adw.PreferencesGroup(title="Nothing matches",
				description=GLib.markup_escape_text(f"No setting on any page mentions “{query}”."))
			self.add(group)
			self.shown.append(group)
		self.scroll_to_top()


HIGHLIGHT_CSS = """
@keyframes jump-flash {
	from { background-color: alpha(@accent_bg_color, 0.55); }
	to { background-color: alpha(@accent_bg_color, 0); }
}
.jump-highlight { animation: jump-flash 2.4s ease-out; }
/* SpinRow numbers are borderless; a box shows they can be typed into. */
row.spin spinbutton > text {
	border: 1px solid alpha(currentColor, 0.25);
	border-radius: 6px;
	padding: 0 6px;
	margin: 8px 4px;
}
row.spin spinbutton > text:focus-within { border-color: @accent_color; }
"""


def load_css():
	provider = Gtk.CssProvider()
	provider.load_from_string(HIGHLIGHT_CSS)
	Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
		Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def scroll_and_flash(row):
	"""Scrolls row into the middle of its page and flashes it."""
	scroller = row.get_ancestor(Gtk.ScrolledWindow)
	if scroller is not None:
		content = scroller.get_child()
		if isinstance(content, Gtk.Viewport):
			content = content.get_child()
		ok, point = row.compute_point(content, Graphene.Point())
		if ok:
			adj = scroller.get_vadjustment()
			target = point.y - (adj.get_page_size() - row.get_height()) / 3
			adj.set_value(max(adj.get_lower(), min(target, adj.get_upper() - adj.get_page_size())))
	row.remove_css_class("jump-highlight")  # restart the animation if it's still running
	row.add_css_class("jump-highlight")
	GLib.timeout_add(2600, lambda: row.remove_css_class("jump-highlight") or False)
	return False

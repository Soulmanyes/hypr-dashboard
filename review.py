"""The "Review changes" dialog shown before an Apply writes anything.

One section per file, with the changed lines and a little context around
them (removed lines in red, added in green), and Apply / Cancel buttons.
"""
import difflib
import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

import hypr  # noqa: E402
import rules  # noqa: E402

CONTEXT = 2
# The app's own bookkeeping: shown last and collapsed, since it mirrors the other changes.
BOOKKEEPING = {rules.STATE_FILE}


def file_diff(old, new, context=CONTEXT):
	"""Unified diff lines (without the ---/+++ header) from old to new text."""
	lines = difflib.unified_diff((old or "").splitlines(), (new or "").splitlines(), lineterm="", n=context)
	return [line for line in lines if not line.startswith(("---", "+++"))]


def summary(old, new):
	diff = file_diff(old, new, 0)
	added = sum(1 for line in diff if line.startswith("+"))
	removed = sum(1 for line in diff if line.startswith("-"))
	if old is None:
		return f"new file, {added} lines"
	if new is None:
		return "deleted"
	return f"{added} added, {removed} removed"


def diff_view(old, new):
	view = Gtk.TextView(editable=False, cursor_visible=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR,
		top_margin=8, bottom_margin=8, left_margin=10, right_margin=10)
	buf = view.get_buffer()
	tags = {
		"+": buf.create_tag("added", foreground="#2ec27e"),
		"-": buf.create_tag("removed", foreground="#e01b24"),
		"@": buf.create_tag("hunk", foreground="#3584e4"),
	}
	for line in file_diff(old, new):
		tag = tags.get(line[:1])
		if line.startswith("@@"):
			line = "⋯ line " + line.split("+")[1].split(",")[0].split(" ")[0]
		end = buf.get_end_iter()
		if tag:
			buf.insert_with_tags(end, line + "\n", tag)
		else:
			buf.insert(end, line + "\n")
	return view


def show(parent, changed, on_confirm, title="Review changes"):
	"""changed: {path: (old text, new text)}. Calls on_confirm() only if the user presses Apply."""
	dialog = Adw.Dialog(title=title, content_width=820, content_height=640)
	box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=12, margin_bottom=12,
		margin_start=12, margin_end=12)
	box.append(Gtk.Label(label="Nothing is written until you press Apply. Removed lines are red, added lines green.",
		wrap=True, xalign=0, css_classes=["dim-label"]))
	order = sorted(changed, key=lambda p: (p in BOOKKEEPING, p))
	for path in order:
		old, new = changed[path]
		name = os.path.relpath(path, hypr.HYPR_DIR)
		expander = Gtk.Expander(expanded=path not in BOOKKEEPING, use_markup=True,
			label=f"<b>{GLib.markup_escape_text(name)}</b>  <span alpha='60%'>{GLib.markup_escape_text(summary(old, new))}</span>")
		frame = Gtk.Frame(child=diff_view(old, new), css_classes=["view"], margin_top=6)
		expander.set_child(frame)
		box.append(expander)

	cancel = Gtk.Button(label="Cancel")
	apply = Gtk.Button(label=f"Apply {len(changed)} file{'s' if len(changed) != 1 else ''}", css_classes=["suggested-action"])
	cancel.connect("clicked", lambda _b: dialog.close())

	def confirm(_b):
		dialog.close()
		on_confirm()
	apply.connect("clicked", confirm)
	header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
	header.pack_start(cancel)
	header.pack_end(apply)
	view = Adw.ToolbarView(content=Gtk.ScrolledWindow(child=box, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER))
	view.add_top_bar(header)
	dialog.set_child(view)
	dialog.set_default_widget(apply)
	dialog.present(parent)
	return dialog

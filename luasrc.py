"""Reads and edits hand-written Hyprland Lua config without disturbing it.

Files are split into tokens that join back into the exact original text. The
hl.config({...}) calls are parsed into option sites: where each option is set,
and whether its value is a plain literal that can be replaced in place.
Anything computed (a variable, a concatenation, a function call) is read-only.
"""
import os
import re
from dataclasses import dataclass, field

HYPR_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "hypr")
ENTRY = os.path.join(HYPR_DIR, "hyprland.lua")
# Files that other tools generate; their values are shown but never edited.
MANAGED = {"noctalia.lua": "Noctalia theme"}

_TOKEN = re.compile(r"""
	(?P<ws>\s+)
	| (?P<comment>--(?:\[(?P<cq>=*)\[[\s\S]*?\](?P=cq)\]|[^\n]*))
	| (?P<longstr>\[(?P<lq>=*)\[[\s\S]*?\](?P=lq)\])
	| (?P<str>"(?:\\[\s\S]|[^"\\\n])*"|'(?:\\[\s\S]|[^'\\\n])*')
	| (?P<num>0[xX][0-9a-fA-F.]+(?:[pP][+-]?\d+)?|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)
	| (?P<name>[A-Za-z_][A-Za-z0-9_]*)
	| (?P<op>\.\.\.|\.\.|==|~=|<=|>=|<<|>>|//|::|[-+*/%^\#&~|<>=(){}\[\];:,.])
""", re.VERBOSE)


@dataclass
class Token:
	kind: str
	text: str
	start: int
	end: int


def tokenize(text):
	tokens = []
	pos = 0
	while pos < len(text):
		m = _TOKEN.match(text, pos)
		if not m:
			raise ValueError(f"cannot read Lua at offset {pos}: {text[pos:pos + 20]!r}")
		kind = next(k for k in ("ws", "comment", "longstr", "str", "num", "name", "op") if m.group(k) is not None)
		tokens.append(Token(kind, m.group(0), pos, m.end()))
		pos = m.end()
	return tokens


@dataclass
class Site:
	"""One place an option is set."""
	name: str  # Lua path joined with dots, e.g. "general.col.active_border"
	file: str
	line: int
	start: int  # value span in the file text
	end: int
	text: str  # value source text
	literal: bool  # plain number/string/bool, safe to replace
	order: int  # load order: higher wins
	field_start: int = 0  # whole "key = value," span, for removing it
	field_end: int = 0
	managed: str = ""  # set when another tool owns the file


@dataclass
class SourceFile:
	path: str
	text: str
	tokens: list
	sites: list = field(default_factory=list)
	locals: dict = field(default_factory=dict)  # top-level `local NAME = literal`, name -> Global


def _code(tokens):
	"""Indexes of non-whitespace, non-comment tokens."""
	return [i for i, t in enumerate(tokens) if t.kind not in ("ws", "comment")]


def _skip_expr(tokens, code, k):
	"""Advances past one expression inside a table constructor; returns the new code index."""
	depth = 0
	while k < len(code):
		t = tokens[code[k]]
		if t.text in ("(", "{", "["):
			depth += 1
		elif t.text in (")", "}", "]"):
			if depth == 0:
				return k
			depth -= 1
		elif t.text in (",", ";") and depth == 0:
			return k
		k += 1
	return k


def _parse_table(src, code, k, path, order_base, out, known):
	"""Parses the table constructor starting at code[k] == "{"; returns the index after "}"."""
	tokens = src.tokens
	k += 1
	while k < len(code) and tokens[code[k]].text != "}":
		first = code[k]
		name = None
		if tokens[code[k]].kind == "name" and k + 1 < len(code) and tokens[code[k + 1]].text == "=" \
				and (k + 2 >= len(code) or tokens[code[k + 2]].text != "="):
			name = tokens[code[k]].text
			k += 2
		vstart = k
		vend = _skip_expr(tokens, code, k)
		value_tokens = [tokens[code[i]] for i in range(vstart, vend)]
		field_end_k = vend + 1 if vend < len(code) and tokens[code[vend]].text in (",", ";") else vend
		if name is not None and value_tokens:
			full = path + [name]
			dotted = ".".join(full)
			is_table = value_tokens[0].text == "{"
			if is_table and dotted not in known:
				_parse_table(src, code, vstart, full, order_base, out, known)
			else:
				start, end = value_tokens[0].start, value_tokens[-1].end
				literal = _is_literal(value_tokens)
				fs = tokens[first].start
				fe = tokens[code[field_end_k - 1]].end if field_end_k > vstart else end
				out.append(Site(dotted, src.path, src.text.count("\n", 0, start) + 1, start, end,
					src.text[start:end], literal, order_base + len(out), fs, fe))
		k = vend
		if k < len(code) and tokens[code[k]].text in (",", ";"):
			k += 1
	return k + 1


def _is_literal(value_tokens):
	kinds = [t.kind for t in value_tokens]
	if kinds in (["num"], ["str"]):
		return True
	if kinds == ["name"] and value_tokens[0].text in ("true", "false"):
		return True
	return len(value_tokens) == 2 and value_tokens[0].text == "-" and kinds[1] == "num"


def parse_file(path, order_base, known):
	with open(path) as f:
		text = f.read()
	src = SourceFile(path, text, tokenize(text))
	tokens = src.tokens
	code = _code(tokens)
	for k in range(len(code) - 3):
		a, b, c, d = (tokens[code[k + i]] for i in range(4))
		if a.text == "hl" and b.text == "." and c.text == "config" and d.text == "(" \
				and k + 4 < len(code) and tokens[code[k + 4]].text == "{":
			_parse_table(src, code, k + 4, [], order_base + len(src.sites) * 0, src.sites, known)
	managed = MANAGED.get(os.path.basename(path), "")
	for i, site in enumerate(src.sites):
		site.order = order_base + i
		site.managed = managed
	return src


def load_order():
	"""Config files in the order Hyprland runs them, following require() calls from the entry file."""
	order = []

	def visit(path):
		if path in order or not os.path.exists(path):
			return
		order.append(path)
		with open(path) as f:
			text = f.read()
		code = _strip_comments(text)
		# require("a.b"), require 'a.b', require "a.b" and dofile("/path/file.lua"), in order.
		for m in re.finditer(r"""\b(require|dofile)\s*\(?\s*(["'])([^"'\n]+)\2""", code):
			kind, name = m.group(1), m.group(3)
			if kind == "dofile":
				visit(name if os.path.isabs(name) else os.path.join(HYPR_DIR, name))
				continue
			base = os.path.join(HYPR_DIR, *name.split("."))
			visit(base + ".lua" if os.path.exists(base + ".lua") else os.path.join(base, "init.lua"))

	visit(ENTRY)
	return order


def loaded_or_entry(path):
	"""path if Hyprland loads it, else the entry file: new statements must land somewhere that runs."""
	return path if path in load_order() else ENTRY


def _strip_comments(text):
	return "".join(t.text for t in tokenize(text) if t.kind != "comment")


def scan(known):
	"""Parses every loaded file. Returns {path: SourceFile} and {option: [sites, load order]}."""
	files = {}
	by_option = {}
	for n, path in enumerate(load_order()):
		src = parse_file(path, n * 100000, known)
		files[path] = src
		for site in src.sites:
			by_option.setdefault(site.name, []).append(site)
	for sites in by_option.values():
		sites.sort(key=lambda s: s.order)
	return files, by_option


def lua_string(value):
	return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def remove_field(text, site):
	"""Removes "key = value," and, if that leaves an empty line, the whole line."""
	return remove_span(text, site.field_start, site.field_end)


def remove_span(text, start, end):
	"""Removes text[start:end] and, if that leaves an empty line, the whole line."""
	line_start = text.rfind("\n", 0, start) + 1
	line_end = text.find("\n", end)
	line_end = len(text) if line_end < 0 else line_end
	if not text[line_start:start].strip() and not text[end:line_end].strip():
		return text[:line_start] + text[line_end + 1:]
	return text[:start] + text[end:].lstrip(" ")


# Calls and globals ----------------------------------------------------------
#
# Phase 2 items are top-level statements: hl.monitor({...}), hl.env("K", "V"),
# NAME = "value", and hl.exec_cmd("...") directly inside
# hl.on("hyprland.start", function () ... end). Anything inside another block
# (if, loop, function) is marked nested and treated as read-only.

DISABLED_MARK = "-- hypr-dashboard off: "
_OPENERS = {"function", "if", "do", "repeat"}  # while/for open with their "do"
_CLOSERS = {"end", "until"}
# A token before one of these ends a value, so what follows starts a statement.
_NOT_STATEMENT_START = {"=", "(", ",", "..", "{", "[", ".", ":", "local", "return", "and", "or", "not",
	"for", "function", "in", "+", "-", "*", "/", "%", "^", "#", "==", "~=", "<", ">", "<=", ">=", "//"}
_BINARY = {"..", "+", "-", "*", "/", "%", "^", "==", "~=", "<", ">", "<=", ">=", "//", "and", "or", "(", "[", ".", ":"}


@dataclass
class Arg:
	start: int
	end: int
	text: str
	literal: bool
	fields: list = None  # [Site] when the argument is a table constructor
	close: int = 0  # offset of the table's closing brace


@dataclass
class Call:
	fn: str  # "monitor", "env", "exec_cmd", ...
	file: str
	line: int
	start: int  # whole statement
	end: int
	args: list
	event: str = ""  # "hyprland.start" when directly inside that handler
	nested: bool = False  # inside some other block: read-only
	disabled: bool = False  # a DISABLED_MARK comment; start/end cover the comment


@dataclass
class Global:
	name: str
	file: str
	line: int
	start: int  # value span
	end: int
	text: str
	literal: bool
	ref: str = ""  # set when the value is just another variable's name (PRIMARY_MONITOR = MONITOR1)


@dataclass
class Handler:
	event: str
	file: str
	end: int  # offset of the "end" that closes the handler function


def _literal_value(tokens):
	"""Lua literal tokens as a Python value."""
	text = "".join(t.text for t in tokens)
	if text in ("true", "false"):
		return text == "true"
	if tokens[0].kind == "str":
		return _unquote(tokens[0].text)
	number = float(text)
	return int(number) if re.fullmatch(r"-?\d+", text) else number


def _unquote(text):
	body = text[1:-1]
	escapes = {"n": "\n", "t": "\t", "\\": "\\", '"': '"', "'": "'"}
	return re.sub(r"\\(.)", lambda m: escapes.get(m.group(1), m.group(1)), body)


def literal_value(arg_or_site):
	return _literal_value(tokenize(arg_or_site.text))


def _table_fields(src, code, k):
	"""Flat fields of the table constructor at code[k]; returns (fields, close offset)."""
	out = []
	_parse_table(src, code, k, [], 0, out, _AllKnown())
	end_k = k
	depth = 0
	while end_k < len(code):
		t = src.tokens[code[end_k]].text
		depth += t in ("(", "{", "[")
		depth -= t in (")", "}", "]")
		if depth == 0:
			break
		end_k += 1
	return out, src.tokens[code[end_k]].start


class _AllKnown:
	"""Makes _parse_table stop at the first level: every key counts as a leaf."""
	def __contains__(self, _name):
		return True


def _call_args(src, code, k):
	"""Arguments of the call whose "(" is at code[k]; returns (args, index after ")")."""
	tokens = src.tokens
	args = []
	k += 1
	while k < len(code) and tokens[code[k]].text != ")":
		end = _skip_expr(tokens, code, k)
		value = [tokens[code[i]] for i in range(k, end)]
		arg = Arg(value[0].start, value[-1].end, src.text[value[0].start:value[-1].end], _is_literal(value))
		if value[0].text == "{":
			arg.fields, arg.close = _table_fields(src, code, k)
		args.append(arg)
		k = end + 1 if end < len(code) and tokens[code[end]].text == "," else end
	return args, k + 1


def scan_items(path):
	"""Top-level calls, globals and hyprland.start handlers in one file."""
	with open(path) as f:
		text = f.read()
	src = SourceFile(path, text, tokenize(text))
	tokens = src.tokens
	code = _code(tokens)
	calls, globals_, handlers = [], [], []
	stack = []  # open blocks: event name for a handler function, else ""
	pending_event = ""
	nesting = 0  # (), {}, [] depth
	line_of = lambda offset: text.count("\n", 0, offset) + 1  # noqa: E731

	def prev_text(k):
		return tokens[code[k - 1]].text if k > 0 else None

	k = 0
	ci = 0  # position in all tokens, for comments
	while k < len(code):
		i = code[k]
		for j in range(ci, i):  # comments since the last code token
			t = tokens[j]
			if t.kind == "comment" and t.text.startswith(DISABLED_MARK) and stack == ["hyprland.start"]:
				inner = t.text[len(DISABLED_MARK):]
				m = re.fullmatch(r'hl\.exec_cmd\((".*")\)\s*', inner)
				if m:
					arg_start = t.start + len(DISABLED_MARK) + len("hl.exec_cmd(")
					arg = Arg(arg_start, arg_start + len(m.group(1)), m.group(1), True)
					calls.append(Call("exec_cmd", path, line_of(t.start), t.start, t.end, [arg],
						"hyprland.start", False, True))
		ci = i + 1
		t = tokens[i]
		word = t.text if t.kind in ("name", "op") else None

		if word in ("(", "{", "["):
			nesting += 1
		elif word in (")", "}", "]"):
			nesting -= 1
		elif t.kind == "name" and word in _OPENERS:
			stack.append(pending_event if word == "function" else "")
			pending_event = ""
		elif t.kind == "name" and word in _CLOSERS:
			if stack:
				event = stack.pop()
				if event:
					handlers.append(Handler(event, path, t.start))

		at_statement = nesting == 0 and prev_text(k) not in _NOT_STATEMENT_START
		# hl.NAME( ... )
		if at_statement and word == "hl" and k + 3 < len(code) and tokens[code[k + 1]].text == "." \
				and tokens[code[k + 2]].kind == "name" and tokens[code[k + 3]].text == "(":
			fn = tokens[code[k + 2]].text
			args, after = _call_args(src, code, k + 3)
			if fn == "on" and len(args) >= 2 and args[0].literal and tokens[code[k + 4]].kind == "str" \
					and args[1].text.startswith("function"):
				# Don't skip the handler body: walk into it so its calls are found.
				pending_event = _unquote(args[0].text)
				k += 4
				continue
			in_event = stack == ["hyprland.start"]
			calls.append(Call(fn, path, line_of(t.start), t.start, tokens[code[after - 1]].end, args,
				"hyprland.start" if in_event else "", bool(stack) and not in_event))
			ci = code[after - 1] + 1
			k = after
			continue
		# local NAME = literal at the top level: a file constant (mainMod, noctCall, ...)
		if at_statement and not stack and word == "local" and k + 3 < len(code) \
				and tokens[code[k + 1]].kind == "name" and tokens[code[k + 2]].text == "=":
			name_tok = tokens[code[k + 1]]
			value = [tokens[code[k + 3]]]
			after = k + 4
			following = tokens[code[after]].text if after < len(code) else None
			if _is_literal(value) and following not in _BINARY:
				g = Global(name_tok.text, path, line_of(t.start), value[0].start, value[0].end, value[0].text, True)
				# Assigned twice means it isn't a constant; drop it.
				src.locals[name_tok.text] = None if name_tok.text in src.locals else g
				k = after
				continue
		# NAME = value at the top level
		if at_statement and not stack and t.kind == "name" and k + 2 < len(code) \
				and tokens[code[k + 1]].text == "=" and word not in _OPENERS | _CLOSERS:
			first = code[k + 2]
			value = [tokens[first]]
			if tokens[first].text == "-" and k + 3 < len(code):
				value.append(tokens[code[k + 3]])
			after = k + 2 + len(value)
			following = tokens[code[after]].text if after < len(code) else None
			literal = _is_literal(value) and following not in _BINARY
			ref = value[0].text if len(value) == 1 and value[0].kind == "name" and following not in _BINARY \
				and value[0].text not in _OPENERS | _CLOSERS | {"true", "false", "nil", "not", "local"} else ""
			start = value[0].start
			end = value[-1].end if literal or ref else (text.find("\n", start) if "\n" in text[start:] else len(text))
			globals_.append(Global(word, path, line_of(t.start), start, end, text[start:end].rstrip(), literal, ref))
			k = after if literal or ref else k + 2
			continue
		k += 1
	src.locals = {n: g for n, g in src.locals.items() if g is not None}
	return src, calls, globals_, handlers


def sub_table(src, site):
	"""The table constructor that is site's value, as an Arg with fields; None if it isn't one."""
	if not site.text.startswith("{"):
		return None
	code = _code(src.tokens)
	k = next((i for i, ci in enumerate(code) if src.tokens[ci].start == site.start), None)
	if k is None:
		return None
	fields, close = _table_fields(src, code, k)
	return Arg(site.start, site.end, site.text, False, fields, close)


def concat_parts(text):
	"""Splits `a .. "b" .. c` into [("name", "a"), ("str", "b"), ("name", "c")].

	Returns None for anything else (calls, long strings, other operators).
	"""
	code = [t for t in tokenize(text) if t.kind not in ("ws", "comment")]
	parts = []
	for i, tok in enumerate(code):
		if i % 2:
			if tok.text != "..":
				return None
		elif tok.kind == "str":
			parts.append(("str", _unquote(tok.text)))
		elif tok.kind == "name" and tok.text not in _OPENERS | _CLOSERS | {"true", "false", "nil", "local"}:
			parts.append(("name", tok.text))
		else:
			return None
	return parts if code and len(code) % 2 else None


class Edits:
	"""Edits from every planner, applied per file in one pass from the end.

	Each edit remembers the file text it was planned against; render() refuses
	if the file on disk is no longer that text.
	"""

	def __init__(self):
		self.by_file = {}  # path -> (planned-against text, [(start, end, replacement or None)])

	def _add(self, path, base, start, end, replacement):
		known, edits = self.by_file.setdefault(path, (base, []))
		if known != base:
			raise ValueError(f"{os.path.relpath(path, HYPR_DIR)} was read twice with different contents; press Refresh")
		edits.append((start, end, replacement))

	def replace(self, path, base, start, end, text):
		self._add(path, base, start, end, text)

	def insert(self, path, base, pos, text):
		self._add(path, base, pos, pos, text)

	def remove(self, path, base, start, end):
		"""Removes the span, and its line if nothing else is left on it."""
		self._add(path, base, start, end, None)

	def render(self):
		texts = {}
		for path, (base, edits) in self.by_file.items():
			try:
				with open(path) as f:
					current = f.read()
			except FileNotFoundError:
				current = None
			if current != base:
				raise ValueError(f"{os.path.relpath(path, HYPR_DIR)} changed since the app read it; press Refresh")
			text = base
			# Applied from the end so offsets stay valid. Edits at the same spot go in
			# reverse planned order, so inserts there end up in planned order.
			ordered = sorted(enumerate(edits), key=lambda ie: (ie[1][0], ie[1][1], ie[0]), reverse=True)
			for _, (start, end, replacement) in ordered:
				text = remove_span(text, start, end) if replacement is None else text[:start] + replacement + text[end:]
			texts[path] = text
		return texts

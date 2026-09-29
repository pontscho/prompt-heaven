#!/usr/bin/env python3
"""
Web search script with multi-backend support:
  1. DDG Lite  — DuckDuckGo lite endpoint via curl_cffi (default, may CAPTCHA)
  2. Bing      — auto-fallback when DDG CAPTCHAs, always works with curl_cffi
  3. CDP       — opt-in, uses real Chrome browser via DevTools Protocol

Usage:
  python3 search_duckduckgo.py "search phrase"
  python3 search_duckduckgo.py "query1" "query2" "query3"  # batch mode

Environment:
  DDG_BACKEND     — force backend: "bing", "cdp", or "ddg" (default: ddg with bing fallback)
  CHROME_CDP_URL  — Chrome debug endpoint for CDP backend (default: http://localhost:9222)

Platform-aware backend selection:
  - Linux: primp with impersonate="chrome" (a bare alias — primp picks and
    rotates the Chrome major itself) + impersonate_os="linux". primp 1.3.1
    auto-injects the browser headers, so none are supplied by hand.
  - macOS / Windows / other: curl_cffi (chrome146 etc.) — auto-generates all
    browser headers when impersonate= is set. Do NOT override User-Agent,
    Sec-CH-UA, Sec-Fetch-*, Accept, Accept-Encoding on curl_cffi sessions
    or the fingerprint breaks. Only Accept-Language needs manual setting.
"""
import sys
import re
import html as html_mod
import json
import platform
import random
import time
import os
import base64
import hashlib  # the generated WebSocket client (cdp backend)
import socket  # the generated WebSocket client (cdp backend)
from urllib.parse import parse_qs, urlparse

from lxml.html import document_fromstring

# ---------------------------------------------------------------------------
# Impersonation profiles — platform-aware backend selection
# Linux: primp with impersonate_os="linux" (curl_cffi has no OS knob and its
#        chrome profile claims macOS, which contradicts a Linux host's real
#        network stack under passive OS fingerprinting — see docs/spec-ddg.md §2.7)
# macOS / Windows / other: curl_cffi
# ---------------------------------------------------------------------------

# These four are VALID in curl_cffi 0.16.0 (verified 2026-08-04) and are the
# configuration the ~80% DDG pass-through was measured with, so they stay. A pin
# is tolerable here because curl_cffi rots LOUDLY: an unknown name raises
# ImpersonateError at request time. The bare aliases ("chrome", "safari") are
# the maintenance-free alternative if this list is ever re-measured.
CURL_CFFI_PROFILES = [
	"chrome146",
	"chrome145",
	"chrome136",
	"safari260",
]

# primp is the opposite: it rots SILENTLY. An unknown impersonate name prints
# one line to stderr and substitutes a RANDOM browser. This file pinned
# chrome_133/131/130/128 until 2026-08-04, and primp 1.3.1 had dropped every
# name below chrome_144 — so the Linux path had been putting an arbitrary
# fingerprint on the wire (a macOS Safari 26.3 UA in one measured run) while a
# hand-built dict announced Linux Chrome 133. Only aliases are accepted: primp
# offers no way to enumerate its valid names, and its Client.impersonate property
# merely echoes the input back — measured, it reports a name that does not exist
# while impersonating something else entirely.
PRIMP_ALIASES = frozenset({"chrome", "firefox", "edge", "safari", "opera", "random"})

# The hand-built _linux_chrome_headers() dict that used to live here is GONE, and
# docs/spec-ddg.md still refers to it by name. Measured against primp 1.3.1 on
# 2026-08-04: primp auto-injects all 13 of those keys, 11 with character-identical
# values, INCLUDING the two the spec records as load-bearing for DDG
# (upgrade-insecure-requests: 1 and sec-fetch-user: ?1). Client-level headers=
# loses every conflict against impersonate=, so re-measured with sentinel values
# both of those overrides were ignored outright — the dict was already inert.
# One difference shows up on the wire, and it is NOT ours to fix: primp stages
# `accept-encoding: gzip, deflate, br, zstd` — character-identical to the old
# dict — and then its transport rewrites the outgoing value to `gzip, br` to match
# what it can actually decode. No header we set can change that. Note also that
# header ORDER is itself a fingerprint and an echo endpoint cannot reveal it: if
# DDG throughput regresses, order or that rewrite is the suspect, not a missing key.

ROTATE_EVERY = 4


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def clean_html_tags(text):
	text = re.sub(r'<[^>]+>', '', text)
	text = html_mod.unescape(text)
	return text.strip()


def _normalize(text):
	return re.sub(r'\s+', ' ', text).strip() if text else ""


# ---------------------------------------------------------------------------
# DDG Lite parsing
# ---------------------------------------------------------------------------

def decode_duckduckgo_url(ddg_url):
	match = re.search(r'uddg=([^&]+)', ddg_url)
	if match:
		from urllib.parse import unquote
		return unquote(match.group(1))
	if ddg_url.startswith('http'):
		return ddg_url
	if ddg_url.startswith('//'):
		return 'https:' + ddg_url
	return ddg_url


def parse_lite_results(html_content):
	results = []
	rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html_content, re.DOTALL)

	current = {}
	for row in rows:
		link_url = None
		link_title = None

		m = re.search(
			r"""<a[^>]*class=['"]result-link['"][^>]*href=['"]([^'"]+)['"][^>]*>(.*?)</a>""",
			row, re.DOTALL,
		)
		if m:
			link_url, link_title = m.group(1), m.group(2)
		else:
			m = re.search(
				r"""<a[^>]*href=['"]([^'"]+)['"][^>]*class=['"]result-link['"][^>]*>(.*?)</a>""",
				row, re.DOTALL,
			)
			if m:
				link_url, link_title = m.group(1), m.group(2)

		if link_url:
			if current.get('title') and current.get('url'):
				if 'snippet' not in current:
					current['snippet'] = 'No snippet available'
				results.append(current)
			current = {
				'url': decode_duckduckgo_url(link_url),
				'title': clean_html_tags(link_title),
			}
			continue

		snippet_match = re.search(
			r"""<td[^>]*class=['"](result-snippet)['"][^>]*>(.*?)</td>""",
			row, re.DOTALL,
		)
		if snippet_match and current.get('title'):
			current['snippet'] = clean_html_tags(snippet_match.group(2))
			results.append(current)
			current = {}

	if current.get('title') and current.get('url'):
		if 'snippet' not in current:
			current['snippet'] = 'No snippet available'
		results.append(current)

	return results


# ---------------------------------------------------------------------------
# Bing parsing
# ---------------------------------------------------------------------------

def _decode_bing_url(href):
	"""Decode Bing's base64-wrapped redirect URLs."""
	if not href or not href.startswith("https://www.bing.com/ck/a"):
		return href
	try:
		u_param = parse_qs(urlparse(href).query).get("u", [""])[0]
		if u_param and len(u_param) > 2:
			b = u_param[2:]
			return base64.urlsafe_b64decode(b + "=" * ((-len(b)) % 4)).decode()
	except Exception:
		pass
	return href


def parse_bing_results(html_text):
	"""Parse Bing search results using lxml (same approach as deedy5/ddgs)."""
	results = []
	try:
		tree = document_fromstring(html_text)
	except Exception:
		return results

	elements = tree.xpath("//li[contains(@class, 'b_algo')]")
	if not isinstance(elements, list):
		return results

	for e in elements:
		hrefxpath = e.xpath("./h2/a/@href | ./div[contains(@class, 'header')]/a/@href")
		href = str(hrefxpath[0]) if hrefxpath and isinstance(hrefxpath, list) else None
		if not href:
			continue

		href = _decode_bing_url(href)
		titlexpath = e.xpath("./h2/a//text() | ./div[contains(@class, 'header')]/a/h2//text()")
		title = _normalize("".join(str(x) for x in titlexpath)) if titlexpath else ""
		bodyxpath = e.xpath(".//p//text()")
		snippet = _normalize("".join(str(x) for x in bodyxpath)).replace("\xa0", " ") if bodyxpath else ""

		results.append({
			'url': href,
			'title': title,
			'snippet': snippet or 'No snippet available',
		})

	return results


# ---------------------------------------------------------------------------
# Session management (platform-aware backend)
# ---------------------------------------------------------------------------

def create_session(imp=None):
	if platform.system() == "Linux":
		import primp
		# "chrome" rotates the Chrome major on its own across whatever majors the
		# installed primp supports (measured: 145/146/147/148 across fresh
		# clients), which is exactly what the pinned bundle list was reaching for.
		profile = imp if imp in PRIMP_ALIASES else "chrome"
		session = primp.Client(
			impersonate=profile,
			impersonate_os="linux",
			timeout=20,
		)
		return session, profile

	from curl_cffi import requests
	if imp is None:
		imp = random.choice(CURL_CFFI_PROFILES)
	session = requests.Session(impersonate=imp)
	session.headers["Accept-Language"] = "en-US,en;q=0.9"
	return session, imp


def warmup_session(session, endpoint="ddg"):
	try:
		if endpoint == "bing":
			session.get("https://www.bing.com/", timeout=10)
		else:
			session.get("https://lite.duckduckgo.com/lite/", timeout=10)
	except Exception:
		pass
	time.sleep(random.uniform(0.8, 1.5))


# ---------------------------------------------------------------------------
# DDG search
# ---------------------------------------------------------------------------

def search_ddg(query, session):
	try:
		resp = session.post(
			"https://lite.duckduckgo.com/lite/",
			data={"q": query, "kl": ""},
			headers={
				# Mimic real Chrome AJAX (fetch) POST from the lite page.
				# CDP Network.requestWillBeSentExtraInfo on 2026-05-24 showed Chrome
				# sends cors/empty/same-origin + Accept */* + Priority u=1 for XHR.
				# Chrome's request went through (status 200) where curl_cffi/primp
				# with navigation pattern (navigate/document) hit CAPTCHA.
				"Accept": "*/*",
				"Referer": "https://lite.duckduckgo.com/",
				"Sec-Fetch-Site": "same-origin",
				"Sec-Fetch-Mode": "cors",
				"Sec-Fetch-Dest": "empty",
				"Priority": "u=1, i",
			},
			timeout=15,
		)
		if "anomaly-modal" in resp.text or "Please complete the following" in resp.text:
			return None  # CAPTCHA
		return parse_lite_results(resp.text)
	except Exception as e:
		print(f"  [DDG error: {e}]", file=sys.stderr)
		return []


# ---------------------------------------------------------------------------
# Bing search
# ---------------------------------------------------------------------------

def search_bing(query, session):
	try:
		resp = session.get(
			"https://www.bing.com/search",
			params={"q": query},
			timeout=15,
		)
		if resp.status_code != 200:
			print(f"  [Bing HTTP {resp.status_code} for: {query}]", file=sys.stderr)
			return []
		return parse_bing_results(resp.text)
	except Exception as e:
		print(f"  [Bing error: {e}]", file=sys.stderr)
		return []


# ---------------------------------------------------------------------------
# CDP backend (opt-in via DDG_BACKEND=cdp)
# ---------------------------------------------------------------------------

def _discover_chrome():
	import urllib.request
	import urllib.error
	candidates = [os.environ.get("CHROME_CDP_URL", "")]
	candidates += [
		"http://192.168.2.2:9222",
		"http://localhost:9222",
		"http://127.0.0.1:9222",
		"http://localhost:9229",
	]
	for base in candidates:
		base = base.rstrip("/")
		if not base:
			continue
		try:
			resp = urllib.request.urlopen(f"{base}/json", timeout=2)
			targets = json.loads(resp.read())
			for t in targets:
				if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
					url = t.get("url", "")
					if url.startswith("devtools://") or url.startswith("chrome://"):
						continue
					return base, t["webSocketDebuggerUrl"]
		except Exception:
			continue
	return None


# The WebSocket client, generated from Scripts/_mcp_websocket.py -- the same
# RFC 6455 core mcp-gdc.py carries, with the blocking-socket wrapper instead of
# the asyncio one. It replaced the third-party `websocket-client` package, so
# the cdp backend needs nothing outside the stdlib. This file is not an MCP
# server; it takes generated blocks because amalgamate.py names it in
# DECLARED_HOSTS. No Origin header is sent, which is what `suppress_origin=True`
# used to buy: Chrome refuses a DevTools socket whose Origin is not allow-listed.
# Refresh: python3 Scripts/amalgamate.py -- do not edit inside the region.
# BEGIN GENERATED: _mcp_websocket.py :: WebSocketError, WS_MAX_HANDSHAKE_BYTES, WS_MAX_FRAME_BYTES, WS_MAX_MESSAGE_BYTES, _ws_parse_url, _ws_handshake_request, _ws_handshake_split, _ws_handshake_verify, _ws_mask, _ws_encode_frame, _ws_parse_frame, _ws_assemble, _ws_control_reply, _WsConnection, _ws_step, _ws_sync_connect, _ws_sync_recv, _ws_sync_send, _ws_sync_close
class WebSocketError(ConnectionError):
	"""A WebSocket protocol violation or a failed handshake.

	A `ConnectionError` on purpose: to every caller that already treats a dead
	link as an `OSError`, a peer that broke the protocol is the same event.
	"""


WS_MAX_HANDSHAKE_BYTES = 64 * 1024


WS_MAX_FRAME_BYTES = 256 * 1024 * 1024


WS_MAX_MESSAGE_BYTES = 256 * 1024 * 1024


def _ws_parse_url(url: str) -> tuple:
	"""Split a ``ws://host[:port]/path`` URL into ``(host, port, path)``.

	``wss://`` is refused by name rather than dialled in clear text on port 80,
	which is what the hand parser this replaced did with it. A bracketed IPv6
	literal loses its brackets here and regains them in the ``Host`` header. The
	query string stays on the path, where the request line needs it; a fragment
	is dropped, since it never goes on the wire.
	"""
	if not url.startswith("ws://"):
		scheme = url.split("://", 1)[0] if "://" in url else url[:16]
		raise WebSocketError("only ws:// URLs are supported, not %r" % scheme)
	rest = url[5:].split("#", 1)[0]
	cut = len(rest)
	for mark in "/?":
		at = rest.find(mark)
		if 0 <= at < cut:
			cut = at
	authority, path = rest[:cut], rest[cut:]
	if not path.startswith("/"):
		path = "/" + path
	if "@" in authority:
		raise WebSocketError("a ws:// URL with user information is refused")
	port = ""
	if authority.startswith("["):
		close = authority.find("]")
		if close < 0:
			raise WebSocketError("unterminated IPv6 literal in %r" % authority)
		host, tail = authority[1:close], authority[close + 1:]
		if tail:
			if not tail.startswith(":"):
				raise WebSocketError("junk after the IPv6 literal in %r" % authority)
			port = tail[1:]
	elif ":" in authority:
		host, port = authority.rsplit(":", 1)
	else:
		host = authority
	if not host:
		raise WebSocketError("a ws:// URL needs a host")
	if not port:
		return host, 80, path
	if not port.isdigit() or not 0 < int(port) < 65536:
		raise WebSocketError("invalid port %r" % port)
	return host, int(port), path


def _ws_handshake_request(host: str, port: int, path: str) -> tuple:
	"""The HTTP upgrade request and the random key it carries: ``(bytes, str)``.

	No ``Origin`` header is sent. Chrome refuses a DevTools WebSocket whose
	Origin is not on its ``--remote-allow-origins`` list, and a client that
	sends none is not a browser page -- which is what ``websocket-client``'s
	``suppress_origin=True`` bought the search script, and why this never
	grew an option to send one.

	A host, or a path, carrying whitespace or a control character is refused:
	either would let a URL write its own header lines into the request.
	"""
	for part in (host, path):
		if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in part):
			raise WebSocketError("refusing a host or path with whitespace or control characters")
	key = base64.b64encode(os.urandom(16)).decode("ascii")
	authority = "[%s]:%d" % (host, port) if ":" in host else "%s:%d" % (host, port)
	lines = ["GET %s HTTP/1.1" % path]
	lines.append("Host: %s" % authority)
	lines.append("Upgrade: websocket")
	lines.append("Connection: Upgrade")
	lines.append("Sec-WebSocket-Key: %s" % key)
	lines.append("Sec-WebSocket-Version: 13")
	return ("\r\n".join(lines) + "\r\n\r\n").encode("ascii"), key


def _ws_handshake_split(buf) -> int:
	"""Where the upgrade response's header block ends in *buf*, or -1 for "read more".

	The returned offset is one past the blank line, so ``buf[:end]`` is the
	header block and ``buf[end:]`` is the first frame bytes if the server sent
	any in the same segment -- which the client this replaced read into its
	header buffer and threw away.
	"""
	end = bytes(buf[:WS_MAX_HANDSHAKE_BYTES + 4]).find(b"\r\n\r\n")
	if 0 <= end and end + 4 <= WS_MAX_HANDSHAKE_BYTES:
		return end + 4
	if end < 0 and len(buf) <= WS_MAX_HANDSHAKE_BYTES:
		return -1
	raise WebSocketError("WebSocket handshake failed: the header block exceeds %d bytes" % WS_MAX_HANDSHAKE_BYTES)


def _ws_handshake_verify(head: bytes, key: str) -> dict:
	"""Check the upgrade response against RFC 6455 4.1; return its headers.

	Header names are folded to lower case and a repeated header is joined with
	``", "``, so every check below reads ONE value and compares it EXACTLY --
	the client this replaced looked for ``101`` anywhere in the status line and
	for the accept key anywhere in the response.
	"""
	lines = head.decode("latin-1").split("\r\n")
	status = lines[0].split(" ", 2)
	if len(status) < 2 or status[0] != "HTTP/1.1" or status[1] != "101":
		raise WebSocketError("WebSocket handshake rejected: %r" % lines[0][:200])
	headers = {}
	for line in lines[1:]:
		if not line:
			continue
		name, sep, value = line.partition(":")
		if not sep or not name or name != name.strip() or line[0] in " \t":
			raise WebSocketError("WebSocket handshake failed: malformed header line %r" % line[:200])
		name = name.lower()
		value = value.strip(" \t")
		headers[name] = headers[name] + ", " + value if name in headers else value
	if headers.get("upgrade", "").lower() != "websocket":
		raise WebSocketError("WebSocket handshake failed: Upgrade is not websocket")
	if "upgrade" not in [token.strip().lower() for token in headers.get("connection", "").split(",")]:
		raise WebSocketError("WebSocket handshake failed: Connection carries no upgrade token")
	digest = hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()
	if headers.get("sec-websocket-accept") != base64.b64encode(digest).decode("ascii"):
		raise WebSocketError("WebSocket handshake failed: invalid accept key")
	if "sec-websocket-extensions" in headers or "sec-websocket-protocol" in headers:
		raise WebSocketError("WebSocket handshake failed: the server negotiated an extension or subprotocol nobody offered")
	return headers


def _ws_mask(key: bytes, data: bytes) -> bytes:
	"""XOR *data* with the 4-byte *key* repeated -- masking and unmasking alike.

	One big-integer XOR over the whole payload instead of a Python-level loop
	over its bytes: the loop this replaced cost a generator step per byte, which
	on a multi-megabyte frame is the whole of the send time.
	"""
	size = len(data)
	if not size:
		return b""
	pad = (bytes(key) * (size // 4 + 1))[:size]
	return (int.from_bytes(data, "big") ^ int.from_bytes(pad, "big")).to_bytes(size, "big")


def _ws_encode_frame(opcode: int, payload: bytes, fin: bool = True) -> bytes:
	"""One client frame: FIN/opcode, the shortest length form, a fresh mask key.

	Always masked -- RFC 6455 5.1 requires it of every client frame, and a
	server is obliged to drop the connection on an unmasked one.
	"""
	if opcode not in (0x0, 0x1, 0x2, 0x8, 0x9, 0xA):
		raise WebSocketError("unknown opcode 0x%X" % opcode)
	size = len(payload)
	if opcode >= 0x8 and (size > 125 or not fin):
		raise WebSocketError("a control frame must be FIN and at most 125 bytes")
	head = bytearray([(0x80 if fin else 0x00) | opcode])
	if size < 126:
		head.append(0x80 | size)
	elif size < 65536:
		head.append(0x80 | 126)
		head += size.to_bytes(2, "big")
	else:
		head.append(0x80 | 127)
		head += size.to_bytes(8, "big")
	key = os.urandom(4)
	return bytes(head) + key + _ws_mask(key, payload)


def _ws_parse_frame(buf):
	"""The first complete server frame in *buf*: ``(fin, opcode, payload, used)``.

	Returns None while *buf* does not yet hold the whole frame; *used* is how
	many bytes of *buf* the frame took. Every refusal that can be made from
	the header is made from the header, before the payload arrives, so an
	announced 2**63-byte frame costs ten bytes of reading and not an attempt.
	"""
	if len(buf) < 2:
		return None
	first, second = buf[0], buf[1]
	if first & 0x70:
		raise WebSocketError("reserved bits set on a frame, and no extension was negotiated")
	opcode = first & 0x0F
	if opcode not in (0x0, 0x1, 0x2, 0x8, 0x9, 0xA):
		raise WebSocketError("unknown opcode 0x%X" % opcode)
	fin = bool(first & 0x80)
	if second & 0x80:
		raise WebSocketError("the server sent a masked frame")
	size = second & 0x7F
	offset = 2
	if size == 126:
		if len(buf) < 4:
			return None
		size, offset = int.from_bytes(bytes(buf[2:4]), "big"), 4
	elif size == 127:
		if len(buf) < 10:
			return None
		size, offset = int.from_bytes(bytes(buf[2:10]), "big"), 10
		if size >> 63:
			raise WebSocketError("the most significant bit of a 64-bit frame length is set")
	if opcode >= 0x8 and (size > 125 or not fin):
		raise WebSocketError("a control frame must be FIN and at most 125 bytes")
	if size > WS_MAX_FRAME_BYTES:
		raise WebSocketError("a %d-byte frame exceeds the %d-byte cap" % (size, WS_MAX_FRAME_BYTES))
	end = offset + size
	if len(buf) < end:
		return None
	return fin, opcode, bytes(buf[offset:end]), end


def _ws_assemble(pending: list, fin: bool, opcode: int, payload: bytes):
	"""Fold one frame into *pending*; return ``(opcode, value)`` or None.

	*pending* is the caller's per-connection list: empty between messages, and
	``[opcode, bytearray]`` while a fragmented one is open. A control frame is
	returned at once as ``(opcode, payload)`` -- RFC 6455 lets it arrive
	between two fragments, and it does not disturb the message. A data message
	is returned once its FIN fragment lands: text as ``str`` (strict UTF-8),
	binary as ``bytes``. None means "a fragment was absorbed, keep reading" --
	the client this replaced returned None for a continuation frame, which its
	caller read as the connection closing.
	"""
	if opcode >= 0x8:
		return opcode, payload
	if opcode == 0x0:
		if not pending:
			raise WebSocketError("a continuation frame arrived with no message open")
	elif pending:
		raise WebSocketError("a new data frame arrived inside a fragmented message")
	else:
		pending[:] = [opcode, bytearray()]
	if len(pending[1]) + len(payload) > WS_MAX_MESSAGE_BYTES:
		raise WebSocketError("a message exceeds the %d-byte cap" % WS_MAX_MESSAGE_BYTES)
	pending[1] += payload
	if not fin:
		return None
	kind, data = pending[0], bytes(pending[1])
	del pending[:]
	if kind == 0x2:
		return kind, data
	try:
		return kind, data.decode("utf-8")
	except UnicodeDecodeError:
		raise WebSocketError("a text message is not valid UTF-8") from None


def _ws_control_reply(opcode: int, payload: bytes):
	"""The frame a control frame obliges the client to send back, or None.

	A ping is answered with a pong carrying the SAME payload (RFC 6455 5.5.2);
	the client this replaced read the ping and answered nothing. A close is
	answered with a close echoing its status code, after the payload is
	checked: one byte is not a status code, the codes an endpoint must never
	send are refused, and so is a reason that is not UTF-8. A pong needs no
	answer.
	"""
	if opcode == 0x9:
		return _ws_encode_frame(0xA, payload)
	if opcode != 0x8:
		return None
	if len(payload) == 1:
		raise WebSocketError("a close frame carried a one-byte payload")
	if not payload:
		return _ws_encode_frame(0x8, b"")
	code = int.from_bytes(payload[:2], "big")
	if code < 1000 or code in (1004, 1005, 1006, 1015):
		raise WebSocketError("a close frame carried the reserved status code %d" % code)
	try:
		payload[2:].decode("utf-8")
	except UnicodeDecodeError:
		raise WebSocketError("a close frame's reason is not valid UTF-8") from None
	return _ws_encode_frame(0x8, payload[:2])


class _WsConnection:
	"""One open client connection: its transport and the parser state between reads.

	The asyncio wrapper sets *reader* and *writer*, the socket wrapper sets
	*sock*; the core never touches any of the three. *buf* holds bytes read
	but not yet parsed -- including any the server sent in the same segment as
	its handshake -- and *pending* is `_ws_assemble`'s open message.
	"""

	def __init__(self, reader, writer, sock, timeout: float):
		self.reader = reader
		self.writer = writer
		self.sock = sock
		self.timeout = timeout
		self.buf = bytearray()
		self.pending = []


def _ws_step(conn):
	"""Advance *conn* over what is buffered: ``(reply, message, closed)`` or None.

	None means the buffer holds no complete frame and the wrapper must read.
	Otherwise *reply* is a frame the wrapper must send first (or None),
	*message* is the next data message as ``str`` (or None), and *closed*
	says the peer sent a close. A binary message is decoded with replacement
	rather than refused, which is what the client this replaced returned for
	one -- CDP never sends binary, so this is compatibility, not a feature.

	A loop, not a recursion: the client this replaced called itself once per
	ping or pong, so a peer streaming control frames grew its stack.
	"""
	while True:
		frame = _ws_parse_frame(conn.buf)
		if frame is None:
			return None
		fin, opcode, payload, used = frame
		del conn.buf[:used]
		event = _ws_assemble(conn.pending, fin, opcode, payload)
		if event is None:
			continue
		kind, value = event
		if kind == 0x1:
			return None, value, False
		if kind == 0x2:
			return None, value.decode("utf-8", "replace"), False
		reply = _ws_control_reply(kind, value)
		if reply is not None or kind == 0x8:
			return reply, None, kind == 0x8


def _ws_sync_connect(url: str, timeout: float = 30.0):
	"""Open a WebSocket to a ``ws://`` URL over a blocking socket.

	*timeout* is the socket's own, so it bounds the connect and every later
	read and write separately -- the semantics of ``websocket-client``'s
	``create_connection(timeout=...)``, which this replaced.
	"""
	host, port, path = _ws_parse_url(url)
	sock = socket.create_connection((host, port), timeout=timeout)
	try:
		request, key = _ws_handshake_request(host, port, path)
		conn = _WsConnection(None, None, sock, timeout)
		sock.sendall(request)
		end = -1
		while end < 0:
			chunk = sock.recv(65536)
			if not chunk:
				raise WebSocketError("WebSocket handshake failed: connection closed")
			conn.buf += chunk
			end = _ws_handshake_split(conn.buf)
		_ws_handshake_verify(bytes(conn.buf[:end]), key)
		del conn.buf[:end]
	except BaseException:
		sock.close()
		raise
	return conn


def _ws_sync_recv(conn):
	"""The next data message as text; None once the peer has closed."""
	while True:
		step = _ws_step(conn)
		if step is None:
			chunk = conn.sock.recv(65536)
			if not chunk:
				raise WebSocketError("the connection ended without a close frame")
			conn.buf += chunk
			continue
		reply, message, closed = step
		if reply is not None:
			conn.sock.sendall(reply)
		if closed:
			return None
		if message is not None:
			return message


def _ws_sync_send(conn, text: str) -> None:
	"""Send *text* as one masked text frame."""
	conn.sock.sendall(_ws_encode_frame(0x1, text.encode("utf-8")))


def _ws_sync_close(conn) -> None:
	"""Send a normal-closure frame, best effort, and close the socket.

	The peer's answering close is not waited for: the caller is done with the
	connection, and a peer that never answers must not keep it open.
	"""
	try:
		conn.sock.sendall(_ws_encode_frame(0x8, (1000).to_bytes(2, "big")))
	except OSError:
		pass
	conn.sock.close()
# END GENERATED: 6d2dd7da88c7


class CDPSearcher:
	def __init__(self, ws_url):
		self.ws = _ws_sync_connect(ws_url, timeout=30)
		self._id = 1
		self._warm = False

	def _send(self, method, params=None):
		msg = {"id": self._id, "method": method, "params": params or {}}
		_ws_sync_send(self.ws, json.dumps(msg))
		while True:
			text = _ws_sync_recv(self.ws)
			if text is None:
				raise WebSocketError("Chrome closed the CDP connection")
			result = json.loads(text)
			if result.get("id") == self._id:
				self._id += 1
				return result

	def search(self, query):
		js = """
		(async () => {
			const fd = new URLSearchParams();
			fd.append('q', %s);
			fd.append('kl', '');
			const r = await fetch('https://lite.duckduckgo.com/lite/', {
				method: 'POST',
				body: fd,
				headers: {'Content-Type': 'application/x-www-form-urlencoded'}
			});
			return await r.text();
		})()
		""" % json.dumps(query)

		result = self._send("Runtime.evaluate", {
			"expression": js,
			"awaitPromise": True,
			"returnByValue": True,
		})
		value = result.get("result", {}).get("result", {}).get("value", "")
		if result.get("result", {}).get("exceptionDetails"):
			print(f"  [CDP JS error for: {query}]", file=sys.stderr)
			return []
		if "anomaly" in value or "Please complete" in value:
			print(f"  [CAPTCHA via CDP for: {query}]", file=sys.stderr)
			return []
		return parse_lite_results(value)

	def close(self):
		try:
			_ws_sync_close(self.ws)
		except Exception:
			pass


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def format_results(results, query=None):
	output = []
	if query:
		output.append(f"## Query: {query}")
		output.append("")
	for i, result in enumerate(results, 1):
		title = result.get('title', 'No title')
		url = result.get('url', 'No URL')
		snippet = result.get('snippet', 'No snippet available')
		output.append(f"### Result {i}: {title}")
		output.append(f"**URL**: {url}")
		output.append(f"**Snippet**: {snippet}")
		output.append("")
	return '\n'.join(output)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _run_cdp(queries):
	chrome = _discover_chrome()
	if not chrome:
		print("  [CDP requested but no Chrome found, aborting]", file=sys.stderr)
		sys.exit(1)
	_, ws_url = chrome
	cdp = CDPSearcher(ws_url)
	print("  [Using CDP/Chrome backend]", file=sys.stderr)

	output_sections = []
	has_results = False
	for i, query in enumerate(queries):
		if i > 0:
			time.sleep(random.uniform(0.8, 1.5))
		results = cdp.search(query)
		if results:
			has_results = True
			section = format_results(results, query=query) if len(queries) > 1 else format_results(results)
			output_sections.append(section)
		elif len(queries) > 1:
			output_sections.append(f"## Query: {query}\n\nNo results found.\n")
	cdp.close()
	return output_sections, has_results


def _run_bing(queries, session):
	"""Pure Bing backend."""
	output_sections = []
	has_results = False
	for i, query in enumerate(queries):
		if i > 0:
			time.sleep(random.uniform(1.5, 3.0))
		if i > 0 and i % ROTATE_EVERY == 0:
			session, _imp = create_session()
		results = search_bing(query, session)
		if results:
			has_results = True
			section = format_results(results, query=query) if len(queries) > 1 else format_results(results)
			output_sections.append(section)
		elif len(queries) > 1:
			output_sections.append(f"## Query: {query}\n\nNo results found.\n")
	return output_sections, has_results


def _run_ddg_with_bing_fallback(queries):
	"""Try DDG lite first; on CAPTCHA switch to Bing for remaining queries."""
	session, _imp = create_session()
	warmup_session(session, "ddg")

	output_sections = []
	has_results = False
	using_bing = False

	for i, query in enumerate(queries):
		if i > 0:
			delay = random.uniform(1.5, 3.0) if using_bing else random.uniform(2.5, 5.0)
			time.sleep(delay)

		if i > 0 and i % ROTATE_EVERY == 0:
			session, _imp = create_session()
			if not using_bing:
				warmup_session(session, "ddg")

		if using_bing:
			results = search_bing(query, session)
		else:
			results = search_ddg(query, session)
			if results is None:
				# CAPTCHA — switch to Bing for this and all remaining queries
				print(f"  [DDG CAPTCHA on: {query} — switching to Bing fallback]", file=sys.stderr)
				using_bing = True
				session, _imp = create_session()
				warmup_session(session, "bing")
				results = search_bing(query, session)

		if results:
			has_results = True
			section = format_results(results, query=query) if len(queries) > 1 else format_results(results)
			output_sections.append(section)
		elif len(queries) > 1:
			output_sections.append(f"## Query: {query}\n\nNo results found.\n")

	return output_sections, has_results


def main():
	if len(sys.argv) < 2:
		print("Usage: python3 search_duckduckgo.py \"search phrase\" [\"query2\" ...]", file=sys.stderr)
		sys.exit(1)

	queries = sys.argv[1:]
	forced = os.environ.get("DDG_BACKEND", "").lower()

	if forced == "cdp":
		output_sections, has_results = _run_cdp(queries)
	elif forced == "bing":
		session, _imp = create_session()
		warmup_session(session, "bing")
		print("  [Using Bing backend]", file=sys.stderr)
		output_sections, has_results = _run_bing(queries, session)
	else:
		output_sections, has_results = _run_ddg_with_bing_fallback(queries)

	if not has_results:
		print("No results found for any query.", file=sys.stderr)
		sys.exit(1)

	if len(queries) > 1:
		print("# DuckDuckGo Search Results\n")
		print('\n---\n\n'.join(output_sections))
	else:
		print(output_sections[0] if output_sections else "")


if __name__ == '__main__':
	main()

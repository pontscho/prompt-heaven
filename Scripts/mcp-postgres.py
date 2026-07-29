#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
r"""mcp-psql — Pure-stdlib PostgreSQL MCP server (this file: mcp-postgres.py).

Registered with Claude Code as `mcp-psql`, so the model-facing tool name is
`mcp__mcp-psql__postgres_call`. The string mcp-postgres.py is only ever this
module's filename, never the server's identity.

Single-tool dispatcher pattern: exposes one MCP tool (postgres_call) that routes
to internal handler functions via the 'function' parameter.

Speaks the PostgreSQL v3 wire protocol directly using only the Python stdlib
(socket, ssl, struct, hashlib, hmac). No libpq, no psycopg, no pip dependency —
the same approach pg8000 proves is possible from pure Python. Authentication
supports SCRAM-SHA-256, MD5 and cleartext.

Requires only Python 3.9+ stdlib modules.

Output shape (cap convention v1): result sets are rendered as an unaligned
header row plus data rows joined by a single '|', no padding and no rule line —
the reader is a model that parses on the delimiter, not an eye scanning a
column. Cells are backslash-escaped so '|' inside a value stays unambiguous
(\\ \| \n \r \t); SQL NULL is the bare token NULL, an empty string is an empty
field, the literal string "NULL" is \NULL. Answers are capped by
max_answer_chars (default 24000 chars, ~6k tokens): row-shaped payloads drop
whole ROWS and say where to resume, everything else is cut on a line boundary
with one closing accounting line.

Usage:
  python3 mcp-postgres.py [--host [ssl://]ip:port] [--user U] [--password P]
                          [--dbname DB] [--sslmode disable|prefer|require]
                          [--debug] [--log-file <path>]

Config precedence: environment variables first, CLI flags override when given.

  MCP_POSTGRE_HOST   ([ssl://]ip:port, default 127.0.0.1:5432) ← --host / --port
  MCP_POSTGRE_USER   (default: OS user)                        ← --user
  MCP_POSTGRE_PASS   (default: "")                             ← --password
  MCP_POSTGRE_DB     (default: postgres)                       ← --dbname
"""

import argparse
import asyncio
import base64
import getpass
import hashlib
import hmac
import json
import logging
import os
import socket
import ssl
import struct
import sys
from typing import Any, Callable, Dict, List, Optional

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

log = logging.getLogger("mcp-postgres")

PROTOCOL_VERSION_3 = 196608           # 3.0
SSL_REQUEST_CODE = 80877103           # magic for SSLRequest

# Cap convention v1. 24000 chars is ~6k tokens at the usual ~4 chars/token — a
# reply one call may spend, not a reply that eats the session. The previous
# 50000 was ~12k tokens for a SINGLE call, and a result set is the one payload
# in this fleet that can be arbitrarily large by accident (one missing WHERE).
# Per-call overridable via the max_answer_chars parameter, so a caller who
# genuinely wants the whole dump asks for it explicitly.
DEFAULT_MAX_ANSWER_CHARS = 24000


# ---------------------------------------------------------------------------
# Host parsing — scheme prefix carries the SSL hint
# ---------------------------------------------------------------------------

def parse_host(raw: str):
    """Parse ``[scheme://]host[:port]`` into ``(host, port, sslmode)``.

    Scheme prefixes encode the SSL intent so it can be expressed env-only:
      * no scheme               → sslmode "prefer"
      * ssl://                  → sslmode "require"  (TLS mandatory)
      * postgres:// / postgresql:// / tcp://ip → sslmode "disable" (plain TCP)

    A trailing ``--sslmode`` CLI flag overrides whatever this returns.
    """
    raw = (raw or "").strip()
    sslmode = "prefer"
    for prefix, mode in (
        ("ssl://", "require"),
        ("postgresql://", "disable"),
        ("postgres://", "disable"),
        ("tcp://", "disable"),
    ):
        if raw.lower().startswith(prefix):
            raw = raw[len(prefix):]
            sslmode = mode
            break

    # Strip any trailing path/query a URL-ish value might carry.
    raw = raw.split("/", 1)[0]

    host = raw
    port = 5432
    if raw.startswith("["):  # bracketed IPv6: [::1]:5432
        close = raw.find("]")
        if close != -1:
            host = raw[1:close]
            rest = raw[close + 1:]
            if rest.startswith(":") and rest[1:].isdigit():
                port = int(rest[1:])
    elif ":" in raw:
        left, right = raw.rsplit(":", 1)
        if right.isdigit():
            host, port = left, int(right)
    if not host:
        host = "127.0.0.1"
    return host, port, sslmode


# ---------------------------------------------------------------------------
# Errors and result container
# ---------------------------------------------------------------------------

class PgError(Exception):
    """A PostgreSQL ErrorResponse, or a synthetic protocol-level error.

    Carries the server's error fields (SQLSTATE 'C', message 'M', detail 'D',
    hint 'H', …). ``str(err)`` renders ``[SQLSTATE] message`` plus optional
    DETAIL/HINT lines.
    """

    def __init__(self, fields):
        if isinstance(fields, str):
            fields = {"M": fields}
        self.fields = fields or {}
        self.sqlstate = self.fields.get("C", "")
        self.message = self.fields.get("M", "unknown error")
        super().__init__(self._format())

    def _format(self) -> str:
        text = f"[{self.sqlstate}] {self.message}" if self.sqlstate else self.message
        detail = self.fields.get("D")
        hint = self.fields.get("H")
        if detail:
            text += f"\nDETAIL: {detail}"
        if hint:
            text += f"\nHINT: {hint}"
        return text


class QueryResult:
    """Result of a single executed statement."""

    def __init__(self, columns=None, rows=None, command_tag="", notices=None):
        self.columns: List[str] = columns or []
        self.rows: List[List[Optional[str]]] = rows or []
        self.command_tag: str = command_tag or ""
        self.notices: List[str] = notices or []


def _parse_scram(text: str) -> Dict[str, str]:
    """Parse a SCRAM message ``k=v,k=v`` into a dict (values may contain '=')."""
    out: Dict[str, str] = {}
    for part in text.split(","):
        if "=" in part:
            key, value = part.split("=", 1)
            out[key] = value
    return out


# ---------------------------------------------------------------------------
# Layer 1 — PostgreSQL v3 wire protocol
# ---------------------------------------------------------------------------

class PgConnection:
    """A single PostgreSQL connection speaking the v3 wire protocol.

    Message framing: every message except the startup/SSL requests is
    ``Int8 type + Int32 length + payload`` (length counts itself but not the
    type byte). Startup and SSLRequest have no type byte.
    """

    def __init__(self, host, port, user, password, dbname, sslmode="prefer", timeout=15.0):
        self.host = host
        self.port = int(port)
        self.user = user
        self.password = password or ""
        self.dbname = dbname
        self.sslmode = sslmode
        self.timeout = timeout
        self.sock: Optional[socket.socket] = None
        self.tx_status = b"I"
        self.server_params: Dict[str, str] = {}
        self._broken = False

    # -- config snapshot (no password) --------------------------------------

    def target(self) -> str:
        return f"{self.user}@{self.host}:{self.port}/{self.dbname} (sslmode={self.sslmode})"

    def is_alive(self) -> bool:
        return self.sock is not None and not self._broken

    # -- low-level IO -------------------------------------------------------

    def _recv_exact(self, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            try:
                chunk = self.sock.recv(n - len(buf))
            except OSError as exc:
                self._broken = True
                raise ConnectionError(f"recv failed: {exc}")
            if not chunk:
                self._broken = True
                raise ConnectionError("connection closed by server")
            buf += chunk
        return bytes(buf)

    def _read_message(self):
        """Read one framed message → ``(type_byte: bytes, payload: bytes)``."""
        type_byte = self._recv_exact(1)
        length = struct.unpack("!i", self._recv_exact(4))[0]
        payload = self._recv_exact(length - 4) if length > 4 else b""
        return type_byte, payload

    def _send_message(self, type_byte: bytes, payload: bytes) -> None:
        frame = type_byte + struct.pack("!i", len(payload) + 4) + payload
        try:
            self.sock.sendall(frame)
        except OSError as exc:
            self._broken = True
            raise ConnectionError(f"send failed: {exc}")

    def _send_raw(self, data: bytes) -> None:
        try:
            self.sock.sendall(data)
        except OSError as exc:
            self._broken = True
            raise ConnectionError(f"send failed: {exc}")

    # -- connect / SSL / startup -------------------------------------------

    def connect(self) -> None:
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.sock.settimeout(self.timeout)
        if self.sslmode != "disable":
            self._negotiate_ssl()
        self._send_startup()
        self._authenticate()
        self._read_until_ready()
        self._broken = False

    def _negotiate_ssl(self) -> None:
        self._send_raw(struct.pack("!ii", 8, SSL_REQUEST_CODE))
        resp = self._recv_exact(1)
        if resp == b"S":
            ctx = ssl.create_default_context()
            # Dev tool: encrypt the link but do not verify the (often self-signed)
            # server certificate — this matches libpq's `require` semantics, where
            # cert verification only kicks in for verify-ca / verify-full.
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            self.sock = ctx.wrap_socket(self.sock, server_hostname=self.host)
        elif resp == b"N":
            if self.sslmode == "require":
                raise ConnectionError(
                    "server does not support SSL but sslmode=require"
                )
            # prefer → fall back to plaintext on the same socket
        else:
            raise ConnectionError(f"unexpected SSL negotiation byte: {resp!r}")

    def _send_startup(self) -> None:
        payload = struct.pack("!i", PROTOCOL_VERSION_3)
        for key, value in (
            ("user", self.user),
            ("database", self.dbname),
            ("application_name", "mcp-postgres"),
            ("client_encoding", "UTF8"),
        ):
            payload += key.encode("utf-8") + b"\x00" + value.encode("utf-8") + b"\x00"
        payload += b"\x00"
        self._send_raw(struct.pack("!i", len(payload) + 4) + payload)

    # -- authentication -----------------------------------------------------

    def _authenticate(self) -> None:
        while True:
            mtype, payload = self._read_message()
            if mtype == b"R":
                code = struct.unpack("!i", payload[:4])[0]
                data = payload[4:]
                if code == 0:          # AuthenticationOk
                    return
                elif code == 3:        # CleartextPassword
                    self._send_message(b"p", self.password.encode("utf-8") + b"\x00")
                elif code == 5:        # MD5Password
                    self._send_md5(data[:4])
                elif code == 10:       # SASL
                    self._scram_authenticate(data)
                else:
                    raise PgError({"M": f"unsupported authentication method {code}",
                                   "C": "0A000"})
            elif mtype == b"E":
                raise PgError(self._parse_error_fields(payload))
            elif mtype == b"N":
                continue  # NoticeResponse during auth — ignore
            else:
                raise PgError({"M": f"unexpected message during auth: {mtype!r}",
                               "C": "08P01"})

    def _send_md5(self, salt: bytes) -> None:
        inner = hashlib.md5(self.password.encode("utf-8") + self.user.encode("utf-8")).hexdigest()
        token = "md5" + hashlib.md5(inner.encode("ascii") + salt).hexdigest()
        self._send_message(b"p", token.encode("ascii") + b"\x00")

    def _scram_authenticate(self, mechanisms_data: bytes) -> None:
        mechs = [m.decode("ascii") for m in mechanisms_data.split(b"\x00") if m]
        if "SCRAM-SHA-256" not in mechs:
            raise PgError({"M": "server offers no supported SASL mechanism: "
                                + ", ".join(mechs), "C": "0A000"})

        client_nonce = base64.b64encode(os.urandom(18)).decode("ascii")
        client_first_bare = "n=,r=" + client_nonce
        client_first = "n,," + client_first_bare

        cf = client_first.encode("utf-8")
        init = b"SCRAM-SHA-256\x00" + struct.pack("!i", len(cf)) + cf
        self._send_message(b"p", init)

        # SASLContinue (code 11)
        mtype, payload = self._read_message()
        if mtype == b"E":
            raise PgError(self._parse_error_fields(payload))
        code = struct.unpack("!i", payload[:4])[0]
        if code != 11:
            raise PgError({"M": f"expected SASLContinue, got auth code {code}", "C": "08P01"})
        server_first = payload[4:].decode("utf-8")
        attrs = _parse_scram(server_first)
        combined_nonce = attrs.get("r", "")
        if not combined_nonce.startswith(client_nonce):
            raise PgError({"M": "SCRAM server nonce does not extend client nonce", "C": "08P01"})
        salt = base64.b64decode(attrs["s"])
        iterations = int(attrs["i"])

        salted = hashlib.pbkdf2_hmac("sha256", self.password.encode("utf-8"), salt, iterations)
        client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
        stored_key = hashlib.sha256(client_key).digest()
        client_final_bare = "c=biws,r=" + combined_nonce
        auth_message = client_first_bare + "," + server_first + "," + client_final_bare
        client_sig = hmac.new(stored_key, auth_message.encode("utf-8"), hashlib.sha256).digest()
        proof = bytes(a ^ b for a, b in zip(client_key, client_sig))
        client_final = client_final_bare + ",p=" + base64.b64encode(proof).decode("ascii")
        self._send_message(b"p", client_final.encode("utf-8"))

        # SASLFinal (code 12)
        mtype, payload = self._read_message()
        if mtype == b"E":
            raise PgError(self._parse_error_fields(payload))
        code = struct.unpack("!i", payload[:4])[0]
        if code != 12:
            raise PgError({"M": f"expected SASLFinal, got auth code {code}", "C": "08P01"})
        final = _parse_scram(payload[4:].decode("utf-8"))
        if "v" in final:
            server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
            server_sig = hmac.new(server_key, auth_message.encode("utf-8"), hashlib.sha256).digest()
            if base64.b64decode(final["v"]) != server_sig:
                raise PgError({"M": "SCRAM server signature verification failed", "C": "08P01"})
        # Outer auth loop reads the following AuthenticationOk.

    def _read_until_ready(self) -> None:
        while True:
            mtype, payload = self._read_message()
            if mtype == b"Z":               # ReadyForQuery
                self.tx_status = payload[:1]
                return
            elif mtype == b"E":             # ErrorResponse
                raise PgError(self._parse_error_fields(payload))
            elif mtype == b"S":             # ParameterStatus
                self._record_parameter(payload)
            elif mtype in (b"K", b"N"):     # BackendKeyData / NoticeResponse
                continue
            # anything else: ignore until ReadyForQuery

    def _record_parameter(self, payload: bytes) -> None:
        try:
            name, value, _ = payload.split(b"\x00", 2)
            self.server_params[name.decode("utf-8", "replace")] = value.decode("utf-8", "replace")
        except ValueError:
            pass

    # -- queries ------------------------------------------------------------

    def simple_query(self, sql: str) -> List[QueryResult]:
        if not self.is_alive():
            raise ConnectionError("connection is not open")
        self._send_message(b"Q", sql.encode("utf-8") + b"\x00")
        return self._read_query_results()

    def extended_query(self, sql: str, params: List[Any]) -> QueryResult:
        if not self.is_alive():
            raise ConnectionError("connection is not open")
        self._send_parse(sql)
        self._send_bind(params or [])
        self._send_describe_portal()
        self._send_execute()
        self._send_sync()
        results = self._read_query_results()
        return results[0] if results else QueryResult()

    def _send_parse(self, sql: str) -> None:
        # unnamed statement, let the server infer parameter types (count 0)
        payload = b"\x00" + sql.encode("utf-8") + b"\x00" + struct.pack("!h", 0)
        self._send_message(b"P", payload)

    def _send_bind(self, params: List[Any]) -> None:
        body = b"\x00\x00"               # unnamed portal + unnamed statement
        body += struct.pack("!h", 0)     # 0 param format codes → all text
        body += struct.pack("!h", len(params))
        for p in params:
            if p is None:
                body += struct.pack("!i", -1)
            else:
                encoded = str(p).encode("utf-8")
                body += struct.pack("!i", len(encoded)) + encoded
        body += struct.pack("!h", 0)     # 0 result format codes → all text
        self._send_message(b"B", body)

    def _send_describe_portal(self) -> None:
        self._send_message(b"D", b"P\x00")

    def _send_execute(self) -> None:
        self._send_message(b"E", b"\x00" + struct.pack("!i", 0))  # unnamed portal, all rows

    def _send_sync(self) -> None:
        self._send_message(b"S", b"")

    def _read_query_results(self) -> List[QueryResult]:
        results: List[QueryResult] = []
        notices: List[str] = []
        pending_error = None
        cur_columns: Optional[List[str]] = None
        cur_rows: List[List[Optional[str]]] = []

        while True:
            mtype, payload = self._read_message()
            if mtype == b"T":                       # RowDescription
                cur_columns = self._parse_row_description(payload)
                cur_rows = []
            elif mtype == b"D":                     # DataRow
                cur_rows.append(self._parse_data_row(payload))
            elif mtype == b"C":                     # CommandComplete
                tag = payload.split(b"\x00", 1)[0].decode("utf-8", "replace")
                results.append(QueryResult(cur_columns or [], cur_rows, tag))
                cur_columns = None
                cur_rows = []
            elif mtype == b"I":                     # EmptyQueryResponse
                results.append(QueryResult([], [], "EMPTY QUERY"))
            elif mtype == b"N":                     # NoticeResponse
                fields = self._parse_error_fields(payload)
                sev = fields.get("S", "NOTICE")
                notices.append(f"{sev}: {fields.get('M', '')}")
            elif mtype == b"E":                     # ErrorResponse
                pending_error = self._parse_error_fields(payload)
            elif mtype == b"Z":                     # ReadyForQuery
                self.tx_status = payload[:1]
                break
            elif mtype == b"S":                     # ParameterStatus
                self._record_parameter(payload)
            elif mtype in (b"1", b"2", b"3", b"n", b"s", b"t", b"K", b"A", b"G", b"H", b"d", b"c"):
                # ParseComplete/BindComplete/CloseComplete/NoData/PortalSuspended/
                # ParameterDescription/BackendKeyData/NotificationResponse/
                # CopyIn/CopyOut/CopyData/CopyDone — not needed here, swallow.
                continue
            # unknown message types are ignored

        if pending_error is not None:
            raise PgError(pending_error)
        if notices:
            for res in results:
                res.notices = notices
        return results

    @staticmethod
    def _parse_row_description(payload: bytes) -> List[str]:
        nfields = struct.unpack("!h", payload[:2])[0]
        offset = 2
        columns: List[str] = []
        for _ in range(nfields):
            end = payload.index(b"\x00", offset)
            columns.append(payload[offset:end].decode("utf-8", "replace"))
            offset = end + 1
            offset += 18  # tableOID(4)+colNo(2)+typeOID(4)+typLen(2)+typMod(4)+format(2)
        return columns

    @staticmethod
    def _parse_data_row(payload: bytes) -> List[Optional[str]]:
        ncols = struct.unpack("!h", payload[:2])[0]
        offset = 2
        row: List[Optional[str]] = []
        for _ in range(ncols):
            length = struct.unpack("!i", payload[offset:offset + 4])[0]
            offset += 4
            if length == -1:
                row.append(None)
            else:
                row.append(payload[offset:offset + length].decode("utf-8", "replace"))
                offset += length
        return row

    @staticmethod
    def _parse_error_fields(payload: bytes) -> Dict[str, str]:
        fields: Dict[str, str] = {}
        offset = 0
        while offset < len(payload):
            code = payload[offset:offset + 1]
            if code == b"\x00":
                break
            offset += 1
            end = payload.index(b"\x00", offset)
            fields[code.decode("ascii", "replace")] = payload[offset:end].decode("utf-8", "replace")
            offset = end + 1
        return fields

    # -- shutdown -----------------------------------------------------------

    def close(self) -> None:
        if self.sock is not None:
            try:
                self._send_message(b"X", b"")  # Terminate
            except Exception:
                pass
            try:
                self.sock.close()
            except Exception:
                pass
        self.sock = None
        self._broken = True


# ---------------------------------------------------------------------------
# Layer 2 — Connection manager (named connections, lazy connect, reconnect)
# ---------------------------------------------------------------------------

class ConnectionManager:
    def __init__(self, default_config: dict):
        self.configs: Dict[str, dict] = {"default": default_config}
        self.connections: Dict[str, PgConnection] = {}

    def add_config(self, name: str, config: dict) -> None:
        self.configs[name] = config

    def has(self, name: str) -> bool:
        return name in self.configs

    def get(self, name: str = "default", force_reconnect: bool = False) -> PgConnection:
        name = name or "default"
        if name not in self.configs:
            known = ", ".join(sorted(self.configs.keys()))
            raise ValueError(f"Unknown connection '{name}'. Known: {known}")

        conn = self.connections.get(name)
        if conn is not None and (force_reconnect or not conn.is_alive()):
            conn.close()
            self.connections.pop(name, None)
            conn = None
        if conn is not None:
            return conn

        conn = PgConnection(**self.configs[name])
        conn.connect()
        self.connections[name] = conn
        return conn

    def _run(self, name: str, fn: Callable[[PgConnection], Any]) -> Any:
        conn = self.get(name)
        try:
            return fn(conn)
        except (ConnectionError, OSError):
            # Socket-level failure → reconnect once and retry. A server-side
            # PgError (bad SQL etc.) is NOT caught here, so it surfaces cleanly.
            conn = self.get(name, force_reconnect=True)
            return fn(conn)

    def simple_query(self, name: str, sql: str) -> List[QueryResult]:
        return self._run(name, lambda c: c.simple_query(sql))

    def extended_query(self, name: str, sql: str, params: List[Any]) -> QueryResult:
        return self._run(name, lambda c: c.extended_query(sql, params))

    def disconnect(self, name: str) -> bool:
        conn = self.connections.pop(name, None)
        if conn is not None:
            conn.close()
        # Keep the "default" config so it can be reopened lazily.
        if name != "default":
            self.configs.pop(name, None)
        return conn is not None

    def close_all(self) -> None:
        for conn in list(self.connections.values()):
            try:
                conn.close()
            except Exception:
                pass
        self.connections.clear()


# ---------------------------------------------------------------------------
# Parameter & function aliases (purity-style)
# ---------------------------------------------------------------------------

PARAM_ALIASES = {
    "conn": "connection",
    "connection_name": "connection",
    "statement": "sql",
    "query": "sql",
    "q": "sql",
    "parameters": "params",
    "values": "params",
    "table_name": "table",
    "relname": "table",
    "relation": "table",
    "rel": "table",
    "namespace": "schema",
    "nsp": "schema",
    "function_name": "name",
    "proc": "name",
    "fn": "name",
    "db": "dbname",
    "database": "dbname",
    "rows": "max_rows",
    "limit": "max_rows",
    "skip": "offset",
    "fmt": "format",
    "system": "include_system",
    "max_chars": "max_answer_chars",
}

PARAM_ALIASES_BY_FUNC: Dict[str, Dict[str, str]] = {
    "query": {"args": "params", "arguments": "params", "binds": "params"},
    "call_function": {"params": "args", "arguments": "args", "parameters": "args", "values": "args"},
    "call_procedure": {"params": "args", "arguments": "args", "parameters": "args", "values": "args"},
    "describe_function": {"identity_args": "args"},
}

FUNCTION_ALIASES = {
    "q": "query",
    "execute": "query",
    "exec": "query",
    "sql": "query",
    "list_procedures": "list_functions",
    # convenience verbs, fleet-style (cf. mcp-tshark stats→statistics, sessions→list_sessions)
    "tables": "list_tables",
    "schemas": "list_schemas",
    "indexes": "list_indexes",
    "functions": "list_functions",
    "connections": "list_connections",
    "call": "call_function",      # dominant reading; CALL a procedure via call_procedure
    "describe": "describe_table",  # dominant reading; functions via describe_function
}


def _canonical_function(function: str) -> str:
    return FUNCTION_ALIASES.get(function, function)


def _resolve_aliases(params: Any, function: Optional[str] = None) -> dict:
    """Return a new dict with aliased parameter names resolved to canonical names."""
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"'params' was a string but not valid JSON: {exc}. "
                "Pass params as an object, not a JSON-encoded string."
            )
    if not isinstance(params, dict):
        raise ValueError(
            f"'params' must be an object (dict) or a JSON-encoded object string; "
            f"got {type(params).__name__}."
        )

    func_aliases = PARAM_ALIASES_BY_FUNC.get(function or "", {})
    resolved: dict = {}
    for key, value in params.items():
        canonical = func_aliases.get(key) or PARAM_ALIASES.get(key, key)
        if canonical not in resolved:
            resolved[canonical] = value
    return resolved


# ---------------------------------------------------------------------------
# Output formatting helpers
# ---------------------------------------------------------------------------

def _quote_ident(ident: str) -> str:
    return '"' + ident.replace('"', '""') + '"'


def _qualified_target(name: str, schema: Optional[str]) -> str:
    """Build a quoted, optionally schema-qualified identifier for use in SQL."""
    if schema:
        return _quote_ident(schema) + "." + _quote_ident(name)
    if "." in name:
        sch, nm = name.split(".", 1)
        return _quote_ident(sch) + "." + _quote_ident(nm)
    return _quote_ident(name)


def _split_name(name: str, schema: Optional[str]):
    """Return ``(schema, name)`` splitting a dotted name when no schema given."""
    if schema:
        return schema, name
    if "." in name:
        sch, nm = name.split(".", 1)
        return sch, nm
    return None, name


# -- the row table ----------------------------------------------------------
#
# The consumer of this output is an LLM, not a terminal, so the table pays for a
# field BOUNDARY and for nothing else. psql's aligned layout charges three
# times over for that one piece of information: the " | " separator, the padding
# that widens every cell to the longest value in its column, and the "-+-" rule
# under the header. Alignment serves a human eye scanning down a column; a model
# parses on the delimiter. On a wide text column the padding is the majority of
# the reply — every row pays for the single longest value in the set.
#
# So: ONE single-character delimiter, no padding, no rule line. The header row
# stays, because the key names carry meaning, but unaligned.
DELIM = "|"

# Dropping the padding must not cost field UNAMBIGUITY: a table whose rows
# cannot be split back into fields is worse than a padded one. Cells are escaped
# C-style, and because the escape character is itself escaped the encoding is
# reversible rather than merely suggestive:
#     \\ = a literal backslash        \| = a literal delimiter
#     \n = newline    \r = CR         \t = tab
# NULL handling rides on the same scheme, keeping the three-way distinction the
# padded table also had: SQL NULL is the bare token below, the empty string is
# an empty field, and the 4-char STRING "NULL" is written \NULL — unreachable
# for any other value, since a real backslash always doubles.
NULL_TOKEN = "NULL"

# No single cell may swallow the answer (a pg_get_functiondef body, a JSONB
# blob). Applied to the RAW value, BEFORE escaping, so the cut can never land
# inside an escape sequence and leave a dangling backslash behind.
CELL_MAX_CHARS = 120

# Room kept free for a page/closing line while filling a row budget.
PAGE_LINE_RESERVE = 80


def _escape_cell(text: str) -> str:
    r"""Escape one field so DELIM cannot occur inside it. Reversible."""
    text = text.replace("\\", "\\\\").replace(DELIM, "\\" + DELIM)
    return text.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")


def _cell(value: Optional[str]) -> str:
    """Render one value: NULL marker, per-cell clamp, then escaping."""
    if value is None:
        return NULL_TOKEN
    text = str(value)
    if len(text) > CELL_MAX_CHARS:
        text = text[:CELL_MAX_CHARS - 1] + "…"
    text = _escape_cell(text)
    # Only reachable when the VALUE is the string "NULL"; see NULL_TOKEN.
    return "\\" + text if text == NULL_TOKEN else text


def _rows_note(start: int, shown: int, total: int) -> str:
    """Row accounting for a row-shaped payload; goes on its LAST line.

    <total> is EXACT and always known here: DataRow messages are fully buffered
    before a QueryResult is handed back (see _read_query_results), so this
    server never streams a cursor and so never has to guess a row count. A
    server that CANNOT know its total must SAY so on this line instead of
    inventing a number.

    Display indices are 1-based inclusive, which makes the 1-based last row
    equal to the 0-based ``offset`` of the next one — so the hint is literally
    the value to pass back. ``offset=`` is emitted only when rows really remain:
    a resume hint that would return nothing is worse than no hint.
    """
    last = start + shown
    if shown == 0:
        # Spelled out rather than as a 1-based range, which would invert
        # ("rows 100-99") when the caller offsets past the end.
        return (f"[no rows at offset {start} of {total}]" if start
                else f"[{total} rows]")
    if last < total:
        return (f"[showing rows {start + 1}-{last} of {total}; "
                f"offset={last} for more]")
    if start > 0:
        return f"[showing rows {start + 1}-{last} of {total}; no rows left]"
    # Whole set shown: just the count, which a model cannot cheaply derive from
    # a long answer without counting lines.
    return f"[{total} row{'s' if total != 1 else ''}]"


def _render_result(res: QueryResult, max_rows: int = 0, offset: int = 0,
                   char_budget: int = 0) -> str:
    """One result set as header + data lines, paged by ROW — never mid-row.

    A result set is row-shaped by definition, so its share of the answer ceiling
    is spent by dropping whole rows rather than by cutting characters: the last
    line kept is always a complete row, and the closing line says where to
    resume. ``char_budget`` of 0 means unlimited.
    """
    columns = res.columns
    lines = [DELIM.join(_escape_cell(c) for c in columns)]
    total = len(res.rows)
    start = max(0, offset)

    budget = char_budget - len(lines[0]) if char_budget > 0 else 0
    shown = 0
    for row in res.rows[start:]:
        if max_rows > 0 and shown >= max_rows:
            break
        line = DELIM.join(_cell(row[i] if i < len(row) else None)
                          for i in range(len(columns)))
        # At least one row always survives: a header on its own tells the caller
        # nothing, and _cap_text() below is the hard backstop for the ceiling.
        if budget > 0 and shown > 0 and budget - len(line) - 1 < PAGE_LINE_RESERVE:
            break
        budget -= len(line) + 1
        lines.append(line)
        shown += 1

    lines.append(_rows_note(start, shown, total))
    return "\n".join(lines)


def _format_results(results: List[QueryResult], max_rows: int = 0,
                    offset: int = 0, char_budget: int = 0) -> str:
    blocks: List[str] = []
    spent = 0
    for res in results:
        if res.columns:
            # Budget left for THIS result set. Clamped to 1 rather than 0: 0
            # means "unlimited" here, and an exhausted budget is the opposite.
            budget = max(1, char_budget - spent) if char_budget > 0 else 0
            block = _render_result(res, max_rows, offset, budget)
        else:
            block = res.command_tag or "OK"
        blocks.append(block)
        spent += len(block) + 2
        for notice in res.notices:
            blocks.append(notice)
            spent += len(notice) + 2
    return "\n\n".join(blocks) if blocks else "(none)"


def _cap_text(text: str, max_chars: int) -> str:
    """Clamp to ``max_chars`` on a LINE boundary, head-biased, one closing line.

    Head-biased because the informative end of every payload this server builds
    is the top: the first rows of a result set, the first nodes of a plan, the
    signature above a function body. The closing line names the end that was
    kept, so the bias is never something the caller has to assume.

    This is the LAST resort, not the main path: row-shaped answers are paged by
    row in _render_result(), so what actually lands here is the row-less
    payloads (an EXPLAIN plan, a function definition, a describe report) and any
    overflow the row pager could not prevent. The notice is counted against the
    ceiling, so the whole reply stays within max_chars.
    """
    if not max_chars or max_chars <= 0 or len(text) <= max_chars:
        return text
    total = len(text)

    def marker(kept: int) -> str:
        return (f"\n[truncated: kept {kept} of {total} chars from the head; "
                f"raise max_answer_chars or narrow the query]")

    # marker(total) is the longest the notice can get (kept <= total), so
    # reserving that much cannot overshoot once the real count is known.
    keep = max_chars - len(marker(total))
    if keep <= 0:
        # The ceiling is smaller than the accounting line itself. The line still
        # wins: a payload with no accounting is worse than no payload.
        return marker(0).lstrip("\n")
    cut = text.rfind("\n", 0, keep + 1)
    if cut <= 0:
        # No line boundary fits: either the payload is one enormous line (a
        # function body, a JSON plan) or the ceiling is narrower than the first
        # line. The cap wins over the boundary here, and the notice sitting
        # right below says so. Row-shaped answers are paged whole-row before
        # they ever reach this point, so the only table that can land here is a
        # single row wider than the entire ceiling.
        cut = keep
    return text[:cut] + marker(cut)


def _conn_name(params: dict) -> str:
    return params.get("connection") or "default"


def _max_answer_chars(params: dict) -> int:
    """The per-call ceiling. <= 0 disables it — an explicit "give me all of it"."""
    try:
        return int(params.get("max_answer_chars", DEFAULT_MAX_ANSWER_CHARS))
    except (TypeError, ValueError):
        return DEFAULT_MAX_ANSWER_CHARS


def _max_rows(params: dict) -> int:
    try:
        return int(params.get("max_rows", 0))
    except (TypeError, ValueError):
        return 0


def _offset(params: dict) -> int:
    """First row to display, 0-based — the value the page line hands back.

    Display-level paging over the rows this call already fetched, NOT a SQL
    OFFSET: the statement is re-executed on every call, so a caller paging a big
    result set pays for the scan each time. It exists so the row-truncation
    line's ``offset=<n> for more`` is an instruction that actually works.
    """
    try:
        return max(0, int(params.get("offset", 0)))
    except (TypeError, ValueError):
        return 0


def _bool_param(value: Any, default: bool = False) -> bool:
    """Coerce a possibly-stringy value to bool.

    The wire frequently carries booleans as strings ("false"/"0"/"no"), where a
    naive ``bool("false")`` would wrongly yield True. Mirrors mcp-tshark's
    ``_bool_param`` so flag handling is consistent across the fleet.
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in ("", "false", "0", "no", "off", "none")
    return bool(value)


def _row_answer(results: List[QueryResult], params: dict) -> dict:
    """The standard row-shaped reply: page by row first, then clamp characters.

    Both layers are in play on purpose. Row paging keeps the common case whole —
    complete rows plus a resume hint — and the character clamp is the hard
    ceiling that also covers what rows alone cannot bound (a single monstrous
    row, notices, several result sets from one multi-statement call).
    """
    cap = _max_answer_chars(params)
    text = _format_results(results, _max_rows(params), _offset(params), cap)
    return {"__raw_text__": _cap_text(text, cap)}


# ---------------------------------------------------------------------------
# Layer 3 — Handlers
# ---------------------------------------------------------------------------

def handle_query(params: dict, mgr: ConnectionManager) -> dict:
    sql = params.get("sql")
    if not sql:
        raise ValueError("Missing required parameter: sql")
    conn = _conn_name(params)
    bind = params.get("params")

    if isinstance(bind, list) and len(bind) > 0:
        results = [mgr.extended_query(conn, sql, bind)]
    else:
        results = mgr.simple_query(conn, sql)
    return _row_answer(results, params)


def handle_explain(params: dict, mgr: ConnectionManager) -> dict:
    sql = params.get("sql")
    if not sql:
        raise ValueError("Missing required parameter: sql")
    analyze = _bool_param(params.get("analyze", False))
    fmt = str(params.get("format", "TEXT")).upper()
    if fmt not in ("TEXT", "JSON", "XML", "YAML"):
        raise ValueError("Parameter 'format' must be one of TEXT, JSON, XML, YAML")
    opts = []
    if analyze:
        opts.append("ANALYZE")
    opts.append("FORMAT " + fmt)
    wrapped = f"EXPLAIN ({', '.join(opts)}) {sql}"
    results = mgr.simple_query(_conn_name(params), wrapped)
    # EXPLAIN returns a single text column ("QUERY PLAN"); print it raw.
    lines: List[str] = []
    for res in results:
        for row in res.rows:
            lines.append(row[0] if row and row[0] is not None else "")
    text = "\n".join(lines) if lines else _format_results(results)
    return {"__raw_text__": _cap_text(text, _max_answer_chars(params))}


def handle_list_schemas(params: dict, mgr: ConnectionManager) -> dict:
    include_system = _bool_param(params.get("include_system", False))
    sql = (
        "SELECT n.nspname AS schema, "
        "pg_catalog.pg_get_userbyid(n.nspowner) AS owner "
        "FROM pg_catalog.pg_namespace n "
    )
    if not include_system:
        sql += "WHERE n.nspname NOT LIKE 'pg\\_%' AND n.nspname <> 'information_schema' "
    sql += "ORDER BY 1"
    results = mgr.simple_query(_conn_name(params), sql)
    return _row_answer(results, params)


def handle_list_tables(params: dict, mgr: ConnectionManager) -> dict:
    schema = params.get("schema")
    sql = (
        "SELECT n.nspname AS schema, c.relname AS name, "
        "CASE c.relkind WHEN 'r' THEN 'table' WHEN 'v' THEN 'view' "
        "WHEN 'm' THEN 'matview' WHEN 'p' THEN 'partitioned' "
        "WHEN 'f' THEN 'foreign' ELSE c.relkind::text END AS type, "
        "c.reltuples::bigint AS est_rows "
        "FROM pg_catalog.pg_class c "
        "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
        "WHERE c.relkind IN ('r','v','m','p','f') "
    )
    conn = _conn_name(params)
    if schema:
        sql += ("AND n.nspname = $1 ORDER BY 1, 2")
        result = mgr.extended_query(conn, sql, [schema])
        results = [result]
    else:
        sql += ("AND n.nspname NOT LIKE 'pg\\_%' AND n.nspname <> 'information_schema' "
                "ORDER BY 1, 2")
        results = mgr.simple_query(conn, sql)
    return _row_answer(results, params)


def handle_describe_table(params: dict, mgr: ConnectionManager) -> dict:
    table = params.get("table")
    if not table:
        raise ValueError("Missing required parameter: table")
    schema = params.get("schema")
    target = _qualified_target(table, schema)  # quoted, passed to ::regclass
    conn = _conn_name(params)

    columns_sql = (
        "SELECT a.attname AS column, "
        "pg_catalog.format_type(a.atttypid, a.atttypmod) AS type, "
        "CASE WHEN a.attnotnull THEN 'NO' ELSE 'YES' END AS nullable, "
        "pg_catalog.pg_get_expr(d.adbin, d.adrelid) AS default "
        "FROM pg_catalog.pg_attribute a "
        "LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum "
        "WHERE a.attrelid = $1::regclass AND a.attnum > 0 AND NOT a.attisdropped "
        "ORDER BY a.attnum"
    )
    index_sql = (
        "SELECT i.relname AS index, ix.indisprimary AS is_primary, "
        "ix.indisunique AS is_unique, pg_catalog.pg_get_indexdef(ix.indexrelid) AS definition "
        "FROM pg_catalog.pg_index ix "
        "JOIN pg_catalog.pg_class i ON i.oid = ix.indexrelid "
        "WHERE ix.indrelid = $1::regclass ORDER BY i.relname"
    )
    fk_sql = (
        "SELECT conname AS name, pg_catalog.pg_get_constraintdef(oid) AS definition "
        "FROM pg_catalog.pg_constraint "
        "WHERE conrelid = $1::regclass AND contype = 'f' ORDER BY conname"
    )

    cols = mgr.extended_query(conn, columns_sql, [target])
    idxs = mgr.extended_query(conn, index_sql, [target])
    fks = mgr.extended_query(conn, fk_sql, [target])

    # Same formatter as query/list_tables — one table shape across the server.
    # Deliberately NOT row-paged, unlike the list_* handlers: this is a
    # fixed-shape three-section report, one offset could not mean anything
    # sensible across three different result sets, and a page line reading
    # "offset=N for more" would be an instruction describe_table does not
    # accept. So the character ceiling (line-boundary) is the only clamp here.
    sections = [
        f"Table: {target}",
        "── Columns ──\n" + _format_results([cols]),
        "── Indexes ──\n" + _format_results([idxs]),
        "── Foreign keys ──\n" + _format_results([fks]),
    ]
    return {"__raw_text__": _cap_text("\n\n".join(sections), _max_answer_chars(params))}


def handle_list_indexes(params: dict, mgr: ConnectionManager) -> dict:
    table = params.get("table")
    schema = params.get("schema")
    conn = _conn_name(params)
    sql = (
        "SELECT schemaname AS schema, tablename AS table, "
        "indexname AS index, indexdef AS definition "
        "FROM pg_catalog.pg_indexes WHERE 1=1 "
    )
    binds: List[Any] = []
    if schema:
        binds.append(schema)
        sql += f"AND schemaname = ${len(binds)} "
    if table:
        binds.append(table)
        sql += f"AND tablename = ${len(binds)} "
    sql += "ORDER BY 1, 2, 3"
    if binds:
        results = [mgr.extended_query(conn, sql, binds)]
    else:
        results = mgr.simple_query(conn, sql)
    return _row_answer(results, params)


def handle_list_functions(params: dict, mgr: ConnectionManager) -> dict:
    schema = params.get("schema")
    conn = _conn_name(params)
    sql = (
        "SELECT n.nspname AS schema, p.proname AS name, "
        "pg_catalog.pg_get_function_arguments(p.oid) AS arguments, "
        "pg_catalog.pg_get_function_result(p.oid) AS returns, "
        "CASE p.prokind WHEN 'f' THEN 'function' WHEN 'p' THEN 'procedure' "
        "WHEN 'a' THEN 'aggregate' WHEN 'w' THEN 'window' ELSE p.prokind::text END AS kind, "
        "l.lanname AS language "
        "FROM pg_catalog.pg_proc p "
        "JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace "
        "JOIN pg_catalog.pg_language l ON l.oid = p.prolang "
    )
    if schema:
        sql += "WHERE n.nspname = $1 ORDER BY 1, 2"
        results = [mgr.extended_query(conn, sql, [schema])]
    else:
        sql += ("WHERE n.nspname NOT LIKE 'pg\\_%' AND n.nspname <> 'information_schema' "
                "ORDER BY 1, 2")
        results = mgr.simple_query(conn, sql)
    return _row_answer(results, params)


def handle_describe_function(params: dict, mgr: ConnectionManager) -> dict:
    name = params.get("name")
    if not name:
        raise ValueError("Missing required parameter: name")
    schema, name = _split_name(name, params.get("schema"))
    want_args = params.get("args")
    conn = _conn_name(params)

    lookup_sql = (
        "SELECT p.oid::bigint AS oid, n.nspname AS schema, p.proname AS name, "
        "pg_catalog.pg_get_function_identity_arguments(p.oid) AS identity_args, "
        "pg_catalog.pg_get_function_arguments(p.oid) AS arguments, "
        "pg_catalog.pg_get_function_result(p.oid) AS returns, "
        "CASE p.prokind WHEN 'f' THEN 'function' WHEN 'p' THEN 'procedure' "
        "WHEN 'a' THEN 'aggregate' WHEN 'w' THEN 'window' ELSE p.prokind::text END AS kind "
        "FROM pg_catalog.pg_proc p "
        "JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace "
        "WHERE p.proname = $1 "
    )
    binds: List[Any] = [name]
    if schema:
        binds.append(schema)
        lookup_sql += f"AND n.nspname = ${len(binds)} "
    lookup_sql += "ORDER BY n.nspname, identity_args"

    found = mgr.extended_query(conn, lookup_sql, binds)
    if not found.rows:
        raise ValueError(f"No function named '{name}'" + (f" in schema '{schema}'" if schema else ""))

    candidates = found.rows  # [oid, schema, name, identity_args, arguments, returns, kind]
    chosen = None
    if len(candidates) == 1:
        chosen = candidates[0]
    elif want_args is not None:
        norm = " ".join(str(want_args).split())
        for row in candidates:
            if " ".join((row[3] or "").split()) == norm:
                chosen = row
                break
        if chosen is None:
            sigs = "\n".join(f"  {r[1]}.{r[2]}({r[3]})" for r in candidates)
            raise ValueError(f"No overload of '{name}' matches args '{want_args}'. Candidates:\n{sigs}")
    else:
        sigs = "\n".join(f"  {r[1]}.{r[2]}({r[3]}) → {r[5]} [{r[6]}]" for r in candidates)
        raise ValueError(
            f"'{name}' is overloaded ({len(candidates)} candidates). "
            f"Pass 'args' (identity argument list) to disambiguate:\n{sigs}"
        )

    oid = chosen[0]
    header = (
        f"{chosen[1]}.{chosen[2]}({chosen[4]})\n"
        f"  returns: {chosen[5]}\n"
        f"  kind:    {chosen[6]}"
    )
    try:
        src = mgr.extended_query(conn, "SELECT pg_catalog.pg_get_functiondef($1::oid)", [oid])
        body = src.rows[0][0] if src.rows and src.rows[0] else ""
        text = header + "\n\n── Definition ──\n" + (body or "(no definition available)")
    except PgError as exc:
        # pg_get_functiondef rejects aggregates/window funcs — show signature only.
        text = header + f"\n\n(definition unavailable: {exc.message})"
    return {"__raw_text__": _cap_text(text, _max_answer_chars(params))}


def handle_call_function(params: dict, mgr: ConnectionManager) -> dict:
    name = params.get("name")
    if not name:
        raise ValueError("Missing required parameter: name")
    schema, name = _split_name(name, params.get("schema"))
    args = params.get("args") or []
    if not isinstance(args, list):
        raise ValueError("Parameter 'args' must be a list")

    target = _qualified_target(name, schema)
    placeholders = ", ".join(f"${i + 1}" for i in range(len(args)))
    sql = f"SELECT {target}({placeholders})"
    result = mgr.extended_query(_conn_name(params), sql, args)
    return _row_answer([result], params)


def handle_call_procedure(params: dict, mgr: ConnectionManager) -> dict:
    name = params.get("name")
    if not name:
        raise ValueError("Missing required parameter: name")
    schema, name = _split_name(name, params.get("schema"))
    args = params.get("args") or []
    if not isinstance(args, list):
        raise ValueError("Parameter 'args' must be a list")

    target = _qualified_target(name, schema)
    placeholders = ", ".join(f"${i + 1}" for i in range(len(args)))
    sql = f"CALL {target}({placeholders})"
    result = mgr.extended_query(_conn_name(params), sql, args)
    return _row_answer([result], params)


def handle_list_connections(params: dict, mgr: ConnectionManager) -> dict:
    lines = ["Configured connections:"]
    for cfg_name in sorted(mgr.configs.keys()):
        cfg = mgr.configs[cfg_name]
        conn = mgr.connections.get(cfg_name)
        state = "connected" if (conn is not None and conn.is_alive()) else "idle"
        lines.append(
            f"  {cfg_name}: {cfg['user']}@{cfg['host']}:{cfg['port']}/{cfg['dbname']} "
            f"(sslmode={cfg.get('sslmode', 'prefer')}) [{state}]"
        )
    return {"__raw_text__": "\n".join(lines)}


def handle_connect(params: dict, mgr: ConnectionManager) -> dict:
    name = params.get("name")
    if not name:
        raise ValueError("Missing required parameter: name")
    default_cfg = mgr.configs["default"]

    raw_host = params.get("host")
    if raw_host:
        host, port, ssl_hint = parse_host(str(raw_host))
    else:
        host, port, ssl_hint = default_cfg["host"], default_cfg["port"], default_cfg.get("sslmode", "prefer")
    if params.get("port") is not None:
        port = int(params["port"])
    sslmode = params.get("sslmode") or ssl_hint

    # Password precedence: explicit `password` > `password_env` (name of an env
    # var read from THIS server's own environment) > the default connection's
    # password. `password_env` lets callers avoid putting the secret in the call.
    pw_env = params.get("password_env")
    if params.get("password") is not None:
        password = params["password"]
    elif pw_env:
        password = os.environ.get(str(pw_env))
        if not password:
            raise ValueError(
                f"password_env='{pw_env}' is unset or empty in the server's "
                f"environment — export it where the MCP server runs, or pass "
                f"'password' directly."
            )
    else:
        password = default_cfg["password"]

    config = {
        "host": host,
        "port": port,
        "user": params.get("user") or default_cfg["user"],
        "password": password,
        "dbname": params.get("dbname") or default_cfg["dbname"],
        "sslmode": sslmode,
    }
    mgr.add_config(name, config)
    # Connect eagerly to validate credentials and report the live server version.
    conn = mgr.get(name, force_reconnect=True)
    server_version = conn.server_params.get("server_version", "?")
    return {"__raw_text__": (
        f"Connected '{name}' → {conn.target()}\n"
        f"server_version: {server_version}"
    )}


def handle_disconnect(params: dict, mgr: ConnectionManager) -> dict:
    name = params.get("name")
    if not name:
        raise ValueError("Missing required parameter: name")
    if name not in mgr.configs:
        raise ValueError(f"Unknown connection '{name}'")
    was_open = mgr.disconnect(name)
    suffix = " (config retained)" if name == "default" else ""
    state = "closed live connection and removed" if was_open else "removed (was idle)"
    if name == "default":
        state = "closed live connection" if was_open else "no live connection"
    return {"__raw_text__": f"Disconnected '{name}': {state}{suffix}"}


# ---------------------------------------------------------------------------
# Handler registry
# ---------------------------------------------------------------------------

HANDLERS: Dict[str, Callable[[dict, ConnectionManager], dict]] = {
    "query": handle_query,
    "q": handle_query,
    "execute": handle_query,
    "exec": handle_query,
    "sql": handle_query,
    "explain": handle_explain,
    "list_schemas": handle_list_schemas,
    "schemas": handle_list_schemas,
    "list_tables": handle_list_tables,
    "tables": handle_list_tables,
    "describe_table": handle_describe_table,
    "describe": handle_describe_table,
    "list_indexes": handle_list_indexes,
    "indexes": handle_list_indexes,
    "list_functions": handle_list_functions,
    "list_procedures": handle_list_functions,
    "functions": handle_list_functions,
    "describe_function": handle_describe_function,
    "call_function": handle_call_function,
    "call": handle_call_function,
    "call_procedure": handle_call_procedure,
    "list_connections": handle_list_connections,
    "connections": handle_list_connections,
    "connect": handle_connect,
    "disconnect": handle_disconnect,
}

# 'offset' is accepted exactly where a row-truncation line can be emitted (the
# row-shaped handlers), because that line tells the caller to pass it back.
HANDLER_ACCEPTED_PARAMS: Dict[str, set] = {
    "query": {"sql", "params", "connection", "max_rows", "offset", "max_answer_chars"},
    "explain": {"sql", "analyze", "format", "connection", "max_answer_chars"},
    "list_schemas": {"include_system", "connection", "max_rows", "offset", "max_answer_chars"},
    "list_tables": {"schema", "connection", "max_rows", "offset", "max_answer_chars"},
    "describe_table": {"table", "schema", "connection", "max_answer_chars"},
    "list_indexes": {"table", "schema", "connection", "max_rows", "offset", "max_answer_chars"},
    "list_functions": {"schema", "connection", "max_rows", "offset", "max_answer_chars"},
    "describe_function": {"name", "schema", "args", "connection", "max_answer_chars"},
    "call_function": {"name", "schema", "args", "connection", "max_rows", "offset",
                      "max_answer_chars"},
    "call_procedure": {"name", "schema", "args", "connection", "max_rows", "offset",
                       "max_answer_chars"},
    "list_connections": set(),
    "connect": {"name", "host", "port", "user", "password", "password_env", "dbname", "sslmode"},
    "disconnect": {"name"},
}

HANDLER_DESCRIPTIONS = {
    "query":             "Run SQL (alias q/execute/exec/sql). params list → safe parameterised (extended) protocol",
    "explain":           "EXPLAIN [ANALYZE] [FORMAT TEXT|JSON] wrapper around sql",
    "list_schemas":      "List namespaces (include_system to show pg_*/information_schema)",
    "list_tables":       "List tables/views/matviews (schema filter, with row estimate)",
    "describe_table":    "Columns, indexes and foreign keys of schema.table",
    "list_indexes":      "Indexes for a table/schema (pg_indexes)",
    "list_functions":    "Functions/procedures (alias list_procedures): args, return, kind, language",
    "describe_function": "Full pg_get_functiondef source (+ args/return); 'args' disambiguates overloads",
    "call_function":     "SELECT schema.name($1,…) via extended protocol with text args",
    "call_procedure":    "CALL schema.name($1,…) via extended protocol with text args",
    "list_connections":  "List configured named connections (host/db/user, never password)",
    "connect":           "Register + open a named connection at runtime (name/host/port/user/password|password_env/dbname)",
    "disconnect":        "Close and drop a named connection",
}


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def handle_postgres_call(arguments: dict, mgr: ConnectionManager) -> dict:
    """Route a postgres_call invocation to the appropriate handler."""
    function = (arguments.get("function") or arguments.get("f") or "").strip()
    canonical_func = _canonical_function(function)
    raw_params = arguments.get("params") or arguments.get("p") or {}

    try:
        params = _resolve_aliases(raw_params, canonical_func)
    except ValueError as exc:
        return {"error": str(exc)}

    if not function:
        default_cfg = mgr.configs["default"]
        func_list = "\n".join(f"  {name}" for name in sorted(HANDLER_DESCRIPTIONS.keys()))
        target = (f"{default_cfg['user']}@{default_cfg['host']}:{default_cfg['port']}"
                  f"/{default_cfg['dbname']} (sslmode={default_cfg.get('sslmode', 'prefer')})")
        return {"__raw_text__": (
            f"mcp-postgres OK — default connection: {target}\n"
            f"(lazy connect: nothing is dialed until a query runs)\n"
            f"Available functions:\n{func_list}"
        )}

    handler = HANDLERS.get(function) or HANDLERS.get(canonical_func)
    if not handler:
        func_list = ", ".join(sorted(HANDLER_DESCRIPTIONS.keys()))
        return {"error": f"Unknown function: {function}. Available: {func_list}"}

    try:
        return handler(params, mgr)
    except PgError as exc:
        # Server-reported error — keep it on its own clean channel.
        return {"error": str(exc)}
    except (ValueError, ConnectionError, OSError) as exc:
        err = str(exc)
        accepted = HANDLER_ACCEPTED_PARAMS.get(canonical_func)
        if accepted is not None:
            unknown = sorted(set(params.keys()) - accepted)
            if unknown:
                err += (
                    f" | Unknown params for '{canonical_func}': {', '.join(unknown)}."
                    f" Accepted: {', '.join(sorted(accepted)) or '(none)'}."
                )
        return {"error": err}
    except Exception as exc:
        log.exception("Unhandled exception in handler '%s'", canonical_func)
        return {"error": f"Internal error in '{canonical_func}': {type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

POSTGRES_CALL_TOOL = {
    "name": "postgres_call",
    "description": (
        "PostgreSQL access over the native v3 wire protocol (pure stdlib — no "
        "libpq, no psycopg). One dispatcher tool; pick the operation via "
        "'function'. Connects lazily to the configured default server; extra "
        "named connections can be opened at runtime.\n\n"
        "ALL SQL is allowed — there is no read-only restriction. Use parameterised "
        "queries (params list) for anything with user/runtime values: it routes "
        "through the extended protocol so values are never string-spliced into SQL.\n\n"
        "Functions:\n"
        "  query (q/execute/exec/sql) — run SQL; params=[...] → parameterised ($1,$2,…)\n"
        "  explain                    — EXPLAIN [ANALYZE] [FORMAT TEXT|JSON]\n"
        "  list_schemas               — namespaces (include_system for pg_*/info schema)\n"
        "  list_tables                — tables/views (schema filter, row estimate)\n"
        "  describe_table             — columns + indexes + foreign keys\n"
        "  list_indexes               — indexes for a table/schema\n"
        "  list_functions (list_procedures) — name, args, return, kind, language\n"
        "  describe_function          — full pg_get_functiondef source\n"
        "  call_function              — SELECT schema.name($1,…) with text args\n"
        "  call_procedure             — CALL schema.name($1,…) with text args\n"
        "  connect/disconnect/list_connections — manage named connections\n\n"
        "Every handler takes an optional 'connection' (default \"default\").\n\n"
        "Result tables are `|`-delimited and UNALIGNED (header row, then one row "
        "per line). Cells are escaped reversibly: `\\\\`=backslash, `\\|`=literal "
        "`|`, plus `\\n`/`\\r`/`\\t`. Bare NULL = SQL null, empty field = empty "
        "string, `\\NULL` = the 4-char string \"NULL\" — so decide NULL-ness on the "
        "RAW field BEFORE unescaping. Cells over 120 chars are clamped with `…`. "
        "Row-shaped replies end with a paging line; pass `offset` to continue.\n\n"
        "Examples:\n"
        "  function=\"query\", params={\"sql\":\"SELECT version()\"}\n"
        "  function=\"query\", params={\"sql\":\"SELECT * FROM users WHERE id=$1\",\"params\":[42]}\n"
        "  function=\"describe_table\", params={\"table\":\"public.users\"}\n"
        "  function=\"call_function\", params={\"name\":\"abs\",\"args\":[-5]}\n"
        "  function=\"explain\", params={\"sql\":\"SELECT 1\",\"analyze\":true}\n"
        "  function=\"connect\", params={\"name\":\"ro\",\"host\":\"ssl://10.0.0.5:5432\",\"dbname\":\"app\"}\n"
        "Call without 'function' for server status + the full function list."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "function": {
                "type": "string",
                "description": "Function name to call",
            },
            "params": {
                "type": "object",
                "description": "Function parameters",
            },
        },
    },
}


class McpServer:
    """Minimal MCP server over stdio (JSON-RPC 2.0, one JSON object per line)."""

    PROTOCOL_VERSION = "2024-11-05"

    def __init__(self, manager: ConnectionManager):
        self.manager = manager

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        log.info("MCP server starting")
        try:
            while True:
                line = await loop.run_in_executor(None, sys.stdin.readline)
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue

                try:
                    msg = json.loads(line)
                except json.JSONDecodeError as exc:
                    log.warning("Invalid JSON: %s", exc)
                    continue

                log.debug("← %s", json.dumps(msg)[:200])
                try:
                    response = self._handle_message(msg)
                except Exception as exc:
                    log.exception("Unhandled exception while handling message")
                    response = self._error(
                        msg.get("id"), -32603,
                        f"Internal error: {type(exc).__name__}: {exc}",
                    )
                if response is not None:
                    out = json.dumps(response)
                    log.debug("→ %s", out[:200])
                    sys.stdout.write(out + "\n")
                    sys.stdout.flush()
        finally:
            log.info("MCP server shutting down")
            self.manager.close_all()

    def _handle_message(self, msg: dict) -> Optional[dict]:
        msg_id = msg.get("id")
        method = msg.get("method", "")
        params = msg.get("params") or {}

        # Notifications (no id) — no response
        if msg_id is None:
            log.debug("Notification: %s", method)
            return None

        if method == "initialize":
            return self._result(msg_id, {
                "protocolVersion": self.PROTOCOL_VERSION,
                "serverInfo": {"name": "mcp-postgres", "version": "1.0.0"},
                "capabilities": {"tools": {}},
            })

        if method == "ping":
            return self._result(msg_id, {})

        if method == "tools/list":
            return self._result(msg_id, {"tools": [POSTGRES_CALL_TOOL]})

        if method == "tools/call":
            return self._handle_tool_call(msg_id, params)

        return self._error(msg_id, -32601, f"Method not found: {method}")

    def _handle_tool_call(self, msg_id: Any, params: dict) -> dict:
        tool_name = params.get("name", "")
        arguments = params.get("arguments") or {}

        if tool_name != "postgres_call":
            return self._error(msg_id, -32602, f"Unknown tool: {tool_name}")

        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                pass
        if not isinstance(arguments, dict):
            return self._result(msg_id, {
                "content": [{"type": "text", "text":
                    f"'arguments' must be an object; got {type(arguments).__name__}."}],
                "isError": True,
            })

        try:
            result = handle_postgres_call(arguments, self.manager)
        except Exception as exc:
            log.exception("Unhandled exception in handle_postgres_call")
            result = {"error": f"Internal server error: {type(exc).__name__}: {exc}"}

        is_error = "error" in result
        text = result.get("__raw_text__") or result.get("error", "")

        return self._result(msg_id, {
            "content": [{"type": "text", "text": text}],
            "isError": is_error,
        })

    @staticmethod
    def _result(msg_id: Any, result: Any) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    @staticmethod
    def _error(msg_id: Any, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}

    @staticmethod
    def _tool_error(msg_id: Any, text: str) -> dict:
        return McpServer._result(msg_id, {"content": [{"type": "text", "text": text}], "isError": True})


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_default_config(args) -> dict:
    """Resolve env vars then CLI overrides into the default connection config."""
    env_host = os.environ.get("MCP_POSTGRE_HOST", "127.0.0.1:5432")
    host, port, ssl_hint = parse_host(env_host)

    if args.host is not None:
        # Allow --host to also carry a scheme (ssl://…) for symmetry with the env var.
        h2, p2, hint2 = parse_host(args.host)
        host = h2
        body = args.host.split("://", 1)[-1].split("/", 1)[0]
        if "://" in args.host:                 # scheme present → adopt its SSL hint
            ssl_hint = hint2
        if ":" in body and body.rsplit(":", 1)[1].isdigit() and not body.endswith("]"):
            port = p2                            # explicit port present
    if args.port is not None:
        port = args.port

    sslmode = args.sslmode if args.sslmode else ssl_hint

    user = args.user or os.environ.get("MCP_POSTGRE_USER") or getpass.getuser()
    if args.password is not None:
        password = args.password
    else:
        password = os.environ.get("MCP_POSTGRE_PASS", "")
    dbname = args.dbname or os.environ.get("MCP_POSTGRE_DB") or "postgres"

    return {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "dbname": dbname,
        "sslmode": sslmode,
    }


def main() -> None:
    if "--list" in sys.argv:
        print("mcp-postgres — available functions:\n")
        for name in sorted(HANDLER_DESCRIPTIONS.keys()):
            print(f"  {name:20s} {HANDLER_DESCRIPTIONS[name]}")
        sys.exit(0)

    parser = argparse.ArgumentParser(description="mcp-postgres — pure-stdlib PostgreSQL MCP server")
    parser.add_argument("--host", default=None, help="Host, optionally with scheme ([ssl://]ip[:port])")
    parser.add_argument("--port", type=int, default=None, help="Port (default 5432)")
    parser.add_argument("--user", default=None, help="Username (default: env or OS user)")
    parser.add_argument("--password", default=None, help="Password (default: env or empty)")
    parser.add_argument("--dbname", default=None, help="Database name (default: env or 'postgres')")
    parser.add_argument("--sslmode", choices=["disable", "prefer", "require"], default=None,
                        help="SSL mode; overrides the host-scheme hint")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging to stderr")
    parser.add_argument("--log-file", help="Log to file (implies --debug)")
    args = parser.parse_args()

    level = logging.DEBUG if (args.debug or args.log_file) else logging.WARNING
    log_handlers: list = []
    if args.log_file:
        log_handlers.append(logging.FileHandler(args.log_file))
    else:
        log_handlers.append(logging.StreamHandler(sys.stderr))

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=log_handlers,
    )

    default_config = _build_default_config(args)
    log.info("default connection: %s@%s:%s/%s (sslmode=%s)",
             default_config["user"], default_config["host"], default_config["port"],
             default_config["dbname"], default_config["sslmode"])

    manager = ConnectionManager(default_config)
    server = McpServer(manager)
    asyncio.run(server.run())


if __name__ == "__main__":
    main()

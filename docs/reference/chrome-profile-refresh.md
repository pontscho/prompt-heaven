---
name: chrome-profile-refresh
type: reference
status: active
title: Chrome profile refresh — re-measuring the stdlib client against a new Chrome
description: The end-to-end procedure that moves the stdlib Chrome client to a new Chrome major — capture on loopback, export fixtures, edit the one profile table until diff exits 0, regenerate the three hosts, re-gate, retire the old fixtures, record the pin — and why the profile-age row is INFO forever.
sources:
  - Scripts/_mcp_chrome.py:_chrome_profile
  - Scripts/_mcp_chrome.py:CHROME_PROFILE
  - Scripts/chrome_capture.py
  - Scripts/amalgamate.py:WHOLE_SOURCES
  - Scripts/amalgamate.py:DECLARED_HOSTS
  - tests/test_mcp_chrome.py:FIXTURES
  - tests/test_chrome_capture.py
  - tests/test_mcp_chrome.py:is_cors_stream
  - tests/test_mcp_chrome.py:profile_problems
  - tests/files/chrome/154/README.md
verified:
  commit: bdbf852
  date: 2026-10-02
links:
  - 0004-never-pin-a-browser-impersonation-version
  - 0019-only-gate-on-what-you-can-prove
  - 0025-generate-do-not-import
  - generated-regions
  - spec-ddg
  - tests
  - scripts
---

# Chrome profile refresh

The stdlib Chrome client pins **one** Chrome: every version-bearing value — user
agent, header orders, TLS lists, h2 settings — lives in a single table,
`Scripts/_mcp_chrome.py:_chrome_profile`, bound once as
`Scripts/_mcp_chrome.py:CHROME_PROFILE`, and every line of it cites the committed
capture it was read from. A refresh is therefore not an edit but a
**re-measurement**: a real browser is recorded on loopback, the recordings become
the new oracle, and the table is changed until the independent parser stops
seeing a difference. This page is that procedure end to end, plus the one
test row that tells you when it is due.

Why the client is pinned at all, when [[0004-never-pin-a-browser-impersonation-version]]
forbade pinning an impersonation *name*: that ADR's hazard was a library silently
resolving a pinned name to a random browser. Here nothing resolves anything — the
pin is the measured bytes, and an old pin degrades into an *old* Chrome, never a
different one. The decision itself is recorded in the ADR 0004 addendum and the
client's own ADR, not here.

## The two keys the procedure turns on

- `CHROME_PROFILE["major"]` — the Chrome major the table describes.
- `CHROME_PROFILE["pinned_on"]` — the capture date of the fixture directory
  (the README's `meta.captured_on`, UTC). The profile-age row counts from it
  (below), and `Scripts/mcp-webfetch.py:handle_webfetch_call` prints both in its
  status text.

## The oracle is not the module under test

The fixtures under `tests/files/chrome/<major>/` are recorded measurements, and
the tool that reads them, `Scripts/chrome_capture.py`, is the independent oracle:
it shares no code with `_mcp_chrome.py` and must never import it. Never
regenerate a fixture from the current client — that would make the client its own
judge. The fixture README (`tests/files/chrome/154/README.md` for the current
pin) holds the environment, the JA4 table, the layout of the sets and the
per-set server flags; the suite reads its numbers from there, so the README is
part of the fixture, not commentary on it.

`tests/files/chrome_capture/navigate-r0017.json` is a different fixture: the one
historical R-0017 ClientHello the `chrome_capture` suite (`tests/test_chrome_capture.py`)
tests the *tool* against. It does not describe the pinned profile and a refresh
leaves it alone.

## The procedure

Run every command from the repo root. Scratch output goes under `.claude/tmp/`.

1. **Install the new Chrome.** Record the full version and
   `navigator.userAgent` / `userAgentData` — the new README's environment table
   needs them.

2. **Record the sets on loopback.** One `serve` per set, with that set's server
   flags as the current README's *Layout* table lists them:

   ```
   python3 -B Scripts/chrome_capture.py serve --port <p> --out .claude/tmp/<dir> --label <label> --count <n> \
       [--alpn http/1.1 | --plain | --set-cookie tc=1 | --hrr-group 0x0017]
   ```

   `serve` binds 127.0.0.1 only and refuses any other `--host`. Without
   `--cert`/`--key` it uses the committed test leaf `tests/files/tls/localhost-cert.pem`
   + `localhost-key.pem`. `--idle` (default 2.0 s) closes a silent connection, so
   the next navigation opens a new one.

3. **Drive Chrome over gdc** (CDP, user-authorized, loopback only). Navigate to
   the set's origin, pass the certificate interstitial once **if one is shown**
   (`#details-button`, then `#proceed-link`) — the 154 run showed none, and
   Chrome still rejected the untrusted leaf at the TLS layer on its failed
   attempts — then navigate / reload / evaluate the set's JavaScript
   once per connection with a pause longer than `--idle` between them. The exact
   JavaScript per set is in the README's *Exact JavaScript evaluated* section.
   Keep only the records without `handshake_error` and with a non-empty request
   list — the README's *four-record phenomenon* section explains why an
   untrusted certificate yields failed attempts next to each successful
   connection. Select by those two properties, never by position: how many
   failed attempts precede a kept record is itself version-dependent (below).

4. **Export the fixtures**, one subdirectory per set:

   ```
   python3 -B Scripts/chrome_capture.py export --from <scratch dir> --to tests/files/chrome/<major>/<set> \
       --label <label> --chrome-version <full version>
   ```

   `--label` is one of the tool's fixed choices (`navigate`, `cors-get`,
   `cors-post`, `cors-head`, `nav-cors-pair`, `h1-tls`, `h1-plain`, `ip-literal`,
   `cookie`, `hrr`). `export` refuses — exit 1, naming the field, writing
   nothing — any capture whose authority, Host, Origin, Referer, request target
   or SNI names anything but loopback; a cookie the capture server did not set,
   or (in a record that says what it set) a server-set cookie carrying another
   value; a credential header (`authorization`, `proxy-authorization`,
   `x-client-data`, `x-api-key`, any `*-token` or `*auth*` name); a request body
   that was truncated, or is non-empty and is neither the scripted sets' own
   (`EXPORT_SCRIPTED_BODIES`: `q=test&kl=` from the cors-post JavaScript) nor
   one you name with `--allow-body <text>` after reading it; or a value
   carrying this machine's home path, repository path or hostname.

   What `export` does **not** check: every other header value and every
   frame payload outside a request body is copied as sent, and a denylist
   catches only the names it lists. So, before committing, **review
   `git diff tests/files/chrome/<major>/`** — every header, cookie and body the
   new fixtures carry — as the last gate (a new major's files are untracked, so
   `git add -N tests/files/chrome/<major>/` first, or `git diff` shows nothing).
   Then write the new README in the
   current one's shape, including the JA4 table (`chrome_capture.py ja4 <file>`
   prints a fixture's JA4).

5. **Run `mcp_chrome` and watch it fail.** The suite's fixture directory is the
   constant `tests/test_mcp_chrome.py:FIXTURES`, which spells the major as a path
   literal — it does **not** derive it from `CHROME_PROFILE["major"]`. So the
   refresh repoints it at `tests/files/chrome/<new major>`, and only then does
   `forge_call test {targets: [mcp_chrome]}` judge the old profile against the
   new captures and FAIL — the expected red: the oracle has moved, the table
   has not.

6. **Edit `_chrome_profile()` only**, line by line, each new value citing the
   fixture subdirectory it was read from, until `diff` reports no fixed
   difference between a new Chrome set and a client capture:

   ```
   python3 -B Scripts/chrome_capture.py diff --reference tests/files/chrome/<major>/<set> --candidate <client capture> [--out <report>]
   ```

   Exit 0 = no fixed difference; 1 = at least one, each named in the report;
   2 = the two sides have no group in common (refused rather than called
   "no difference"). GREASE and randomness are normalised away, and the report's
   *varying* section says what was. A client capture is the client pointed at
   `serve`: a `_ch_session_new(transport="chrome")` session — the Chrome path; the
   default `transport="verified"` is Python's ssl ClientHello and measures nothing
   here — whose default `connect_policy=None` is `_ch_unvetted_policy`, the
   tests-only policy that admits loopback.

7. **Regenerate the hosts:** `python3 Scripts/amalgamate.py`. `_mcp_chrome.py` is
   a whole source (`Scripts/amalgamate.py:WHOLE_SOURCES`) and no host imports it
   ([[0025-generate-do-not-import]]); its blocks live as generated regions in
   `Scripts/mcp-webfetch.py` and in the two declared non-server hosts
   `Scripts/search_duckduckgo.py` and `Scripts/search_github.py`
   (`Scripts/amalgamate.py:DECLARED_HOSTS`). See [[generated-regions]].

8. **Re-gate:** `forge_call test {targets: [mcp_chrome, generated_region, amalgamate_check]}`
   (the last one runs `python3 -B Scripts/amalgamate.py --check`; a clean tree
   prints nothing and the exit code is the verdict). If
   the suite's case count moved, the one place it is written is the `SUITES`
   table in `tests/run.py`, and a drift there is a hard FAIL ([[tests]]).

9. **Delete the old fixture directory** `tests/files/chrome/<old major>/`, once
   nothing reads it.

10. **Record the new pin as an ADR 0004 addendum** — the major, the full version,
    the platform and the capture date — appended with the wiki's
    `ClaudeCode/skills/wiki/scripts/addendum.py --page <adr> --item-file <staged JSON>`,
    the JSON staged under `.claude/tmp/`. An accepted ADR is append-only; the
    addendum is its one legal write.

11. **Live check, with the user's OK** — never without it. One request per real
    endpoint the hosts use (DuckDuckGo's POST, Bing's GET, grep.app's cors GET,
    one ordinary site through webfetch's `profile=chrome`), and on loopback the
    client's JA4, as computed by `chrome_capture.py ja4`, equal to the value the
    new README records for a fresh ClientHello with an SNI.

## What the code says the refresh touches, and what it also touches

The client's own docstring says a refresh "edits that function and the fixture
directory, and nothing else" (`Scripts/_mcp_chrome.py`). Measured against the
tree that is a floor, not the whole set:

- the fixture path in `tests/test_mcp_chrome.py:FIXTURES` (step 5), and the
  suite's other `tests/files/chrome/<old major>/...` mentions, which are row
  *source* strings rather than paths it opens;
- the docstring of `_chrome_profile` itself, which names its source of record,
  and the h2 stream numbers its comments cite for the cors values — those move
  when Chrome's stream layout moves (below);
- hand-written "Chrome <old major>" prose **outside** the generated regions — the
  module docstrings of the three hosts, webfetch's `profile` refusal text and
  bot-block hint, and the test rows that compare those strings verbatim. Only
  `handle_webfetch_call`'s status text reads the major from `CHROME_PROFILE`.

None of these is caught by `amalgamate.py --check`, which judges regions only.
A search of the tree for the old major after step 8 is the check.

## What the 153 → 154 refresh corrected (R-0051)

The first run of this procedure (Chrome 153.0.8010.37 → 154.0.8037.58, captured
2026-10-02) found three assumptions the 153 capture had baked in. They are
recorded in `tests/files/chrome/154/README.md`; the rules they leave are these.

- **An h2 stream id is a measurement, not a constant.** Under 153 every
  navigate-then-fetch connection also requested
  `/.well-known/appspecific/com.chrome.devtools.json`, so the page's `fetch`
  was stream 7; 154 sent no such request, so the fetch is stream 5 (whether
  that is Chrome or the driven tab's DevTools state was not determined). The
  suite's helper had matched `sid == 7`, and under 154 that comparison would
  have been **skipped silently** — the rows still passed on the navigation and
  subresource streams. The suite now finds the fetch by its fixture's
  `sec-fetch-mode: cors` `tests/test_mcp_chrome.py:is_cors_stream`, and fails a
  cors-set connection that does not carry exactly one such stream
  `tests/test_mcp_chrome.py:profile_problems`. A test locates a captured stream
  by its content, never by a typed id. Note also that `chrome_capture.py diff`
  judges stream 1 only, so `diff` exiting 0 (step 6) says nothing about the cors
  stream; the `mcp_chrome` profile-header rows are what cover it.
- **The failed-attempt count per connection moves.** Against the untrusted
  loopback leaf 153 left three records per kept connection, 154 four (one more
  failed full handshake). Step 3 therefore selects by `handshake_error` and the
  request list, not by position.
- **The interstitial may not appear.** None was shown in the 154 run; step 3
  passes it only if it is there.

## The profile-age row is INFO forever

`mcp_chrome`'s throughput group ends with one row, `profile-age`, recorded with
`status=H.INFO` (`tests/test_mcp_chrome.py`): it reads
`CHROME_PROFILE["pinned_on"]` and reports `Chrome <major> profile pinned on
<date>: <n> day(s) old`; a `pinned_on` that is not an ISO date is reported in the
same INFO row, not failed.

It never FAILs, by the rule of [[0019-only-gate-on-what-you-can-prove]]: an age
is a measurement, not a verdict. No threshold can be proven — an older profile is
not wrong on a known day, it is merely less like what current browsers send, and
whether that matters is decided by servers this repo does not control. A gate on
the calendar would turn a tree red with no change in it, and FAIL is reserved for
rules that cannot flap ([[tests]]). So the row is the reminder, and the refresh
above is the response to it — when the number looks large, or when a host's
answers start looking like blocks ([[spec-ddg]] records what a block looks like).

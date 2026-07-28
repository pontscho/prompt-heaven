#!/usr/bin/env python3
"""Suite for ClaudeCode/hooks/mcp-first-guard.py, the PreToolUse Bash guard
(165 cases, groups A-J).

Runs the hook as a subprocess once per case, feeding a JSON payload on stdin.
A case is DENY when stdout parses to a dict whose
hookSpecificOutput.permissionDecision == "deny"; otherwise ALLOW.

Also asserts the hook ALWAYS exits 0 and that ALLOW means literally no stdout.

Cases whose `expect` is "INFO" are informational: actual behaviour is recorded
and printed, never counted as a failure.  An INFO case that breaks a hard
invariant (non-zero exit, dirty stderr) still fails.

Coverage by group:
  A  new DENY entries as the PRIMARY command of a statement (cat/head/tail/awk)
  B  new ALLOW: `tail` follow-mode exemption (-f/-F/--follow/--retry, abbrevs)
  C  new ALLOW: a blocked binary as a DOWNSTREAM pipe stage
  D  the follow-mode exemption must not leak to other tools
  E  regression: every pre-existing primary-DENY / downstream-ALLOW plus steers
  F  heredoc awareness: bodies stripped, intro line and post-terminator analyzed
  G  plumbing: non-Bash tools, missing/null fields, fail-open, separators, dedup
  H  extra edges, informational only -- actual behaviour recorded
  I  herestring fix (D1): `<<<` is no longer mistaken for a heredoc
  J  `&` as a statement separator, plus its false-positive probes (2>&1, URLs)

The hook is never imported in the default run -- only `--whitebox` imports it,
and then only via a loader that cannot write __pycache__ into the repo.

Usage:
  python3 tests/test_mcp_first_guard.py
  python3 tests/test_mcp_first_guard.py --brief
  python3 tests/test_mcp_first_guard.py --whitebox
Exit code 0 iff every non-informational case passes.
"""

import json
import os
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _harness as H  # noqa: E402

NAME = "mcp_first_guard"
HOOK = H.repo_path("ClaudeCode", "hooks", "mcp-first-guard.py")

DENY, ALLOW, INFO = "DENY", "ALLOW", "INFO"


def case(group, name, cmd, expect, must=(), must_not=(), note=""):
    return {
        "group": group, "name": name, "expect": expect,
        "payload": json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}}),
        "must": list(must), "must_not": list(must_not), "note": note,
    }


def raw(group, name, payload, expect, note=""):
    return {"group": group, "name": name, "expect": expect, "payload": payload,
            "must": [], "must_not": [], "note": note}


CASES = []
A = "A. New DENY (primary command)"
CASES += [
    case(A, "awk program on file", "awk '{print $2}' f.txt", DENY, must=["`awk`"]),
    case(A, "cat relative file", "cat f.txt", DENY, must=["`cat`"]),
    case(A, "cat absolute system file", "cat /etc/hosts", DENY, must=["`cat`"]),
    case(A, "head -20", "head -20 f.txt", DENY, must=["`head`"]),
    case(A, "tail -20", "tail -20 f.txt", DENY, must=["`tail`"]),
    case(A, "tail -n 100", "tail -n 100 f.txt", DENY, must=["`tail`"]),
    case(A, "tail bare", "tail f.txt", DENY, must=["`tail`"]),
    case(A, "sudo cat", "sudo cat /etc/shadow", DENY, must=["`cat`"]),
    case(A, "env cat", "env cat f", DENY, must=["`cat`"]),
    case(A, "xargs cat", "xargs cat", DENY, must=["`cat`"]),
    case(A, "cat && ls (two hits)", "cat a.txt && ls", DENY, must=["`cat`", "`ls`"]),
    case(A, "VAR=1 awk -f prog", "VAR=1 awk -f prog.awk f.txt", DENY, must=["`awk`"]),
    case(A, "absolute /bin/cat", "/bin/cat f", DENY, must=["`cat`"]),
    case(A, "cat with > redirect", "cat f.txt > out.txt", DENY, must=["`cat`"]),
    # extra edges in the same class
    case(A, "tail --lines=100 (not follow)", "tail --lines=100 f.txt", DENY, must=["`tail`"]),
    case(A, "head -c 10", "head -c 10 f.txt", DENY, must=["`head`"]),
    case(A, "awk -F: script", "awk -F: '{print $1}' /etc/passwd", DENY, must=["`awk`"]),
    case(A, "cat two files", "cat a.txt b.txt", DENY, must=["`cat`"]),
    case(A, "nohup time cat (nested wrappers)", "nohup time cat f", DENY, must=["`cat`"]),
    case(A, "cat | grep (cat primary)", "cat f.txt | grep x", DENY,
         must=["`cat`"], must_not=["`grep`"]),
    case(A, "tail -f as 2nd statement", "echo hi && tail -20 f", DENY, must=["`tail`"]),
]

B = "B. New ALLOW (tail follow-mode exemption)"
CASES += [
    case(B, "tail -f", "tail -f /var/log/x.log", ALLOW),
    case(B, "tail -F", "tail -F x", ALLOW),
    case(B, "tail -fn 100", "tail -fn 100 x", ALLOW),
    case(B, "tail -nf 100", "tail -nf 100 x", ALLOW),
    case(B, "tail --follow", "tail --follow x", ALLOW),
    case(B, "tail --follow=name", "tail --follow=name x", ALLOW),
    case(B, "tail --fol (abbrev)", "tail --fol x", ALLOW),
    case(B, "tail --retry", "tail --retry x", ALLOW),
    case(B, "sudo tail -f", "sudo tail -f /var/log/x", ALLOW),
    # extra edges in the same class
    case(B, "tail -f piped to grep", "tail -f /var/log/x | grep err", ALLOW),
    case(B, "tail -Fn 5", "tail -Fn 5 x", ALLOW),
    case(B, "tail --retr (abbrev)", "tail --retry=x x", ALLOW),
    case(B, "tail -f in 2nd statement", "echo hi && tail -f x", ALLOW),
    case(B, "exempt does not rescue sibling cat", "tail -f x && cat y", DENY,
         must=["`cat`"], must_not=["`tail`"]),
]

C = "C. New ALLOW (downstream pipe stage)"
CASES += [
    case(C, "git log | awk", "git log | awk '{print $1}'", ALLOW),
    case(C, "journalctl | cat", "journalctl -u foo | cat", ALLOW),
    case(C, "ps aux | head -5 -> DENY on ps only", "ps aux | head -5", DENY,
         must=["`ps`", "inspect_call(function=processes)"], must_not=["`head`"]),
    case(C, "python3 | tail -20", "python3 x.py | tail -20", ALLOW),
    case(C, "echo | awk", "echo hi | awk '{print}'", ALLOW),
    # extra edges in the same class
    case(C, "3-stage pipe, blocked in middle+end", "git log | cat | tail -5", ALLOW),
    case(C, "pipe with awk containing ; inside quotes", "git log | awk '{n++; print n}'", ALLOW),
    case(C, "pipe into head then blocked stmt after", "git log | head -5; cat f", DENY,
         must=["`cat`"], must_not=["`head`"]),
]

D = "D. Exemption must not leak to other tools"
CASES += [
    case(D, "grep -f patterns.txt .", "grep -f patterns.txt .", DENY, must=["`grep`"]),
    case(D, "ls -F", "ls -F", DENY, must=["`ls`"]),
    case(D, "find . -type f", "find . -type f", DENY, must=["`find`"]),
    # extra edges in the same class
    case(D, "sed --follow-ish long opt", "sed --file=script.sed f", DENY, must=["`sed`"]),
    case(D, "head -f (no entry for head)", "head -f x", DENY, must=["`head`"]),
    case(D, "awk -f (no entry for awk)", "awk -f prog.awk x", DENY, must=["`awk`"]),
    case(D, "du -f", "du -f /", DENY, must=["`du`"]),
]

E = "E. Regression (pre-existing behaviour)"
PRIMARY_DENY = [
    "grep -r foo .", "egrep foo f", "fgrep foo f", "rg foo", "ripgrep foo",
    "find . -name x", "fd x", "fdfind x", "locate x", "mlocate x", "plocate x",
    "ls -la", "sed -n 1p f", "mkdir /tmp/x", "ps aux", "lsof -i", "netstat -an",
    "ss -tlnp", "df -h", "du -sh .", "free -m",
]
for c in PRIMARY_DENY:
    CASES.append(case(E, f"primary DENY: {c}", c, DENY, must=[f"`{c.split()[0]}`"]))

DOWNSTREAM_ALLOW = [
    "journalctl | grep x", "git log | sed -n 1p", "git log | egrep x",
    "cmd | rg x", "cmd | ls", "cmd | find x", "cmd | fd x", "cmd | locate x",
    "cmd | mkdir x", "cmd | ps", "cmd | lsof", "cmd | netstat", "cmd | ss",
    "cmd | df", "cmd | du", "cmd | free", "cmd | fgrep x", "cmd | plocate x",
]
for c in DOWNSTREAM_ALLOW:
    CASES.append(case(E, f"downstream ALLOW: {c}", c, ALLOW))

CASES += [
    case(E, "mkdir -p .claude/tmp/x -> create_temp_dir steer",
         "mkdir -p .claude/tmp/x", DENY, must=["`mkdir`", "create_temp_dir", "create_text_file"]),
    case(E, "sudo mkdir /x", "sudo mkdir /x", DENY, must=["`mkdir`"]),
    case(E, "grep steer text", "grep foo f", DENY,
         must=["purity_call(search_for_pattern)"]),
    case(E, "ls steer text", "ls", DENY, must=["purity_call(list_dir)"]),
    case(E, "non-blocked primary allowed", "python3 -V", ALLOW),
    case(E, "git status allowed", "git status", ALLOW),
]

F = "F. Heredoc awareness"
HD_PLAIN = (
    "git commit -F - <<'EOF'\n"
    "cat and awk are mentioned here\n"
    "grep ls mkdir also\n"
    "EOF"
)
HD_TAB = (
    "git commit -F - <<-EOF\n"
    "\tcat f.txt\n"
    "\tmkdir -p /x\n"
    "\tEOF"
)
HD_AFTER = (
    "git commit -F - <<'EOF'\n"
    "harmless body with awk\n"
    "EOF\n"
    "cat /etc/hosts"
)
HD_UNQUOTED = (
    "git commit -F - <<EOF\n"
    "ls -la inside body\n"
    "EOF"
)
CASES += [
    case(F, "heredoc body words ignored (quoted delim)", HD_PLAIN, ALLOW),
    case(F, "heredoc <<-EOF tab-indented terminator", HD_TAB, ALLOW),
    case(F, "blocked command AFTER terminator still denies", HD_AFTER, DENY,
         must=["`cat`"], must_not=["`awk`"]),
    case(F, "unquoted delimiter body ignored", HD_UNQUOTED, ALLOW),
    case(F, "heredoc intro line itself is analyzed",
         "cat > f <<'EOF'\nbody\nEOF", DENY, must=["`cat`"]),
    case(F, "two heredocs in sequence",
         "git commit -F - <<'EOF'\nls\nEOF\ngit tag -F - <<'EOT'\nmkdir\nEOT", ALLOW),
]

G = "G. Plumbing"
CASES += [
    raw(G, "tool_name != Bash",
        json.dumps({"tool_name": "Read", "tool_input": {"command": "cat f"}}), ALLOW),
    raw(G, "tool_name missing",
        json.dumps({"tool_input": {"command": "cat f"}}), ALLOW),
    case(G, "empty command", "", ALLOW),
    case(G, "whitespace-only command", "   \n\t ", ALLOW),
    raw(G, "tool_input missing", json.dumps({"tool_name": "Bash"}), ALLOW),
    raw(G, "tool_input null", json.dumps({"tool_name": "Bash", "tool_input": None}), ALLOW),
    raw(G, "command null",
        json.dumps({"tool_name": "Bash", "tool_input": {"command": None}}), ALLOW),
    raw(G, "malformed JSON (fail-open)", "{not json at all", ALLOW),
    raw(G, "empty stdin (fail-open)", "", ALLOW),
    raw(G, "JSON array instead of object (fail-open)", "[1,2,3]", ALLOW),
    case(G, "; separator finds later hit", "echo a; ls", DENY, must=["`ls`"]),
    case(G, "&& separator finds later hit", "echo a && cat f", DENY, must=["`cat`"]),
    case(G, "|| separator finds later hit", "false || find .", DENY, must=["`find`"]),
    case(G, "newline separator finds later hit", "echo a\nsed -n 1p f", DENY, must=["`sed`"]),
    case(G, "quoted && not split (double quotes)", 'echo "a && b"', ALLOW),
    case(G, "quoted ; not split (single quotes)", "echo 'a; ls'", ALLOW),
    case(G, "quoted | not split", 'echo "a | cat"', ALLOW),
    case(G, "quoted newline-ish string", "echo 'multi cat line'", ALLOW),
    case(G, "dedup: two cats -> single mention", "cat a; cat b", DENY, must=["`cat`"]),
    case(G, "sorted multi-hit reason", "ls; cat f; grep x .", DENY,
         must=["`cat`, `grep`, `ls`"]),
]

H_GROUP = "H. Extra edges (informational — actual behaviour recorded)"
CASES += [
    case(H_GROUP, "subshell `(cat f)`", "(cat f.txt)", INFO,
         note="paren-wrapped primary command"),
    case(H_GROUP, "brace group `{ cat f; }`", "{ cat f; }", INFO,
         note="brace-group wrapped primary command"),
    case(H_GROUP, "command substitution `echo $(cat f)`", "echo $(cat f)", INFO,
         note="blocked cmd inside $( )"),
    case(H_GROUP, "bash -c 'cat f'", "bash -c 'cat f'", INFO, note="blocked cmd inside -c string"),
    case(H_GROUP, "tail 'weird -f name'", "tail 'weird -f name.txt'", INFO,
         note="mode_exempt splits without quote awareness"),
    case(H_GROUP, "tail -- -f (after end-of-options)", "tail -- -f", INFO,
         note="`--` terminator not honoured by mode_exempt"),
    case(H_GROUP, "herestring `foo <<<EOF` then blocked line", "foo <<<WORD\ncat f", INFO,
         note="does <<< get mistaken for a heredoc, swallowing the next line?"),
    case(H_GROUP, "backgrounded `cat f &`", "cat f &", INFO, note="& is not a split separator"),
    case(H_GROUP, "`&` chain: echo a & cat f", "echo a & cat f", INFO,
         note="`&` separator not split -> later hit may be missed"),
    case(H_GROUP, "tab-separated command", "\tcat\tf.txt", INFO, note="leading tab + tab args"),
    case(H_GROUP, "CAT uppercase", "CAT f", INFO, note="case sensitivity"),
    case(H_GROUP, "./cat f", "./cat f", INFO, note="basename of ./cat"),
    case(H_GROUP, "herestring probe: `<<<WORD` .. WORD .. ls",
         "foo <<<WORD\ncat f\nWORD\nls", INFO,
         note="if `<<<` is parsed as heredoc, `cat` is swallowed and only `ls` is flagged"),
    case(H_GROUP, "herestring quoted `<<<'WORD'` then blocked line",
         "foo <<<'WORD'\ncat f", INFO, note="quoted herestring variant"),
]

# ---------------------------------------------------------------------------
# Added after the D1 (herestring lookarounds) + `&` separator fixes.
# ---------------------------------------------------------------------------
VIOL = "MCP-first routing violation: "


def only(*names):
    """Reason-prefix assertion: the hit list is EXACTLY these names, in order."""
    return VIOL + ", ".join(f"`{n}`" for n in names) + " is forbidden"


I_GROUP = "I. Herestring fix (D1) — `<<<` is no longer a heredoc"
CASES += [
    case(I_GROUP, "`foo <<<WORD` + cat on next line", "foo <<<WORD\ncat f", DENY,
         must=[only("cat")]),
    case(I_GROUP, "`foo <<<'WORD'` + cat on next line", "foo <<<'WORD'\ncat f", DENY,
         must=[only("cat")]),
    case(I_GROUP, "`foo <<<\"$var\"` + ls on next line", 'foo <<<"$var"\nls', DENY,
         must=[only("ls")]),
    case(I_GROUP, "`grep x <<< \"$var\"` (single line)", 'grep x <<< "$var"', DENY,
         must=[only("grep")]),
    case(I_GROUP, "`python3 x.py <<<WORD` + cat", "python3 x.py <<<WORD\ncat f", DENY,
         must=[only("cat")]),
    case(I_GROUP, "`<<<WORD` .. WORD .. ls -> BOTH cat and ls",
         "foo <<<WORD\ncat f\nWORD\nls", DENY, must=[only("cat", "ls")]),
    # regression: real heredocs must still have their bodies stripped
    case(I_GROUP, "real heredoc quoted delim still ALLOW", HD_PLAIN, ALLOW),
    case(I_GROUP, "real heredoc <<-EOF tab form still ALLOW", HD_TAB, ALLOW),
    case(I_GROUP, "no space before `<<`: `git commit -F -<<EOF`",
         "git commit -F -<<EOF\ncat f\nawk '{print}'\nEOF", ALLOW),
    case(I_GROUP, "redirect glued: `cmd 2><<EOF`",
         "git commit -F - 2><<EOF\ncat f\nls -la\nEOF", ALLOW),
    case(I_GROUP, "heredoc after pipe: `foo | git commit -F - <<'EOF'`",
         "foo | git commit -F - <<'EOF'\nmkdir /x\nEOF", ALLOW),
    case(I_GROUP, "herestring alone (no later line)", "foo <<<WORD", ALLOW),
    case(I_GROUP, "herestring then heredoc: body stripped, herestring line not",
         "foo <<<WORD\ngit commit -F - <<'EOF'\ncat f\nEOF", ALLOW),
    case(I_GROUP, "4 angle brackets `foo <<<<WORD` + cat", "foo <<<<WORD\ncat f", DENY,
         must=[only("cat")]),
]

J_GROUP = "J. `&` statement separator"
CASES += [
    case(J_GROUP, "echo a & cat f", "echo a & cat f", DENY, must=[only("cat")]),
    case(J_GROUP, "sleep 1 & grep x .", "sleep 1 & grep x .", DENY, must=[only("grep")]),
    case(J_GROUP, "cat f & (unchanged)", "cat f &", DENY, must=[only("cat")]),
    case(J_GROUP, "echo a && cat f -> `cat` named once", "echo a && cat f", DENY,
         must=[only("cat")]),
    case(J_GROUP, "echo a &&& cat f", "echo a &&& cat f", INFO,
         note="`&&` consumed first, then a bare `&` -> empty middle statement"),
    # false-positive probes: must stay ALLOW
    case(J_GROUP, "quoted & in URL", 'curl "http://x?a=1&b=2"', ALLOW),
    case(J_GROUP, "UNQUOTED & in URL (2nd stmt starts `b=2`)", "curl http://x?a=1&b=2", ALLOW),
    case(J_GROUP, "echo x 2>&1", "echo x 2>&1", ALLOW),
    case(J_GROUP, "echo x >&2", "echo x >&2", ALLOW),
    case(J_GROUP, "cat f 2>&1 -> names ONLY cat", "cat f 2>&1", DENY, must=[only("cat")]),
    case(J_GROUP, "find . -name 'a&b' -> names ONLY find", "find . -name 'a&b'", DENY,
         must=[only("find")]),
    # extra & edges
    case(J_GROUP, "background then blocked, 3 deep", "sleep 1 & sleep 2 & ls", DENY,
         must=[only("ls")]),
    case(J_GROUP, "tail -f backgrounded stays exempt", "tail -f x &", ALLOW),
    case(J_GROUP, "& inside heredoc body ignored",
         "git commit -F - <<'EOF'\na & cat f\nEOF", ALLOW),
    case(J_GROUP, "&& then & mix", "echo a && echo b & cat f", DENY, must=[only("cat")]),
    case(J_GROUP, "cmd | tail -5 & (downstream, backgrounded)", "git log | tail -5 &", ALLOW),
]


# ---------------------------------------------------------------------------
# execution
# ---------------------------------------------------------------------------

def run_hook(payload):
    return H.run_process([sys.executable, HOOK], stdin_text=payload, timeout=20)


def classify(stdout):
    s = stdout.strip()
    if not s:
        return ALLOW, ""
    try:
        obj = json.loads(s)
    except Exception:
        return "BADJSON", s
    if not isinstance(obj, dict):
        return "BADJSON", s
    hso = obj.get("hookSpecificOutput") or {}
    if hso.get("permissionDecision") == "deny":
        if hso.get("hookEventName") != "PreToolUse":
            return "BADSHAPE", s
        if not isinstance(hso.get("permissionDecisionReason"), str):
            return "BADSHAPE", s
        return DENY, hso.get("permissionDecisionReason") or ""
    return "OTHER", s


def whitebox():
    """Show the hook's raw splitter / heredoc-stripper output.

    Needed because some reasons are string-identical whether or not `&&` was
    mis-split into two bare `&` (empty statements yield no hit), so the
    subprocess view alone cannot prove the split shape.
    """
    g = H.load_module_from_path("guard", HOOK)
    seps = ["&&", "||", ";", "\n", "&"]
    print("\n--- white-box: split_top(cmd, ['&&','||',';','\\n','&']) ---")
    for s in ["echo a && cat f", "echo a & cat f", "echo a &&& cat f",
              "echo x 2>&1", "echo x >&2", "curl http://x?a=1&b=2",
              'curl "http://x?a=1&b=2"', "echo a || cat f", "cat f &"]:
        print(f"  {s!r:34s} -> {g.split_top(s, seps)}")
    print("--- white-box: _strip_heredocs ---")
    for s in ["foo <<<WORD\ncat f", "foo <<<'WORD'\ncat f",
              "git commit -F -<<EOF\ncat f\nEOF",
              "git commit -F - 2><<EOF\ncat f\nEOF",
              "foo <<<<WORD\ncat f", "grep x <<< \"$var\""]:
        print(f"  {s!r:44s} -> {g._strip_heredocs(s)!r}")


def run(opts=None):
    """Drive every case through the hook; return the Suite."""
    opts = opts or H.Options()
    suite = H.Suite(NAME, title="mcp-first-guard PreToolUse Bash hook",
                    opts=opts, mode="grouped")

    for c in CASES:
        rc, out, err = run_hook(c["payload"])
        actual, reason = classify(out)
        problems = []
        if rc != 0:
            problems.append(f"exit code {rc} (must be 0)")
        if err.strip():
            problems.append(f"stderr not empty: {err.strip()[:200]}")
        if c["expect"] != INFO:
            if actual != c["expect"]:
                problems.append(f"expected {c['expect']}, got {actual}")
            if actual == DENY:
                for m in c["must"]:
                    if m not in reason:
                        problems.append(f"reason missing {m!r}")
                for m in c["must_not"]:
                    if m in reason:
                        problems.append(f"reason wrongly contains {m!r}")

        if c["expect"] == INFO:
            status = INFO
        else:
            status = H.FAIL if problems else H.PASS
        detail = [f"cmd/payload : {c['payload']!r}",
                  f"expected={c['expect']:5s} actual={actual:7s} exit={rc}"]
        if c["note"]:
            detail.append(f"note        : {c['note']}")
        if actual == DENY:
            detail.append(f"reason      : {reason[:220]}")
        brief = (f"{status} | {c['name']} | exp={c['expect']} | act={actual}"
                 + (" | " + "; ".join(problems) if problems else ""))
        suite.record(c["group"], c["name"], problems, status=status,
                     detail=detail, brief=brief,
                     text=f"payload={c['payload']!r} actual={actual} reason={reason[:400]}")

    suite.render()
    if opts.whitebox:
        whitebox()

    suite.print_summary()
    return suite


def main(argv=None):
    opts = H.parse_options(argv)
    if opts.help:
        print(__doc__)
        return 0
    return run(opts).exit_code


if __name__ == "__main__":
    sys.exit(main())

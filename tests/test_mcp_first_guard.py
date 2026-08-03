#!/usr/bin/env python3
"""Suite for ClaudeCode/hooks/mcp-first-guard.py, the PreToolUse Bash guard
(groups A-N).

The case COUNT is deliberately absent from this docstring: it is written down
once, in the SUITES table in tests/run.py, which checks it against the run. A
second copy here would be a number nobody verifies.

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
  H  the wrapper forms deferred as D76, now CLOSED and asserted: subshell,
     brace group, command substitution, `bash -c`, ALL-CAPS spelling -- plus
     the two D3 probes that stay informational (see their notes)
  I  herestring fix (D1): `<<<` is no longer mistaken for a heredoc
  J  `&` as a statement separator, plus its false-positive probes (2>&1, URLs)
  K  wrapper unwrapping in depth (nesting, quoting, exemptions and
     false-positive probes) + the `python3 -m py_compile` steer and the scope
     limit that keeps `python3 -c` / `-m json.tool` allowed
  L  `python3 -m compileall` (every spelling and wrapper form that reaches the
     module, plus its false-positive probes) and the bundled short-option
     CLUSTER (`-Bm`, `-BEsm`, `-BEsmMOD`) for BOTH blocked modules
  M  the ALL-CAPS SHELL fold: `BASH -c 'cat f'` and every other SHELL_C member,
     which used to ALLOW because the fold covered only BLOCKED + the python
     interpreters, so an ALL-CAPS shell's payload was never extracted at all.
     Also the MAX_DEPTH budget the fold spends, the invariants that must survive
     inside a folded payload, and the false-positive probes (mixed case, a file
     literally named BASH, `VAR=val` prefixes, `$BASH`)
  N  `node --check` / `node -c` -> inspect_call(function=javascript), including
     every wrapper form and the short-cluster reading -- plus the probes that
     must stay ALLOW (`node file.js`, `-e`, `-p`, `-pc`, `--version`, npm/npx,
     a `--check` past the script operand). The rule is GATED on node being
     installed, so on a host without node the DENY rows assert the gate instead

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
import shutil
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


VIOL = "MCP-first routing violation: "


def only(*names):
    """Reason-prefix assertion: the hit list is EXACTLY these names, in order.

    Stronger than a bare `must=["`cat`"]`: it also proves nothing ELSE was
    flagged, which is how a peeled wrapper (`(cat f)`) is distinguished from a
    guard that started flagging the wrapper itself.
    """
    return VIOL + ", ".join(f"`{n}`" for n in names) + " is forbidden"


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

H_GROUP = "H. Wrapper forms (D76) — closed, now asserted"
CASES += [
    case(H_GROUP, "subshell `(cat f)`", "(cat f.txt)", DENY, must=[only("cat")],
         note="the `(` opener is peeled; only `cat` is named, not `(cat`"),
    case(H_GROUP, "brace group `{ cat f; }`", "{ cat f; }", DENY, must=[only("cat")],
         note="`{` is a token of its own here and is skipped like a wrapper"),
    case(H_GROUP, "command substitution `echo $(cat f)`", "echo $(cat f)", DENY,
         must=[only("cat")], must_not=["`echo`"],
         note="the $( ) body is scanned as a command in its own right"),
    case(H_GROUP, "bash -c 'cat f'", "bash -c 'cat f'", DENY, must=[only("cat")],
         must_not=["`bash`"], note="the -c STRING is scanned; bash itself is not blocked"),
    case(H_GROUP, "tail 'weird -f name'", "tail 'weird -f name.txt'", INFO,
         note="STILL INFO: mode_exempt splits the raw stage without quote "
              "awareness, so a filename containing `-f` reads as follow-mode. "
              "That is D3, a separate deferred decision the user has not "
              "reopened; it fails in the ALLOW direction, like every other "
              "limit in a fail-open guard."),
    case(H_GROUP, "tail -- -f (after end-of-options)", "tail -- -f", INFO,
         note="STILL INFO: same D3 deferral — mode_exempt does not honour the "
              "`--` end-of-options terminator, so a file literally named `-f` "
              "reads as follow-mode. Fails ALLOW-wards."),
    case(H_GROUP, "herestring `foo <<<EOF` then blocked line", "foo <<<WORD\ncat f", DENY,
         must=[only("cat")], note="`<<<` is not a heredoc: the next line is still scanned"),
    case(H_GROUP, "backgrounded `cat f &`", "cat f &", DENY, must=[only("cat")],
         note="`&` is a statement separator (D75)"),
    case(H_GROUP, "`&` chain: echo a & cat f", "echo a & cat f", DENY, must=[only("cat")],
         note="second statement after a bare `&` is scanned"),
    case(H_GROUP, "tab-separated command", "\tcat\tf.txt", DENY, must=[only("cat")],
         note="the tokenizer splits on any isspace(), leading tab included"),
    case(H_GROUP, "CAT uppercase", "CAT f", DENY, must=[only("cat")], must_not=["`CAT`"],
         note="ALL-CAPS spelling of a blocked name is folded to lower case "
              "(/bin/CAT resolves on a case-insensitive filesystem), and the "
              "reason names the canonical `cat` so dedup still works"),
    case(H_GROUP, "./cat f", "./cat f", DENY, must=[only("cat")], note="basename of ./cat"),
    case(H_GROUP, "herestring probe: `<<<WORD` .. WORD .. ls",
         "foo <<<WORD\ncat f\nWORD\nls", DENY, must=[only("cat", "ls")],
         note="both statements survive: `<<<` swallows nothing"),
    case(H_GROUP, "herestring quoted `<<<'WORD'` then blocked line",
         "foo <<<'WORD'\ncat f", DENY, must=[only("cat")], note="quoted herestring variant"),
]

# ---------------------------------------------------------------------------
# Added after the D1 (herestring lookarounds) + `&` separator fixes.
# ---------------------------------------------------------------------------

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

K_GROUP = "K. Wrapper unwrapping in depth + `python3 -m py_compile`"
CASES += [
    # -- subshell / brace group ---------------------------------------------
    case(K_GROUP, "spaced subshell `( cat f )`", "( cat f )", DENY, must=[only("cat")]),
    case(K_GROUP, "subshell plus sibling `(cat f) && ls`", "(cat f) && ls", DENY,
         must=[only("cat", "ls")]),
    case(K_GROUP, "innocent subshell `(cd x && make)`", "(cd x && make)", ALLOW),
    case(K_GROUP, "innocent brace group `{ echo hi; }`", "{ echo hi; }", ALLOW),
    case(K_GROUP, "wrapper plus group `time (cat f)`", "time (cat f)", DENY,
         must=[only("cat")], note="peeling composes with SKIP_WRAPPERS in any order"),
    case(K_GROUP, "inspection in a subshell `(ps aux)`", "(ps aux)", DENY, must=[only("ps")]),
    case(K_GROUP, "brace group, two statements", "{ cat a; ls; }", DENY,
         must=[only("cat", "ls")]),
    case(K_GROUP, "exemption survives peeling `(tail -f x)`", "(tail -f x)", ALLOW),
    case(K_GROUP, "`{cat f;}` is not a group in bash", "{cat f;}", ALLOW,
         note="bash needs whitespace after `{`, so `{cat` is a command NAME; "
              "peeling it would deny a command bash never runs"),
    # -- command substitution ----------------------------------------------
    case(K_GROUP, 'double-quoted subst `echo "$(cat f)"`', 'echo "$(cat f)"', DENY,
         must=[only("cat")], note='"$( )" still runs the command'),
    case(K_GROUP, "single-quoted subst `echo '$(cat f)'`", "echo '$(cat f)'", ALLOW,
         note="nothing expands inside '...' — false-positive probe"),
    case(K_GROUP, "backticks", "echo `cat f`", DENY, must=[only("cat")]),
    case(K_GROUP, "process substitution", "diff <(cat a) <(cat b)", DENY, must=[only("cat")]),
    case(K_GROUP, 'quoted process subst `echo "<(cat f)"`', 'echo "<(cat f)"', ALLOW,
         note='inside "..." a <( ) is literal text, not a command — FP probe'),
    case(K_GROUP, "arithmetic `echo $((1+2))`", "echo $((1+2))", ALLOW,
         note="`$((` is arithmetic; it holds no command"),
    case(K_GROUP, "subst feeding another command", "kill $(lsof -t -i:3000)", DENY,
         must=[only("lsof")]),
    case(K_GROUP, "innocent subst", "echo $(git rev-parse HEAD)", ALLOW),
    case(K_GROUP, "apostrophe inside \"...\" then a subst",
         "echo \"it's $(cat f)\"", DENY, must=[only("cat")],
         note="a lone `'` inside \"...\" must not derail the extractor"),
    case(K_GROUP, "unterminated subst", "echo $(cat f", DENY, must=[only("cat")],
         note="an unclosed opener yields the rest of the string, so the text is "
              "still scanned instead of silently dropped"),
    case(K_GROUP, "unterminated backtick", "echo `cat f", DENY, must=[only("cat")]),
    case(K_GROUP, "3 nested substitutions", "echo $(echo $(echo $(cat f)))", DENY,
         must=[only("cat")]),
    case(K_GROUP, "4 nested substitutions -> MAX_DEPTH cap",
         "echo $(echo $(echo $(echo $(cat f))))", ALLOW,
         note="documented cap: MAX_DEPTH=3 unwrap layers, and the miss is in the "
              "ALLOW direction like every other limit in a fail-open guard"),
    # -- `-c STRING` payloads ----------------------------------------------
    case(K_GROUP, 'sh -c "cat f"', 'sh -c "cat f"', DENY, must=[only("cat")]),
    case(K_GROUP, "nested quoting in a payload", "bash -c 'cat \"my file.txt\"'", DENY,
         must=[only("cat")]),
    case(K_GROUP, "two shell layers", "bash -c 'bash -c \"cat f\"'", DENY,
         must=[only("cat")], note="recursion, not one-level-and-stop"),
    case(K_GROUP, "short cluster `bash -lc`", "bash -lc 'cat f'", DENY, must=[only("cat")],
         note="`c` found letterwise inside the cluster"),
    case(K_GROUP, "payload with BACKSLASH-escaped inner quotes",
         'bash -c "bash -c \\"cat f\\""', INFO,
         note="INFO on purpose, and NOT the depth cap: the tokenizer (unchanged "
              "here — it is the old first_cmd_token body) has no backslash-escape "
              "handling, so `\\\"` mangles the payload into `bash -c \\\\cat`. Only "
              "reachable past two nesting layers, because two quote styles cover "
              "two layers without escaping. Closing it means changing the "
              "tokenizer all 200+ cases depend on; fails ALLOW-wards until then."),
    case(K_GROUP, "zsh -c 'ls'", "zsh -c 'ls'", DENY, must=[only("ls")]),
    case(K_GROUP, "bash running a script file", "bash script.sh", ALLOW),
    case(K_GROUP, "bash -c with no payload", "bash -c", ALLOW),
    case(K_GROUP, "downstream rule holds inside a payload", "bash -c 'ps aux | head -5'",
         DENY, must=[only("ps")], must_not=["`head`"]),
    case(K_GROUP, "follow-mode exemption holds inside a payload",
         "bash -c 'tail -f /var/log/x'", ALLOW),
    case(K_GROUP, "innocent payload", "bash -c 'git status'", ALLOW),
    case(K_GROUP, "xargs plus payload", "xargs bash -c 'cat \"$1\"' _", DENY,
         must=[only("cat")]),
    # -- ALL-CAPS spelling --------------------------------------------------
    case(K_GROUP, "LS -la", "LS -la", DENY, must=[only("ls")]),
    case(K_GROUP, "GREP foo f", "GREP foo f", DENY, must=[only("grep")]),
    case(K_GROUP, "assignment `CAT=/bin/cat echo hi`", "CAT=/bin/cat echo hi", ALLOW,
         note="VAR=val is skipped before the fold — false-positive probe"),
    case(K_GROUP, "variable `$CAT f`", "$CAT f", ALLOW,
         note="`$CAT` is not the NAME `CAT` — false-positive probe"),
    case(K_GROUP, "Cat f (mixed case)", "Cat f", INFO,
         note="INFO on purpose: only an ALL-CAPS spelling is folded. Mixed case "
              "would also resolve on a case-insensitive filesystem, and this "
              "host has no case-insensitive collision with ANY blocked name in "
              "15 PATH dirs, so broadening is available — but it was not asked "
              "for, and a mixed-case name is likelier to be a DIFFERENT program."),
    # -- python3 -m py_compile ---------------------------------------------
    case(K_GROUP, "python3 -m py_compile", "python3 -m py_compile x.py", DENY,
         must=[only("python3 -m py_compile"), "inspect_call(function=python)", "__pycache__"]),
    case(K_GROUP, "python -m py_compile (unversioned)", "python -m py_compile x.py", DENY,
         must=[only("python3 -m py_compile")]),
    case(K_GROUP, "python3.12 -m py_compile", "python3.12 -m py_compile x.py", DENY,
         must=[only("python3 -m py_compile")]),
    case(K_GROUP, "cluster `-Bm py_compile`", "python3 -Bm py_compile x.py", DENY,
         must=[only("python3 -m py_compile")], note="letterwise inside the cluster"),
    case(K_GROUP, "glued `-mpy_compile`", "python3 -mpy_compile x.py", DENY,
         must=[only("python3 -m py_compile")]),
    case(K_GROUP, "option with an argument before -m",
         "python3 -X importtime -m py_compile x.py", DENY,
         must=[only("python3 -m py_compile")]),
    case(K_GROUP, "GLUED option whose argument contains an m",
         "python3 -Ximporttime -m py_compile x.py", DENY,
         must=[only("python3 -m py_compile")],
         note="the `m` of `importtime` must not read as `-m`: an "
              "argument-taking letter ends the cluster"),
    case(K_GROUP, "plain flag clustered before -m", "python3 -B -m py_compile x.py", DENY,
         must=[only("python3 -m py_compile")]),
    case(K_GROUP, "glued -W argument before -m", "python3 -Wignore -m py_compile x.py",
         DENY, must=[only("python3 -m py_compile")]),
    case(K_GROUP, "-c must not read as -m", "python3 -c 'import py_compile'", ALLOW,
         note="`c` takes an argument too, but only `m` names a module"),
    case(K_GROUP, "py_compile inside a payload", "bash -c 'python3 -m py_compile x.py'",
         DENY, must=[only("python3 -m py_compile")]),
    case(K_GROUP, "py_compile plus a blocked sibling", "python3 -m py_compile a.py && ls",
         DENY, must=[only("ls", "python3 -m py_compile")]),
    case(K_GROUP, "py_compile with a blocked downstream stage",
         "python3 -m py_compile a.py | tail -5", DENY,
         must=[only("python3 -m py_compile")], must_not=["`tail`"]),
    case(K_GROUP, "dedup: two py_compile runs named once",
         "python3 -m py_compile a.py; python -m py_compile b.py", DENY,
         must=[only("python3 -m py_compile")]),
    case(K_GROUP, "python3 -c stays ALLOW", "python3 -c 'print(1)'", ALLOW,
         note="scope limit: read-only, and far too much legitimate use to block"),
    case(K_GROUP, "python3 -m json.tool stays ALLOW", "python3 -m json.tool f.json", ALLOW,
         note="scope limit: read-only"),
    case(K_GROUP, "python3 -m pytest stays ALLOW", "python3 -m pytest tests/", ALLOW),
    case(K_GROUP, "`-m py_compile` after the script is the SCRIPT's",
         "python3 tool.py -m py_compile", ALLOW,
         note="python's option region ends at the script operand — FP probe"),
    case(K_GROUP, "the prescribed in-memory replacement stays ALLOW",
         "python3 -c \"import sys; compile(open(sys.argv[1],'rb').read(), "
         "sys.argv[1], 'exec')\" f.py", ALLOW,
         note="denying the documented py_compile substitute would be a dead end"),
    case(K_GROUP, "downstream py_compile stage not inspected",
         "foo | python3 -m py_compile x.py", ALLOW,
         note="consistent with every other binary: only stage 0 is the primary "
              "command. Asserted so the asymmetry is visible, not accidental."),
    # -- invariants the unwrapping must not break ---------------------------
    case(K_GROUP, "commit body naming every wrapper form stays ALLOW",
         "git commit -F - <<'EOF'\nsee $(cat f) and (ls -la) and bash -c 'cat x'\nEOF",
         ALLOW, note="heredoc bodies are DATA — this is the repo's commit path"),
    case(K_GROUP, "unquoted-delimiter body with a substitution stays ALLOW",
         "git commit -F - <<EOF\nsee $(cat f)\nEOF", ALLOW,
         note="bash WOULD expand this body, but stripping bodies whole is what "
              "keeps `git commit -F -` working; the miss is ALLOW-wards"),
    case(K_GROUP, "pathological unbalanced openers", "echo $((((( `` <( $(", ALLOW,
         note="malformed nesting must not crash, hang or block"),
]

# ---------------------------------------------------------------------------
# `python3 -m compileall`, plus the short-option CLUSTER for both modules.
#
# compileall is py_compile's argument only broader: it writes __pycache__/*.pyc
# across an entire directory tree.  Both go through the same BLOCKED_MODULES ->
# python_module() path, so the cluster spellings below are asserted for BOTH --
# that is the point of having one extractor rather than two.
#
# The cluster half answers a specific question: CPython accepts bundled short
# options, so `python3 -Bm py_compile x.py` IS `python3 -B -m py_compile x.py`.
# Measured against the hook as a subprocess BEFORE any change here: every
# cluster form was already DENIED, because python_module() walks the LETTERS of
# the token (`for at, letter in enumerate(tok[1:], start=1)`) instead of
# exact-matching `-m`.  K already pinned `-Bm`; these pin the siblings, so the
# rule that cost this repo two bugs (`git hash-object -wt blob`, `tail -fn 100`)
# cannot be optimised back into an exact-token test unnoticed.
# ---------------------------------------------------------------------------

L_GROUP = "L. `python3 -m compileall` + bundled short-option clusters"
CA = "python3 -m compileall"
PC = "python3 -m py_compile"
CASES += [
    # -- compileall: the spellings that reach the module ---------------------
    case(L_GROUP, "python3 -m compileall .", "python3 -m compileall .", DENY,
         must=[only(CA), "inspect_call(function=python)",
               'forge_call(function=build, targets=["syntax"])', "__pycache__"],
         note="the steer must name the in-memory replacement AND the forge "
              "target that is this exact check for the whole repo"),
    case(L_GROUP, "python -m compileall (unversioned)", "python -m compileall .", DENY,
         must=[only(CA)]),
    case(L_GROUP, "python3.12 -m compileall (versioned)", "python3.12 -m compileall Scripts",
         DENY, must=[only(CA)]),
    case(L_GROUP, "glued `-mcompileall`", "python3 -mcompileall .", DENY, must=[only(CA)]),
    case(L_GROUP, "subshell `(python3 -m compileall .)`", "(python3 -m compileall .)",
         DENY, must=[only(CA)]),
    case(L_GROUP, "brace group `{ python3 -m compileall .; }`",
         "{ python3 -m compileall .; }", DENY, must=[only(CA)]),
    case(L_GROUP, "substitution `$(python3 -m compileall .)`", "$(python3 -m compileall .)",
         DENY, must=[only(CA)]),
    case(L_GROUP, "payload `bash -c 'python3 -m compileall .'`",
         "bash -c 'python3 -m compileall .'", DENY, must=[only(CA)],
         must_not=["`bash`"]),
    case(L_GROUP, "ALL-CAPS `PYTHON3 -m compileall .`", "PYTHON3 -m compileall .", DENY,
         must=[only(CA)], must_not=["`PYTHON3`"],
         note="the fold now covers the python interpreters too: /usr/bin/PYTHON3 "
              "resolves on a case-insensitive filesystem, so this really does run "
              "compileall. Its only consequence is that the -m check runs"),
    case(L_GROUP, "compileall plus a blocked sibling", "python3 -m compileall . && ls",
         DENY, must=[only("ls", CA)]),
    case(L_GROUP, "dedup: two compileall runs named once",
         "python3 -m compileall a; python -m compileall b", DENY, must=[only(CA)]),
    case(L_GROUP, "both modules in one command -> both labels, both steers",
         "python3 -m compileall . && python3 -m py_compile x.py", DENY,
         must=[only(CA, PC), "inspect_call(function=python)"],
         note="two BLOCKED_FORMS labels coexist in one reason, sorted"),
    case(L_GROUP, "compileall with a blocked downstream stage",
         "python3 -m compileall . | tail -5", DENY, must=[only(CA)],
         must_not=["`tail`"]),
    case(L_GROUP, "downstream compileall stage not inspected",
         "foo | python3 -m compileall .", ALLOW,
         note="same asymmetry as py_compile: only stage 0 is the primary command"),
    # -- compileall: false-positive probes ----------------------------------
    case(L_GROUP, "`python3 -c 'import compileall'` stays ALLOW",
         "python3 -c 'import compileall'", ALLOW,
         note="scope limit D98: `-c` is read-only and far too common to block"),
    case(L_GROUP, "`python3 -m json.tool` stays ALLOW (compileall-named arg)",
         "python3 -m json.tool compileall.json", ALLOW, note="scope limit D98"),
    case(L_GROUP, "a FILE merely named compileall", "python3 compileall.py", ALLOW,
         note="an operand, not `-m`: the module is never named"),
    case(L_GROUP, "`-m compileall` after the script is the SCRIPT's",
         "python3 tool.py -m compileall", ALLOW,
         note="python's option region ends at the script operand"),
    case(L_GROUP, "compileall merely MENTIONED as an argument",
         "echo 'python3 -m compileall'", ALLOW,
         note="a quoted argument is not a command — `echo` is the primary"),
    # -- bundled short-option clusters: py_compile --------------------------
    case(L_GROUP, "cluster `-Em py_compile`", "python3 -Em py_compile x.py", DENY,
         must=[only(PC)]),
    case(L_GROUP, "cluster `-sm py_compile`", "python3 -sm py_compile x.py", DENY,
         must=[only(PC)]),
    case(L_GROUP, "cluster `-BEm py_compile`", "python3 -BEm py_compile x.py", DENY,
         must=[only(PC)]),
    case(L_GROUP, "cluster AND glued `-Bmpy_compile`", "python3 -Bmpy_compile x.py",
         DENY, must=[only(PC)],
         note="`m` trailing a cluster with its module glued on: one token, "
              "`-B -m py_compile`"),
    # -- bundled short-option clusters: compileall --------------------------
    case(L_GROUP, "cluster `-Bm compileall`", "python3 -Bm compileall .", DENY,
         must=[only(CA)], note="the fix for one module is the fix for both"),
    case(L_GROUP, "cluster `-BEsm compileall`", "python3 -BEsm compileall .", DENY,
         must=[only(CA)]),
    case(L_GROUP, "cluster AND glued `-BEsmcompileall`", "python3 -BEsmcompileall .",
         DENY, must=[only(CA)]),
    case(L_GROUP, "cluster inside a payload", "bash -c 'python3 -Bm compileall .'",
         DENY, must=[only(CA)]),
    case(L_GROUP, "arg-taking letter ends the cluster, next token still read",
         "python3 -Ximporttime -Bm compileall .", DENY, must=[only(CA)],
         note="`-Ximporttime` consumes its own argument; the `-Bm` after it is "
              "still a cluster carrying -m"),
    # -- cluster false-positive probes --------------------------------------
    case(L_GROUP, "`-Bcm py_compile` is `-B -c m` -> ALLOW", "python3 -Bcm py_compile",
         ALLOW,
         note="`c` takes an argument, so it ends the cluster: python runs the "
              "one-letter PROGRAM `m` and `py_compile` is argv[1]. Denying this "
              "would be reading letters past the point where they are options"),
    case(L_GROUP, "`-Bc 'print(1)'` stays ALLOW", "python3 -Bc 'print(1)'", ALLOW,
         note="the cluster letters must not turn `-c` into `-m`"),
]

# ---------------------------------------------------------------------------
# The ALL-CAPS SHELL fold.
#
# The fold that makes `CAT f` deny (decision D97) was restricted to two sets:
# names in BLOCKED, and the python interpreters.  SHELL_C was NOT in it, so an
# ALL-CAPS shell's `-c STRING` payload was never extracted and EVERYTHING inside
# it escaped the guard entirely -- a strictly worse leak than the `CAT` case,
# because one bypass token covered every rule at once.  Measured against the
# hook as a subprocess BEFORE the change: `BASH -c 'cat f'` was ALLOW, and so
# were SH/ZSH/DASH/KSH/MKSH/ASH, `BASH -lc`, and `BASH -c 'python3 -m
# compileall .'`.
#
# The fix is not a third special case: primary() now gates the fold on
# has_opinion(), the one predicate naming everything the guard acts on at all
# (BLOCKED + SHELL_C + PY_INTERP_RE).  That is what keeps the safety argument to
# one sentence -- folding can only ever reach a name the guard was already going
# to act on.
#
# ALL-CAPS resolution was measured per name on this host, to the D97 standard
# (all 15 PATH dirs enumerated, 3 of them non-existent):
#   BASH -> /usr/local/bin/BASH,  SH -> /bin/SH,  ZSH -> /bin/ZSH,
#   DASH -> /bin/DASH,  KSH -> /bin/KSH   -- each the SAME inode as its
#                                            lower-case spelling, so the
#                                            ALL-CAPS form really runs the shell
#   MKSH, ASH                             -- NOT INSTALLED (neither spelling
#                                            resolves).  Folding them is inert
#                                            today and correct the day one is
#                                            installed; they are asserted here so
#                                            the set stays complete rather than
#                                            "complete on this laptop".
# No PATH dir ships a file whose name is the ALL-CAPS spelling of ANY name in
# the three sets, so the fold shadows no real program.
# ---------------------------------------------------------------------------

M_GROUP = "M. ALL-CAPS shell fold (`BASH -c 'cat f'`)"
CASES += [
    # -- every SHELL_C member, ALL-CAPS ------------------------------------
    case(M_GROUP, "BASH -c 'cat f'", "BASH -c 'cat f'", DENY, must=[only("cat")],
         must_not=["`bash`", "`BASH`"],
         note="was ALLOW before the fold covered SHELL_C: the payload was never "
              "extracted, so the INNER command escaped. The reason must name the "
              "inner `cat`, never the shell"),
    case(M_GROUP, "SH -c 'cat f'", "SH -c 'cat f'", DENY, must=[only("cat")],
         must_not=["`sh`"]),
    case(M_GROUP, "ZSH -c 'ls'", "ZSH -c 'ls'", DENY, must=[only("ls")],
         must_not=["`zsh`"]),
    case(M_GROUP, "DASH -c 'cat f'", "DASH -c 'cat f'", DENY, must=[only("cat")]),
    case(M_GROUP, "KSH -c 'grep x .'", "KSH -c 'grep x .'", DENY, must=[only("grep")]),
    case(M_GROUP, "MKSH -c 'ls'", "MKSH -c 'ls'", DENY, must=[only("ls")],
         note="`mksh` is NOT INSTALLED on this host (neither spelling resolves), "
              "so this fold is inert here — asserted anyway so the set stays "
              "complete rather than complete-on-this-laptop"),
    case(M_GROUP, "ASH -c 'ls'", "ASH -c 'ls'", DENY, must=[only("ls")],
         note="`ash` is NOT INSTALLED on this host either; same reasoning"),
    # -- spelling / option variants of the folded shell ---------------------
    case(M_GROUP, "short cluster `BASH -lc`", "BASH -lc 'cat f'", DENY,
         must=[only("cat")],
         note="dash_c_payload reads `c` letterwise, and it runs on the FOLDED "
              "name — both halves have to work or the payload is lost"),
    case(M_GROUP, "long option before -c", "BASH --login -c 'cat f'", DENY,
         must=[only("cat")]),
    case(M_GROUP, "./BASH -c 'cat f'", "./BASH -c 'cat f'", DENY, must=[only("cat")],
         note="basename() runs before the fold, so a path prefix is no dodge"),
    case(M_GROUP, "/bin/BASH -c 'cat f'", "/bin/BASH -c 'cat f'", DENY,
         must=[only("cat")]),
    case(M_GROUP, "sudo BASH -c 'cat f'", "sudo BASH -c 'cat f'", DENY,
         must=[only("cat")]),
    case(M_GROUP, "VAR=1 BASH -c 'cat f'", "VAR=1 BASH -c 'cat f'", DENY,
         must=[only("cat")]),
    # -- composes with every other wrapper form ----------------------------
    case(M_GROUP, "subshell `(BASH -c 'cat f')`", "(BASH -c 'cat f')", DENY,
         must=[only("cat")]),
    case(M_GROUP, "brace group `{ BASH -c 'cat f'; }`", "{ BASH -c 'cat f'; }", DENY,
         must=[only("cat")]),
    case(M_GROUP, "substitution `$(BASH -c 'ls')`", "$(BASH -c 'ls')", DENY,
         must=[only("ls")]),
    case(M_GROUP, "xargs plus folded payload", "xargs BASH -c 'cat \"$1\"' _", DENY,
         must=[only("cat")]),
    case(M_GROUP, "the two folds compose: `DASH -c 'python3 -m py_compile x.py'`",
         "DASH -c 'python3 -m py_compile x.py'", DENY, must=[only(PC)],
         note="folded SHELL unwraps the payload, then the interpreter check runs "
              "inside it — one predicate now gates both"),
    case(M_GROUP, "folded shell hiding compileall",
         "BASH -c 'python3 -m compileall .'", DENY, must=[only(CA)],
         note="was ALLOW: the broadest single bypass the hole offered"),
    case(M_GROUP, "folded shell hiding mkdir", "BASH -c 'mkdir /x'", DENY,
         must=[only("mkdir")]),
    # -- MAX_DEPTH interaction ---------------------------------------------
    case(M_GROUP, "folded shell wrapping a lower-case shell",
         "BASH -c 'bash -c \"cat f\"'", DENY, must=[only("cat")],
         note="2 unwrap layers, mixed spellings"),
    case(M_GROUP, "folded shell wrapping a FOLDED shell",
         "BASH -c 'BASH -c \"cat f\"'", DENY, must=[only("cat")],
         note="the fold has to apply at every depth, not just at depth 0"),
    case(M_GROUP, "SH wrapping SH", "SH -c 'SH -c \"cat f\"'", DENY,
         must=[only("cat")]),
    case(M_GROUP, "nested folded shells plus a blocked sibling",
         "BASH -c 'BASH -c \"ls\"' && cat y", DENY, must=[only("cat", "ls")],
         note="statement splitting is unaffected by the fold; both hits, sorted"),
    case(M_GROUP, "2 substitutions then a folded shell -> depth 3 payload",
         "echo $(echo $(BASH -c 'cat f'))", DENY, must=[only("cat")],
         note="the shell is found AT depth 2, so its payload is scanned at depth "
              "3 — the last layer MAX_DEPTH allows"),
    case(M_GROUP, "3 substitutions then a folded shell -> MAX_DEPTH cap",
         "echo $(echo $(echo $(BASH -c 'cat f')))", ALLOW,
         note="documented cap: the shell is found AT depth 3, where `depth < "
              "MAX_DEPTH` is false, so its payload is never unwrapped. Same cap "
              "the lower-case spelling hits, and the miss is ALLOW-wards"),
    case(M_GROUP, "folded shell then 3 substitutions -> MAX_DEPTH cap",
         "BASH -c 'echo $(echo $(echo $(cat f)))'", ALLOW,
         note="the fold spends one of the three layers, so the innermost "
              "substitution falls off the cap — pinned so the budget is visible"),
    case(M_GROUP, "folded shell then 2 substitutions -> still caught",
         "BASH -c 'echo $(echo $(cat f))'", DENY, must=[only("cat")]),
    # -- invariants that must hold INSIDE a folded payload ------------------
    case(M_GROUP, "follow-mode exemption holds in a folded payload",
         "BASH -c 'tail -f /var/log/x'", ALLOW),
    case(M_GROUP, "non-follow tail in a folded payload still denies",
         "BASH -c 'tail -20 f'", DENY, must=[only("tail")]),
    case(M_GROUP, "downstream rule holds in a folded payload",
         "BASH -c 'ps aux | head -5'", DENY, must=[only("ps")],
         must_not=["`head`"]),
    case(M_GROUP, "innocent folded payload", "BASH -c 'git status'", ALLOW),
    case(M_GROUP, "folded shell as a DOWNSTREAM stage not inspected",
         "git log | BASH -c 'cat f'", ALLOW,
         note="same asymmetry as every other binary: only stage 0 is the primary "
              "command. Asserted so it is visible, not accidental"),
    # -- false-positive probes: mixed case ---------------------------------
    case(M_GROUP, "Mixed case `Bash -c 'cat f'` stays ALLOW", "Bash -c 'cat f'",
         ALLOW,
         note="POLICY, asserted rather than left INFO: only an ALL-CAPS spelling "
              "folds, because a mixed-case name is likelier to be a DIFFERENT "
              "program. The reasoning is stated once, on group K's `Cat f` row; "
              "this pins the SHELL half of the same policy without restating it"),
    case(M_GROUP, "Mixed case `bAsH -c 'cat f'` stays ALLOW", "bAsH -c 'cat f'",
         ALLOW, note="not merely a leading-capital check — `.isupper()`, nothing else"),
    case(M_GROUP, "Mixed case `Zsh -c 'ls'` stays ALLOW", "Zsh -c 'ls'", ALLOW),
    case(M_GROUP, "Mixed case `Sh -c 'cat f'` stays ALLOW", "Sh -c 'cat f'", ALLOW),
    # -- false-positive probes: BASH as DATA -------------------------------
    case(M_GROUP, "a file literally named BASH as an argument", "git show BASH",
         ALLOW, note="an operand is not a command name"),
    case(M_GROUP, "`cat BASH` names ONLY cat", "cat BASH", DENY, must=[only("cat")],
         must_not=["`bash`", "`BASH`"],
         note="the fold reads argv[0], never the arguments"),
    case(M_GROUP, "`head BASH` names ONLY head", "head BASH", DENY,
         must=[only("head")]),
    case(M_GROUP, "assignment `BASH=/bin/bash echo hi`", "BASH=/bin/bash echo hi",
         ALLOW, note="VAR=val is peeled BEFORE the fold — the D97 probe, for shells"),
    case(M_GROUP, "assignment VALUE spelled BASH", "SHELL=BASH echo hi", ALLOW),
    case(M_GROUP, "assignment then a real blocked command",
         "BASH=/bin/bash cat f", DENY, must=[only("cat")],
         note="peeling the assignment must not also swallow the command"),
    case(M_GROUP, "variable `$BASH -c 'cat f'`", "$BASH -c 'cat f'", ALLOW,
         note="`$BASH` is not the NAME `BASH`; the guard does no expansion"),
    case(M_GROUP, "BASH running a script file", "BASH script.sh", ALLOW,
         note="no `-c`, so there is no payload to scan — the shell itself is not "
              "blocked, only its payload is inspected"),
    case(M_GROUP, "BASH -c with no payload", "BASH -c", ALLOW),
    case(M_GROUP, "BASH -c with an EMPTY payload", "BASH -c ''", ALLOW),
    case(M_GROUP, "BASH alone", "BASH", ALLOW),
    case(M_GROUP, "BASH merely echoed", "echo BASH", ALLOW),
    case(M_GROUP, "a folded shell command MENTIONED as a quoted argument",
         "echo 'BASH -c cat f'", ALLOW,
         note="a quoted argument is not a command — `echo` is the primary"),
    case(M_GROUP, "folded shell inside a heredoc BODY stays data",
         "git commit -F - <<'EOF'\nBASH -c 'cat f'\nEOF", ALLOW,
         note="heredoc bodies are DATA; a commit message may describe the bypass "
              "this group closes without being blocked by it"),
]

# ---------------------------------------------------------------------------
# `node --check` -> inspect_call(function=javascript).
#
# The MODE has an MCP equivalent, the BINARY does not: `node file.js` RUNS the
# file and no tool here does that, so only `--check` / `-c` is redirected.  The
# rule is additionally gated on node being INSTALLED -- without node the steer
# would point at a validator that FAILs for the same missing binary.
#
# That gate makes the expectation environment-dependent, which is why NODE_PRESENT
# is computed here and the DENY cases flip to ALLOW when node is absent: the suite
# then asserts the OTHER half of the same rule (the guard stays out of the way)
# instead of failing for a reason that is not a defect.  The false-positive probes
# are unconditional -- `node file.js` must be allowed either way.
# ---------------------------------------------------------------------------

NODE_PRESENT = shutil.which("node") is not None
NODE_EXPECT = DENY if NODE_PRESENT else ALLOW
NODE_MUST = [only("node --check"), "inspect_call(function=javascript"] \
    if NODE_PRESENT else []
GATE = ("node is installed, so the rule fires"
        if NODE_PRESENT else
        "node is NOT installed on this host: the rule is gated off, so the "
        "guard must stay out of the way")

N_GROUP = "N. `node --check` -> inspect_call(javascript), gated on node"
CASES += [
    # -- the syntax-check form (DENY only when node exists) ------------------
    case(N_GROUP, "node --check f.js", "node --check f.js", NODE_EXPECT,
         must=NODE_MUST, note=GATE),
    case(N_GROUP, "node -c f.js (short spelling)", "node -c f.js", NODE_EXPECT,
         must=NODE_MUST, note=GATE),
    case(N_GROUP, "node --check on stdin (no file)", "node --check", NODE_EXPECT,
         must=NODE_MUST, note="the mode, not the operand, is what is redirected"),
    case(N_GROUP, "flag before --check", "node --no-warnings --check f.js",
         NODE_EXPECT, must=NODE_MUST),
    case(N_GROUP, "cluster `-ce`", "node -ce 'x'", NODE_EXPECT, must=NODE_MUST,
         note="letterwise: the `c` is read before `e` ends the cluster"),
    case(N_GROUP, "absolute path `/usr/local/bin/node --check f.js`",
         "/usr/local/bin/node --check f.js", NODE_EXPECT, must=NODE_MUST,
         note="the basename is what is matched"),
    case(N_GROUP, "ALL-CAPS `NODE --check f.js`", "NODE --check f.js", NODE_EXPECT,
         must=NODE_MUST,
         note="NODE resolves to the same inode on a case-insensitive filesystem, "
              "so has_opinion() folds it like BASH/CAT"),
    case(N_GROUP, "inside a `bash -c` payload", "bash -c 'node --check f.js'",
         NODE_EXPECT, must=NODE_MUST),
    case(N_GROUP, "inside a substitution", "echo $(node --check f.js)",
         NODE_EXPECT, must=NODE_MUST),
    case(N_GROUP, "sudo node --check f.js", "sudo node --check f.js", NODE_EXPECT,
         must=NODE_MUST, note="wrapper peeled first, like every other rule"),
    case(N_GROUP, "with a blocked sibling", "node --check f.js && ls", DENY,
         must=[only("ls", "node --check")] if NODE_PRESENT else [only("ls")],
         note="two labels coexist in one reason, sorted — and `ls` denies "
              "regardless of the node gate"),
    case(N_GROUP, "dedup: two check runs named once",
         "node --check a.js; node -c b.js", NODE_EXPECT, must=NODE_MUST),
    # -- false-positive probes: unconditional, node gate or not --------------
    case(N_GROUP, "`node script.js` RUNS the file -> ALLOW", "node script.js",
         ALLOW, note="execution has no MCP equivalent; only the check mode does"),
    case(N_GROUP, "`node --version` -> ALLOW", "node --version", ALLOW),
    case(N_GROUP, "`node -v` -> ALLOW", "node -v", ALLOW,
         note="`v` is not `c`, and it takes no argument"),
    case(N_GROUP, "`node -e 'code'` -> ALLOW", "node -e 'console.log(1)'", ALLOW,
         note="execution, deliberately out of scope"),
    case(N_GROUP, "`node -p 'expr'` -> ALLOW", "node -p '1+1'", ALLOW),
    case(N_GROUP, "`node -pc` is `-p c` -> ALLOW", "node -pc", ALLOW,
         note="an argument-taking letter ends the cluster, so this `c` is `-p`'s "
              "ARGUMENT — the mirror of python's `-Bcm py_compile` probe"),
    case(N_GROUP, "`node` alone (REPL) -> ALLOW", "node", ALLOW),
    case(N_GROUP, "`npm run build` -> ALLOW", "npm run build", ALLOW,
         note="npm/npx are not node; the rule reads argv[0]"),
    case(N_GROUP, "`npx tsc --check` -> ALLOW", "npx tsc --check", ALLOW),
    case(N_GROUP, "`--check` AFTER the script is the SCRIPT's",
         "node tool.js --check", ALLOW,
         note="node's option region ends at the script operand — the mirror of "
              "`python3 tool.py -m py_compile`"),
    case(N_GROUP, "`--` ends the option region", "node -- --check", ALLOW),
    case(N_GROUP, "`--check` as a quoted argument to echo",
         "echo 'node --check f.js'", ALLOW,
         note="a quoted argument is not a command — `echo` is the primary"),
    case(N_GROUP, "node --check inside a heredoc BODY stays data",
         "git commit -F - <<'EOF'\nnode --check f.js\nEOF", ALLOW,
         note="heredoc bodies are DATA, like every other rule"),
    case(N_GROUP, "downstream node --check stage not inspected",
         "foo | node --check f.js", ALLOW,
         note="only stage 0 is the primary command — same asymmetry as py_compile"),
    case(N_GROUP, "a file literally named `--check` passed to a runner",
         "git show node", ALLOW, note="an operand is not a command name"),
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
    print("--- white-box: substitutions() / primary() ---")
    for s in ["echo $(cat f)", 'echo "$(cat f)"', "echo '$(cat f)'",
              "echo \"it's $(cat f)\"", "diff <(cat a) <(cat b)",
              'echo "<(cat f)"', "echo $((1+2))", "echo $(cat f",
              "(cat f.txt)", "{ cat f; }", "{cat f;}", "time (cat f)",
              "CAT f", "$CAT f", "bash -c 'bash -c \"cat f\"'",
              "python3 -m py_compile x.py", "python3 tool.py -m py_compile"]:
        print(f"  {s!r:38s} -> subs={g.substitutions(s)} primary={g.primary(s)}")
    # The cluster question the L group answers: a DENY reason is identical
    # whether `-Bm` was read letterwise or the module came from somewhere else,
    # so only python_module()'s own return value proves the bundling is handled.
    print("--- white-box: python_module() on bundled short-option clusters ---")
    for s in ["python3 -m py_compile x.py", "python3 -Bm py_compile x.py",
              "python3 -BEsm py_compile x.py", "python3 -Bmpy_compile x.py",
              "python3 -m compileall .", "python3 -Bm compileall .",
              "python3 -BEsmcompileall .", "python3 -Ximporttime -Bm compileall .",
              "python3 -Bcm py_compile", "python3 -Bc 'print(1)'",
              "PYTHON3 -m compileall .", "python3 compileall.py"]:
        name, argv = g.primary(s)
        print(f"  {s!r:42s} -> name={name!r} module={g.python_module(argv)!r} "
              f"label={g.BLOCKED_MODULES.get(g.python_module(argv))!r}")
    # The ALL-CAPS shell fold: a DENY reason names only the INNER command, so it
    # cannot distinguish "the shell was folded and its payload extracted" from
    # some other route to the same hit.  has_opinion() + primary() + the payload
    # are the three values that actually prove it, and the last column shows the
    # fold's whole domain in one place.
    print("--- white-box: has_opinion() / the ALL-CAPS shell fold ---")
    for s in ["BASH -c 'cat f'", "SH -c 'cat f'", "ZSH -c 'ls'",
              "DASH -c 'cat f'", "KSH -c 'grep x .'", "MKSH -c 'ls'",
              "ASH -c 'ls'", "BASH -lc 'cat f'", "/bin/BASH -c 'cat f'",
              "Bash -c 'cat f'", "bAsH -c 'cat f'", "BASH=/bin/bash echo hi",
              "$BASH -c 'cat f'", "BASH script.sh", "cat BASH", "CAT f",
              "PYTHON3 -m compileall .", "NODE --check f.js", "Node --check f.js"]:
        name, argv = g.primary(s)
        print(f"  {s!r:30s} -> name={name!r} opinion={g.has_opinion(name or '')} "
              f"payload={g.dash_c_payload(argv)!r}")
    print("  fold domain: %d BLOCKED + %d SHELL_C + PY_INTERP_RE + node"
          % (len(g.BLOCKED), len(g.SHELL_C)))
    print("  SHELL_C = %r" % sorted(g.SHELL_C))
    # `node --check` is a MODE, so a DENY reason cannot show whether the flag was
    # read letterwise or the operand boundary respected. node_check_mode()'s own
    # return value is the only thing that does -- and NODE_PRESENT states which
    # half of the gated rule this host exercised.
    print("--- white-box: node_check_mode() (NODE_PRESENT=%s) ---"
          % g.NODE_PRESENT)
    for s in ["node --check f.js", "node -c f.js", "node --check",
              "node --no-warnings --check f.js", "node -ce 'x'", "node -pc",
              "node -p '1+1'", "node -e 'x'", "node -v", "node --version",
              "node script.js", "node tool.js --check", "node -- --check",
              "node", "npm run build", "npx tsc --check", "NODE --check f.js"]:
        name, argv = g.primary(s)
        print(f"  {s!r:34s} -> name={name!r} check_mode={g.node_check_mode(argv)} "
              f"hits={g.scan(s)}")


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

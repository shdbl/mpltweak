# Contributing to mpltweak

Thanks for considering a contribution. This project has one hard rule (below) and
otherwise tries to stay out of your way.

## The one hard rule

**The user's plotting script must stay free of any trace of this tool.**

No `import mpltweak`, no `gaitu()`, no `--tweak` branch, no `_POS`/`_FONTS` block
written back into a script. Everything the tool does lives *outside* the script
(the CLI plus the params file). If a change would put tool-specific symbols into a
user's script, it won't be merged.

## Development setup

```bash
git clone https://github.com/shdbl/mpltweak
cd mpltweak
pip install -e ".[mcp]"      # editable install, incl. the MCP extra
pip install matplotlib numpy
mpltweak doctor              # check your interactive backend is usable
```

**Qt (PyQt5) is an extra, not a default dependency** — `pyproject.toml` declares it as
`qt = ["PyQt5>=5.15"]` (see also `mcp` and `all`), because servers / headless boxes /
pure-agent users only need `describe` / `check` / `apply` / `mcp` and should not pull in
~100MB of Qt. Opening the window does need it, so add the extra when you work on the GUI:

```bash
pip install -e ".[qt]"       # PyQt5, for the interactive window
pip install -e ".[all]"      # qt + mcp
pip install -e . --no-deps   # then install only what you need (fully headless)
```

## Running the tests

Every suite is a **plain script** that prints `ALL PASS` or a failure list, so no pytest is needed
for a quick loop. The **seven original suites** (the only ones the CI shell loop below still runs on
every matrix leg) are:

```bash
python tests/test_tweak.py        # core interaction helpers
python tests/test_events.py       # synthetic mouse/key events through the callbacks
python tests/test_redo.py         # undo/redo
python tests/test_writeback.py     # AST write-back engine  ← most sensitive
python tests/test_agent_api.py    # describe / schema / apply --json
python tests/test_check.py        # layout health check
python tests/test_mcp.py          # MCP server (skips itself if `mcp` isn't installed)
```

**The authoritative list of suites is `SUITES` in `tests/test_suites.py`** (26 entries today; two
more are deliberately excluded — see its `SUITES_EXCLUDED` and header docstring). That list is also
what the 3.12 legs' `pytest -q` collects, and **every** `tests/test_*.py` must appear on at least one
matrix leg's list — that is machine-checked, see *CI coverage* below.
The two newest suites are `tests/test_shapes.py` (420 lines, **72 assertions**, ~50 s) and
`tests/test_demo.py` (147 lines, offscreen `mpltweak demo` acceptance, §119 — it never opens a window).
`test_shapes.py`
builds one figure per layout shape (subplots / `subplots(2, 2)` / unpacked tuples / mosaic / gridspec /
loop / pure `plt.*`) and asserts, per shape and per field, that the write-back either lands
(`rc=0`, re-run `rc=0`) or is refused for a reason on the whitelist — no silent "supported" claims.
The bar for a PR is a green **`pytest -q`** (all registered suites + the hygiene ratchets), not
"these seven".

### The pytest route (optional locally, but this is what CI trusts)

`tests/test_suites.py` wraps **every suite in `SUITES`** — 26 today, not just the seven above — one
test case per suite, so CI reports, single-suite selection and IDE integration all work.
`pyproject.toml` pins `python_files = ["test_suites.py", "test_hygiene.py"]`, so plain `pytest -q`
collects exactly those two wrappers and the suites themselves run as subprocesses:

```bash
pip install -e ".[dev]"
pytest -q                       # all registered suites + the hygiene ratchets
pytest -q -k test_writeback     # just the write-back engine
pytest -q -k notation_discipline  # or any suite registered later
```

`tests/test_hygiene.py` holds the engineering-hygiene ratchets that used to live only in prose (31
test functions today: module registration, GBK encodability, the broad-`except Exception` count, the
child-process env-pinning ledger, the "no real GUI objects in tests" guard, the CI per-leg coverage
check, …). The two original ones:

- **`except Exception` may not grow unregistered.** Broad catches are correct in GUI
  callbacks and runtime probes, but at ~150 of them you can no longer tell "expected
  degradation" from "hiding a bug". Narrowing them all at once is too risky, so the test
  only pins the count per file (`BASELINE`); going down is welcome, going up needs a
  registered reason. Full list with "narrowable" hints:
  `python tools/audit_exceptions.py --markdown`.
- **Every message template must be GBK-encodable.** On a Chinese Windows, stdout captured
  through a pipe is cp936 with `errors='strict'` — a single `✓` turns "write-back
  succeeded" into `rc=1` + `UnicodeEncodeError`, *after* the file was already written.
  `messages.py` says this in a comment; now it is enforced.

  **Every new message key — and every reworded template — must go through this ratchet.** Add it to the
  tables in `src/mpltweak/messages.py` (zh **and** en; `MPLTWEAK_LANG=en` is a supported path) and run

  ```bash
  pytest -q -k hygiene                # the ratchets (test_hygiene.py is pytest-only:
                                      #  it has no __main__, so `python tests/test_hygiene.py`
                                      #  runs nothing)
  pytest -q -k every_suite_is_assigned   # just the CI per-leg coverage check
  ```

  The usual offenders are characters outside cp936: box drawing, emoji, `✓`, and `⇒` (U+21D2) /
  `⇔` (U+21D4) / `⟹` (U+27F9) — **`⇒` cannot be encoded in GBK, use `→` (U+2192) instead**. (`→`,
  the en/em dashes `–`/`—` and CJK punctuation such as `「」` *are* encodable; `⇒`/`⇔`/`⟹`/`✓` are
  not, and one of them turns the message into `UnicodeEncodeError` on a cp936 pipe.) Prefer ASCII or
  plain Chinese. The JSON path has the same trap for the
  *other* reason (UTF-8 consumers): emit through `messages.emit_json` (fd-level UTF-8) instead of
  `print()`.

### Every fix needs a negative test (reverse verification)

A fix is only accepted together with a test that would have **failed before it**. The cheap and
reliable way: flip the implementation back to the old behaviour on a scratch copy and confirm the new
assertion trips. A regression test that passes on both the old and the new code proves nothing.

Two examples from the 0.1.9 batch:

- the write-back engine must stop inserting a block when the params already equal the source
  (`--style auto` → `no_change`). Before the fix, the "clean round-trip" scenario rewrote the user's
  file, so the test asserts the **sha256 is unchanged** — revert the guard in `writeback.py` and it must
  go red;
- the adjustment block must carry the marker the code actually writes, so the write-back suite should
  grep the rewritten source for `# ===== auto layout (generated) - do not edit inside =====`
  (`fig1.py` must **not** contain the legacy Chinese marker).

Put the reverse check in the PR description ("reverting `writeback.py:<line>` makes
`test_writeback.py::<case>` fail"), so a reviewer can reproduce it with one command.

### Adding a suite

**One registration point: `SUITES` in `tests/test_suites.py`.** That list is what pytest collects
(`pyproject.toml` pins `python_files = ["test_suites.py", "test_hygiene.py"]`) and therefore what CI
executes. The `for t in ...` loop in `.github/workflows/tests.yml` is **only the seven legacy
suites** and does *not* need editing for a new one. Both recent precedents landed with exactly that
single edit — `test_notation_discipline` (HEAD `99fe55b`) and `test_docs` (`d24b9bb`) each added one
tuple to `SUITES`; neither touched the workflow.

1. write `tests/test_yoursuite.py`, printing `ALL PASS` (copy the shape of `test_check.py`);
2. add one tuple to `SUITES` in `tests/test_suites.py` — second element = extra env vars for that
   suite (usually `{}`):

   ```python
   ('test_notation_discipline', {}),   # ← the precedent: one line
   ```

3. if the suite must **not** run under pytest, put it in `SUITES_EXCLUDED` instead, with a one-line
   reason — `test_real_window` (opens a real window) and `test_corpus_invariants` (needs the
   out-of-repo corpus + minutes of runtime) are there today;
4. keep this file in sync: the suite list above and this section are the human-facing registry (the
   machine side is `python tools/ci_suite_coverage.py`: a `tests/test_*.py` that no leg's list names
   turns it **red** — see *CI coverage* below);
5. **name it so the checkers actually see it** (t98): do *not* give a real suite a temp/probe name —
   starting with `.tmp`, `tmp_` or `_jsonenc`, or ending with `_probe.py`.
   `tools/notation_discipline.py`'s `iter_py()` deliberately **skips** those names, because other
   tests write throwaway probes into the repo and delete them afterwards
   (`tests/test_agent_api.py:309` → `tests/.tmp_dpi.py`, `:445` → `tests/_jsonenc9.py`), and a
   concurrent run would otherwise see another arm's probe file and report a **false red**.
   A real suite named that way would **silently drop out of the notation discipline**.
   (Details: that tool's docstring, section “扫描面”.)

If what you added is a *source* module rather than a suite, the registries are the `BASELINE` list
pinned by `tests/test_hygiene.py` (`test_all_modules_registered`) and, if it becomes an extra,
`pyproject.toml`.

### After changing `src/mpltweak/writeback.py`: regenerate its section index

`writeback.py` ends with a **generated** block — its `§N` section banners plus every top-level
function mapped to a line number. It is neither decoration nor hand-maintained: `tools/gen_nav.py`
computes it, so a hand edit is silently overwritten the next time the tool runs.

Any structural edit (adding, renaming or moving a function, adding a section) makes the block stale:

```bash
python tools/gen_nav.py --write     # regenerate after the edit
python tools/gen_nav.py --check     # rc 0 = fresh / 1 = stale / 2 = usage or format problem
```

Forget it and `pytest -q tests/test_hygiene.py -k auto_nav` turns red, naming the first entry that no
longer matches (`# _name 218` where it should be `# _name 219`); rerun it after `--write` and it goes
green again. Keep new functions under the right `# ==== §N … ====` banner: the banners are
hand-written, only the line numbers come from the tool.

### CI coverage: one list per leg, and a check that no suite falls through

A **leg** is one cell of the workflow matrix (`ubuntu-latest` / `windows-latest` × Python
3.9 / 3.10 / 3.11 / 3.12 = 8 legs), and the legs do **not** all run the same list of suites.
The split is deliberate — **3.9 is the key leg** (minimum supported interpreter, so it is the one
that must carry evidence for the write-back/CLI surfaces), and **3.10 / 3.11 are interpreter
sentinels** (a small list is enough there: they only have to prove the package imports, the CLI
starts and the core flows run on that interpreter — they are not a regression net, and their short
list is not a gap):

- the plain-script loop runs the **seven legacy suites** on all 8 legs;
- the 3.12 legs additionally run `pytest -q`, i.e. **every suite in `SUITES`** plus the hygiene
  ratchets (that step is guarded by `if: matrix.python-version == '3.12'`), and — in that same step,
  with the same interpreter — one explicit `python -m pytest -q tests/test_cli_output.py`:
  `test_cli_output` is pytest-function-style, so the bare `pytest -q` route above does not collect it
  (`python_files` pins only the two wrappers). Cost: +13–14 s, on the two 3.12 legs only;
- the 3.9 legs additionally run the fast smoke (`-k "docs or notation_discipline or
  tmpdir_discipline or msg_key_audit or cli_fuzz or verify_onepass or sandbox or
  apply_layout_conflict"`): the three document-class suites, plus — since 0.1.15 — five
  CLI/write-back suites, so "the minimum supported interpreter can actually write back" has
  evidence. All eight are subprocess-only, window-free and need no new dependency; measured on
  Python 3.9.25 + matplotlib 3.9.4: 3.0 / 4.3 / 10.3 / 12.0 / 29.0 s (whole `-k` run: 10 passed,
  65 s), i.e. **+~59 s on each 3.9 leg only**. Deliberately still *not* on 3.9: `test_figure_matrix`
  (N=100 would cost 260–350 s on CI), `test_journey_wheel` (the one suite with no green 3.9
  evidence — it fails locally for an unrelated non-editable-install reason), `test_gui_smoke`
  (~100 MB of PyQt5; 3.12 only) and `test_suites` (the wrapper that runs the whole table).

So of the 31 `tests/test_*.py` files, **29 are named by at least one leg**; the other two are
`SUITES_EXCLUDED` (`test_real_window`, `test_corpus_invariants`).

Coverage is therefore *assigned*, not automatic — a new suite can be silently on no leg at all
(that is exactly how `test_launch` used to sit at "3.12 only" without anyone noticing). So
"every `tests/test_*.py` is on at least one leg's list" is now machine-checked:

```bash
python tools/ci_suite_coverage.py        # rc 0 = every suite assigned / 1 = one is missing / 2 = usage or config
python tools/ci_suite_coverage.py -v     # per-leg suite names + the "covered on N legs" histogram
```

The checker is **zero-dependency, offline and millisecond-fast**, and it **parses the workflow as
YAML** instead of grepping it: a suite name that only lives in a comment, or a list split across a
`\` continuation, would fool a text search in both directions. What counts as a leg's list: the
bash `for … in` list of a loop that runs `tests/$t.py`, literal `tests/<name>.py` paths, `pytest`
selectors (`-k`, explicit paths, or a bare `pytest` — which pulls in everything registered through
`python_files` / `SUITES`), and a `python tools/<x>.py` step whose script runs pytest itself
(detected from its AST, not its comments).

**It is wired into `tests/test_hygiene.py`, not into a new workflow step** — deliberate: the 3.12
legs already run `pytest -q`, so it rides along at **zero CI cost** (a new step would need another
interpreter setup or an extra install on another leg), and you get the same verdict locally with
`pytest -q -k every_suite_is_assigned`. The test also pins the leg count (8) and carries two teeth:
an extra unassigned suite must turn it red, and a workflow with the 3.12 step removed must turn it
red *and* name `test_launch`.

Two rules come with it:

1. **A new suite needs a leg.** The single registration point is still one tuple in `SUITES`
   (`tests/test_suites.py`) — that is the 3.12 legs' list. A suite that only makes sense on one
   version goes into that leg's step in the workflow instead.
2. **Exemptions carry a reason and are printed on every run.** A suite that must not run in CI is
   registered either in `SUITES_EXCLUDED` (suite side: "not in the pytest path" — `test_real_window`,
   `test_corpus_invariants`) or in `EXTRA_EXEMPT` inside `tools/ci_suite_coverage.py` (CI side).
   `EXTRA_EXEMPT` is **empty today**: the one suite that used to need it, `test_cli_output` (a
   pytest-function-style suite whose self-check entry is `python -m pytest -q tests/test_cli_output.py`),
   now runs on the 3.12 legs — so removing it from the workflow turns the checker red instead of
   passing silently. Both tables demand a one-line *why*; never register an exemption just to make
   the checker green.

**Still a known gap (kept as a TODO, not hidden): the check is "at least one leg", not "every leg".**
The plain-script loop still runs only the seven legacy suites on every leg, so everything added
after 0.1.9 is exercised on 3.12 only — **except** the eight suites the 3.9 smoke picks up.
Closing that means adding those suites to the loop or dropping the 3.12-only guard — a
release-pipeline change that multiplies CI time, so it is recorded here rather than changed
silently. `python tools/ci_suite_coverage.py -v` prints the histogram; today it reads
"8 legs: 7 / 4 legs: 9 / 2 legs: 13" (the 3.9 legs carry 16 suites, the 3.12 legs carry all 29).

**Pin the interface language in your suite.** `messages.detect_lang()` resolves
`--lang > MPLTWEAK_LANG > locale > LANG > en`, so the *same* assertion on Chinese prose
passes locally (Windows, zh-CN) and fails on CI (Linux, `LANG=C` → English). That is not
hypothetical: it turned the whole 0.1.8 CI red after a green local run. Every suite now
starts with `os.environ['MPLTWEAK_LANG'] = 'zh'`; if you want to assert the English branch
too, override it per call:

```python
rc, out, err = run('apply', src, '--verify-fast', env={'MPLTWEAK_LANG': 'en'})
```

The same trap applies to the *encoding* of subprocess output: decode by bytes with a
candidate list (`utf-8` then `locale.getpreferredencoding(False)`), never assume UTF-8.
Both suites that assert human-readable prose (`test_agent_api`, `test_check`) cover the
zh **and** en branches explicitly.

`test_writeback.py` deserves special care: the write-back engine edits a user's
real source file, so any change there needs a green run of that suite plus, ideally,
a try on a real script of your own.

### Document references (`file.py:NNN`): a release-time gate, not a CI assertion

Docs and the manual quote code by line number, and **every change to a source file moves them**.
`tools/check_doc_refs.py` checks them, but it is deliberately **not** wired into CI as
"the repo must be clean": any task that edits `writeback.py` would turn that red, and a check
that goes red for a legitimate change gets relaxed sooner or later. Run it **before a release**
(and after finishing a batch that touched source):

```
python tools/check_doc_refs.py                  # docs/** + README*.md + skills/**: rc=0 required
python tools/check_doc_refs.py --with-maintenance   # + the (untracked) maintenance manual
python tools/check_doc_refs.py --json            # machine-readable
```

Three things to know before you write a reference:

1. **Bare `:NNN` works — but only inside the same line.** `` `writeback.py:4513` … `:4527` ``
   inherits `writeback.py` from the **last `.py` name earlier on that same line**; a continuation
   line does *not* inherit the filename from the paragraph above (paragraph-scope inheritance was
   measured to cause silent false greens, so the gate was narrowed). A bare reference with nothing
   to inherit is printed as a hint (`bare ref not inherited`), never as suspicious — write
   `` `filename:NNN` `` on the continuation line instead.
2. **Anchor new references**: `` `writeback.py:2255 # _prune_backups` `` — the gate looks for that
   symbol **exactly on the quoted line**: `ANCHOR_WINDOW` defaults to **0** since 0.1.14. Widen it
   for one run with `--anchor-window N` (e.g. `--anchor-window 3` restores the old ±3 tolerance)
   and it reports `symbol_mismatch` with the nearest line the symbol does appear on. Existence
   alone was never enough: `t72` measured four anchors that had drifted by +5…+13 while still
   pointing at *a* non-blank line — and the old ±3 default hid 1–3 line shifts entirely.
3. **Exemptions carry a written reason, and they only shrink.** Three tables in the tool, all keyed
   by `(doc path, reference)` — **never by doc line number** (that is what drifts):
   `HISTORICAL_ALLOW` (the maintenance log quotes the *then-current* line: correct history, keep it),
   `KNOWN_DRIFT` (a reference that really is stale today, with an owner; delete the entry once fixed)
   and `UNANCHORED_ALLOW` (pre-anchor references, frozen). New references do **not** get added to
   the freeze — anchor them instead. A stale entry is printed with "please delete".
   One document-level rule sits above them: `ANCHOR_FREE_DOCS` (today only `MAINTENANCE.md`, the
   unversioned log manual) — its references are **not required** to carry an anchor, because
   anchoring a historical line only tempts someone into editing history. They are still checked for
   resolution / range / blank line / a wrongly written anchor.

Never relax a criterion to make the gate green — register an exemption with its reason instead.
The gate ships its own self-test (`--self-test`, 9 directions: the four `t103` resolution cases plus
anchors / window / exemptions / inheritance / exemption-scope) and can be made deliberately red with
`--self-test --break MODE` (`bare`, `anchor`, `window`, `allow`, `resolve`, `herit`, `exempt`) —
that is how you prove the self-test is not permanently green.
`tests/test_docs.py` runs the self-test and the criterion-level teeth in CI.

### The real-window suite: one manual run before every release

`tests/test_real_window.py` opens **real** windows and drives them with *injected* events (QtTest —
the physical cursor is never moved). It contains no `def test_` functions, so `pytest` cannot collect
it; that is why it sits in `SUITES_EXCLUDED` instead of in the suite list. It also **takes the
foreground** and flashes windows on the screen, so it must not run on a machine somebody is using —
and because no CI leg runs it, nothing keeps it alive except a schedule. This is that schedule:

**Before every release, run it once by hand.** Two entry points, cheapest first:

```bash
python tests/test_real_window.py --selftest      # window-free: asserts the assertion machine itself
python tests/test_real_window.py --backend qt    # really opens windows (also --backend tk / all)
```

`--selftest` needs no display and finishes in seconds — treat it as the mandatory part of the gate.
The full `--backend qt` run owns the foreground for minutes (6 scenarios, 6,000 gestures in the
documented baseline): run it **only when nobody is at the machine**, and record which of the two you
ran in the release entry. Neither command belongs in CI or in `pytest`'s path — see
`### CI coverage` for why the exclusion is a declaration, not an accident.

Every release record carries this row (copy it verbatim; "not run" needs a reason):

| Field | Value |
|---|---|
| Real-window suite (`tests/test_real_window.py`) | `--selftest` rc=0, full run **not run** (user at the machine) — or — full `--backend qt`: **ran**, ALL PASS, hard assertions 27/27 |

An unrecorded skip is the failure mode this line exists to prevent: "nobody ran it and nobody wrote
that down" looks exactly like green.

## What to change where

| Area | File |
|---|---|
| Interactions (drag, snap, align, undo, hover) | `src/mpltweak/toolbox.py` |
| Opening the window / backend probing | `src/mpltweak/launch.py` |
| Reading params, schema, validation | `src/mpltweak/params.py` |
| AST write-back engine | `src/mpltweak/writeback.py` |
| Verification + rollback | `src/mpltweak/verify.py` |
| CLI surface | `src/mpltweak/cli.py` |
| Layout health check rules | `src/mpltweak/check.py` |

## Pull requests

- Keep them small and focused; one concern per PR.
- Say *what* changed and *why* in the description.
- If you touch `cli.py` or add a command, update the command table in both
  `README.md` and `README.en.md`.
- If you touch `writeback.py`, add a regression test to `test_writeback.py` —
  that engine has bitten us before (byte-vs-character offsets on non-ASCII
  scripts, colorbar axes that snap back to an automatic position, dpi assumptions).
- Match the surrounding style. There's no enforced linter, but the codebase is
  mostly 4-space indented with comments in Chinese; English comments are fine too.
- Reference the audit finding id (`E1`, `CH-4`, `F3`, `RC-8`, …) in the PR and in the CHANGELOG entry,
  and update `CHANGELOG.md` under the **unreleased** section — the ids are how the release notes stay
  traceable back to the evidence.
- If your change alters user-visible behaviour, update **both** `README.md` and `README.en.md`; if it
  alters what the tool *executes* or *writes to disk*, update the "execution boundary" section in both
  as well.

## Reporting bugs

Include:

- `pip show mpltweak` version line
- your OS and Python version
- the `backend` line from `mpltweak doctor`
- a **minimal plotting script** that reproduces it (if it's about write-back)

The `check` command is often useful when reporting layout problems:

```bash
mpltweak check your_script.py --json
```

## License

By contributing you agree your work is released under the project's MIT license.

### 文案 i18n 的边界（t78 血案，硬规则）

`messages` 的语言表**只用于纯人读文本**。**协议载荷 / 日志分帧 / 被 `==`、`startswith` 比较或当字典键用的字符串，一律不许套表。**

真实反例：`src/mpltweak/mcp_server.py` 的 MCP 启动行套了 `_msg.t_ui(...)` 之后，MCP **握手直接断**（`tests/test_mcp.py` 红，尾行 `FAILED: 1 项 -> ['MCP 端到端']`）；把该文件写回 HEAD 后立刻 ALL PASS。判据：**改完必须跑受影响的那条命令/套件**，`ast.parse` 通过不代表行为没坏。

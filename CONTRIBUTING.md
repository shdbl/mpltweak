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

**The authoritative list of suites is `SUITES` in `tests/test_suites.py`** (17 entries as of
2026-10-13; two more are deliberately excluded — see its `SUITES_EXCLUDED` and header docstring).
The bar for a PR is a green **`pytest -q`** (all registered suites + the hygiene ratchets), not
"these seven".

### The pytest route (optional locally, but this is what CI trusts)

`tests/test_suites.py` wraps **every suite in `SUITES`** — 17 today, not just the seven above — one
test case per suite, so CI reports, single-suite selection and IDE integration all work.
`pyproject.toml` pins `python_files = ["test_suites.py", "test_hygiene.py"]`, so plain `pytest -q`
collects exactly those two wrappers and the suites themselves run as subprocesses:

```bash
pip install -e ".[dev]"
pytest -q                       # all registered suites + the hygiene ratchets
pytest -q -k test_writeback     # just the write-back engine
pytest -q -k notation_discipline  # or any suite registered later
```

`tests/test_hygiene.py` holds the engineering-hygiene ratchets that used to live only in prose (21
test functions today: module registration, GBK encodability, the broad-`except Exception` count, the
child-process env-pinning ledger, the "no real GUI objects in tests" guard, …). The two original
ones:

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
  python tests/test_hygiene.py        # GBK-encodable + the ratchets
  pytest -q -k hygiene
  ```

  The usual offenders are characters outside cp936: box drawing, emoji, `→` / `✓`, en/em dashes and
  CJK punctuation such as `「」`. Prefer ASCII or plain Chinese. The JSON path has the same trap for the
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
4. keep this file in sync: the suite list above and this section are the human-facing registry.
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

**CI coverage — known gap, stated plainly (as of 2026-10-13).** The plain-script loop
(`.github/workflows/tests.yml:53-58`) runs **only the seven legacy suites**, on all 8 matrix legs.
Everything in `SUITES` *does* run through `pytest -q` in the `pytest report + hygiene ratchet` step
(`:63-72`), but that step is guarded by `if: matrix.python-version == '3.12'` ⇒ **the 10 suites added
after 0.1.9 are never exercised on Python 3.9 / 3.10 / 3.11**. Closing it means either adding them to
the loop or dropping the 3.12-only guard; that is a release-pipeline change, so it is recorded here as
a TODO rather than changed silently.

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

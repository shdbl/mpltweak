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

There are seven suites, all plain scripts (no pytest needed):

```bash
python tests/test_tweak.py        # core interaction helpers
python tests/test_events.py       # synthetic mouse/key events through the callbacks
python tests/test_redo.py         # undo/redo
python tests/test_writeback.py     # AST write-back engine  ← most sensitive
python tests/test_agent_api.py    # describe / schema / apply --json
python tests/test_check.py        # layout health check
python tests/test_mcp.py          # MCP server (skips itself if `mcp` isn't installed)
```

Each prints `ALL PASS` or a failure list. **All seven must pass before a PR.**

### The pytest route (optional, same suites)

`tests/test_suites.py` wraps those seven scripts in pytest — one test case per suite, so CI
reports, single-suite selection and IDE integration all work:

```bash
pip install -e ".[dev]"
pytest -q                       # all suites + the hygiene ratchet
pytest -q -k test_writeback     # just the write-back engine
```

`tests/test_hygiene.py` holds two ratchets that used to live only in prose:

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

### Adding a suite

Adding a test file means touching four places — CI, both test wrappers and the docs:

1. `tests/test_yoursuite.py`, printing `ALL PASS` (copy the shape of `test_check.py`);
2. the `for t in ...` loop in `.github/workflows/tests.yml`;
3. `SUITES` in `tests/test_suites.py`;
4. the list above, plus the handbook section that describes the suites.

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

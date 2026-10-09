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

Qt (PyQt5) is in the default dependencies because opening the window requires it.
For headless work you can skip it:

```bash
pip install -e . --no-deps   # then install only what you need
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

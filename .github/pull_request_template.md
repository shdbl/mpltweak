## What changed

<!-- One or two sentences. -->

## Why

<!-- What problem does this solve? A concrete case beats an abstract one. -->

## Checklist

- [ ] All seven suites pass:
      `test_tweak` `test_events` `test_redo` `test_writeback`
      `test_agent_api` `test_check` `test_mcp`
- [ ] If this touches the write-back engine (`writeback.py`) or verification
      (`verify.py`), I ran `tests/test_writeback.py` and it's green — and ideally
      tried it on a real script of my own.
- [ ] If this changes the CLI, the command tables in `README.md` **and**
      `README.en.md` are updated too.
- [ ] **The hard rule still holds**: nothing from this tool leaks into a user's
      plotting script (no `import mpltweak`, no `gaitu()`, no `--tweak` branch,
      no `_POS`/`_FONTS` block written back).

## Notes for the reviewer

<!-- Anything you're unsure about, or a decision you'd like a second opinion on. -->

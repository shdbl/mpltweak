<div align="center">

<img src="https://cdn.jsdelivr.net/gh/shdbl/mpltweak@e9b34f0/docs/banner.png" width="720" alt="mpltweak — matplotlib layout, the PPT way">

# mpltweak

**Lay out matplotlib figures the way you arrange slides in PowerPoint, then write the numbers back into your code.**

[![PyPI](https://img.shields.io/pypi/v/mpltweak?style=flat-square&color=2F6FEB)](https://pypi.org/project/mpltweak/)
[![Python](https://img.shields.io/pypi/pyversions/mpltweak?style=flat-square)](https://pypi.org/project/mpltweak/)
[![License](https://img.shields.io/badge/license-MIT-2F6FEB?style=flat-square)](LICENSE)
[![Tests](https://github.com/shdbl/mpltweak/actions/workflows/tests.yml/badge.svg)](https://github.com/shdbl/mpltweak/actions/workflows/tests.yml)

[中文](https://github.com/shdbl/mpltweak/blob/main/README.md) | **English**

Docs linked from this file (`docs/*.md`) are currently written in Chinese.

</div>

## What problem it solves

When you lay out a multi-panel figure, the trouble is usually the layout rather than the data: move one subplot and the neighbouring axis label is covered; increase a font size a little and the legend overlaps a curve. And every change means re-running the script and looking at the figure again.

**What mpltweak does**: you drag panels the way you arrange pictures in PowerPoint, and then those few numbers go back into your source code. It **only handles style and position; it does not invent content**: text, data and graphics are still decided by your code.

| Approach | Flow |
|---|---|
| **Traditional** | change a number → re-run the script → wait tens of seconds → look at the PNG → still wrong → change again |
| **mpltweak** | open the window → drag into place → `mpltweak apply --write` → those numbers are back in the code |

Demo:

<table>
<tr>
<td width="50%"><img src="https://cdn.jsdelivr.net/gh/shdbl/mpltweak@e9b34f0/docs/gifs/01_drag_layout.gif" alt="Drag a panel with snapping guides"><br>
<b>Drag + edge snapping</b><br><sub>A ghost preview and alignment guides appear while dragging; near an alignment position it snaps</sub></td>
<td width="50%"><img src="https://cdn.jsdelivr.net/gh/shdbl/mpltweak@e9b34f0/docs/gifs/02_multi_align.gif" alt="Multi-select align + distribute"><br>
<b>Multi-select align / distribute</b><br><sub>Ctrl-click three panels → left align + vertical distribute</sub></td>
</tr>
<tr>
<td><img src="https://cdn.jsdelivr.net/gh/shdbl/mpltweak@e9b34f0/docs/gifs/03_fontsize.gif" alt="Change font size by hovering"><br>
<b>Font size on hover</b><br><sub>Hover a title, axis label, tick label or legend and press <code>+</code> / <code>-</code></sub></td>
<td><img src="https://cdn.jsdelivr.net/gh/shdbl/mpltweak@e9b34f0/docs/gifs/04_wireframe.gif" alt="cartopy wireframe mode"><br>
<b>Wireframe mode for speed (space bar)</b><br><sub>Full redraw of a cartopy world map <b>494ms → 169ms</b>, so dragging keeps up</sub></td>
</tr>
</table>

> The full gesture set and key map (drag / multi-select, align / distribute, snapping, `Ctrl+F` margin trim, space-bar wireframe, font size on hover, legend snapping to 8 standard positions, colorbar length / thickness) are in [docs/interaction.md](https://github.com/shdbl/mpltweak/blob/main/docs/interaction.md).

## Install

Every command below runs in a **terminal** — on Windows cmd / PowerShell, on macOS / Linux Terminal, and in PyCharm the bottom **Terminal** panel (**not** the Python Console / interactive interpreter).

```bash
pip install mpltweak            # core: describe / check / apply (mcp is an optional adapter layer, see "Using it with Agents")
pip install "mpltweak[qt]"      # add this if you want to drag figures in a window (installs the PyQt5 backend)
```

The second line is for **opening a window**: that needs a graphics backend (in plain words, the component set that lets a figure appear in a draggable window — PyQt5 recommended, about +150MB). Command-line-only use (`describe` / `check` / `apply`) does not need it. The official Python installers for Windows / macOS bundle `tkinter`, so `pip install mpltweak` alone usually opens a window; in that case it uses the **Tk backend (reduced interaction)**, which is neither an installation failure nor a broken install. With no graphics backend at all, opening the window exits with `rc=4` (exit code 4: "this machine currently has nothing that can draw a window") and prints which one to install.

> Backend comparison table, per-platform installs, `mpltweak doctor` probing order and per-symptom handling of a failed window — [docs/interaction.md](https://github.com/shdbl/mpltweak/blob/main/docs/interaction.md).
> Which commands **execute** your script, and how exit codes are derived — [docs/exec-boundaries.md](https://github.com/shdbl/mpltweak/blob/main/docs/exec-boundaries.md).

## 5-minute quick start

`cd` into the folder that holds your script first:

```bash
cd your-folder          # ← replace with the folder that holds fig1.py (copy the whole line)
```

The example script `fig1.py` (ordinary matplotlib: **not one line to change**, no `import mpltweak`, no hooks):

```python
# fig1.py -- ordinary matplotlib, nothing added
import matplotlib.pyplot as plt
import numpy as np

fig = plt.figure(figsize=(10, 8))
x = np.linspace(0, 1, 200)

ax1 = fig.add_axes([0.08, 0.66, 0.30, 0.21])      # the position is these numbers
ax1.plot(x, np.sin(6 * x))
ax1.set_title('(a)')

ax2 = fig.add_axes([0.11, 0.38, 0.30, 0.21])      # hand-written numbers, usually uneven
ax2.scatter(np.random.rand(60), np.random.rand(60), s=8)

ax3 = fig.add_axes([0.05, 0.08, 0.30, 0.21])
ax3.hist(np.random.randn(300), bins=20)

fig.savefig('fig1.png', dpi=150)
```

**What it will do (up front)**: it will **run your script once** (in your own environment, with your own data; mpltweak itself does not go online and does not change your files), then show the figure in a window you can drag around. **Until you close the window, your `.py` has not changed by a single character** — whatever you drag lands in `.tweak_params/fig1.json` (a plain-text params file recording the positions and font sizes you dragged to).

| Step | Command | What you will see |
|---|---|---|
| **1 · Install** | `pip install mpltweak`<br>`pip install "mpltweak[qt]"` | the core package plus whichever graphics library lets the figure open in a window; command-line-only use (`describe` / `check` / `apply`) skips the second line |
| **2 · Open the window and drag** | `mpltweak fig1.py` | the layout window: **drag** panels to move them, drag **edges / corners** to resize, **hover text** and press `+` / `-` for font size, **space** for wireframe speed, **Ctrl+F** to trim margins; press **`?`** for the full key map |
| **3 · Close the window** | —— | the script has **not changed by a single character**; the params land in `.tweak_params/fig1.json` (open the window again to keep adjusting). If you **really dragged something**, closing the window prints the next command for you to copy — nothing to memorise |
| **4 · See what it intends to change** | `mpltweak apply fig1.py` | a per-axis list of positions and font sizes, plus whether it will **edit a number / insert a layout block / refuse**; read-only: it does not execute the script and writes no file |
| **5 · Write back** | `mpltweak apply fig1.py --write` | **three stages: preview (read-only, does not run the script) → you decide → write back**; the write-back **backs the file up** (`.tweak_params/fig1.tweak.bak`), reports success in **one line**, and **one command undoes it** (`mpltweak revert fig1.py --write`) |
| **6 · Re-run and look** | `python fig1.py` | the figure looks the way you dragged it; not happy? `mpltweak revert fig1.py --write` goes back to before the write-back (without `--write` it is a preview) |

**Writing back is deterministic.** AST parses your code into a syntax tree (it reads code structure, not text patterns), so it can pinpoint those numbers. Write-back takes one of two forms: **when the code holds literals, those numbers are edited directly** (cleanest, nothing inserted); **otherwise a self-contained layout block is appended at the end of the file** (adding one independent piece of code: no `import`, no change to your own logic). Both forms go through **three verification layers** (syntax / re-run / semantic: first check the syntax, then really run the script, then confirm the positions and font sizes on the figure match what you dragged), and if any layer does not pass the file is **rolled back by default** (back to how it was; `--keep-unverified` keeps the write-back, and then you must check it yourself).

**Built the figure with `plt.subplots()` / `GridSpec`?** The source often has no number you can edit directly, so it takes the "append a layout block" route — and `mpltweak revert fig1.py --write` takes that block away again; the code you wrote is not rewritten.

> Stuck? See [docs/troubleshooting.md](https://github.com/shdbl/mpltweak/blob/main/docs/troubleshooting.md) (dragging does nothing / the panel snaps back / an error — each one is "symptom → the reason in one line → what to do").

> **More detail**: the complete output sample of one real write-back, the per-field list under `-v`, the two single-command routes `--style inplace` / `--style block`, what a layout block looks like (the `auto layout` marker), and the params-file field table — [docs/verify.md](https://github.com/shdbl/mpltweak/blob/main/docs/verify.md) and [docs/params.md](https://github.com/shdbl/mpltweak/blob/main/docs/params.md).

## Is it reliable?

We ran three kinds of tests: **static judgement** — take **10,158 public plotting scripts** (real research plotting code, unique by sha256) and decide for each whether this change can land **in place in the source**, without executing anything; **real re-runs** — actually run **1,780** of those scripts and check that they still produce a figure and that the positions and font sizes on it match the dragged params; **random operations against window interaction** — **13,000** off-screen operations actually run (every operation checked afterwards) and **6,000 gestures + 27 hard assertions** in real windows. The measured results (full version in [docs/reliability.md](https://github.com/shdbl/mpltweak/blob/main/docs/reliability.md)):

| Layer | What was tested | Result |
|---|---|---|
| Static corpus (**scripts not executed**) | **What it tests**: whether a change can land **in place in the source** (the target is found, only the target moves, the syntax is unchanged)<br>**Sample**: 10,158 scripts → of which **4,427 are plotting scripts** → **4,101 measurable sites** (of which **3,610 are reachable in place**) | **Landing back in place**: **97.8%** (3,531 / 3,610); **appending the code block instead**: **100%** (427 / 427 — source outside the block kept byte-for-byte · no run-time location · statically provable to hit the target); **3 more landed via a block · 7 refused (target not provable) · 37 rejected as invalid params** (all within those 3,610 sites reachable in place); broken promises **0** |
| Full re-run (**scripts really re-run**) | **What it tests**: after the write-back the script still runs, the figure still comes out, and positions / font sizes match the params<br>**Sample**: pool of **1,837** → **1,780 runnable candidates** (= 1,837 − 28 statically known to open a window − 29 statically known to need network / subprocesses) | **Written back and verified: 928**, the four laws (idempotent / reversible / syntax preserved / only the target moves) **928 / 928 each**; the other **852 are undetermined, not failures** (needs a window 317 · waits for input 464 · hit the 90 s timeout 71; 928 + 852 = 1,780); no-tamper across all 1,837: **0 broken promises** |
| Window interaction (random ops + real windows) | **What it tests**: under random operations — **no crash / undo-redo round-trip consistency / legal geometry / landing matches the params**<br>**Sample**: off-screen **12 figure kinds × 1,000 ops** (13,000 ops actually run, invariants checked after every op); real windows **6 figure kinds × 1,000 = 6,000 gestures** | Off-screen: **invariant checks that must pass: 100%** (13,000 / 13,000); targeted operation assertions **100%** (356 / 356, with 47 more not applicable, each carrying a reason); real-window hard assertions **100%** (27 / 27); ALL PASS on both console encodings (UTF-8 / GBK) |

**"Broken promise"** = the tool says "written back / verified" while the bytes did not actually change, or verification never ran — **0** in this version. (On the static layer we really take public plotting scripts and decide where the edits land; the interaction layer is not judged by "looks right" but by checking no-crash / round-trip consistency / legal geometry / landing matches the params under random operations.)

> Full version (per-family pass tables, the block-channel criteria, both real-window log rounds, and "what the part below 100% actually is") — [docs/reliability.md](https://github.com/shdbl/mpltweak/blob/main/docs/reliability.md).
> What it **does not claim** (cases it does not apply to / what verification does not claim / where the corpus numbers come from) — [docs/boundaries.md](https://github.com/shdbl/mpltweak/blob/main/docs/boundaries.md).

## Using it with Agents

**You do not need to know any command line — just hand this to an Agent.** (Or type the commands yourself: dragging gets it right, and **you never hand-write those position numbers**.)
(It has to be one that can read and write files on your machine directly; a plain web chat window cannot.)

Send this link to the Agent you already use (Claude / ChatGPT / Cursor / DeepSeek — anything that can read and write files on your machine), and it will set itself up and do the tweaking for you:

- **Operating manual for the Agent**: https://github.com/shdbl/mpltweak/blob/main/skills/mpltweak/SKILL.md
  (Or ask it to open `skills/mpltweak/SKILL.md` in this repository, or paste that file's content to it.)

### The recommended flow: six steps (just talk to your Agent)

1. **Ask the Agent to draw the figure.** You only say what you want ("plot these two groups as a comparison bar chart"); the Agent writes the script and runs it.
   *What you see*: the Agent shows you the figure it drew.
2. **First check that the content is right** (do not start on the layout yet). Are the axes, labels and data what you meant? If not, say so now.
   *What you see*: a finalised version of the content — from here on you are only tweaking layout, not what is drawn.
3. **Tell the Agent "I want to tweak it interactively".** It opens the layout window itself (you install nothing and type no commands).
   *What you see*: a window pops up with your figure in it, ready to drag and zoom.
4. **You make the changes in the window.** Drag panels and legends, adjust font sizes, align, space evenly (press `?` in the window to bring up the full key legend at any time).
   *What you see*: a translucent preview while dragging; the change lands when you let go.
5. **The Agent tells you in plain words what it changed and waits for your confirmation.** For example: "panel (a) moved slightly to the right; title font size 12 → 14". At this point **nothing in your code has changed yet** — if you disagree, keep talking and it keeps adjusting.
   *What you see*: a plain-language list of what changed + one question: "shall I write it into the code?"
6. **Only after you say yes does the Agent write the change into the code, and then re-run it for you.** Compare the re-run against step 2 to make sure nothing was broken.
   *What you see*: the new figure plus which spots changed; if you are not happy, keep tweaking, or ask it to undo the last write-back (`revert`).

> **You never open a command line and never memorise a command** — you look at figures and talk.

```mermaid
flowchart LR
    A["1. Tell the Agent<br/>what figure you want"]:::script --> B["2. The Agent writes the script<br/>and runs it"]:::win
    B --> C{"3. You look: is the content right?"}:::check
    C -->|no| B
    C -->|yes| D["4. Say: tweak it interactively<br/>the Agent opens the layout window"]:::win
    D --> E["You drag things in the window<br/>(press ? for the key legend)"]:::win
    E --> F{"5. The Agent explains in plain words<br/>what it changed"}:::check
    F -->|not happy| E
    F -->|happy| G["6. The Agent writes it into the code<br/>and re-runs it"]:::win
    G --> Z(["You look at the new figure - done"]):::ok

    classDef script fill:#F7F8FA,stroke:#C9D1DB,stroke-width:1.4px,color:#16181D
    classDef win fill:#2F6FEB,stroke:#2F6FEB,stroke-width:1.6px,color:#FFFFFF
    classDef check fill:#F5F8FF,stroke:#2F6FEB,stroke-width:1.6px,color:#16181D
    classDef ok fill:#EAF7EF,stroke:#2E9E5B,stroke-width:1.4px,color:#16181D
```

**Technical pointers (all in `docs/`)** — MCP (the optional adapter layer) and the JSON contract, **the three read → modify → write → check commands**, the table of the four subcommands with exit codes, the parameter signatures of the four MCP tools, **the ways humans and agents split the work**, and **the layout-engine conflicts to look at before touching the layout** — all of them: [docs/agent.md](https://github.com/shdbl/mpltweak/blob/main/docs/agent.md).

## License

MIT

---

**Full documentation:** keys and gestures [docs/interaction.md](https://github.com/shdbl/mpltweak/blob/main/docs/interaction.md) · params-file field table [docs/params.md](https://github.com/shdbl/mpltweak/blob/main/docs/params.md) · three verification layers, backups and `revert` [docs/verify.md](https://github.com/shdbl/mpltweak/blob/main/docs/verify.md) · stuck [docs/troubleshooting.md](https://github.com/shdbl/mpltweak/blob/main/docs/troubleshooting.md) · measured numbers [docs/reliability.md](https://github.com/shdbl/mpltweak/blob/main/docs/reliability.md) · boundaries [docs/boundaries.md](https://github.com/shdbl/mpltweak/blob/main/docs/boundaries.md) · execution boundaries and exit codes [docs/exec-boundaries.md](https://github.com/shdbl/mpltweak/blob/main/docs/exec-boundaries.md) · manual for Agents [docs/agent.md](https://github.com/shdbl/mpltweak/blob/main/docs/agent.md) and [skills/mpltweak/SKILL.md](https://github.com/shdbl/mpltweak/blob/main/skills/mpltweak/SKILL.md) · development and tests [docs/dev.md](https://github.com/shdbl/mpltweak/blob/main/docs/dev.md)

# hic et nunc

An experimental command-line tool that checks whether a finished
illustration is consistent with a recording of how it was drawn.

**What it does.** A plugin for [Krita](https://krita.org) records your
drawing while you work: pen strokes, a picture of the canvas every 5
seconds, and Krita actions such as paste. Afterwards `hic-et-nunc report`
reads the recording and writes one web page with a result (consistent,
needs review or inconsistent) and the reasons. It checks that the
recording's entries still link up, that the delivered image matches the
last recorded state, whether images were pasted in and, if you give it a
reference image, whether the lines follow that image's outlines
(tracing). It can also read a time-lapse video, with weaker results.

**What it doesn't do.** It doesn't prove who made an image, that a
person made it, or that no AI was used. A program can write a recording
that passes, and tracing an image the tool isn't given goes unnoticed
([`EVALUATION.md`](EVALUATION.md)). It is not an AI detector. Tested by one
person, on one Mac, with one tablet.

**Who might find it useful.** People studying what a recording of the
drawing process can and can't show; developers of drawing-tool plugins;
artists who want a record of their own process. It is not ready to settle
a dispute about who made a piece.

**See a result.** Two reports from the author's own Krita recordings:
[a free drawing](https://andreanerla.github.io/hic-et-nunc/examples/reports/free-drawing.html)
(consistent) and
[a tracing over a Matisse cut-out](https://andreanerla.github.io/hic-et-nunc/examples/reports/matisse-2-tracing.html)
(needs review). Files: `examples/reports/`.

**Try it.** About two minutes on a Mac with Python 3.10 or newer: follow
Install, then "Try it with synthetic data" (no Krita needed). Recording
your own drawings needs Krita 5 and a pen tablet.

**Status.** Experimental, version 0.1.0. See
[Status and maintenance](#status-and-maintenance).

## What it does not prove

A time-lapse is read after the fact, so the evidence is **weak**: it shows
that the delivered image is consistent with a recorded process. It is not
proof against:

- a determined forger who re-creates a process for an image they already
  have (painting over a projected image, or generating a fake time-lapse);
- an AI agent or plotter that draws the image stroke by stroke;
- edits to the recording itself. Time-lapses aren't signed or
  tamper-evident, and nothing here timestamps them.

It is **not an AI detector**. It never labels an image as AI-generated from
the image alone; an unexplained paste or traced reference leads to `review`,
not to an AI label. A `pass` means "no inconsistency found", not "proven
human-made".

Thresholds are **calibrated on synthetic images**, except the tracing
limit 2.5, set from six real Krita sessions; the delivered-image check
was tested on nine (`plugins/krita/SPIKE.md`). All were drawn by one
person with one tablet. No real `.procreate` file has been used.

## Install

Tested on macOS 15 (Apple silicon) only; Linux and Windows are untested.

Needs Python ≥ 3.10: check with `python3 --version`. The `python3` that
comes with macOS is 3.9 and won't work; install a newer one (from
python.org, or `brew install python@3.13`) and call it by name, as below.

```bash
git clone https://github.com/andreanerla/hic-et-nunc.git
cd hic-et-nunc
python3.13 -m venv .venv    # any Python ≥ 3.10
source .venv/bin/activate
pip install -e .            # add ".[dev]" for tests and C2PA signing
pytest                      # with [dev]
```

`pytest` should end with `81 passed, 49 skipped`. The skipped tests need
the author's own Krita recordings, which aren't published.

### Krita plugin

Tested with Krita 5.3.4.1. The plugin is in `plugins/krita/`.

1. In Krita: Settings → Manage Resources → **Open Resource Folder**.
2. Copy `plugins/krita/hic_et_nunc_capture.desktop` and the
   `plugins/krita/hic_et_nunc_capture/` folder into its `pykrita/`
   subfolder (create it if it's missing).
3. Settings → Configure Krita → **Python Plugin Manager** → enable
   "Hic et nunc Capture (spike)". Restart Krita.
4. Open or create the document, then Tools → Scripts → **Hic et nunc:
   start/stop capture**. Draw. Run the same action again to stop.

Each recording is one folder, `~/hic-et-nunc-capture/<date>-<time>/`
(for example `20261114-183713`), holding the log and the canvas
snapshots. Give that folder, not `~/hic-et-nunc-capture/` itself, to
`hic-et-nunc report`. Add reference images with Krita's Reference Images
tool, not as a layer (see Known limitations), and save the document: the
saved `.kra` is what you pass as `--reference`. More detail, and the
recordings the limits were measured on, in `plugins/krita/SPIKE.md`.

## Usage

```bash
hic-et-nunc evaluate artwork.procreate --delivered artwork.png
hic-et-nunc evaluate timelapse.mp4 --delivered artwork.png --reference stock=photo.jpg
```

| Option | Meaning |
|---|---|
| `SOURCE` | `.procreate` file, a time-lapse video, or a Krita capture session directory |
| `--delivered IMG` | the image that was actually delivered; checked against the last frame |
| `--reference ORIGIN=IMG` | a reference image the artist declares; `ORIGIN` is one of `camera`, `stock`, `own`, `ai`, `unknown`. Repeatable. For a Krita reference image, pass the saved `.kra`: it records where the reference sat on the canvas, while a plain image is stretched to the document's shape |
| `--policy p1\|p2\|p3` | which example policy turns the facts into a verdict (default `p2`) |
| `--d PX` | edge-proximity distance for tracing, in pixels (default 2) |

### Reviewer report

For someone who isn't a developer (a client, a jury), `hic-et-nunc report`
writes one self-contained HTML page:

```bash
hic-et-nunc report artwork.procreate --delivered artwork.png -o report.html
hic-et-nunc report ~/hic-et-nunc-capture/<session> --reference drawing.kra -o report.html
hic-et-nunc report ~/hic-et-nunc-capture/<first> ~/hic-et-nunc-capture/<second> --delivered artwork.png -o report.html
```

`SOURCE` is the same as for `evaluate` (a Krita session directory, a
time-lapse video or a `.procreate` file); `--reference` (one image, or the
saved `.kra` holding it) and `--delivered` are optional. A piece recorded
over several Krita sessions is given as all its session directories, in
the order they were recorded: each session must start where the previous
one ended, and only the last is compared with the delivered image. A
recording whose canvas already has marks on more than 0.01% of it when it
starts gets "needs review", unless it follows an earlier session of the
same piece (see Known limitations). The page gives:

- a result: **consistent** (nothing to look at), **needs review** (the
  line work follows the reference's outlines, an image was pasted, or
  the delivered image lines up with the last recorded state but differs
  from it, overall or in boxed places such as a signature) or
  **inconsistent** (the delivered image doesn't line up with the last
  recorded state and doesn't match it, or a Krita recording fails
  verification). Never "AI";
- the reasons in plain language, with the limit each was measured against;
- pictures: the reference, the final state and delivered image, every
  stroke drawn in red over a faded reference (for a time-lapse, the line
  work that appeared), each paste boxed on the canvas where it appeared,
  and eight states across the process;
- what it can't tell (not an AI detector, forgers who redraw stroke by
  stroke, fill-heavy tracing, pastes in recordings made before the plugin
  logged Krita's actions, ...) and the
  measurements behind each reason.

The thresholds are the same as `evaluate`'s P2 policy: tracing ratio 2.5,
delivered-image similarity 0.9, changed share 0 (any locally changed
block means review). The page holds pictures of the drawing and
its progress: share it only with whoever should see them.

On the real Krita sessions (`plugins/krita/SPIKE.md`) the three line
tracings give "needs review" for tracing, the free drawing and the
fill-heavy tracing give "consistent", and the paste session gives "needs
review" for its one paste. A session with Paste, Transform and Paste into
Active Layer gives "needs review" for its two pastes, the first at its
final size after scaling; a control session with fills and undo gives
"consistent".

### Try it with synthetic data

`examples/synthetic_timelapse.py` draws a simple landscape stroke by stroke
(no third-party artwork) and writes time-lapse videos plus finished images:

```bash
python examples/synthetic_timelapse.py example-out
hic-et-nunc evaluate example-out/timelapse.mp4 --delivered example-out/delivered.png   # pass,   SSIM 0.996
hic-et-nunc evaluate example-out/timelapse.mp4 --delivered example-out/other.png       # fail,   SSIM 0.85: a different drawing
hic-et-nunc evaluate example-out/pasted.mp4    --delivered example-out/pasted.png      # review, paste at frame 71
```

Note the second case: a different drawing of a similar flat-colour scene
still scores 0.85 against a 0.9 limit. The margin is small, which is one
reason the thresholds need calibration on real work.

The same three cases as reviewer reports; open the `.html` files in a
browser:

```bash
hic-et-nunc report example-out/timelapse.mp4 --delivered example-out/delivered.png -o example-out/same.html     # consistent
hic-et-nunc report example-out/timelapse.mp4 --delivered example-out/other.png -o example-out/other.html        # inconsistent
hic-et-nunc report example-out/pasted.mp4 --delivered example-out/pasted.png -o example-out/pasted.html         # needs review
```

Output of the first command:

```json
{
  "policy": "P2 human-made image",
  "verdict": "pass",
  "reasons": ["pass: no issues found"],
  "evidence": {
    "tier": "weak",
    "consistency_ssim": 0.9964,
    "imports": [],
    "paste_steps": [],
    "notes": ["time-lapse: 142 frames sampled"],
    ...
  },
  "tracing": {}
}
```

- `consistency_ssim`: grayscale SSIM between the last frame and the
  delivered image, after aligning them (SIFT features, a shift, scale
  and rotation fitted with RANSAC), over their overlap.
  `consistency_aligned` says whether they aligned; if not, the delivered
  image is stretched to the frame. Below 0.9 the policies fail an image
  that didn't align and send one that did to review.
- `changed_share`, `changed_regions`: the share of the overlap in 32 px
  blocks with SSIM below 0.6, and their boxes. Above 0 (`max_changed`)
  the policies send it to review: a signature, a logo or replaced
  content.
- `paste_steps`: frame indices where at least 15% of the canvas changed at
  once with high edge density. Bucket fills change large areas too but add
  almost no edges, so they aren't flagged.
- `imports[].tracing_ratio`: among the new line work lying near the
  reference's structure (within 3.65% of the canvas's longer side of its
  edges), the share within 2 px of an edge, divided by the share of that
  area within 2 px. About 1 means unrelated; above 2.5
  (`policy.TRACING_RATIO_LIMIT`, a policy parameter) the policies treat it
  as traced. Restricting to that band keeps a drawing that just sits where
  the reference is from scoring as traced. `tracing` also gives a
  `p_value`: the share of 200 random placements of the reference (rotated
  and moved on the canvas) that score as high.
- Verdicts: `pass`, `pass_with_disclosure`, `review`, `fail`. Exit code 0
  for the first two, 1 for the others, 2 for unreadable input. `report`
  exits 0 whenever it writes the page, whatever the result, and 2 for
  unreadable input.

Each time-lapse is sampled to at most 300 frames, downscaled to 1024 px,
grayscale.

### Known limitations

- While capturing, each snapshot (every 5 s) stalls Krita for about
  260 ms. A snapshot due during a stroke waits until the pen is lifted
  (at most 30 s), so painting hitches right after lifting the pen, a
  stroke started during that hitch loses its first pen samples, and a
  stroke longer than 30 s still gets one freeze.
- The reviewer report hasn't been read by anyone new to the tool. Its
  one reader so far, the author, found it repetitive and too technical:
  a per-session prefix on most lines, and scores and counts in the main
  text.
- The `.procreate` reader expects the time-lapse at
  `video/segments/segment-N.mp4` inside the archive. This layout comes from
  public descriptions and hasn't been checked against a real file.
- Thresholds are calibrated on synthetic images, except the tracing limit
  2.5 (six real Krita sessions) and the delivered-image check (nine), all
  drawn by one person with one tablet. No real `.procreate` file has been
  used.
- Delivered image, measured on nine real Krita sessions
  (`plugins/krita/SPIKE.md`): no honest variant of 151 (crops, resizes,
  JPEG, colour conversions, letter- and pillarboxed time-lapses, logos,
  signatures) gets "inconsistent", and none of 19 altered images
  "matches". But every logo or signature (1-3% of the image) gives
  "needs review", as does a CMYK round trip that shifts a mostly dark
  image (47 of 151 honest variants).
- Krita recordings: events after the last checkpoint (up to 60 s)
  aren't covered by it; the report says how many. The checkpoints are
  stored with the recording, so its owner could rebuild both. A last log
  line cut off by a crash is left out and reported; the rest is checked.
- Removal of the final log entries can't be detected: it looks the same
  as a recording that ended in a crash. Both get "needs review", so an
  honest recording that ended in a crash does too.
- A recording that starts on a canvas already holding part of the
  drawing (an imported sketch, a template, a textured paper, or an
  earlier session not given to the report) gets "needs review" when
  more than 0.01% of the canvas (about 74 px of the 1024 px snapshot)
  differs from its background by more than 30 levels in any colour
  channel. Less than that, or marks fainter than that, goes unseen.
  Honest recordings measured: 14, all on plain white, all from one
  person. Reports over several sessions are tested only on three single
  recordings split in two, not on a real recording made in two sittings.
- An edit made outside the recording, on the delivered image or on the
  canvas between two sessions, can go unseen if it is smaller than about
  one 32 px block of the 1024 px snapshot (about 0.1% of the canvas, a
  ~90 px square on a 2480×3508 page): 2 of 12 such edits between
  sessions gave "consistent".
- A sketch on a layer that is hidden, or at 0% opacity, when capture
  starts and is shown later isn't seen: the first snapshot is blank,
  and showing a layer is not a stroke, a logged action or a new layer.
  Measured on a rebuilt log only (line art on 0.34% of the canvas gave
  "consistent"); not yet checked in a real Krita recording.
- `evaluate` doesn't check whether the canvas was already drawn on when
  recording started; only `report` does. A recording started on line
  art (0.34% of the canvas) gets "needs review" from `report` but
  "pass" from `evaluate`.
- Tracing is checked only against a reference image given to the
  command. Tracing an image that isn't given, or that is declared as
  your own (`own=`), isn't detected: three real tracings got
  "consistent" from `report` and "pass" from `evaluate`, also under the
  policy named "P1 no generative AI" (`EVALUATION.md`, E1).
- A recording written by a program, with no Krita and no person
  (pressure constant, samples exactly 5 ms apart), gets "consistent" and
  tier "strong", and the report says it was "Recorded live in Krita,
  with a pen tablet". The recording holds whatever its writer put in it
  (`EVALUATION.md`, E2).
- How much of a declared reference survives in the final image isn't
  measured; pasted references are caught by paste detection instead.
- How AI detectors behave on traced or redrawn AI images is unmeasured.
- The tracing ratio can't exceed 1 / `baseline` (in `tracing`). On small
  frames the band is barely wider than the 2 px edge distance: on 200 px
  frames a perfect tracing scores ~2.1. When even a perfect tracing
  couldn't exceed the limit, `trace` and `evaluate` report "can't tell at
  this resolution" instead of a ratio, and no `tracing_ratio` is recorded.
- A drawing that clusters at scales below the band (~37 px on a 1024 px
  frame) right around the reference's edges still raises the ratio.
- Tracing that is mostly fill (hatching inside dark areas) scores low: it
  isn't line work along edges. A real fill-heavy tracing scored 1.93 /
  1.25 and got "consistent".
- `p_value` is context, not a verdict. With a reference that fills the
  canvas only half-turns and small shifts fit, and unrelated drawings have
  scored p 0.005-0.025 at ratios 1.17-1.40.
- Krita pastes, untested cases: whether Duplicate Layer, cut or copy
  selection to a new layer, copy merged + paste, and paste then undo are
  reported as pastes (honest sessions could get "needs review"); paste
  into an existing layer then scale up (the final area may stay at the
  arrival size, as the layer's bounds barely change); paste on a new
  layer, paint on it, then scale up (the scaling is counted from bounds
  that include the painting, so the final area can be off).
- Krita pastes that go unreported or mis-sized: in recordings made before
  the plugin logged Krita's actions, a paste into an existing layer isn't
  reported and a new-layer paste's final size is unknown. Content that
  reaches a layer through no logged action (undo through the History
  docker, a script) isn't reported. Transforming only a selection, or
  painting and transforming the same layer within one snapshot
  interval (5 s; up to 35 s during a long stroke), throws off the final area.
- A reference image on a visible layer blinds the snapshot method:
  strokes drawn on its edges add no new edges to the rendered document.
  The stroke method still works. Add references with Krita's Reference
  Images tool instead.
- Reference rotation and scale follow Krita's saved format and are tested
  on synthetic documents only.
- On Windows with WinTab, Krita may handle pen input in ways that bypass
  Qt tablet events. The plugin's capability report shows whether that
  happens.

## Modules

| Part | Status |
|---|---|
| `timelapse.py`, `assess.py`, `hic-et-nunc evaluate` (Procreate / video ingestion) | main use case; video tested on time-lapses made from real Krita sessions, `.procreate` untested on a real file |
| `analysis.py` (consistency, pastes, tracing, structural change) | stable; tracing limit set on real Krita sessions, the rest on synthetic images |
| `eventlog.py` (hash-chained log, RFC 6962 Merkle commitments), `credentials.py` (C2PA custom assertion `io.github.andreanerla.hic-et-nunc.process.v1`) | experimental |
| `evidence.py` (evidence tiers), `policy.py` (example policies P1–P3) | experimental |
| `session.py`, `plugins/krita/` (live capture of pen input and canvas states in Krita) | spike, tested on one machine (Krita 5.3.4.1, macOS 15.7, one pen tablet); see `plugins/krita/SPIKE.md` |

Evidence tiers, from `evidence.Tier`:

| Tier | Meaning |
|---|---|
| STRONG | live pen input + canvas states, rolling timestamps (Krita spike) |
| MEDIUM | live canvas states per history step, rolling timestamps |
| WEAK | project file or time-lapse read after the fact (Procreate, video) |
| NONE | final file only |

Other commands, all experimental: `hic-et-nunc inspect` and `hic-et-nunc trace`
read a Krita capture session; `hic-et-nunc devcerts`, `hic-et-nunc sign` and
`hic-et-nunc read` embed and read a C2PA Content Credential with
development certificates (validators report them as untrusted).

## Design notes

- **Thresholds are parameters, not facts.** They live in `Policy`
  (`policy.py`) and in function arguments in `analysis.py`, never hard-coded
  in the analysis.
- **Analysis is tool-agnostic.** Every function takes numpy images or point
  arrays, so Krita snapshots, Photoshop history captures and video frames go
  through the same code.
- **Structural change** is SSIM at about 128 px, restricted to the changed
  region. Global SSIM lets a small generative fill pass as "detail only";
  full-resolution SSIM penalizes upscalers. See `analysis.structural_change`.
- **Raw process data stays with the artist.** Only a summary and the log's
  Merkle root go into a credential; single events can be revealed later
  with inclusion proofs.
- **References.** `D-NNN` and `F-NNN` in the code and in
  `plugins/krita/SPIKE.md` point to the author's private decision log, and
  short commit hashes to the private development history. Neither is
  published.

## Status and maintenance

hic et nunc is an experimental research tool. It works as this README
describes and is tested on macOS only. There is no release schedule and
no promised feature work; it may stay as it is for long periods. Pull
requests are reviewed. Forks are welcome: everything needed to run, test
and change the tool is in this repository, except the author's own
recordings behind the 49 skipped tests.

## Contributing

To propose a change, open a pull request; for a larger change, open an
issue first so the approach can be agreed. Before sending it:

```bash
pip install -e ".[dev]"
pytest                                # 81 passed, 49 skipped
ruff check . && ruff format --check .
```

Say what you changed and how you checked it. A change to the analysis or
to a result needs a test showing the cases it separates. Don't commit
drawings, `.kra` or `.procreate` files from other people. What the code
does is summarized in Modules above; what has been tested and attacked is
in [`EVALUATION.md`](EVALUATION.md) and `plugins/krita/SPIKE.md`.

Bounded pieces of work, each tied to a known gap:

- **Check the `.procreate` reader against a real file** (intermediate;
  needs Procreate). The reader expects the time-lapse at
  `video/segments/segment-N.mp4`, a layout taken from public
  descriptions; no real file has been opened (`timelapse.py`). Done
  when one real file has been read, its result is reported with its own
  exported image and with an altered one, and the reader is fixed if
  the layout differs. Report the results, not the file.
- **Run it on Linux or Windows** (intermediate). Only macOS 15 has been
  tested. Done when `pytest` results on another system are reported in
  an issue, with fixes for what fails. On Windows the plugin's
  capability report also shows whether Krita's pen input bypasses the
  events the plugin records (see Known limitations).
- **Command help and a clean error for a bad output path** (beginner).
  `hic-et-nunc --help` lists seven commands with no description, and
  `report -o /no/such/dir/x.html` ends in a Python traceback (`cli.py`).
  Done when each command has a one-line description and a bad path gives
  a message and exit code 2, with a test that fails without the fix.
- **Why opencv 5.1 moves a paste box** (intermediate). With
  opencv-python-headless 5.1.0,
  `tests/test_timelapse.py::test_paste_visible_in_video` finds the
  paste's lower edge at 250 (the test needs more than 250), so
  `pyproject.toml` caps opencv below 5.1. Done when the cause is named,
  and the cap is either removed with the test passing on both versions
  or kept with the measured reason.
- **Impossible pauses inside strokes** (advanced). The bundled
  free-drawing log, `tests/data/krita/free`, has a pause of 56,475 s
  between two consecutive pen samples of one stroke. Stroke timings derived from them are wrong; the effect on results is
  unmeasured (`plugins/krita/hic_et_nunc_capture/capture.py`,
  `session.py`). Done when the cause is found, its effect on the tracing
  ratio is measured, and it is fixed in the plugin or the reader.

## License

AGPL-3.0-only. See `LICENSE`. Contributions are accepted under the same
license.
# Krita capture spike

Question this answers: **can a Python plugin record enough of the painting
process to tell tracing from free drawing?** If not, the strong evidence tier
doesn't work as designed.

All data stays in `~/hic-et-nunc-capture/<session>/` on your machine.

## Install (Krita 5.x)

1. Krita → Settings → Manage Resources → **Open Resource Folder**.
2. Copy `hic_et_nunc_capture.desktop` and the `hic_et_nunc_capture/` folder into its `pykrita/` subfolder.
3. Settings → Configure Krita → **Python Plugin Manager** → enable "Hic et nunc Capture (spike)". Restart Krita.
4. The actions appear under Tools → Scripts: **Hic et nunc: start/stop capture** and **Hic et nunc: capability report**.

## Protocol (~30 minutes, use the pen if you have one)

**Session A, tracing.** New document. Add an image you didn't make with the
**Reference Images tool** (not as a layer, see limitations). Start capture,
trace its main lines on a paint layer for ~10 minutes, add some colour, stop.

**Session B, free drawing.** New document, start capture, draw freely for
~10 minutes, stop.

## Protocol: pastes, transforms and brush size (2026-11-15, ~25 minutes)

Since 2026-11-15 the plugin also logs every Krita action triggered (menu
or shortcut: paste, import, undo, filters...) with the active tool and
layer, the tool, layer and brush size with each stroke, and each layer's
bounds. These two sessions check that real Krita delivers them and give
the data to report pastes from them.

**Before you start.** Copy the updated `hic_et_nunc_capture/` folder into
`pykrita/` again and restart Krita. Write down your setup: tablet model,
how it's connected (USB cable, Bluetooth, other), whether the Mac is on
its charger, and whether any of that differed between the 14 Nov evening
and the 15 Nov morning sessions (pen samples dropped from ~190/s to
~65/s between them; see Results).

Note the clock time (HH:MM:SS) of each numbered step below; the session
folder name is the start time.

**Session N, no pastes (control, ~10 min).** New A4 document (2480 x
3508, as before). Start capture.

1. Draw freely ~3 min with a brush at its default size.
2. Change the brush size a lot (e.g. 5 px, then 150 px; note both) and
   draw ~2 min with each.
3. With the **Fill tool** (click its toolbox button, don't use a shortcut),
   click once inside a closed shape you drew.
4. Press Cmd+Z once (undo the fill), then Cmd+Shift+Z (redo).
5. Draw ~1 min more. Stop capture.

Expected: strokes carry `tool` (`KritaShape/KisToolBrush`, then
`KritaFill/KisToolFill` for step 3) and the brush sizes you set;
`action` events `edit_undo`, `edit_redo` at step 4; nothing reported as
a paste.

**Session P, pastes (~12 min).** New A4 document. Copy an image you
didn't make (e.g. the Matisse print) in a browser or Preview. Start
capture.

1. Draw ~2 min on Paint Layer 1.
2. Cmd+V. Krita adds a new layer with the image. Make it small if it
   isn't (Transform tool, under ~2% of the canvas, Enter).
3. Wait ~15 s (three snapshots), then **click the Transform tool button**
   in the toolbox, scale the pasted image up to about a quarter of the
   canvas, press Enter.
4. Draw ~2 min on Paint Layer 1, partly over the image.
5. Click Paint Layer 1 in the Layers docker. Choose **Paste into Active
   Layer** from the Edit menu (it may sit in a paste submenu). Note where
   it landed and roughly how big it is.
6. Wait ~15 s, then with the **Move tool** (toolbox button) drag the
   content of Paint Layer 1 a little.
7. Draw ~2 min. Stop capture. Save as `paste 2.kra`.

Expected: `action` events `edit_paste` at step 2 and `paste_into` at
step 5, each with the active layer; transform strokes at step 3 with
`tool` `KisToolTransform` on the pasted layer, whose `bounds` in the next
`layers` event grow to ~25% of the canvas; move strokes at step 6 with
`KritaTransform/KisToolMove`.

**Send back:** the two session folders (events, commitments,
capabilities.json, snapshots), `paste 2.kra`, your setup notes and the
step times. If anything didn't behave as above (an action you can't
find, a paste that went to an unexpected layer), note what happened.

## Analyze

```bash
pip install -e ".[dev]"
hic-et-nunc inspect ~/hic-et-nunc-capture/<A>
hic-et-nunc trace   ~/hic-et-nunc-capture/<A> --reference path/to/A.kra
hic-et-nunc trace   ~/hic-et-nunc-capture/<B> --reference path/to/A.kra
```

Save session A as `.kra` and pass that: it holds the reference image and
where Krita showed it, including any move, scale or rotation (applied from
the reference's saved transform). A plain image file is stretched to the
document's shape, which misplaces it unless the aspect ratios match.

## What to look at

| Check | Where | Good result |
|---|---|---|
| Pen events reach the canvas | `capabilities.json` → `event_counts_by_widget` has `tablet:Kis…Canvas…` | non-zero |
| Sampling rate | `inspect` → `median_points_per_sec` | ~100-200 with a pen |
| Coordinates mappable | `inspect` → `view_transforms_recorded` | `true` |
| No lag while painting | your impression; `flush_ms_max` | unnoticeable; < 20 ms |
| Errors | `filter_errors`, `last_error` | 0 |
| **Tracing separates** | `trace` ratios, both methods | A > 3, B < 2 |

The last row is the kill criterion: if tracing doesn't separate, the STRONG
tier doesn't work as designed. The `inspect`/`trace` output and
`capabilities.json` contain no images and are enough to judge it.

## Results (2026-11-14)

Krita 5.3.4.1, PyQt5, macOS 15.7 on a Retina display, pen input. All
three sessions: log and commitments verify, 0 filter errors, ~185-195
points/s, flush at most 12 ms, view transforms recorded and correct
(Retina handled by Krita's own transforms; checked against the pixels each
stroke changed).

| Session | What was drawn | vs its reference: snapshots / strokes |
|---|---|---|
| Matisse, 9 min, 61 strokes | line tracing of a print | **10.38 / 13.63** |
| free, 16 min, 149 strokes | free drawing | 1.12 / 0.77 (vs the Matisse reference) |
| A, 33 min, 227 strokes | outlines plus mostly hatching fills over a photo | 2.09 / 1.37 |
| Matisse 2 (2026-11-15), 19 min, 90 strokes | line tracing, reference moved | **7.99 / 10.02** |
| rotated Matisse (2026-11-15), 12 min, 37 strokes | line tracing, reference half size, rotated 28° | **18.05 / 20.74** |

These ratios use the whole-canvas baseline (see below for the current one).

**Line tracing separates clearly; the kill criterion passes for it.**
Tracing that is mostly fill doesn't: hatching inside dark areas keeps
points away from edges, and only 18% of A's strokes score above 3 (free
drawing: 8-15%). Whole-session ratios only catch tracing of outlines.

**Paste session (2026-11-15, 10 min, 50 strokes).** An image pasted with
Cmd+V as a new layer at 2.85 min, scaled up with the transform tool at
~4.6 min, painted over. The snapshot detector (`paste_events`) misses it:
the paste changed 0.46% of the canvas, its minimum is 15%. Snapshot
timing, edge threshold and layer coverage were fine (snapshot taken at the
paste, edge density 0.28 vs 0.04 needed, snapshots render all visible
layers). `trace` then also reported `layer_pastes` (now part of `pastes`,
see "Pastes from Krita actions" below): a new layer whose
arrival changed the canvas where no stroke was being drawn. It reports one
paste at 2.85 min (0.0046 of the document, bbox in document px) and none
in the five earlier sessions.

The Matisse, free and paste logs are test data (`tests/test_krita_sessions.py`);
session A's isn't, because it traces a photo of two people.

**Pen sampling rate (checked 2026-11-15).** The 14 Nov sessions record
183-194 points/s, the 15 Nov ones 63-69. The plugin isn't losing them:
its code, Krita (5.3.4.1), macOS (15.7.3) and the document size are the
same in all six, with 0 filter errors, and every pen event that reaches
the canvas is kept. What changed is the spacing the events arrive with:
intervals are 4-5 ms on 14 Nov and a regular 14-16 ms on 15 Nov, a lower
reporting rate from the tablet or the OS, not random gaps. The tablet is
an XP-Pen Deco MW; the 15 Nov afternoon sessions (66-73 points/s) were on
its USB cable, so Bluetooth isn't the cause. Still unexplained; the logs
don't record the driver's settings. Separately, on both days gaps of 40 ms
or more cluster near the 5 s snapshot tick (2.5-5x the other half
seconds in all six sessions): snapshots run on Krita's GUI thread
and samples arriving meanwhile get coalesced. Gaps of 30 ms or more are
0.4-0.6% of intervals on 14 Nov, 2.0-3.0% on 15 Nov. `capabilities.json`
now reports `snapshot_ms_max` / `snapshot_ms_median` to measure it.

**Tracing baseline (2026-11-15).** The whole-canvas baseline assumed marks
spread evenly over the canvas. Drawings cluster, so a small reference over
the busy part of a drawing scored above 1 without being traced: free
drawing vs the rotated Matisse reference 1.74 / 1.51. Scoring against 200
random placements of the reference (rotated, moved anywhere on the canvas)
didn't fix it: 1.62 / 1.24 (p 0.015 / 0.045), because the free drawing has
1.70x / 1.24x more marks under that reference than under an average
placement; a synthetic drawing confined around a small reference scored
10.5. The ratio is now taken inside a band around the reference's edges
(3.65% of the canvas's longer side: 128 document px, 37 snapshot px):
of the marks in the band, the share within 2 px of an edge, against the
same share of the band's area. The placements give a p-value for it
(floor 0.005).

| Session | Reference | whole canvas | band | p |
|---|---|---|---|---|
| Matisse (traced) | its own | 10.38 / 13.63 | **3.84 / 6.07** | 0.005 / 0.005 |
| Matisse 2 (traced) | its own | 7.99 / 10.02 | **5.08 / 6.60** | 0.005 / 0.005 |
| rotated Matisse (traced) | its own | 18.05 / 20.74 | **4.03 / 4.61** | 0.005 / 0.005 |
| A (outlines + fills) | its own | 2.09 / 1.37 | 1.93 / 1.25 | 0.005 / 0.005 |
| free | Matisse | 1.12 / 0.77 | 1.12 / 0.99 | 0.10 / 0.71 |
| free | Matisse 2 | 0.86 / 0.85 | 0.95 / 1.02 | 0.74 / 0.51 |
| free | rotated Matisse | 1.74 / 1.51 | 1.03 / 1.01 | 0.53 / 0.51 |
| free | A's photo | 0.76 / 1.37 | 0.78 / 1.17 | 0.98 / 0.025 |
| paste | Matisse | 1.19 / 1.07 | 0.96 / 0.81 | 0.73 / 0.89 |
| paste | Matisse 2 | 1.44 / 1.38 | 1.12 / 1.04 | 0.28 / 0.40 |
| paste | rotated Matisse | 0.54 / 1.14 | 0.71 / 0.91 | 0.92 / 0.78 |
| paste | A's photo | 1.44 / 1.02 | 1.33 / 0.89 | 0.35 / 0.65 |

Snapshots / strokes. Free drawing is ~1 against every reference; traced
sessions stay above 3, with a smaller gap than before (the lowest is
Matisse from snapshots, 3.84, whose cap 1 / baseline is 5.6). Layer pastes
are unchanged: one in the paste session, none in the others.

**Default limit 2.5** (`policy.TRACING_RATIO_LIMIT`, a parameter). Evidence,
six sessions, both methods: free and paste sessions vs every reference at
most 1.33; any drawing vs a reference it didn't trace (tracings against
the other references included) at most 1.42; line tracings vs their own
reference at least 3.84. Fill-heavy A (1.93 / 1.25) stays below it. The
lowest cap (1 / baseline) on these sessions is 5.6, so none reports
"can't tell at this resolution".

**Reviewer report (2026-11-15).** `hic-et-nunc report SESSION --reference
X.kra -o report.html` on the six sessions, no delivered image:

| Session | Reference | Result | Why |
|---|---|---|---|
| Matisse (traced) | its own | needs review | tracing 3.84 / 6.07 |
| Matisse 2 (traced) | its own | needs review | tracing 5.08 / 6.60 |
| rotated Matisse (traced) | its own | needs review | tracing 4.03 / 4.61 |
| A (outlines + fills) | its own | **consistent** | tracing 1.93 / 1.25, below 2.5 |
| free | rotated Matisse | consistent | 1.03 / 1.01 |
| paste | Matisse | needs review | one paste, minute 2.8, 0.5% of the canvas |

The ratios are the band ratios above, unchanged. A is the known miss: a
reviewer gets "consistent" for a fill-heavy tracing, which the report's
"what this can't tell you" section states. With each `.kra`'s
`mergedimage.png` as the delivered image, Matisse and rotated Matisse
match their last snapshot (similarity 0.9999); the Matisse image against
the free session gives "inconsistent" (0.52).

**Pastes from Krita actions (2026-11-15).** Since the plugin logs actions,
tools, brush sizes and layer bounds (protocol above), `pastes` reports
each paste or import action (`edit_paste`, `paste_at`, `paste_into`,
`paste_layer_from_clipboard`, `import_layer_*`), measured between the
snapshots around it, plus any new layer that arrives holding content no
stroke drew (drag and drop; older logs). Each later change of the
receiving layer's bounds while nothing is being painted on it is a
transform: the final area is the area on arrival times their bounds-area
ratios. `trace` and `report` use it; the report no longer repeats a
transform of a reported paste as a one-step paste.

Two new sessions (Krita 5.3.4.1, macOS 15.7.3, pen; 73 and 66 points/s,
like the other 15 Nov sessions):

| Session | What happened | Reported |
|---|---|---|
| no paste, 10 min, 270 strokes | brush sizes 5, 40, 401 px; two Fill clicks; 2x undo, 2x redo | nothing |
| paste 2, 9 min, 39 strokes | Cmd+V at 2.42 min (new layer), shrunk then scaled up to ~quarter canvas with the Transform tool, painted on; Paste into Active Layer on Paint Layer 1 at 5.24, moved with the Move tool at 6.43 | Paste 0.0602 on arrival, **final 0.1219** (six bounds changes, 2.67-3.59 min); Paste into Active Layer **0.0596**, moved at 6.43 |

| paste small, 4 min, 7 strokes | a 256x256 image pasted at 0.97 min, moved, then scaled up to ~quarter canvas with the Transform tool | Paste 0.0061 on arrival, **final 0.2636** |

While the Transform tool is active Krita reports the layer's bounds as
empty (paste small, 2.5 min); those readings are skipped, otherwise the
final area comes out 0.0. The painting on the pasted layer afterwards
(paste 2) grows its bounds 1.30x more;
it's not counted, because strokes were in progress on that layer. The
transform drags themselves weren't recorded as strokes (mouse or trackpad
input is dropped once a pen has been seen); the tool-switch actions and the
bounds carry them.

Brush size barely matters: on brush-only snapshot intervals the change no
stroke path explains is at most 0.0000 (no paste) and 0.0001 (paste 2),
with 20 px around every stroke or with half the brush size. The large
unexplained changes come from other operations: Fill click 0.104 (0.072
with brush size), undo/redo 0.052, transforms and moves 0.06-0.19. The
0.046 seen before on older logs (0.040 in Matisse 2, 0.038 in free) is
likely the same, but those logs have no tools to tell. Brush size is used
when measuring a paste's area (synthetic: a 60 px brush stroke drawn
alongside a paste adds 0.028 to it without it). There's no scan for
unexplained change outside paste actions and new layers: no session needs
it, and Fill and undo would have to be excluded by tool.

Earlier results unchanged (no tracing code changed): line tracings
3.84-6.60, free and paste sessions vs every reference at most 1.33, one
paste in the paste session, none in the five others. The new sessions vs
the four references: no paste 0.67-1.41, paste 2 0.18-1.39 (snapshots /
strokes; the lowest p-value, no paste vs Matisse 2 from strokes, is 0.010
at ratio 1.41, below the 2.5 limit).

## Delivered image and recording checks (2026-11-15)

Question: does "inconsistent" ever hit an honest delivery? Measured
without changing any code. Each session's honest export (the `.kra`'s
merged image; for no paste the exported PNG, because its `.kra` was
saved after more work and scores 0.66), edited the way a delivery
might be, written to a temp dir outside the repo and read back as the
CLI reads `--delivered`. Similarity is the report's: grayscale SSIM of
the last snapshot (723x1024) against the delivered image stretched to
it; below 0.9 the report says "inconsistent", otherwise "matches".

| Variant | A | free | Matisse | Matisse 2 | rotated | paste | no paste | paste 2 | paste small |
|---|---|---|---|---|---|---|---|---|---|
| exported file as is | 0.9991 | 0.9956 | 0.9998 | 0.9993 | 0.9999 | 0.9994 | – | 0.9989 | – |
| crop 1% per side | **0.7647** | **0.5627** | **0.8574** | **0.8953** | 0.9557 | **0.8664** | **0.6720** | **0.8125** | **0.5560** |
| crop 5% per side | **0.5465** | **0.4367** | **0.8192** | **0.8163** | 0.9357 | **0.8230** | **0.5698** | **0.7337** | **0.4687** |
| 2048 px wide | 0.9994 | 0.9980 | 0.9999 | 0.9995 | 1.0000 | 0.9995 | 0.9986 | 0.9988 | 0.9985 |
| 1080 px wide | 0.9983 | 0.9978 | 0.9996 | 0.9995 | 0.9998 | 0.9994 | 0.9983 | 0.9989 | 0.9981 |
| JPEG 85 | 0.9991 | 0.9960 | 0.9998 | 0.9994 | 0.9999 | 0.9980 | 0.9970 | 0.9978 | 0.9974 |
| JPEG 70 | 0.9508 | 0.9917 | 0.9998 | 0.9992 | 0.9998 | 0.9968 | 0.9964 | 0.9969 | 0.9969 |
| sRGB -> Display P3 | 0.9991 | 0.9973 | 0.9999 | 0.9994 | 0.9999 | 0.9986 | 0.9978 | 0.9965 | 0.9960 |
| sRGB -> CMYK -> sRGB | **0.6402** | 0.9577 | 0.9998 | 0.9988 | 0.9996 | 0.9973 | 0.9949 | 0.9962 | 0.9928 |
| logo 1% (opaque badge) | 0.9890 | 0.9876 | 0.9898 | 0.9893 | 0.9898 | 0.9893 | 0.9878 | 0.9888 | 0.9879 |
| logo 3% | 0.9703 | 0.9695 | 0.9732 | 0.9728 | 0.9733 | 0.9728 | 0.9688 | 0.9722 | 0.9709 |
| signature 1% (ink scribble) | 0.9941 | 0.9931 | 0.9948 | 0.9944 | 0.9949 | 0.9944 | 0.9940 | 0.9938 | 0.9930 |
| signature 3% | 0.9885 | 0.9881 | 0.9888 | 0.9884 | 0.9889 | 0.9884 | 0.9887 | 0.9878 | 0.9872 |
| 2048 + JPEG 85 + logo 1% | 0.9890 | 0.9853 | 0.9897 | 0.9891 | 0.9897 | 0.9869 | 0.9865 | 0.9872 | 0.9874 |
| *negative:* 5% replaced | 0.9549 | 0.9475 | 0.9492 | 0.9490 | 0.9493 | 0.9500 | 0.9484 | 0.9491 | 0.9472 |
| *negative:* 20% replaced | 0.8317 | 0.8166 | 0.8229 | 0.8217 | 0.8314 | 0.8220 | 0.8159 | 0.8243 | 0.8025 |

Bold: an honest delivery reported "inconsistent". Flatten onto white
doesn't apply: every merged image and export is fully opaque. The
Matisse image delivered for the free session: 0.5164, "inconsistent".
Replaced: a square of 5% / 20% of the image, centred on the drawing,
taken from another session's export.

Time-lapse path (WEAK tier): each session's snapshots written as an
MP4, read by `timelapse_report` with the honest export delivered.
Filling the frame: 0.9730-0.9980, "matches". Letterboxed (the portrait
canvas in a 1080x1920 frame, black bars top and bottom): 0.3485-0.7364;
pillarboxed (in 1920x1080): 0.1448-0.3514. All 18 "inconsistent".

**Result: fails the "Done when".** Honest deliveries reported
"inconsistent": 35 of 151 (crop 1% 8/9, crop 5% 8/9, CMYK 1/9, letter-
or pillarboxed time-lapse 18/18). Negatives reported "matches": 9 of 19
(5% replaced, 0.9472-0.9549, all nine). Causes: the comparison
stretches the delivered image to the canvas, so a crop of 1% per side
misaligns line work by ~7 px at 723 px; SSIM's luminance term collapses
near black, and A is 44% near-black (gray < 40), which the CMYK round
trip lifts by ~12 levels (luminance factor ~0.04 there); a 5% patch
moves a whole-image mean by ~0.05.

Measured options (not applied; Andrea decides):

| Option | Honest "inconsistent" | Honest "needs review" | Negatives "matches" |
|---|---|---|---|
| now | 35/151 | 0 | 9/19 (5% replaced) |
| 1. align first: SIFT + RANSAC similarity transform from delivered to the last state, SSIM over the overlap only; current fallback when it doesn't align | 1/151 (A via CMYK, 0.6352) | 0 | 9/19 (5% replaced, 0.9291-0.9488) |
| 2. option 1 + local check: 32 px blocks (at 1024 px) with SSIM < 0.6; their share of the overlap > X -> needs review, with the region shown | 1/151 | X = 0: 46/151 (all logos and signatures, A via CMYK); X = 2%: 10/151 (logo 3%, A via CMYK); X = 4%: 0 | 0/19 for every X tried (0, 0.5, 1, 2, 4%) |
| 3. option 2 (X = 0) + an aligned image below 0.9 gives "needs review" instead of "inconsistent"; "inconsistent" stays for images that don't align | 0/151 | 47/151 | 0/19 (17 review, 2 inconsistent) |

Option 1 aligns every crop (min 0.9770) and letterbox (min 0.9685);
honest exports drop slightly (0.9795-0.9993 vs 0.9956-0.9999) because
the warp resamples. Changed-block share, option 2: 0.0% on every honest
variant except logos and signatures (logo 1% 1.1%, logo 3% 3.9%,
signature 1% 0.4-0.6%, signature 3% 1.2-1.6%) and A via CMYK (39.4%);
negatives 5% replaced 4.6-6.1%, 20% 18.5-21.8%, the Matisse image
58.7%. A logo is replaced content, so area is the only thing telling
them apart: X = 4% passes every logo and catches every 5% patch here,
but a 4% patch would pass. In option 3 the two remaining
"inconsistent" negatives are the ones that don't align (the Matisse
image; rotated with 20% replaced, whose patch covers its drawing).
Tried and rejected for the CMYK case: histogram matching before SSIM
(raised rotated 20% replaced from 0.83 to 0.96), a least-squares
gain/offset (an opaque logo skews it: A logo 3% 0.66), a p5/p95 gain
(mostly-white line art has p5 = p95: honest rotated export 5.4%
changed).

**Option 3 applied (2026-11-15, Andrea's choice).** `analysis.delivery_check`
(`max_changed` 0 in `Policy`), used by `report` and `evaluate`. The same
151 honest and 19 altered cases, re-run through the report's code: honest
0 "inconsistent", 47 "needs review" (all 45 logo, signature and
2048 + JPEG 85 + logo variants at 0.4-3.9% changed; A via CMYK, 0.6352,
39.4%; free via CMYK, 2.4%), 104 "consistent"; every honest case
aligned. Altered: 0 "matches", 17 "needs review" (5% replaced 4.6-6.2%
changed, 20% replaced 18.7-21.8%), 2 "inconsistent" (the Matisse image
for free, 0.522; rotated with 20% replaced, 0.8334: neither aligned).
Time-lapses filling the frame: 9/9 "consistent". The full suite with
`HIC_ET_NUNC_KRITA_DATA` set: 116 passed.

**Recording verification** (copies of free and paste 2, report run
without reference or delivered image):

| Case | free | paste 2 |
|---|---|---|
| copy as is | consistent | needs review (its two pastes) |
| crash: log cut mid-line (free at entry 175, commitments up to 165; paste 2 at 92, up to 81) | **error: `JSONDecodeError` traceback, exit 1, no report** | same |
| crash: log cut at a line end, same points | consistent, "checks out" | needs review (one paste), "checks out" |
| last commitment missing (241 of 242, 137 of 138 covered) | consistent, "checks out" | needs review, "checks out" |
| one entry edited (a stroke, middle) | inconsistent (hash mismatch) | inconsistent |
| edited, chain rehashed | inconsistent (commitments fail) | inconsistent |
| edited, chain rehashed, commitments recomputed | consistent | needs review |
| one entry deleted | inconsistent (sequence gap) | inconsistent |
| deleted, chain renumbered and rehashed | inconsistent (commitments fail) | inconsistent |
| crash cut, then an uncommitted entry edited or deleted and rehashed | consistent | needs review |

Edits and deletions in committed entries always fail, rehashed or
not. A crash that tears the last line crashes `hic-et-nunc report` before
any verification; skipping the torn line (measured only), the rest
verifies (174 and 91 entries, 9 and 10 after the last commitment). A
crash at a line end, or a missing last commitment, passes, and the page
still says no event was changed "after the checkpoints were written",
without saying how many events no checkpoint covers. Those entries (up
to 60 s, `CHECKPOINT_EVERY_MS`) can be edited or deleted undetected, as
can everything if the commitments are recomputed (they're local; known
limit). So crash vs tampering isn't told apart yet: the torn crash is an
error, the clean crash and tail tampering look the same as an intact
log.

**Cut-off last line handled (2026-11-15).** `eventlog.read_log` leaves
out a last line with no newline that isn't valid JSON, and the report
says so; any other unreadable line still raises. The two cut copies
above, through `hic-et-nunc report`: free "consistent", 174 events and 7
checkpoints check out; paste 2 "needs review" (its first paste), 91
events and 4 checkpoints; both exit 0. The full suite with
`HIC_ET_NUNC_KRITA_DATA` set: 118 passed.

**No clean close gives "needs review" (2026-11-16, D-037, D-038).** A
log whose chain and checkpoints verify but whose last event isn't
`session_stop` now gets "needs review", its first finding starting
"Chain intact; ended without clean close: crash or removal of final
entries". All 12 local sessions and the 3 committed logs end in
`session_stop`. Reports with each session's test reference, before /
after:

| Log | Events | Last event | Before | After |
|---|---|---|---|---|
| 7 full sessions (Matisse, Matisse 2, rotated tracing, free, paste, no paste, paste 2) | 136-409 | session_stop | 5 needs review, 2 consistent; "checks out" | unchanged |
| free, crash-cut mid-line 175 | 174 | stroke | consistent, 0 review findings | needs review, 1 |
| paste 2, crash-cut mid-line 92 | 91 | stroke | needs review, 1 | needs review, 2 |
| free, final entry removed (to the checkpoint before the stop) | 241 | snapshot | consistent, 0 | needs review, 1 |
| paste 2, final entry removed (same) | 137 | snapshot | needs review, 2 | needs review, 3 |

`evaluate` adds the same note to its tier notes; its verdict (from the
policy) is unchanged. Full suite with `HIC_ET_NUNC_KRITA_DATA` set: 120
passed.

**One piece over two sessions** (simulated, nothing changed): the
plugin starts a new folder at each start and nothing links it to the
previous one; `report` takes one session. Splitting real logs where a
restart would (session 2 = a new chain starting with the canvas and
layers as they were, commitments every 60 s), with each piece's final
export delivered:

| Piece | Report on | Result | Similarity | First snapshot holds of the final marks |
|---|---|---|---|---|
| free | whole recording | consistent | 0.9979 | 0% |
| free | session 1 (to minute 8) | **inconsistent** | 0.7676 | 0% |
| free | session 2 | consistent | 0.9979 | 88% |
| Matisse 2 | whole | needs review (tracing 5.08 / 6.60) | 0.9994 | 0% |
| Matisse 2 | session 1 (to minute 9.5) | inconsistent (+ tracing 4.86 / 4.96) | 0.8875 | 0% |
| Matisse 2 | session 2 | needs review (tracing 5.11 / 8.75) | 0.9994 | 12% |
| paste 2 | whole | needs review (two pastes) | 0.9989 | 0% |
| paste 2 | session 1 (to minute 4) | needs review (first paste) | 0.9072 | 0% |
| paste 2 | session 2 | needs review (**second paste only**) | 0.9989 | 66% |

An honest piece gets "inconsistent" on every session but the last.
The last session alone "matches" and doesn't say the canvas already
held 66-88% of the final marks when it started, nor what happened
before: paste 2's first paste disappears. The same holds for starting
capture after pasting.

**Multi-session options (2026-11-17, nothing changed in `src/`).** The
split above, rebuilt with a throwaway script outside the repo: each
part rechained from genesis with commitments every 60 s and a clean
close; session 2 opens with the last snapshot and `layers` record
before the cut. Delivered: the `.kra` merged image for Matisse 2 and
paste 2. Free's only `.kra` was saved at 20:11, 6 min before capture
stopped (20:16:50), so its delivered image is the last snapshot scaled
to 2480x3508; that's why free's session 1 now gets "needs review" (it
aligns, 0.7688) where the table above has "inconsistent" (0.7676; the
export used then is not recorded). Canvas at start: share of the final
snapshot's marks (more than 30 grey levels from the paper colour, the
mode grey) already marked in the first snapshot: free 84.6%, Matisse 2
11.1%, paste 2 65.7% (above: 88 / 12 / 66%; the old measure isn't
recorded). All 12 real whole recordings: 0.0%.

Options, all thresholds as today (`min_consistency` 0.9, `max_changed`
0, tracing 2.5):

- **A. Earlier sessions skip the delivery check.** The illustrator
  says a session isn't the last; it is reported without the delivered
  image.
- **B. Non-blank start.** A session whose canvas at start already
  holds more than X of its final marks gets "needs review", with the
  share stated. X = 1% here (honest whole recordings 0.0%, split
  session 2 at least 11.1%).
- **C. Chain.** `report` takes the session folders in order. Each
  session's first snapshot is compared with the previous session's
  last using the delivery check's rules (D-015: "inconsistent" if it
  doesn't align and similarity < 0.9; "needs review" if < 0.9 or any
  block changed). Only the last session is compared with the
  delivered image; the result is the worst of all parts and links.
  Links are by picture content only: no new log record (D-024).

| Piece | Variant | Today | A | B | C | C link: similarity / changed |
|---|---|---|---|---|---|---|
| free | whole (control) | consistent | consistent | consistent | consistent | – |
| free | honest session 1 | needs review (0.7688, 22.5% changed) | consistent | needs review | in chain | – |
| free | honest session 2 | consistent | consistent | needs review (84.6%) | **consistent** | 1.0000 / 0% |
| free | NEG canvas edited between sessions, 5% | needs review | needs review | needs review | needs review | 0.9728 / 2.9% |
| free | NEG same, 1% | **consistent** | **consistent** | needs review | needs review | 0.9938 / 0.6% |
| free | NEG unrelated session 1 (Matisse 2) in front | – | – | – | inconsistent | 0.7206 / 29.5% |
| free | NEG session 1 + another piece's image | inconsistent | **consistent** | inconsistent | inconsistent (last session) | – |
| Matisse 2 | whole (control) | needs review (tracing) | needs review | needs review | needs review | – |
| Matisse 2 | honest session 1 | **inconsistent** (0.8893) | needs review (tracing) | **inconsistent** | in chain | – |
| Matisse 2 | honest session 2 | needs review (tracing) | needs review | needs review (11.1%) | needs review (tracing) | 1.0000 / 0% |
| Matisse 2 | NEG canvas edited between sessions, 5% | needs review | needs review | needs review | needs review | 0.9872 / 1.5% |
| Matisse 2 | NEG same, 1% | needs review | needs review | needs review | needs review | 0.9968 / 0.4% |
| Matisse 2 | NEG unrelated session 1 (paste 2) in front | – | – | – | inconsistent | 0.8320 / 19.4% |
| Matisse 2 | NEG session 1 + another piece's image | inconsistent | needs review | inconsistent | inconsistent (last session) | – |
| paste 2 | whole (control) | needs review (2 pastes) | needs review | needs review | needs review | – |
| paste 2 | honest session 1 | needs review (9.6% changed) | needs review (1st paste) | needs review | in chain | – |
| paste 2 | honest session 2 | needs review (**2nd paste only**) | needs review | needs review (65.7%) | needs review (both pastes) | 1.0000 / 0% |
| paste 2 | NEG canvas edited between sessions, 5% | needs review | needs review | needs review | needs review | 0.9837 / 1.7% |
| paste 2 | NEG same, 1% | needs review | needs review | needs review | needs review | 0.9962 / 0.4% |
| paste 2 | NEG unrelated session 1 (free) in front | – | – | – | inconsistent | 0.6852 / 34.0% |
| paste 2 | NEG session 1 + another piece's image | inconsistent | needs review (1st paste) | inconsistent | inconsistent (last session) | – |

"Canvas edited between sessions": a square of an outside image
(`ai_pasted_image .png`) pasted on every session 2 snapshot and on the
delivered image, as if added in another app before capture restarted.
Matisse 2 and paste 2 get "needs review" for tracing or pastes
anyway, so only free tests whether an option catches it.

| | Today | A | B | C |
|---|---|---|---|---|
| Honest parts "inconsistent" (item 3a: 0) | 1 of 6 | 0 of 6 | 1 of 6 | 0 of 3 chains |
| Negatives "consistent" (target 0) | 1 of 9 (free, edit 1%) | 2 of 9 (free, edit 1%; free session 1 with another image) | 0 of 9 | 0 of 12 |
| Last session hides the earlier work (first paste, non-blank start) | yes, 3 of 3 | yes | stated, not shown | no: both pastes reported |
| Cost | – | a user-declared "not last" setting; it turns off the delivery check, which is the abuse in the table | one new parameter X; an honest session started on an imported sketch or template would get "needs review" (no such session recorded) | `report` takes several folders; links reuse `delivery_check`; F-001 uploads several folders per piece (still deleted after analysis, D-017) |

Only C meets item 3a on these pieces: 0 honest "inconsistent", 0
negatives "consistent". B can be added to C for a chain whose first
session starts non-blank (capture started after pasting, above); A
fails trust first. Limits: three pieces, all split from single real
recordings (no real two-session recording exists, all one person, one
tablet); edits between sessions below 0.4% changed weren't tried.

**C + B built (2026-11-17, D-041, D-044).** `hic-et-nunc report S1 S2 ...`
(`report.piece_report`); B in `session_report`, limit
`Policy.max_marked_at_start` 1%, on the first session only, also when
it is the only one. The split pieces and negatives rebuilt with a
throwaway script outside the repo, as above (cuts: free minute 8,
Matisse 2 minute 9.5, paste 2 minute 4; edits: a square of
`ai_pasted_image .png` at the centre). References as in
`test_real_sessions_report` (free: rotated Matisse; Matisse 2: its own;
paste 2: Matisse). "Another piece's image": free gets Matisse 2's,
Matisse 2 gets paste 2's, paste 2 gets free's.

| Piece | Chain | Result | Link: similarity / changed | Delivered: similarity / changed |
|---|---|---|---|---|
| free | honest | consistent | 1.0000 / 0.0% | 0.9766 / 0.0% |
| free | NEG edit 5% | needs review | 0.9749 / 2.5% | 0.9697 / 0.4% |
| free | NEG edit 1% | needs review | 0.9937 / 0.4% | 0.9748 / 0.3% |
| free | NEG unrelated session 1 in front | inconsistent | 0.7206 / 29.5% | 0.9766 / 0.0% |
| free | NEG another piece's image | inconsistent | 1.0000 / 0.0% | 0.5036 / 62.7% |
| Matisse 2 | honest | needs review (tracing, both sessions) | 1.0000 / 0.0% | 0.9967 / 0.0% |
| Matisse 2 | NEG edit 5% | needs review | 0.9824 / 2.2% | 0.9907 / 0.4% |
| Matisse 2 | NEG edit 1% | needs review | 0.9964 / 0.3% | 0.9951 / 0.3% |
| Matisse 2 | NEG unrelated session 1 in front | inconsistent | 0.8320 / 19.4% | 0.9967 / 0.0% |
| Matisse 2 | NEG another piece's image | inconsistent | 1.0000 / 0.0% | 0.7281 / 34.3% |
| paste 2 | honest | needs review (both pastes) | 1.0000 / 0.0% | 0.9928 / 0.0% |
| paste 2 | NEG edit 5% | needs review | 0.9831 / 1.8% | 0.9871 / 0.7% |
| paste 2 | NEG edit 1% | needs review | 0.9960 / 0.4% | 0.9912 / 0.3% |
| paste 2 | NEG unrelated session 1 in front | inconsistent | 0.6852 / 34.0% | 0.9928 / 0.0% |
| paste 2 | NEG another piece's image | inconsistent | 1.0000 / 0.0% | 0.5050 / 60.2% |

Honest chains "inconsistent": 0 of 3 (target 0). Negatives
"consistent": 0 of 12 (target 0; free edit 1% was "consistent" one
session at a time). Session 2 reported alone now gets "needs review"
for B: free 79.1%, Matisse 2 11.2%, paste 2 65.7% of the final marks at
the start. Free measured 84.6% with the earlier throwaway script;
why it differs is unknown (that script isn't kept). Every chain's
session 1 started at 0.0%.

The 12 whole recordings with the same references, before / after: same
result and same number of review findings on 12 of 12 (6 consistent, 6
needs review); "Final drawing already on the canvas at the start" 0.0%
on all 12. `pytest` with `HIC_ET_NUNC_KRITA_DATA`: 123 passed (was 121);
without: 75 passed, 48 skipped (was 73 / 48). The synthetic fake
session now starts with a blank snapshot, as the plugin does.

## Multi-session attack review (2026-11-17)

Attacks on `piece_report` (C + B, D-041, D-044), nothing changed in
`src/`. The pieces were rebuilt with the earlier throwaway build script
(same cuts, same rechaining) plus a throwaway attack script, both outside
the repo. References and delivered images are as in "C + B built".
Thresholds are today's: `min_consistency` 0.9, `max_changed` 0, link
and delivery blocks 32 px with block SSIM below 0.6.

**Gate.** The honest 2-part chains reproduce "C + B built" exactly:
links 1.0000 / 0.0%, delivered 0.9766 / 0.9967 / 0.9928, results
consistent / needs review / needs review.

**Attacks.**

- (a) A square of `ai_pasted_image .png` at the centre of every session 2
  snapshot and of the delivered image, covering 0.05%, 0.1%, 0.2% and
  0.3% of the canvas. That is a 19, 27, 38 and 47 px side on the
  1024×723 snapshot; one 32 px block is 0.14%.
- (b) The sessions given in the wrong order: s2, s1.
- (c) Each piece cut in three (free at 5.4 and 10.9 min, Matisse 2 at
  6.4 and 12.8, paste 2 at 2.0 and 4.0, so paste 2's middle session
  holds its first paste at 2.42 min), reported as t1, t3. Control: the
  honest t1, t2, t3.
- (d) The same session given twice: s1, s1, s2 and s1, s2, s2.

Link: similarity / share changed for each link, in order. Delivered:
similarity / share changed of the last session against the delivered
image.

| Piece | Run | Result | Link(s) | Delivered |
|---|---|---|---|---|
| free | (a) edit 0.05% | **consistent** | 0.9992 / 0.0% | 0.9764 / 0.0% |
| free | (a) edit 0.1% | **consistent** | 0.9988 / 0.0% | 0.9763 / 0.0% |
| free | (a) edit 0.2% | needs review | 0.9981 / 0.1% | 0.9760 / 0.0% |
| free | (a) edit 0.3% | needs review | 0.9975 / 0.1% | 0.9758 / 0.0% |
| free | (b) s2, s1 | inconsistent | 0.5776 / 50.4% | 0.7688 / 22.3% |
| free | (c) control t1, t2, t3 | consistent | 1.0000 / 0.0%; 1.0000 / 0.0% | 0.9766 / 0.0% |
| free | (c) t1, t3 | needs review | 0.6521 / 37.4% | 0.9766 / 0.0% |
| free | (d) s1, s1, s2 | inconsistent | 0.7618 / 22.8%; 1.0000 / 0.0% | 0.9766 / 0.0% |
| free | (d) s1, s2, s2 | needs review | 1.0000 / 0.0%; 0.7664 / 23.4% | 0.9766 / 0.0% |
| Matisse 2 | (a) edit 0.05% | needs review (tracing only) | 0.9997 / 0.0% | 0.9966 / 0.0% |
| Matisse 2 | (a) edit 0.1% | needs review (tracing only) | 0.9996 / 0.0% | 0.9966 / 0.0% |
| Matisse 2 | (a) edit 0.2% | needs review (tracing only) | 0.9992 / 0.0% | 0.9963 / 0.0% |
| Matisse 2 | (a) edit 0.3% | needs review (tracing only) | 0.9989 / 0.0% | 0.9962 / 0.0% |
| Matisse 2 | (b) s2, s1 | inconsistent | 0.8669 / 15.9% | 0.8893 / 13.6% |
| Matisse 2 | (c) control t1, t2, t3 | needs review (tracing) | 1.0000 / 0.0%; 1.0000 / 0.0% | 0.9967 / 0.0% |
| Matisse 2 | (c) t1, t3 | needs review | 0.9549 / 4.0% | 0.9967 / 0.0% |
| Matisse 2 | (d) s1, s1, s2 | needs review | 0.9484 / 4.3%; 1.0000 / 0.0% | 0.9967 / 0.0% |
| Matisse 2 | (d) s1, s2, s2 | inconsistent | 1.0000 / 0.0%; 0.8892 / 13.7% | 0.9967 / 0.0% |
| paste 2 | (a) edit 0.05% | needs review (pastes only) | 0.9997 / 0.0% | 0.9927 / 0.0% |
| paste 2 | (a) edit 0.1% | needs review (pastes only) | 0.9996 / 0.0% | 0.9926 / 0.0% |
| paste 2 | (a) edit 0.2% | needs review (pastes only) | 0.9991 / 0.0% | 0.9924 / 0.0% |
| paste 2 | (a) edit 0.3% | needs review | 0.9986 / 0.1% | 0.9922 / 0.0% |
| paste 2 | (b) s2, s1 | inconsistent | 0.8130 / 22.4% | 0.9054 / 9.6% |
| paste 2 | (c) control t1, t2, t3 | needs review (pastes) | 1.0000 / 0.0%; 1.0000 / 0.0% | 0.9928 / 0.0% |
| paste 2 | (c) t1, t3 | needs review | 0.8857 / 14.2% | 0.9928 / 0.0% |
| paste 2 | (d) s1, s1, s2 | inconsistent | 0.8715 / 15.4%; 1.0000 / 0.0% | 0.9928 / 0.0% |
| paste 2 | (d) s1, s2, s2 | needs review | 1.0000 / 0.0%; 0.9074 / 9.6% | 0.9928 / 0.0% |

"(tracing only)" / "(pastes only)": same number of review findings as
the honest chain, so the edit added no finding.

| | Runs | "consistent" | Edit not seen by the link (0.0% changed) |
|---|---|---|---|
| (a) small edits | 12 | **2** (free 0.05%, 0.1%) | 9 of 12 |
| (b) wrong order | 3 | 0 (3 inconsistent) | – |
| (c) middle left out | 3 | 0 (3 needs review) | – |
| (d) session twice | 6 | 0 (3 inconsistent, 3 needs review) | – |
| (c) honest 3-part control | 3 | not inconsistent: 3 of 3 | – |

(b), (c) and (d) are caught on 3 of 3 pieces. In paste 2's (c), the
first paste vanishes from the report, but the gap shows (0.8857 /
14.2%). (a) misses edits below about one 32 px block. 2 of 12 runs get
"consistent"; in the other 7 misses the piece got "needs review" only
for its tracing or pastes.

**The same gap exists for one session.** The same edits on the delivered
image only, against the honest last snapshot: 0.00% changed at 0.05% on
3 of 3 pieces, and at 0.1% on 2 of 3 (paste 2 0.14%). So the delivery
check (D-015) has the same floor; the chain inherits it.

**Honest mistakes.** Given in the wrong order or twice, an honest
piece's sessions get "inconsistent": (b) 3 of 3, (d) 3 of 6. They were
counted here as attacks (Claude's call, delegated by Andrea, 2026-11-17).
The finding says the session doesn't start where the previous one ended.

**Options for the small-edit miss** (measured on the same 12 edits; link
only, delivery check unchanged):

| Option | Edits seen by the link | Honest links | Unmeasured |
|---|---|---|---|
| 1. Keep 32 px blocks, state the floor as a limit | 3 of 12 | 0.0% on 3 + 6 honest links | – |
| 2. Link blocks 16 px (parameter) | 12 of 12 (0.03-0.28% changed) | 0.0% (also at 8 px) | a real restart's first snapshot vs the previous last |
| 3. Link needs an exact pixel match | 12 of 12 (52-2,209 px differ) | 0 px differ | the same; any re-render difference would give every honest multi-session piece "needs review" |

The honest links here are pixel-identical by construction (session 2
opens with session 1's last snapshot), so they can't show a false
"needs review" for option 2 or 3. Within one open session, about 483 of
about 1,761 snapshot ticks on the 14 recordings rendered byte-identical
to the previous one and were not stored (`capture.py`, `snapshot`; ticks
estimated as duration / 5 s). That suggests, but doesn't show, that an
unchanged canvas re-renders identically after a restart.

Andrea picked option 1 (2026-11-17, D-045); the protocol below is what
would reopen it.

**Protocol to measure a real restart** (Andrea, ~15 minutes; it decides
between options 2 and 3):

1. Copy (don't move) `plugins/krita/hic_et_nunc_capture/` into `pykrita/`
   and restart Krita.
2. New document. Tools → Scripts → **Hic et nunc: start capture**. Draw
   freely for ~5 minutes. **Stop capture**. Save as a `.kra`.
3. Quit Krita completely. Reopen it and open the same `.kra`.
4. Without touching the canvas, **start capture**, wait 10 s, then draw
   ~5 minutes more. **Stop capture**. Save, and export a PNG.
5. Note the two session folder names in `~/hic-et-nunc-capture/`.

Expected: `hic-et-nunc report S1 S2 --delivered export.png` gets
"consistent", with the link at 1.0000 / 0.0% at 32 px. Then measure the
pixels that differ between S1's last and S2's first snapshot: 0 favours
option 3, a few favours option 2, many rules out both.

## Snapshot files checked against the log (2026-11-17, D-046)

The plugin logs each snapshot's sha256 (`capture.py`, `snapshot`);
until now nothing read it, so the pictures could be swapped with the
chain intact. `Session.snapshot_mismatches` now lists every snapshot
whose file is missing, hashes differently, or whose entry has no
sha256; any → "inconsistent" in `report`, tier none in `evaluate`,
exit 1 in `inspect`.

**Attack, before / after.** Free's log (20261114-200035) with its 90
snapshot files overwritten, in order, by 90 of Matisse's 105
(20261114-203940) spread evenly from first to last; delivered image
Matisse's last snapshot; no reference. Before: "consistent", "The
recording checks out". Copying Matisse's first 90 instead gave "needs
review" (2.0% of the delivered image differing from the last picture),
so the spread is the attack that works. After: "inconsistent", 89 of 90
pictures mismatched (the first, a blank canvas, is byte-identical).
Test: `test_real_log_with_another_drawings_snapshots_fails`. Synthetic:
a swap and a deleted file were both "consistent" before, "inconsistent"
after (`test_swapped_snapshot_files_fail_verification`).

**Honest data.** 14 of 14 recordings in `~/hic-et-nunc-capture`: 1,278
snapshot entries, 0 without sha256, 0 files missing, 0 mismatched.
Results before (HEAD 08adbb3) / after, references as in
`test_real_sessions_report` (others: `matisse.jpg.kra`), no delivered
image: same result and same number of review findings on 14 of 14
(8 consistent, 6 needs review). The 15 split cases of "C + B built",
rebuilt with a throwaway script (same cuts, edits and pairings; edited
snapshots get a recomputed sha256, as someone rebuilding the log would
do): 15 of 15 unchanged, and they match the "C + B built" results.
With the edits' sha256 left as recorded, the 6 edit cases go from
"needs review" to "inconsistent".

**Cost.** The check on the largest session (329 snapshots, 37 MB):
210 ms with a cold disk cache, 22 ms warm. A report on that session
takes 11.3 s.

**Not covered.** Someone who rebuilds the log, its sha256 fields and its
checkpoints can still swap pictures: that is the existing limit
(D-037). A session folder given without its `snapshots/` folder now
gets "inconsistent" (every picture missing).

## Non-blank start measured on the first snapshot alone (2026-11-17, D-047)

**Attack.** The Matisse tracing's line art (20261114-203940, last
snapshot, grey < 128: 2,537 px, 0.34% of the 723x1024 snapshot) placed
under every snapshot of free (20261114-200035), in dark (40,40,40),
yellow (0,255,255) or pale (200,240,230) BGR; snapshot sha256 recomputed
and the log rechained, so D-046 passes (the D-037 forger). Before:
share of the last snapshot's grey marks already there 0.6% / 0.6% /
0.0%, limit 1%: "consistent" 3 of 3. Free's final marks 62% of the
canvas dilute the share; pale is 22 grey levels from white, under the
30-level cutoff.

**Measure now** (`analysis.marked_at_start`): share of the first
snapshot's pixels more than 30 levels from its most common colour in
any one channel; limit `Policy.max_marked_at_start` 0.01% (~74 px),
Andrea's pick of 0.01% / 0.1%. First snapshots (throwaway scripts
outside the repo):

| Start canvas | Background | Largest gap | > 10 | > 20 | > 30 | Old measure |
|---|---|---|---|---|---|---|
| 14 whole recordings (each) | white | 0 | 0 px | 0 px | 0 px | 0.0% |
| split session 2, free | white | 255 | 68.8% | 65.9% | 62.4% | 79.1% |
| split session 2, Matisse 2 | white | 255 | 2.14% | 2.04% | 1.97% | 11.2% |
| split session 2, paste 2 | white | 255 | 27.7% | 19.8% | 15.0% | 65.7% |
| attack, dark / yellow / pale | white | 215 / 255 / 55 | 0.34% | 0.34% | 0.34% | 0.6 / 0.6 / 0.0% |

**After.** Attack 3 of 3 "needs review" (0.34%). Results unchanged on
35 of 35 rows: 14 whole recordings, 15 split cases, 6 split edits with
the logged sha256 left as recorded. Cost: 34 ms per first snapshot
(723x1024); the first version, `np.unique` over rows, took 452 ms.

**Not covered.** Content within 30 levels of the background in every
channel, or under ~74 px. Honest data is one person, plain white
canvases only: a textured paper or a background placed before capture
is unmeasured and would get "needs review".

## Attack review of D-046 and D-047 (2026-11-17)

**Attacker.** Rebuilds the log with the plugin's own writer
(`logwriter.BufferedEventLog`), sha256 of every edited picture
recomputed, checkpoints every 60 s, clean close: chain, checkpoints and
D-046 all pass (the D-037 forger). Base: 20261117-130338 (free, layer
bounds recorded). Sketch: the Matisse line art as in the D-047 section
(0.34% of the canvas), dark (40,40,40), on paper pixels only. Delivered
image: the last picture. `evaluate` with its default policy p2.
Throwaway scripts `forge.py`, `attack.py` outside the repo.

| Case | Target | report | evaluate | Start measure |
|---|---|---|---|---|
| Real log / rebuilt unchanged (controls) | – | consistent | pass | 0 |
| B0: sketch visible in every picture | D-047 | needs review | **pass** | 0.34% |
| B1: sketch on a layer hidden at the 1st picture, shown at the 3rd (layer record says so) | D-047 | **consistent** | pass | 0 |
| B1b: same, layer opacity 0 → 255 | D-047 | **consistent** | pass | 0 |
| B2: sketch at (230,230,230), within 30 levels | D-047 | consistent | pass | 0 (stated limit) |
| B3: switch to another document holding the sketch at the 3rd picture | D-047 | needs review (new-layer paste, 0.3%) | pass | 0 |
| B4: blank 2-picture decoy session, then B0 as session 2 | D-047 | needs review (link 1.8%) | fail | 0 |
| A1: 1st picture is non-image bytes with matching sha256 | D-046 | needs review | pass | 0.17% (2nd picture) |
| A2: every entry names the 1st picture's file, sha256 matching | D-046 | consistent | pass | 0 |
| A3: real log byte-identical up to entry 96 (real checkpoints 1-3 kept); 12 of 34 pictures after it carry the sketch | D-046 | consistent | pass | 0 |

**Proven, outside accepted limits.**
- B1, B1b: a sketch that exists before capture on a hidden or 0%-opacity
  layer misses the start check (the first picture is blank) and is
  then shown without a stroke or a logged action. Line art at 0.34%
  is under the one-step paste detector's 15% and is not a new layer.
  The log has what is needed to see it: the layer record at the start
  lists the layer with non-empty bounds and `visible` false / `opacity`
  0. Built from a rebuilt log, not a Krita recording: whether Krita
  reports bounds for a hidden layer, and whether the docker's eye icon
  is a logged action, is unverified (protocol below).
- B0: `evaluate` has no start check (it runs only in `report`, D-044 /
  D-047); a recording started on line art gets verdict "pass".

**Not findings.** B2: within D-047's stated limit. A1: picture 2 is
measured instead, a worse result, not a better one; the unreadable
file is not reported (labels shift by one). A2, A3: only reachable by
rewriting the log or its tail, which D-037 accepts; A2's pictures
show a blank canvas and the delivered image is that blank. Every
picture the code reads comes from a logged entry (`Session.snapshots`,
`Session._snapshot_image`), and `inspect`, `evaluate` and every session
of a multi-session `report` run `snapshot_mismatches`.

**Protocol to confirm B1 in Krita** (Andrea): 1. New 2480×3508
document; on a new layer "Sketch" draw a few lines, then hide it (eye
icon in the Layers docker). 2. Select another layer, start capture.
3. Draw 2-3 strokes, wait 10 s, show "Sketch" with the eye icon, wait
10 s, draw over it for a minute, stop capture, export a PNG. 4. Note
the time you showed the layer. Expected: the first layer record lists
"Sketch" with `visible` false and non-empty `bounds`; no `action` near
the time noted; `hic-et-nunc report <session> --delivered <png>` gives
"consistent" (finding confirmed) or "needs review" (finding refuted:
say which line).

## Snapshot freeze (2026-11-17)

**Where the time goes.** `capabilities.json` of the five 15 Nov sessions
that record it: snapshot median 239-283 ms, max 253-323 ms. Timed in
Krita's Scripter (Andrea, 2026-11-17) on `matisse 2.kra`, 2480×3508 →
723×1024, 3 layers, 10 runs:

| Step | Median | Max |
|---|---|---|
| `doc.thumbnail` | 190.7 ms | 206.3 ms |
| PNG encode | 31.7 ms | 32.0 ms |
| sha256 + write + layer list | ~1 ms | ~1.2 ms |
| total | 223.0 ms | 239.4 ms |

`thumbnail` (86%) renders a QPixmap inside Krita, so it can't leave the
GUI thread (Qt 5 docs, "Threads and QObjects"). A worker thread
spinning against `QImage.save` kept 1.01x the main-thread rate of a
sleeping one, so Krita's PyQt5 releases the lock while encoding; moving
the encode off would save ~32 ms and log the snapshot after strokes drawn
meanwhile. Not done.

**Baseline, 12 real sessions** (throwaway scripts outside the repo):
1608 timer ticks, 851 (53%) inside a stroke. Gaps ≥ 40 ms within
strokes whose start lies in the 0.5 s before a logged snapshot: 2.5-6.7x
the mean of the other nine half-seconds within ±2.5 s (10 sessions with
gaps). Anchored on the session start + k·5 s instead, three sessions
come out below 1: the timer drifts (snapshot offset from the grid,
median 0.08-0.76 s). Strokes are long: p90 2.0-27.6 s, max 104 s; pen
down 29-75% of the time; long strokes (> 10 s) are real contact (62-192
points/s, ≤ 0.1% zero pressure).

**Fix: a snapshot due mid-stroke waits for pen-up**, at most
`SNAPSHOT_MAX_DELAY_MS` (30 s, Andrea, 2026-11-17). Simulated on the
same strokes, ticks still taken mid-stroke: 3 s cap 519, 10 s 267, 30 s
73 (4.5%), 120 s 0. The ~220 ms stall still happens, at pen-up; a stroke
started within it can lose its first samples.

| | Before (12 sessions) | Before, 20261117-125231 (old code still loaded) | After, 20261117-130338 |
|---|---|---|---|
| Snapshot ticks mid-stroke (target ≤ 5%) | 851 of 1608 (53%) | 46 snapshots inside strokes, of 63 | 2 of 57 (3.5%): one at the cap (31.1 s into a 41.6 s stroke), one in a 0.3 s stroke |
| Gaps ≥ 40 ms before a snapshot vs other half-seconds (target ≤ 1.5x) | 2.5-6.7x | 13.6x | 1.9x (6 vs mean 3.1) |
| Snapshot time, median / max | 239-283 / 253-323 ms (5 sessions) | 269 / 293 ms | 264 / 282 ms |

After: 4.8 min, 138 strokes (longest 41.6 s), `filter_errors` 0. Of
the 9 gaps ≥ 100 ms within 0.3 s of a snapshot, 8 fall on a stroke's
first sample (a stroke started during the pen-up stall) and 1 at the
cap. Andrea (2026-11-17): a hitch after lifting the pen "quite often",
felt on short strokes started while the snapshot ran; one freeze in
the long stroke. Waiting 0.3/0.5/1.0 s of pen idle before the snapshot
doesn't help (simulated on the 12 of 14 local sessions with ≥ 5 strokes: a stroke starts inside the
stall for 10.3/10.0/17.8% of postponed snapshots, vs 9.9% with no wait).

## Report run time, basis for F-001's target (2026-11-17, D-048)

`hic-et-nunc report` at d1288c6, wall-clock time on Andrea's Mac (arm64), on
the 14 recordings in `~/hic-et-nunc-capture`, each with its last picture as
the delivered image. Command: a shell loop timing
`hic-et-nunc report SESSION --delivered LAST [--reference matisse.jpg.kra] -o OUT`.
All exit 0.

| Input | Size | No reference | Reference `matisse.jpg.kra` |
|---|---|---|---|
| 20261114-183713 (329 pictures, largest) | 46 MB | 4.4 s | 13.1 s |
| Other 13 recordings (2-173 pictures) | 44 KB-30 MB | 0.7-2.2 s | 0.9-6.5 s |
| All 14 as one piece (1,278 pictures) | ~180 MB | not run | 26.3 s |

The reference adds the tracing check (200 placements), which takes most
of the time. Target set at 60 s on the server, upload excluded: 2.3x
over the worst case measured here. A €4-12/month server's speed relative
to this Mac is a guess (2-3x slower), to be measured in F-001 part 5.

**Memory and repeat run (2026-11-17, ba053e7).** Same inputs, reference
`matisse.jpg.kra`, last picture of the last session as delivered image.
Peak memory is `ru_maxrss` of the child process, read through Python's
`resource.getrusage(RUSAGE_CHILDREN)` (`/usr/bin/time -l` is blocked by
the sandbox). Two runs each:

| Input | Time | Peak memory |
|---|---|---|
| 20261114-183713 (largest) | 11.7 / 12.8 s | 1.4 / 1.7 GB |
| All 14 as one piece | 57.4 / 57.8 s | 1.6 / 1.7 GB |

The single session matches the earlier 13.1 s; all 14 as one piece took
over twice the 26.3 s above. Cause unknown. A server for F-001 needs
≥ 4 GB of memory (1.7 GB peak plus the system and web server: guess).

## Upload size, basis for F-001's cap (2026-11-18, D-049)

The 14 recordings in `~/hic-et-nunc-capture`, measured with `du -sk` and
`find -type f` (disk blocks, so slightly above the bytes sent):

| Input | Size | Files | Largest file |
|---|---|---|---|
| 20261114-183713 (largest) | 46 MB | 332 | 9.4 MB |
| Other 13 recordings | 44 KB-30 MB | 5-176 | 16 KB-3.3 MB |
| All 14 together | 188 MB | 1,320 | 9.4 MB |
| `matisse.jpg.kra` (reference) | 2.1 MB | 1 | 2.1 MB |

Cap set at 300 MB and 3,000 files per upload: 1.6x and 2.3x all 14
together.

## Known limitations

- Delivered image (option 3, measured above): every logo or signature
  gives an honest delivery "needs review", and so does a CMYK round trip
  that shifts a mostly dark image. A delivery that doesn't align and
  differs is "inconsistent"; no honest one tested did.
- Recording: events after the last checkpoint (up to 60 s) aren't
  covered; since 2026-11-15 the page says how many (cut copies: 9 and
  10 events, the final 29 s and 20 s; full recordings: 0, the plugin
  writes a checkpoint when capture stops).
- A piece recorded over several sessions can only be reported one
  session at a time; the last one doesn't say the canvas wasn't blank
  when it started.
- A reference on a **visible layer** blinds the snapshot method: strokes drawn
  on its edges create no new edges in the rendered document. The stroke method
  still works. Fix: per-layer snapshots (next iteration).
- Reference transforms: rotation and scale follow Krita's saved format
  (`matrix(a b c d e f)`, points) and are tested on synthetic documents
  only. The one real moved reference (Matisse 2) was shifted 0.76 pt
  (3 px), too little to confirm the units: the stroke ratio only moves
  from 9.21 to 10.19 across shifts of 0-6 px, with no peak at 3 px.
- Pastes: logs from before 2026-11-15 have no actions or bounds, so a
  paste into an existing layer goes unreported there and a new-layer
  paste's final size is unknown (the 15 Nov paste session: 0.0046 on
  arrival, ~0.12 after scaling, reported as 0.0046). Content that
  reaches a layer through no logged action and no new layer (undo
  through the History docker, a script) isn't reported. The final area
  scales by the whole layer's bounds: a transform of only a selection,
  or painting and transforming the same layer within one snapshot
  interval (5 s; up to 35 s during a long stroke), throws it off. Calibration: two real pastes and one
  control session, one person, one tablet.
- Untested: whether Duplicate Layer, cut or copy selection to a new
  layer, copy merged + paste, and paste then undo are reported as
  pastes. The "new layer holding content no stroke drew" rule and the
  paste actions may report them, giving honest sessions "needs review".
- Untested: paste into an existing layer, then scale it up. The final
  area may stay at the arrival size, because the layer's bounds barely
  change when the rest of the layer already spans them.
- Untested: paste on a new layer, paint on it, then scale it up. The
  scaling is counted, but from bounds that include the painting, so the
  final area can be off.
- Tracing ratio: a drawing that clusters at scales below the band (128
  document px) right around the reference's edges still raises it, and it
  can't exceed 1 / baseline, which on small frames is barely above 1
  (synthetic 200 px frames: cap 2.14); below the limit, `trace` and
  `evaluate` report "can't tell at this resolution". The p-value is unreliable for a
  reference that fills the canvas (A's photo): only half-turns and small
  shifts fit, and unrelated drawings scored p 0.005-0.025 at ratios
  1.17-1.40 against it.
- Commitments are written locally; the timestamping server comes later.
- On Windows with WinTab, Krita may handle pen input in ways that bypass Qt
  tablet events. The capability report shows whether that happens.

# Evaluation: what hic et nunc's evidence can and cannot establish

An adversarial evaluation of the evidence the tool produces, not of the
intentions behind it. Written 2026-11-21 at commit 8411ec8 (hic-et-nunc
0.1.0). Measurements live in `plugins/krita/SPIKE.md` (cited below by
section name); this file orders them, adds two new experiments (E1, E2)
and gives the verdicts. `D-NNN` refers to the author's private decision
log (see README, "References").

## 1. Executive summary

- A hic et nunc recording is a file the artist's own machine writes and
  the artist keeps. Every check in the tool runs on that file. Nothing
  in it is signed by, stamped by or sent to anyone else.
- So the tool can show **internal consistency**: the log, its
  checkpoints, its canvas pictures and the delivered image agree with
  each other, and the recording shows no paste, no traced *declared*
  reference and no drawing already on the canvas at the start.
- It can't show that the recording is genuine. Whoever holds the files
  can rebuild them so every check passes (measured, SPIKE "Delivered
  image and recording checks"; "Attack review of D-046 and D-047"). A
  recording written entirely by a short program, with no person and no
  Krita, gets "consistent", tier "strong" and "pass" under the policy
  named "no generative AI" (E2, new).
- It doesn't detect tracing of an image it isn't given. Three real
  tracings of an image get "consistent" and "pass" when the traced image
  isn't declared, or is declared as the artist's own (E1, new).
- What it can defend: a **failed** check is informative (edits to a
  committed log entry, swapped canvas pictures, a different delivered
  image, sessions out of order or missing are caught on every tested
  case). A **pass** means "no inconsistency found in files the artist
  controls". It says nothing about who drew, whether AI was used, or
  whether the recording is the real process.

## 2. Scope

In scope: the Krita path (plugin + `report` + `evaluate`), the main
workflow; the time-lapse and `.procreate` path (WEAK tier) briefly; the
C2PA commands briefly. Out of scope: performance, usability, the
`.procreate` layout (untested on a real file, README).

Method: read the code at 8411ec8; map every attack already measured in
SPIKE to the attack classes below; run new experiments only where no
measurement existed and one was cheap (E1, E2). Items that need a real
Krita recording are listed as not tested, with a protocol.

## 3. Tested implementation

Environment: macOS 15.7 arm64, Python 3.13.1, hic-et-nunc 0.1.0
(8411ec8). Real data: 14 recordings by one person (not an
illustrator), one tablet, plain white canvas (SPEC, "Technical
constraints").

| Component | What is collected / done | Where |
|---|---|---|
| Pen input | Qt tablet and mouse events on Krita's canvas widget: position, pressure, tilt, Qt event time (ms, a separate clock); one `stroke` entry per pen-up, `source` set to `"tablet"` from the Qt event type | `plugins/krita/hic_et_nunc_capture/capture.py`, `InputFilter.eventFilter` |
| Canvas pictures | the merged document every 5 s (deferred to pen-up, at most 30 s), longest side ≤ 1024 px, stored only if changed; sha256 in the log | `capture.py`, `snapshot` |
| Krita actions, layers | action names (paste, import, transform...); layer list with bounds, visibility, opacity when it changes | `capture.py`, `on_action`, `layers` |
| Log | JSONL, each entry `seq`, `t` (the machine's `time.time()`), `type`, `data`, `prev`, `hash` = sha256 of the canonical entry | `logwriter.py`; verified by `src/hic_et_nunc/eventlog.py` |
| Checkpoints | RFC 6962 Merkle root of all entries so far, every 60 s, appended to `commitments.jsonl` **in the same folder** with the local time | `logwriter.py`, `checkpoint` |
| Verification | chain, checkpoints, snapshot sha256 (D-046), clean close (D-038) | `src/hic_et_nunc/session.py`, `report.py`, `assess.py` |
| Analysis | delivered image vs last picture (D-015); pastes from actions, new layers and changes no stroke explains (D-011); tracing vs a *given* reference (D-010); canvas already marked at start (D-047); links between sessions (D-041) | `src/hic_et_nunc/analysis.py`, `session.py`, `report.py` |
| Outputs | HTML report: consistent / needs review / inconsistent. `evaluate`: JSON, tier, policy verdict | `report.py`, `cli.py`, `policy.py` |

Implemented vs intended:

- Inclusion proofs (`eventlog.inclusion_proof`) are implemented, but no
  root is ever published anywhere, so they prove nothing about time or
  about what existed before the artist chose to show it. The
  `logwriter.checkpoint` docstring's "Later this goes to a timestamping
  server" was rejected (D-037).
- C2PA signing (`sign`) uses development certificates only; validators
  report the signer as untrusted (README). The key is the artist's, so
  the signature attests to nothing beyond "this key signed this
  summary".
- No identity: the log holds Krita version, Qt binding, Python version
  and platform string (`session_start`); no user, device serial or key.
- `capabilities.json` is written outside the log and isn't hashed.

## 4. Threat model

The assumption that decides most rows: **the evaluator gets the files
from the artist, and the artist's machine wrote them.**

| Adversary | Capabilities | Limits | Goal | Does the tool help? | Detects? |
|---|---|---|---|---|---|
| T1 honest artist preserving the process | runs the plugin | snapshot hitch (README) | keep a record | yes: pictures, timings, report | n/a |
| T2 honest artist falsely accused of AI | as T1, after the fact | the accuser must trust files she holds | be believed | only if the accuser accepts self-held evidence; a pass is consistency, not proof | n/a |
| T3 artist fabricating human evidence | reads the public format; writes JSON; ~60 lines of Python (E2) | needs to write plausible snapshots too | a "consistent" report for an image she didn't draw live | no | **no** (E2; rebuild, SPIKE) |
| T4 AI user making it look human | generates an image; traces or redraws it live in Krita | must actually draw | "consistent" for AI-derived work | no, unless she declares the AI image as reference with origin `ai` | **no** if undeclared (E1); yes if pasted (SPIKE "Results") |
| T5 post-hoc modifier of files | edits log, pictures or delivered image | none beyond T3 | change the record | partly | committed entries: yes; tail, small edits, full rebuild: no |
| T6 stager / selective recorder | starts late, hides layers, records parts | must give the sessions to the report | hide earlier work | partly | visible start content yes; hidden layer, faint or tiny content no |
| T7 evaluator who trusts neither | receives the files | has only what the artist sends | decide | only by rerunning the checks on the same files | cannot tell an honest file from a rebuilt one |

Fundamentally outside the tool's scope as built: T3, T4-undeclared, and
attribution (who held the pen). These need something outside the
artist's control (an independent witness, a trusted capture device, an
external stamp), and the project has rejected the external parts
(D-037, D-051).

## 5. Methodology

- Existing attack reviews were taken as they are: each run in the
  table below cites its SPIKE section. They were not re-run for this
  evaluation; the tests that encode some of them pass (`pytest` at
  8411ec8 without real data: 81 passed, 49 skipped, 2026-11-21; with
  real data 130 passed the same day, before this evaluation). The
  throwaway scripts behind them (`forge.py`, `attack.py`, the split
  builder) were not kept, so those runs are reproducible from their
  descriptions, not from code.
- New runs E1, E2: commands in section 6. E2 needs only the public repo.
  E1 needs the private real recordings (D-017, D-026).
- Classes: **Detected** (reliably flagged on every tested case),
  **Partially detected** (flagged as "needs review" or only in one
  command, or can't be told from an honest case), **Undetected**,
  **Not tested** (with the reason). "Reasoned" marks a class that comes
  from reading the code, not from a run.

## 6. Experiments and results

### 6.1 Attack index

| # | Attack | Result | Class | Source |
|---|---|---|---|---|
| R1 | Edit one committed log entry | inconsistent (hash mismatch), 2 of 2 sessions | Detected | SPIKE "Delivered image and recording checks", recording verification |
| R2 | Edit, rehash the chain | inconsistent (checkpoints fail), 2 of 2 | Detected | same |
| R3 | Delete an entry, with or without renumber and rehash | inconsistent, 2 of 2 | Detected | same |
| R4 | Reorder entries | not run separately; each entry hashes the previous one | Detected (reasoned) | `eventlog.verify_chain` |
| R5 | Edit or delete entries after the last checkpoint (≤ 60 s) | consistent; the report states how many events are uncovered | Undetected | same section; D-037 |
| R6 | Cut the final entries | needs review, same as a crash | Partially detected | same section; D-038 |
| R7 | Edit, rehash, recompute checkpoints (full rebuild) | consistent / needs review as the original | Undetected | same section |
| R8 | Swap canvas pictures, log untouched | before D-046 consistent; after: inconsistent, 89 of 90 mismatched | Detected | SPIKE "Snapshot files checked against the log" |
| R9 | Swap pictures and rebuild their sha256 and the log | consistent (A2, A3) | Undetected | SPIKE "Attack review of D-046 and D-047" |
| R10 | Write a whole recording by program from a target image | consistent, tier strong, P1 and P2 pass | Undetected | **E2** |
| S1 | Start capture on a visible sketch (0.34% of the canvas) | `report` needs review 3 of 3; `evaluate` pass | Partially detected | SPIKE "Non-blank start..."; "Attack review" B0 |
| S2 | Start content within 30 levels of the paper, or under ~74 px | consistent | Undetected (stated limit) | same, B2 |
| S3 | Sketch on a hidden or 0%-opacity layer at start, shown later | consistent (rebuilt log) | Undetected; unconfirmed in Krita | "Attack review" B1, B1b |
| S4 | Switch to another document holding the sketch | needs review (new-layer paste) | Detected | "Attack review" B3 |
| S5 | Blank decoy session, then the sketch as session 2 | needs review / fail | Detected | "Attack review" B4 |
| S6 | Give only the last session | needs review (canvas marked at start: 79.1 / 11.2 / 65.7%) | Detected | SPIKE "C + B built" |
| S7 | Leave out a middle session | needs review, 3 of 3 | Detected | SPIKE "Multi-session attack review" (c) |
| S8 | Sessions in the wrong order, or one twice | inconsistent or needs review, 9 of 9 | Detected (also hits honest mistakes) | same, (b), (d) |
| S9 | Edit the canvas between sessions, 0.2-0.3% | an added "needs review" finding on 3 of 6 runs; the other 3 got "needs review" only for their tracing or pastes | Partially detected | same, (a) |
| S10 | Same, 0.05-0.1% | consistent 2 of 6; link sees 0.0% on 9 of 12 | Undetected | same, (a); D-045 |
| D1 | Deliver another image or a 5-20% replaced one | 0 of 19 "matches" | Detected | SPIKE "Delivered image and recording checks" |
| D2 | Edit the delivered image below one 32 px block (0.05%) | 0.00% changed, 3 of 3 | Undetected | "Multi-session attack review", same gap for one session |
| A1 | Paste an image as a new layer | needs review | Detected | `test_real_sessions_report` (PASTE) |
| A2 | Paste small, then scale up | final area reported | Detected (D-011's cases) | SPIKE "Protocol: pastes...", D-011 |
| A3 | Content via History docker undo or a script | not reported | Undetected (stated) | README limitations |
| A4 | Trace an image declared with origin `ai` | fail, 3 of 3 | Detected | **E1** |
| A5 | Trace the same image declared `own` | pass, 3 of 3 (ratios 4.6-6.6) | Undetected | **E1** |
| A6 | Trace an image not given to the tool | consistent 3 of 3; P1 "no generative AI" pass 3 of 3 | Undetected | **E1** |
| A7 | Trace mostly with fill or hatching | consistent (1.93 / 1.25) | Undetected | README limitations |
| A8 | Copy an AI image by eye, without following its edges | — | Not tested: the tracing measure only scores line work near a given reference's edges, so by construction it doesn't apply (reasoned) | `analysis._tracing` |
| P1 | Script inside Krita posting synthetic tablet events | — | Not tested: needs Krita. Reasoned: the filter records any tablet-type event on the canvas widget and can't tell its origin | `capture.py`, `InputFilter` |
| P2 | Plotter, robot, or software imitating pen input | — | Not tested; stated on every report page | report "What this can't tell you" |
| I1 | Someone else draws, artist submits the recording | — | Not testable: nothing in the data names a person (reasoned) | `capture.py`, `session_start` |
| T1 | Shift or invent timestamps | — | Undetected (reasoned from R7: `t` is a field like any other, written from the local clock, never compared with an outside time) | `logwriter.append`, `checkpoint` |

Honest cases the checks also flag (they cost the honest artist, not
the forger): a crash gives "needs review" (D-038); every logo or
signature on the delivered image gives "needs review" (47 of 151 honest
variants, D-015); sessions given in the wrong order give "inconsistent"
(SPIKE "Multi-session attack review").

### 6.2 E1: tracing an image the tool isn't told about

- **Hypothesis:** tracing is measured only against a reference given to
  the command, and the policy trusts the declared origin; so the same
  real tracing passes when the image is undeclared or declared `own`.
- **Setup:** the 3 real traced recordings and their saved `.kra`
  references: 20261114-203940 (`matisse.jpg.kra`), 20261115-111336
  (`matisse 2.kra`), 20261115-120348 (`rotated matisse.kra`). The
  Matisse image stands in for an AI image: the measure sees only edges,
  not where the image came from.
- **Steps:** for each, `hic-et-nunc report SESSION -o x.html` with and
  without `--reference KRA`; `hic-et-nunc evaluate SESSION --policy p1|p2`
  with no reference, `--reference ai=KRA`, `--reference own=KRA`.
- **Expected:** with the reference, "needs review" and fail; without, or
  declared `own`, consistent and pass.

| Run | 203940 | 111336 | 120348 |
|---|---|---|---|
| `report`, reference given (control) | needs review | needs review | needs review |
| `report`, no reference | **consistent** | **consistent** | **consistent** |
| `evaluate` P1 "no generative AI", no reference | **pass**, tier strong | **pass**, strong | **pass**, strong |
| `evaluate` P1, `ai=` | fail (ratio 6.1) | fail (6.6) | fail (4.6) |
| `evaluate` P1, `own=` | **pass** | **pass** | **pass** |
| `evaluate` P2 "human-made image" | same three results as P1 | same | same |

- **Detected?** Only when the artist declares the image as AI. The
  no-reference page does say "No reference image was given, so tracing
  wasn't checked"; `evaluate`'s JSON says "pass: no issues found".
- **Limits:** one person, three tracings of one image (two placements).
  `own` passing is the policy's design (tracing your own work is
  allowed); the finding is that origin is the artist's word.

### 6.3 E2: a recording written entirely by a program

- **Hypothesis:** the format is public and every check runs on data the
  writer controls, so a recording generated without Krita or a person
  passes.
- **Setup:** the test suite's own `_fake_session`
  (`tests/test_session_and_credentials.py`), which writes a session with
  the plugin's vendored writer: strokes copied from a target image's
  outlines (`conftest.make_reference`, standing in for an AI image),
  pictures rendered with OpenCV, checkpoints, clean close.
  Generated: 21 strokes, pressure 0.6 on every sample, samples exactly
  5 ms apart, 28 events, 5 checkpoints. Delivered image: the last
  picture.
- **Steps** (from the repo root, venv active):

  ```python
  import sys

  sys.path.insert(0, "tests")
  from pathlib import Path
  import cv2
  from conftest import make_reference
  from test_session_and_credentials import _fake_session

  ref = make_reference()
  Path("/tmp/e2").mkdir(exist_ok=True)
  cv2.imwrite("/tmp/e2/target.png", ref)
  d = _fake_session(Path("/tmp/e2"), ref, traced=True)
  ```

  then `hic-et-nunc report /tmp/e2/session --delivered <last snapshot>
  -o e2.html` with and without `--reference target.png`, and `evaluate`
  as in E1.
- **Expected:** consistent / pass without the reference.

| Run | Result |
|---|---|
| `report`, no reference | **consistent**; page: "The recording checks out", "Recorded live in Krita, with a pen tablet" |
| `report`, target given as reference (control) | needs review (tracing) |
| `evaluate` P1 / P2, no reference | **pass**, tier **strong** |
| `evaluate`, `ai=target` | fail (ratio 3.5) |
| `evaluate`, `own=target` | **pass** |

- **Detected?** No. Constant pressure and a perfectly regular sample
  clock are not looked at; nothing in the tool examines pen dynamics.
- **Evidence remained convincing?** To the tool, yes. A reader who
  inspected the pictures would see machine-drawn outlines; a forger
  using a real drawing's strokes (R7, R9) would not leave even that.
- **Limits:** a synthetic target image, 400 px; E2 shows that the checks
  accept a code-made recording, not how hard a convincing forgery of a
  real illustration is.

## 7. Evidence strength

Ratings, against the adversary of section 4 who holds the files:

- **High:** holds against that adversary, measured.
- **Medium:** catches accidental changes and some deliberate ones,
  fails against a rebuild.
- **Low:** holds only if the artist is trusted.
- **None:** the data doesn't address it.

| Evidence | Authenticity | Integrity | Completeness | Timing | Attribution | Independent verification | Resistance to fabrication | Interpretability |
|---|---|---|---|---|---|---|---|---|
| Pen strokes in the log | Low | Medium | Low | Low | None | Low | Low | Medium |
| Canvas pictures | Low | Medium | Medium | Low | None | Low | Low | High |
| Chain + checkpoints | n/a | Medium | Low | None | None | Low | Low | Medium |
| Timestamps (`t`) | Low | Medium | n/a | Low | None | None | Low | High |
| Delivered image vs last picture | Medium | Medium | n/a | None | None | Medium | Medium | High |
| Paste detection | Low | n/a | Medium | n/a | None | Low | Low | High |
| Tracing measure | Low | n/a | Low | n/a | None | Low | Low | Medium |
| Start check, session links | Low | Medium | Medium | n/a | None | Low | Low | Medium |
| Time-lapse / `.procreate` (WEAK) | Low | None | Low | None | None | None | Low | High |
| C2PA credential (dev certs) | Low | Medium | n/a | Low | Low | Low | Low | Low |

Reasons, by column:

- **Authenticity Low everywhere:** every item is written by the
  artist's machine and can be written by a program (E2). The delivered
  image check is Medium because it ties the picture to a file the
  recipient already has.
- **Integrity Medium:** committed entries and pictures can't be changed
  without the rebuild (R1-R3, R8 detected); with the rebuild they can
  (R7, R9). Delivered image: the recipient's own copy is compared, so
  it can't be rebuilt, but edits below one 32 px block pass (D2).
- **Completeness:** nothing shows that all work was recorded. Visible
  start content and missing sessions are caught (S1, S6, S7); hidden
  layers, faint marks, off-canvas work, undeclared references are not
  (S2, S3, A6).
- **Timing Low:** local clock, no outside stamp (T1, D-037); Qt point
  times use another clock and jump inside two honest strokes (STATE,
  Known broken).
- **Attribution None:** nothing names a person (I1). C2PA Low: a key
  names a signer, but a dev certificate the artist made. The C2PA row
  is reasoned from C2PA's design (the manifest is bound to the image
  bytes), not measured here: unverified.
- **Independent verification:** a third party can rerun every check,
  but only on the files the artist sends; it can't tell them from a
  rebuild. Delivered image Medium: the comparison with the recipient's
  own copy is the one link to something the artist doesn't control.
- **Resistance to fabrication Low:** E2. Delivered image Medium: to
  pass, a forger needs a recording that ends on exactly that image.
- **Interpretability:** pictures and the plain-language report read
  well; scores (tracing ratio, SSIM) need the README (STATE, Known
  broken: the report reads as confusing to its one reader).

The claims are different and the evidence reaches only the first, and
the second only under trust:

| Claim | Supported? |
|---|---|
| A recording exists and its parts agree | yes |
| The recording wasn't modified | only against someone who didn't rebuild it |
| The recording is the actual process | no |
| A particular person did it | no |
| The artwork is entirely human-made | no |
| No AI tools were used | no |

## 8. Known attack scenarios and limitations

The cheapest successful attacks, ordered by cost:

1. Don't declare the image you trace (A6): costs nothing.
2. Declare it as your own (A5): one word.
3. Rebuild the log after editing (R7, R9) or write one (R10): the
   plugin's writer is 120 lines, public, dependency-free.
4. Hide a sketch on a layer before capture (S3): unconfirmed in Krita.
5. Small edits below one 32 px block (D2, S10).

The full list of user-visible limits is README → Known limitations.

## 9. Use cases it can support

| Use case | Verdict | Why |
|---|---|---|
| Personal record of the creative process | **Useful** | pictures every 5 s, stroke timings, one report page; the snapshot hitch is the cost |
| Voluntary transparency (showing the process to an audience) | **Potentially useful, with substantial limitations** | the pictures show a process; the viewer trusts the artist for everything else |
| Showing a plausible sequence of creative activity | **Potentially useful, with substantial limitations** | it shows the recording is internally consistent; E2 shows plausibility to the tool is cheap to fake |

## 10. Use cases it cannot support

| Use case | Verdict | Why |
|---|---|---|
| Supporting a dispute or accusation | **Marginal** | a pass can't convince someone who doesn't trust the artist (T7); a fail on the artist's own files is informative, and an honest artist can show the pictures |
| Evidence for a publisher, client, competition, marketplace or certifier | **Marginal** | same; 0 recipients have said they'd accept a self-run verdict (D-051) |
| Establishing human authorship | **Not supported** | no attribution; code-made recordings pass (E2) |
| Establishing that AI was not used | **Not supported** | undeclared or `own`-declared AI references pass (E1); never labels AI (non-goal) |

## 11. Recommended changes

**Essential** (outputs now say more than the data supports):

1. The report line "Recorded live in Krita, with a pen tablet" is shown
   for a program-written recording (E2). Say what the log states, not
   what happened: e.g. "The log says it was recorded in Krita with a pen
   tablet."
2. `evaluate` prints `"policy": "P1 no generative AI"` / `"P2 human-made
   image"` with verdict "pass" on traced images whose origin was not
   declared or declared `own` (E1). Rename the policies after what they
   check, or add to every pass that reference origins are as declared
   and undeclared references weren't checked.
3. Tier "strong" comes from a `source` string any writer sets (E2).
   Rename it, or say in the README tier table and in the tier note that
   it describes what the log claims.
4. The report's first finding says "none was changed, removed or
   reordered after the checkpoints were written"; the caveat that the
   checkpoints can be rebuilt sits under "What this can't tell you".
   Put the condition in the same sentence, as D-056 did for
   "tamper-evident".

Each is a wording change; done when E2's page and JSON no longer state
the three claims and the 14 real recordings keep their results.

**High value:**

5. Confirm S3 (hidden layer) in Krita: the protocol is in SPIKE,
   "Attack review of D-046 and D-047" (already STATE Next #2).
6. Confirm P1 (script-posted tablet events) in Krita with the protocol
   below. It decides whether the log can say "pen tablet" at all.
7. Decide `evaluate`'s missing start check (S1; already STATE Next #3).
8. Keep attack scripts: the builders behind R7, R9, S6-S10 weren't
   kept, so those results can't be rerun by someone else.

**Optional:** flag recordings whose pen pressure is constant or whose
samples are perfectly regular. It would catch E2 as written, and a
forger would add noise; the security gain is speculative, so it isn't
recommended.

**Out of scope:** external timestamping (rejected, D-037; it would
date a log, not make it genuine: E2 could be stamped too); identity,
keys or accounts; a server (D-051); AI detection (non-goal); device
attestation.

**Protocol for P1** (Andrea, ~10 minutes, Krita):

1. Install the plugin as in README, "Krita plugin"; new document; start
   capture.
2. In Tools → Scripts → Scripter, run a script that posts
   `QTabletEvent`s (press, 50 moves, release) to the canvas widget
   (`QApplication.postEvent`). Claude writes it when this is picked.
3. Wait 10 s; stop capture.
4. Expected if P1 holds: one `stroke` entry with `"source": "tablet"`
   and the scripted points; `report` says "with a pen tablet". If no
   stroke appears or it is marked differently, P1 is refuted.

## 12. Conclusions

**Research readiness: RESEARCH-READY WITH LIMITATIONS.** The integrity
layer has been attacked systematically (over 60 measured runs across
four SPIKE sections, plus E1 and E2), the results are written with
their numbers, and the negative results are stated. Limits: one person,
14 recordings, one tablet, plain white canvas; real recordings are
private, so E1 and most SPIKE runs can't be rerun by an outsider; the
throwaway attack scripts are gone; S3 and P1 need a real Krita run.

What its creator can defend confidently: if the files are given
unrebuilt, edits to any committed entry, swapped pictures, a different
delivered image, and missing or reordered sessions are caught on every
tested case; and the report says plainly what it can't tell.

What its creator has to admit: the evidence is produced and held by the
person whose claim is being checked. Anyone can rebuild or write a
recording that passes, tracing of an undeclared image passes, and
nothing links the recording to a person. It can't show that a piece is
human-made, that no AI was used, or that the recording is the real
process.

# Maintainer and agent guide

Read this before changing the app. The source is GPL-3.0-only; do not vendor code,
weights or media without preserving the applicable notices and permissions.

## Product contract

The release fixes **V2** and **bbox** in the backend and exposes no selectors for
those choices. Resolution (`source` / `480p`), bbox expansion and Union strength
remain editable. Preserve the English interface, one/two-character support and
multiple source samples. Do not restore V1, pixel masks, SDPose or a grey-reference
preprocessing path as part of routine cleanup.

Keep the main README and interface focused on user actions and results. Do not
advertise fixed internal choices such as V2/bbox, model precision, sampling seed,
canvas padding or internal pipeline names. Use plain labels such as "Guidance
strength" for the Union strength setting. Preserve editable settings, output
resolution, tracking warnings, upload disclosure and paid confirmation. Keep
technical details in this guide, model setup, or collapsed advanced sections;
tracking statistics, box coordinates and job IDs should be available on demand.

Prompt generation and H3 are independent stages. No automatic chaining, paid
retries or model substitutions. New paid tests require explicit approval covering
the model, settings, number of calls and disclosed cost or unknown total. Local
regression tests must not use real provider credentials or inference endpoints.

## Architecture

| File | Responsibility |
| --- | --- |
| `app.py` | Local HTTP server; project/checkpoint state; plans, submissions and polling |
| `cloud.py` | Comfy Cloud API, credential reading/redaction, upload/download |
| `media.py` | FFmpeg preparation, geometry/padding, frames and thumbnails |
| `workflow.py`, `templates/` | Independent Gemini and H3 API graphs; fixed replacement rules |
| `sample_points.py` | Up to six source samples per role, atlases and per-view locator descriptions |
| `controls.py` | Settings validation, cache identity, isolated local worker supervision |
| `controls_worker.py` | Tracking/recovery orchestration, bbox masks, DWPose and aligned videos |
| `edge_tracker.py` | One object per EdgeTAM session |
| `tracking_review.py` | Shot segmentation, cached session stitching and review candidates |
| `text_locator.py` | Source-only Grounding DINO candidates and one-to-one A/B assignment |
| `settings.py` | Release defaults, not personal configuration |
| `web/` | Build-free English UI, crop editor and source/review timelines |
| `tests/` | Synthetic local media and mocked Cloud regressions |

Model dependencies are imported inside the worker, not at app startup. The UI
server needs only `requirements.txt`. Model setup is explicit and separate.

## State and identity invariants

- Prepared checkpoints are immutable snapshots. Jobs read their own snapshot and
  hashes; later project edits must never overwrite running job inputs.
- Role A source samples map to replacement A; role B maps to replacement B.
  Preserve this pairing regardless of gender, resemblance or screen position.
- Replacement references supply appearance. Recovery uses **source** descriptions.
  Never locate the original person using target-reference appearance.
- Track each role/shot/sample in an independent EdgeTAM session. Reuse model weights,
  not temporal/object state. Prefer a nonempty track nearest its source sample.
- Recovery prioritizes samples inside the relevant shot, then other samples. Audit
  each attempted sample even when identical detector queries share cached results.
  Exhaust available samples before leaving a region flagged for review.
- Keep valid role tracks locked while recovering another role. Maintain one-to-one
  A/B assignment; ambiguous same-person matches remain unresolved. Do not simply
  remove the overlap/conflict checks to make a test pass.
- Raw tracking cache version is 5; control output identity version is 6. Bump the relevant identity/version when
  changes could make stored masks or cached track plans unsafe to reuse.
- Sample-only edits retain clip, targets, exact prompt and existing facts under a
  new source checkpoint. Remap unchanged per-view facts by time/crop; new samples
  inherit the source summary. Recommend, but do not require, new descriptions.
- Locator edits, sample edits or changed mask geometry invalidate old approvals
  and submission plans. Strength is a sampling setting; it must reach both the
  preview graph and rebuilt upload graph without requiring different mask pixels.

## Geometry and output

All prepared clips are 24 fps. Source crops use original-display coordinates.
Preparation records the explicit source transform and padding; use those values
rather than recomputing guessed offsets. Local tracking uses a proxy at most 480
pixels high; expansion is in tracking pixels. DWPose uses original-resolution
person regions. Control videos must match the H3 canvas and aligned frame count.

Native-resolution review output must match the input dimensions after padding is
removed, rather than resizing a generated 480p result. Preserve the selected
interval, original audio and raw generation alongside the review copy. Do not
silently change an existing project's chosen resolution.

## Security and spend boundaries

- Bind to loopback only. Preserve Host/Origin checks, session tokens and
  project-relative path validation. The server is not a public hosting service.
- Never log keys or authorization headers, return keys to the browser, embed them
  in exported graphs, or forward them to signed storage URLs.
- Keep configuration, projects, models, uploads, logs and outputs out of Git.
  `config.example.json` contains placeholders only. Do not commit personal paths.
- Workers run offline with credentials removed. Never silently install packages
  or download weights to repair a missing dependency.
- Paid confirmation binds a single-use plan to exact inputs, settings and graph.
  Preserve current-control review and hash checks at plan and submission time.
- Network failures do not authorize a resubmission. Keep the Cloud job ID and
  resume polling it. A local timeout/stop is not a confirmed Cloud cancellation.
- Do not publish, push, rewrite Git history or restart an active server as a side
  effect of source cleanup. Prepare source archives independently of local data.

## Validation

Run the commands in [README.md](README.md#development) with `requirements-dev.txt`.
No weights, API key or paid calls should be needed. For a state, geometry or graph
change, include a focused regression covering its behavior and stale-state gates.
Changing a template requires checking its reachable output graph, reference order,
node links and both H3 model consumers. Preserve the full 20-step non-turbo model
chain and Union 2.0 INT8 unless a model change is explicitly requested.

When testing the interface, use an isolated `V2V_DATA_DIR` and a different port.
Never use someone's active projects as disposable fixtures. Document which checks
were mocked, which used actual local inference, and which ran on Cloud. Passing
unit tests or tracking review is not proof of accepted H3 video quality.

Before publishing a source package, inspect the committed file list and archive:
no credentials, personal paths, local project media, weights, dependency
environments or prior private Git history. Only the deliberately selected
`docs/examples/` and `docs/demos/` demo assets belong in this repository; retain
their separate licensing scope and do not treat other generated results as
publication assets. Keep README setup steps, license and third-party notices
consistent with the actual distributed files.

## Known limitations

Grounding and shot boundaries are heuristic, not face/ReID. Small characters,
cut transitions, occlusion, large bbox overlaps, missing poses and identity drift
remain possible. H3 may produce grey halos or background artifacts even with valid
masks and poses. Windows launch paths exist; Windows inference is not validated.
Do not label these limitations fixed without representative output evidence.


## Standalone public repository

This repository starts with a fresh history and contains the release source plus
`docs/examples/` and `docs/demos/`. It has no remote by default and must not import
private workspace history. Keep public setup portable; never copy a working installation's config,
API keys, model cache, project snapshots or logs into this tree. The example viewer
embeds its four video previews; its sibling GIF folders provide downloads and
Markdown previews. Building examples requires original private inputs and is not
part of app startup or CI. The app works without running the example viewer.


## Tracking warnings and explicit continuation

Missing tracking in a few frames must not permanently block a usable workflow.
After recovery tries the available samples, retain unresolved issues and empty
mask frames, export the available masks/poses/aligned video, and show a RED warning.
Always include these recommendations in that warning:

1. Add source character samples within the missing interval and retry local tracking.
2. Split the video into shorter tasks for character replacement, review each segment,
   and join the results. This is a manual recommendation, not an implemented
   automatic multi-job split/stitch feature or permission to launch more paid jobs.

Users can explicitly acknowledge the warning, approve the current control previews,
and then use the normal single-use paid submission dialog. Keep the warning visible
in that dialog. Do not silently mark issues resolved, invent masks, mark a character
absent, or automatically acknowledge anything. Bind warning acceptance to its exact
issue signature and control checkpoint; edits/new issues revoke prior acceptance.

A legacy needs_input result without complete mask/pose/source files must be prepared
again. Failed/incomplete files, changed hashes, invalid inputs, no masks anywhere,
and unusable pose output remain technical failures; a quality-warning acknowledgement
does not bypass them. Completed video jobs labelled for visual review remain usable.


## Usage recordings and user-reported prices

Maintain public recordings under `docs/demos/` and use an isolated local data folder
for recording the release UI. Never capture credentials, private config paths or
unrelated windows. The Barbie case is authorized for the walkthrough. Existing
prepared inputs, descriptions and control previews may be reused with that stated
explicitly. No actual Cloud submission is authorized for documentation recording;
stop at the review dialog and cancel. Do not imply prerecorded controls were freshly
computed or show a fabricated successful inference. Keep scratch frames/fixtures
under ignored `verification/`, with only intentional MP4/GIF documentation in Git.

Price guidance supplied by the maintainer, expressed per 7-second generation:
480p 50–70 credits, 720p approximately 281–350, 1080p untested. The 720p lower
estimate replaces the earlier 230-credit figure: the maintainer identified the
222-credit run as Barbie, whose selected duration is 5.529 seconds. Normalize
that charge as 222 × 7 / 5.529 = 281.063 credits (rounded to 281). Label it a
duration-based estimate, not a measured 7-second charge. Conversion basis: 221 credits per
US$1. Calculate USD as credits / 221, rounded to two decimal places: $0.23–0.32
and $1.27–1.58 respectively. Label this empirical per-run guidance, not live provider pricing or a
universal per-second quote; clip duration/settings matter. Do not assert that Gemini
fees are included without billing evidence. No extra paid run is needed to document
these observations. Duration was verified in the saved release studio jobs for
both resolutions: selected 6.084–13.084 seconds, review video 7.000 seconds / 168
frames at 24 fps; raw H3 output 175 frames before trimming. Local records do not
verify billed credits. Keep this distinct from the driving walkthrough's
5.529-second selected range. Never publish private project IDs or job snapshots
as documentation evidence.

# Maintainer and agent guide

Read this before changing the app. The source is GPL-3.0-only; do not vendor code,
weights or media without preserving the applicable notices and permissions.

## Product contract

The release fixes **V2** and **bbox** in the backend and exposes no selectors for
those choices. Resolution (`source` / `480p`), bbox expansion and Union strength
remain editable. Preserve the English interface, one/two-character support and
multiple source samples. Do not restore V1, pixel masks, SDPose or a grey-reference
preprocessing path as part of routine cleanup.

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
- Current tracking cache version is 5. Bump the relevant identity/version when
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
`docs/examples/` demo assets belong in this repository; retain their separate
licensing scope and do not treat other generated results as publication assets. Keep README setup steps, license and third-party notices
consistent with the actual distributed files.

## Known limitations

Grounding and shot boundaries are heuristic, not face/ReID. Small characters,
cut transitions, occlusion, large bbox overlaps, missing poses and identity drift
remain possible. H3 may produce grey halos or background artifacts even with valid
masks and poses. Windows launch paths exist; Windows inference is not validated.
Do not label these limitations fixed without representative output evidence.


## Standalone public repository

This repository starts with a fresh history and contains the release source plus
`docs/examples/`. It has no remote by default and must not import private workspace
history. Keep public setup portable; never copy a working installation's config,
API keys, model cache, project snapshots or logs into this tree. The example viewer
embeds its four video previews; its sibling GIF folders provide downloads and
Markdown previews. Building examples requires original private inputs and is not
part of app startup or CI. The app works without running the example viewer.

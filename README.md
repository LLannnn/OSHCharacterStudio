# Frame / Character Studio

Replace one or two characters in a video while keeping the source performance.
A local browser interface handles trimming, character samples, tracking review and
prompt editing. Gemini descriptions and MiniMax H3 video generation are separate,
explicitly confirmed Comfy Cloud jobs.

This is a source release, not a self-contained desktop installer. Local tracking
requires a model runtime and cached weights. A Comfy Cloud account and API key are
required for Cloud generation. **Cloud generation costs credits.**

## Features

- Thumbnail timeline for trimming a 2–15 second replacement interval.
- One or two characters, with up to six source keyframes and boxes per role.
- Labelled source-image atlases for optional Gemini descriptions.
- Independent EdgeTAM tracking sessions and source-description recovery after cuts.
- A review timeline for tracking gaps, black transitions and recovered regions.
- DWPose body motion control and expanded bounding-box redraw masks.
- Editable descriptions and prompts; sample corrections retain existing prompts.
- Original-resolution output or a 480p test mode, with source audio restored.

The release always uses **V2 + bbox**. These two choices have no selectors.
Resolution, mask expansion and Union strength remain editable.

## Examples

Selected source ranges compared with their existing generated results.
The animated divider shows source on the left and generated content on the right.

### Studio performance · 6.084–13.084 seconds

![Studio performance before and after](docs/examples/studio/comparison.gif)

### Driving scene · 5.420–10.949 seconds

![Driving scene before and after](docs/examples/driving/comparison.gif)

[Separate source/result GIFs and comparison notes](docs/examples/README.md) ·
[Interactive comparison viewer](docs/examples/comparison.html)

Download the repository and open `docs/examples/comparison.html` in a browser to
use the draggable divider, synchronized playback and frame scrubbing. The viewer
works offline. GitHub Markdown displays GIFs but does not run the slider's
JavaScript; the HTML page must be opened locally or served by a static host.
Example media are separate from the application's GPL license; see
[third-party notices](THIRD_PARTY_NOTICES.md#example-media).

## Quick start

Requirements:

- Python 3.11 or 3.12 for the app.
- [FFmpeg and FFprobe](https://ffmpeg.org/download.html) on your PATH.
- A separate local model environment, described in [Model setup](docs/model-setup.md).
- A modern browser. Node.js is only needed for contributor tests.

Download the source package and open a terminal in the extracted project folder.

### macOS

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python app.py
```

### Windows PowerShell

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe app.py
```

After setup, use `Start Mac.command` or `Start Windows.bat` to launch again.
The interface opens at **http://127.0.0.1:8765/**. Keep the server terminal open;
Ctrl+C stops the app. For a different port:

```sh
python app.py --port 8766
```

`--no-browser` starts the server without opening a tab. The app binds to localhost
only. If the page says “connection refused,” check that the server is running on
the port shown in its terminal.

## Configure once

Open **Settings**:

1. Set **Comfy key file path** to a file containing your API key. A plain key,
   JSON object (`COMFY_API_KEY`, `COMFY_CLOUD_API_KEY` or `api_key`), or environment
   assignment is accepted. Keep this file outside your public source tree.
2. Expand **Local model runtime** and set the model Python, the
   `comfyui_controlnet_aux` checkout and the `dw-ll_ucoco_384.onnx` model path.
   Extra site-packages is optional for an existing custom environment.
3. Save settings. Follow [Model setup](docs/model-setup.md) to prepare dependencies
   and cache weights before attempting tracking.

Alternatively, the server reads `COMFY_API_KEY` or `COMFY_CLOUD_API_KEY` from its
environment. Only a key file path is saved in app configuration; key contents are
not returned to the browser or included in exported workflow graphs.
[`config.example.json`](config.example.json) documents the configuration fields;
do not commit your actual `data/config.json` or key file.

## Replace characters

1. Create a project and upload a source video. Choose one or two characters.
2. Use the timeline to trim the interval. Under **Output resolution**, choose
   **Match source video** or **480p test**.
3. For each role, select a clear source frame, draw a character box and upload its
   replacement reference. Use **Add sample at playhead** for other views or shots.
4. Prepare and inspect the actual clip, source atlases and replacement images.
5. Generate descriptions in a separate Cloud job, or paste existing description
   JSON / a complete prompt. Review the prompt before continuing.
6. In step 2, check source locator descriptions. They describe the **original**
   characters, not the replacement references. Adjust mask expansion or Union
   strength if needed, then prepare masks and DWPose locally.
7. Inspect the mask and pose previews and tracking timeline. If a role is missed,
   return to step 1 and add samples or revise its locator description. Sample-only
   edits retain the prepared clip, replacement references and existing prompt;
   a notice recommends regenerating descriptions without requiring it.
8. Resolve tracking review items, then approve the current controls. Review the
   H3 graph and cost disclosure, confirm and submit once.

Prompt generation never automatically starts H3. Submissions are not retried
when the network times out. If a job has a Cloud ID, resume polling that same job
instead of submitting again. Stopping the local app does not cancel a Cloud job.

## Processing settings

| Setting | Behavior |
| --- | --- |
| Pipeline | V2: MiniMax H3 ref2va + Fun ControlNet Union 2.0 INT8 |
| Mask | Bounding box; expansion 0–64 tracking pixels, default 16 |
| Union strength | 0.1–2.0, default 1.0 |
| Resolution | Match source by default, or 480p test |
| Frame rate | 24 fps |
| Duration | 2–15 seconds |
| Sampling | Full 20 steps; seed 261662374822964 |
| Descriptions | Gemini 3.1 Flash-Lite; optional when supplying existing text |

Source-resolution mode keeps the input dimensions in the review output. H3's
internal canvas is padded to multiples of 32; the exported review copy removes
that padding, trims to the selected duration and restores source audio. Native
resolution increases Cloud work; it is not just an upscale after 480p generation.
Existing 480p preparations remain 480p until explicitly changed and prepared again.

EdgeTAM uses a proxy at most 480 pixels high. Bounding-box expansion is measured
on this proxy. DWPose reads original-resolution person regions. H3 receives the
normal source video, merged mask and pose video: no grey-out preprocessing,
SDPose or image-generation stage is used.

## Data and costs

Projects, uploads, crop images, descriptions, prompts, masks, job records and outputs
are stored under `data/` by default. Set `V2V_DATA_DIR` before launching to use another
folder. Do not run two instances against the same data folder.

Media preparation and tracking run on your computer. A confirmed description job
uploads the selected clip, source atlases and replacement references to Comfy Cloud.
A confirmed video job uploads the selected clip, replacement references, masks,
poses, aligned video and prompt. Local worker processes have provider credentials
removed from their environment.

Cloud jobs incur provider and/or GPU runtime charges. The in-app disclosure uses
a historical quote, not live pricing. Check your Comfy Cloud account for current
prices and final charges. A successful local tracking check does not guarantee
video generation quality.

## Limitations

- Tracking recovery and shot detection are heuristics, not face recognition or a
  guarantee of identity. Small people, occlusions and cuts can remain unresolved.
- Two-person bbox masks can overlap. H3 may mix identities, alter motion or leave
  halos/background artifacts even when tracking passes review.
- Very short intervals are accepted, but may be less reliable than longer clips.
- Comfy Cloud must provide the exact nodes and model names in the bundled graph;
  changes to the service can require template updates.
- macOS local inference has been tested. Windows launchers are included, but
  Windows model inference has not been validated in this project.
- The server is intended for personal localhost use, not public hosting.

The app does not bundle model weights or a user project database. The selected
comparison media in `docs/examples/` are included for demonstration separately
from the application source. Use media you have permission to process and share.

## Development

The app is plain Python, JavaScript and CSS; there is no frontend build step.
Install the lightweight test dependencies and run:

```sh
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -p 'test_*.py'
node tests/timeline.test.js
node tests/samples.test.js
node tests/descriptions.test.js
node --check web/app.js
node --check web/timeline.js
node --check web/review-timeline.js
git diff --check
```

Tests use synthetic media and mocked Cloud clients. They need no API key or model
weights and make no paid inference calls. FFmpeg-dependent tests require FFmpeg.
The GitHub Actions workflow runs these checks on Linux; it is not a Windows/GPU
inference certification.

See [agent.md](agent.md) for architecture, state invariants, validation requirements
and known constraints. Keep fixes focused and include a regression for changes to
tracking assignment, source samples or submission integrity.

## License and source distribution

Application source is licensed under **GPL-3.0-only**. See [LICENSE](LICENSE).
Third-party libraries, model weights and hosted services retain their own licenses
and terms; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). They are not bundled
or relicensed by this project.

To create a source archive from a reviewed commit:

```sh
git archive --format=zip --prefix=frame-character-studio/ --output=../frame-character-studio-source.zip HEAD
```

This includes committed source and the selected documentation examples, with no
`.git` history, ignored local data, credentials or cached weights. For an existing private development repository,
start the public repository from the reviewed source package rather than pushing
old branches/history. Inspect the archive before publishing. No remote repository
or publication is configured by the app.

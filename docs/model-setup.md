# Model and Cloud setup

Follow this after installing the app from the [README](../README.md#quick-start).
Installing `requirements.txt` alone starts the interface; it does not install the
tracking models or prepare your Comfy Cloud model library.

| Runs on your computer | Runs in Comfy Cloud |
| --- | --- |
| Video trimming, character samples, EdgeTAM tracking, Grounding DINO recovery and DWPose skeleton extraction | Gemini descriptions and MiniMax H3 video generation |
| Requires a local Python/model environment and downloaded weights | Requires a Comfy API key, credits, supported nodes and the required Cloud weights |

You do not need a local ComfyUI server or local MiniMax weights. You do need internet
access to GitHub, PyPI/PyTorch and Hugging Face for initial local setup, and to Comfy
Cloud for generation. Leave enough disk space for dependencies, weights and videos.

## 1. Prepare the local model environment

For a fresh installation, use the app's `.venv` created in the README. Activate it
before running the remaining local setup commands from the repository folder.

macOS:

```sh
source .venv/bin/activate
python --version
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
python --version
```

Use Python 3.11 or 3.12. If PowerShell does not allow activation, use
`.\.venv\Scripts\python.exe` instead of `python` in the commands below. You can also
reuse an existing compatible model environment and point Settings to its Python.

Install **PyTorch and Torchvision** for your operating system and hardware using the
[official PyTorch installer](https://pytorch.org/get-started/locally/). Choose a CUDA
build for a supported NVIDIA GPU; macOS uses its supported Apple Silicon/CPU build.
The observed working environment used Torch 2.9.1 and Torchvision 0.24.1. Windows
model inference has not been validated in this project.

Fetch the pose preprocessor and install its dependencies into this same environment:

```sh
git clone https://github.com/Fannovel16/comfyui_controlnet_aux.git models/comfyui_controlnet_aux
git -C models/comfyui_controlnet_aux checkout 59b1fc411ede8623b2997855b8018f0b3b6cf49f
python -m pip install -r models/comfyui_controlnet_aux/requirements.txt
```

Then install the version of Transformers used by the worker, along with its local
support packages:

```sh
python -m pip install "transformers==5.17.0" "numpy>=1.26,<3" "Pillow>=10.1,<13" huggingface_hub
```

The preprocessor dependencies supply OpenCV and other pose dependencies. Its full
requirements also cover preprocessors this app does not use. Review their platform
requirements and [third-party notices](../THIRD_PARTY_NOTICES.md#pose-rendering-notice).
The observed working environment used NumPy 2.2.6 and Pillow 12.3.0; these observations
are not a cross-platform lockfile.

DWPose uses the supplied character boxes, so no separate DWPose person-detector
weights are needed. Its ONNX pose model can use ONNX Runtime if installed, with
OpenCV as the upstream-supported fallback.

## 2. Download local models

Run these commands with the **same Python environment** you will select in Settings.
They download weights without running inference or submitting Cloud jobs. Normal
tracking workers load cached files offline and do not download missing models.

### EdgeTAM: character tracking

```sh
python -c "from huggingface_hub import snapshot_download; snapshot_download('yonigozlan/EdgeTAM-hf', revision='c266ce53b3fc00f0f495b583f6a116c4e57f53bb', allow_patterns=['*.json', 'model.safetensors'])"
```

### Grounding DINO: finding characters after cuts or tracking loss

From this repository's root:

```sh
python text_locator.py --download
```

This caches `IDEA-Research/grounding-dino-tiny` at revision
`a2bb814dd30d776dcf7e30523b00659f4f141c71`.

### DWPose: skeleton extraction

Download the ONNX file from the [upstream DWPose repository](https://huggingface.co/yzd-v/DWPose/tree/main):

```sh
python -c "from huggingface_hub import hf_hub_download; print(hf_hub_download('yzd-v/DWPose', 'dw-ll_ucoco_384.onnx', local_dir='models'))"
```

This places the file at `models/dw-ll_ucoco_384.onnx`. The EdgeTAM and Grounding DINO
commands use the Hugging Face cache instead. Use the same OS user and cache location
when downloading and running the app; another user's cached weights are not enough.

## 3. Configure the app

Create a [Comfy API key](https://platform.comfy.org/profile/api-keys) for the same
account that will hold the Cloud imports below. Save it in a local text file outside
the public source tree. In **Settings**, set **Comfy key file path** to that file.
The same Comfy key is used for description and video jobs; this app does not require
a separate Google Gemini API key.

Under **Settings → Local model runtime**, set these **absolute paths**:

| Field | Fresh-install value |
| --- | --- |
| Model runtime Python | `.venv/bin/python` on macOS or `.venv\Scripts\python.exe` on Windows, inside your project folder |
| `comfyui_controlnet_aux` directory | `models/comfyui_controlnet_aux` inside your project folder |
| DWPose model | `models/dw-ll_ucoco_384.onnx` inside your project folder |
| Extra Python site-packages | Leave blank for the environment installed above |

If reusing another environment or preprocessor checkout, select its actual paths
instead. Save settings. The key indicator checks configuration presence; it does
not prove Cloud authentication, available credits or model availability.

EdgeTAM chooses CUDA, MPS or CPU according to availability. Unsupported MPS tracking
operations fall back to CPU; CPU processing can be slow. Grounding DINO currently
runs on CPU. Changing output resolution does not increase the tracking proxy beyond
480 pixels high.

## Comfy Cloud prerequisites

### Account and nodes

Use the same Comfy Cloud account for your API key and model library, and ensure it
has credits for generation. If required weights are missing, importing them requires
**Creator or higher** under the [current Cloud import requirements](https://docs.comfy.org/cloud/import-models).
Importing models is separate from configuring the API key.

Cloud must support the nodes in the bundled graphs, including
`MiniMaxH3ReferenceToVideo`, `MiniMaxH3FunControlNetApply`, `GeminiNodeV3` and
`SaveText`. **You import model weights, not node implementations.** Importing a
weight file does not fix a missing-node error. This app neither installs Cloud
nodes nor imports models automatically.

### Required MiniMax model files

Check all six entries below in your Cloud library. Some may already be provided by
Comfy Cloud; import only what is missing. These filenames were checked against the
current video graph and the [official Comfy-Org/MiniMax-H3 repository](https://huggingface.co/Comfy-Org/MiniMax-H3).
The links below are direct download links suitable for Cloud import.

| Cloud folder | Required file / import link |
| --- | --- |
| `diffusion_models` | [minimax_h3_ref2va_pruned_int8_convrot.safetensors](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors) |
| `text_encoders` | [qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors) |
| `vae` | [minimax_h3_video_vae_int8_convrot.safetensors](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/vae/minimax_h3_video_vae_int8_convrot.safetensors) |
| `vae` | [minimax_h3_audio_vae_fp32.safetensors](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/vae/minimax_h3_audio_vae_fp32.safetensors) |
| `loras` | [minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/loras/minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors) |
| `model_patches` | [minimax_h3_fun_controlnet_union_2.0_pruned_int8_convrot.safetensors](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/model_patches/minimax_h3_fun_controlnet_union_2.0_pruned_int8_convrot.safetensors) |

The LoRA entry is referenced by the current graph and belongs in this checklist;
use the bundled sampling settings. The Union patch must be **2.0**. The older file
without `_2.0_` in its name is a different model.

### Import missing models

1. Open [Comfy Cloud](https://cloud.comfy.org/) with the account used by your API key.
2. Open **Models → Model Library → Import**.
3. Copy the required model's link from the table above and paste it into Import.
4. Choose the corresponding model type and target folder from the table. For Union
   2.0, select **`model_patches`**, not `controlnet` or `diffusion_models`.
5. Wait for the download to finish, then check **My Models** and the loader's model
   selector. Repeat for other missing files.

See [Comfy's illustrated import guide](https://docs.comfy.org/cloud/import-models).
For Union 2.0, this release's loader expects the imported Cloud identifier:

```text
Comfy-Org__MiniMax-H3__minimax_h3_fun_controlnet_union_2.0_pruned_int8_convrot.safetensors
```

Other loader names match the filenames in the table. Cloud may assign a different
identifier to an imported file. If it does, the corresponding loader input in
`templates/h3-union20.api.json` must match the actual Cloud selector after verifying
that it is the same weight file. Do not substitute an unrelated model or the older
Union patch to bypass validation.

You do not need to open or run the bundled workflow manually in Cloud. The app
submits it after your review and confirmation. The template files are API graphs,
not editor `.ui.json` documents. Imported Cloud weights stay in Cloud; placing
MiniMax files in this app's local `models/` folder does not make them available there.

## 4. Check the installation

With the model environment active, check the local executables and Python imports:

```sh
ffmpeg -version
ffprobe -version
python -c "import torch, torchvision, cv2, numpy, PIL; from transformers import EdgeTamVideoModel, Sam2VideoProcessor, AutoModelForZeroShotObjectDetection; print('Tracking imports ready')"
python -c "import sys; sys.path.insert(0, 'models/comfyui_controlnet_aux/src'); from custom_controlnet_aux.dwpose.wholebody import Wholebody; from custom_controlnet_aux.dwpose import draw_poses; from custom_controlnet_aux.dwpose.types import Keypoint, BodyResult, PoseResult; print('Pose imports ready')"
```

Use your actual preprocessor path in the last command if it differs. These checks
import code only; they do not run models or submit a generation. Also confirm that
all three local model downloads completed and that all six Cloud model identifiers
are available. No GPU-memory minimum or clean Windows inference run has been
established for this release.

Then create a project, prepare inputs and use **Prepare tracking and motion previews**.
Play the mask and skeleton previews and check the timeline before approving them.
A Cloud generation should be a separate, deliberate submission after local review.

## Troubleshooting

- **FFmpeg/FFprobe not found:** install both and put their executables on PATH before
  starting the app; restart the server terminal after changing PATH.
- **Missing EdgeTamVideoModel:** check the configured Python and Transformers version.
- **Cannot find cached files / offline mode:** download the pinned revisions with
  the same user and environment; the worker does not download on demand.
- **Cannot import custom_controlnet_aux:** select the checkout root in Settings,
  not its `src` directory, and install dependencies in the configured environment.
- **Pose model cannot load:** check the ONNX path and OpenCV/ONNX Runtime setup.
- **Cloud model missing / invalid model name:** check the exact loader
  names, completed imports, target folders and API-key account. In particular,
  verify Union 2.0 in `model_patches`; a local model download cannot fix Cloud lookup.
- **Cloud node missing:** the Cloud runtime must support the named node. Importing
  weights or installing a node on your local computer does not add it to Cloud.
- **Tracking needs review:** add source samples in the marked shot or revise the
  original character description. You can split into shorter replacement tasks,
  or explicitly accept the red warning when complete previews are available.
- **A submitted Cloud job times out:** retain its Cloud ID and resume polling that
  job. Do not submit another generation just to check whether the first succeeded.

No models or third-party preprocessor code are redistributed here. See
[third-party notices](../THIRD_PARTY_NOTICES.md) for their separate terms.

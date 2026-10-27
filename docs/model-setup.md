# Local model setup

The browser app and model worker can use different Python environments. Installing
`requirements.txt` starts the interface; it does not install Torch or download
models. You can reuse an existing working model environment.

## 1. Create or select a model environment

Use Python 3.11 or 3.12 and install Torch / Torchvision for your hardware using the
[official PyTorch instructions](https://pytorch.org/get-started/locally/).
The local development environment used Torch 2.9.1, Torchvision 0.24.1,
Transformers 5.17.0, NumPy 2.2.6 and Pillow 12.3.0. These are observed working
versions, not a cross-platform lockfile or a guarantee for every device.

In that environment, install the Transformers version with EdgeTAM support:

```sh
python -m pip install "transformers==5.17.0" "numpy>=1.26,<3" "Pillow>=10.1,<13" huggingface_hub
```

Fetch the external pose preprocessor checkout separately. The following revision
matches the API used by this release:

```sh
git clone https://github.com/Fannovel16/comfyui_controlnet_aux.git models/comfyui_controlnet_aux
git -C models/comfyui_controlnet_aux checkout 59b1fc411ede8623b2997855b8018f0b3b6cf49f
python -m pip install -r models/comfyui_controlnet_aux/requirements.txt
```

Review that project's dependency and licensing requirements before installation.
Its requirements cover more preprocessors than this app uses. Keep this checkout
and its dependencies local; they are not included in the source distribution.
If you use an existing checkout, it must expose `Wholebody.format_result`,
`draw_poses`, `PoseResult`, `BodyResult` and `Keypoint` from its DWPose package.

DWPose uses the supplied tracked boxes; no separate person-detector weights are
needed. Its ONNX pose model can use ONNX Runtime if installed, with OpenCV as an
upstream-supported fallback. Platform-specific Torch/ONNX installations may need
additional setup.

## 2. Explicitly cache weights

Run these commands with the **model** Python. They download files and do not submit
Cloud jobs or run inference. Normal app workers use cached-only, offline loading.

Cache EdgeTAM:

```sh
python -c "from huggingface_hub import snapshot_download; snapshot_download('yonigozlan/EdgeTAM-hf', revision='c266ce53b3fc00f0f495b583f6a116c4e57f53bb', allow_patterns=['*.json', 'model.safetensors'])"
```

Cache the pinned source-description locator from the app folder:

```sh
python text_locator.py --download
```

This prepares `IDEA-Research/grounding-dino-tiny` at revision
`a2bb814dd30d776dcf7e30523b00659f4f141c71`.

Download `dw-ll_ucoco_384.onnx` from the
[upstream DWPose model repository](https://huggingface.co/yzd-v/DWPose/tree/main)
and save it somewhere local, for example `models/dw-ll_ucoco_384.onnx`.
Use the same account and model-cache location when downloading and running the
worker. Models cached under a different home directory will not be found.

## 3. Configure the app

Under **Settings → Local model runtime**, set:

| Field | Path |
| --- | --- |
| Model runtime Python | The executable of the model environment, such as `.venv-model/bin/python` on macOS or `.venv-model\Scripts\python.exe` on Windows |
| `comfyui_controlnet_aux` directory | The checkout root containing `src/custom_controlnet_aux/dwpose` |
| DWPose model | The downloaded `dw-ll_ucoco_384.onnx` file |
| Extra Python site-packages | Leave blank unless reusing a custom dependency directory |

Use absolute paths in Settings. The default portable layout looks under
`models/` and the app's `.venv/`; explicit settings take precedence. Save settings
and prepare local masks from an input checkpoint.

EdgeTAM chooses CUDA, MPS or CPU according to availability. Unsupported MPS
tracking operations are retried on CPU by the existing local worker; CPU operation
can be slow. Grounding DINO currently runs on CPU. Changing the upload resolution
does not change the 480-high maximum tracking proxy.

## Comfy Cloud prerequisites

Local ComfyUI is not required for H3 submission. The Cloud instance must provide
the exact nodes and models used by `templates/h3-union20.api.json`, including
`MiniMaxH3FunControlNetApply`, the H3 ref2va chain and Union patch:

`Comfy-Org__MiniMax-H3__minimax_h3_fun_controlnet_union_2.0_pruned_int8_convrot.safetensors`

The app preserves the full non-turbo 20-step chain, INT8 video VAE and both patched
model consumers. Gemini uses `GeminiNodeV3`; text output uses `SaveText`. Templates
are API graphs used by the app, not editor `.ui.json` documents. Availability in a
particular Cloud account must be checked when submitting. Do not rename/swap a
model just to bypass validation.

## Troubleshooting

- **Missing EdgeTamVideoModel:** check which Python executable is configured and
  whether that environment has the required Transformers version.
- **Cannot find cached files / offline mode:** explicitly cache the pinned revision
  with the same user/environment. The app deliberately does not download on demand.
- **Cannot import custom_controlnet_aux:** select the checkout root, not its `src`
  directory; install dependencies in the model environment.
- **Pose model cannot load:** check the ONNX path and OpenCV/ONNX Runtime setup.
- **Tracking needs review:** inspect the timeline, add source samples in that shot
  or revise source-only descriptions. You can also split into shorter replacement
  tasks. When complete controls are available, explicitly accept the red warning
  to continue anyway. This is not necessarily a setup problem.
- **Cloud validation error:** inspect the exported graph and exact model names.
  Retain the job ID; do not automatically submit a replacement job.

No models or third-party preprocessor code are redistributed here. See
[third-party notices](../THIRD_PARTY_NOTICES.md) for their separate terms.

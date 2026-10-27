# Third-party components

Application source is GPL-3.0-only; see [LICENSE](LICENSE). This license does not
relicense external packages, model weights, media, or Cloud services. External inference
packages and weights are installed, downloaded or accessed separately and are not
bundled. The selected demonstration media have a separate scope described below. Preserve upstream notices when distributing any of those components.

| Component | Source / terms |
| --- | --- |
| HTTPX | [Project](https://github.com/encode/httpx), BSD-3-Clause |
| Pillow | [Project and license](https://github.com/python-pillow/Pillow/blob/main/LICENSE), HPND |
| NumPy (tests and model worker) | [Project and license](https://github.com/numpy/numpy/blob/main/LICENSE.txt), BSD-3-Clause |
| PyTorch / Torchvision | [PyTorch](https://github.com/pytorch/pytorch), [Torchvision](https://github.com/pytorch/vision); see their license files and bundled notices |
| Transformers / Hugging Face Hub | [Transformers](https://github.com/huggingface/transformers), [Hub](https://github.com/huggingface/huggingface_hub), Apache-2.0 |
| OpenCV | [Project](https://github.com/opencv/opencv); see the installed version's license and third-party notices |
| FFmpeg / FFprobe | [FFmpeg licensing](https://ffmpeg.org/legal.html); terms vary with the build and enabled libraries |
| EdgeTAM converted weights | [yonigozlan/EdgeTAM-hf](https://huggingface.co/yonigozlan/EdgeTAM-hf), model card declares Apache-2.0 |
| Grounding DINO converted weights | [IDEA-Research/grounding-dino-tiny](https://huggingface.co/IDEA-Research/grounding-dino-tiny), model card declares Apache-2.0 |
| DWPose ONNX weights | [yzd-v/DWPose](https://huggingface.co/yzd-v/DWPose), repository declares Apache-2.0 |
| Pose preprocessor code | [comfyui_controlnet_aux](https://github.com/Fannovel16/comfyui_controlnet_aux); contains components with separate upstream notices |
| ComfyUI workflow node definitions | [ComfyUI](https://github.com/Comfy-Org/ComfyUI), GPL-3.0; the bundled files are API graph descriptions, not vendored node implementations |
| MiniMax H3 models | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3), MiniMax H3 Community License Agreement; consult the exact model repositories as well |
| Gemini and Comfy Cloud | Hosted services with their own account, usage and billing terms; not part of the application's GPL grant |

## Pose rendering notice

The configured `comfyui_controlnet_aux` DWPose package includes OpenPose-derived
rendering code. Its pinned
[package header](https://github.com/Fannovel16/comfyui_controlnet_aux/blob/59b1fc411ede8623b2997855b8018f0b3b6cf49f/src/custom_controlnet_aux/dwpose/__init__.py)
contains a CMU non-commercial-use notice. The DWPose weight repository's license
alone does not describe every imported rendering component. Review the relevant
upstream permissions for your use or redistribution; this source release makes
no blanket commercial-use grant for the complete external inference stack.

## What is distributed

The project distributes its Python/JavaScript/CSS/HTML application, prompt and API
graph templates, documentation, launchers, regression tests, selected comparison
examples and website walkthrough recordings. It does not bundle model weights,
FFmpeg binaries, Python environments, preprocessor checkouts or a user project
database. No third-party fonts or frontend libraries are bundled.

## Example media

`docs/examples/` contains selected input-range GIFs, generated-result GIFs, animated
comparison wipes, and an HTML viewer with embedded short video previews. They are
included separately from the GPL application source. `docs/demos/` contains GIF/MP4
recordings of the website using the selected driving scene and reference images;
the same media scope applies to the content visible within these recordings.
The application's GPL grant
does not grant rights in the underlying source footage, depicted people, reference
images or generated media. Existing rights and applicable permissions remain with
their respective holders. No separate reusable-media license is asserted here.

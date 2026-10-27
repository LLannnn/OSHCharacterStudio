"""Local control preparation supervisor. Never submits a Cloud job."""
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys

from media import binary, hash_file
from settings import GENERATION

ROOT = Path(__file__).resolve().parent
UNION_MODEL = 'Comfy-Org__MiniMax-H3__minimax_h3_fun_controlnet_union_2.0_pruned_int8_convrot.safetensors'
EDGE_MODEL = 'yonigozlan/EdgeTAM-hf'
EDGE_REVISION = 'c266ce53b3fc00f0f495b583f6a116c4e57f53bb'


def runtime(config):
    # Existing environments may be reused; never install into them automatically.
    model_root = ROOT / 'models'
    python = ROOT / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    defaults = {
        'control_python': python,
        'control_aux': model_root / 'comfyui_controlnet_aux',
        'control_pose_model': model_root / 'dw-ll_ucoco_384.onnx',
        'control_extra_site': model_root / 'site-packages',
    }
    result = {}
    for key, fallback in defaults.items():
        value = config.get(key) or os.environ.get('V2V_' + key.upper())
        result[key] = str(Path(value).expanduser()) if value else (str(fallback) if fallback.exists() else '')
    if not result['control_python']:
        result['control_python'] = sys.executable
    result['paths_ready'] = bool(
        result['control_python'] and Path(result['control_python']).is_file() and
        result['control_aux'] and (Path(result['control_aux']) / 'src/custom_controlnet_aux/dwpose').is_dir() and
        result['control_pose_model'] and Path(result['control_pose_model']).is_file()
    )
    return result


def options(data):
    for key in ('generation_version', 'mask_mode'):
        if key in data and data[key] != GENERATION[key]:
            raise ValueError('This release uses V2 with bbox masks.')
    grow, strength = data.get('grow_px', 16), data.get('strength', 1.0)
    if type(grow) is not int or not 0 <= grow <= 64:
        raise ValueError('Mask expansion must be an integer from 0 to 64 tracking pixels.')
    if isinstance(strength, bool) or not isinstance(strength, (float, int)) or not math.isfinite(strength) or not .1 <= strength <= 2:
        raise ValueError('Union strength must be between 0.1 and 2.')
    return dict(GENERATION, grow_px=grow, strength=float(strength))


def identity(checkpoint, settings, review=None):
    # Strength affects sampling, not locally prepared pixels.
    value = {'checkpoint': checkpoint, 'mode': settings['mask_mode'], 'grow': settings['grow_px'],
             'edge_model': EDGE_MODEL, 'edge_revision': EDGE_REVISION, 'worker_version': 5,
             'review': review or {'anchors': [], 'decisions': [], 'ignored_boundaries': []}}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def execute(request, folder, config):
    env = os.environ.copy()
    # Local subprocess gets no Comfy/provider credential or download permission.
    for key in list(env):
        if any(x in key.upper() for x in ('API_KEY', 'TOKEN', 'SECRET')):
            env.pop(key)
    env.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', PYTHONUNBUFFERED='1')
    request.update(runtime=runtime(config), ffmpeg=binary('ffmpeg'))
    path = folder / 'request.json'
    path.write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding='utf-8')
    with (folder / 'local-worker.log').open('w') as log:
        result = subprocess.run([request['runtime']['control_python'], str(ROOT / 'controls_worker.py'), str(path)],
                                env=env, stdout=log, stderr=log, timeout=3600,
                                creationflags=0x08000000 if os.name == 'nt' else 0)
    if result.returncode:
        failure = folder / 'failure.json'
        detail = json.loads(failure.read_text()).get('error', '') if failure.exists() else ''
        raise ValueError(detail or 'Local control preparation failed. Check the Python runtime, DWPose paths and cached EdgeTAM dependencies in Settings.')
    manifest = json.loads((folder / 'manifest.json').read_text())
    for role, name in manifest['files'].items():
        file = (folder / name).resolve()
        if not file.is_relative_to(folder.resolve()) or not file.is_file() or hash_file(file) != manifest['hashes'][role]:
            raise ValueError('Control file integrity check failed.')
    return manifest

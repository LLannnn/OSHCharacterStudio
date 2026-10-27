"""Local media operations. No cloud or model calls."""
import hashlib
import json
import os
import shutil
import subprocess
import math
from pathlib import Path
from PIL import Image, ImageOps
from workflow import finite


def binary(name):
    configured = os.environ.get('V2V_' + name.upper())
    candidates = [configured, shutil.which(name), str(Path(__file__).parent / 'bin' /
                  (name + ('.exe' if os.name == 'nt' else '')))]
    if os.name != 'nt':
        candidates += ['/opt/homebrew/bin/' + name]
    for item in candidates:
        if item and Path(item).is_file():
            return item
    raise ValueError('Cannot find ' + name + '. Install FFmpeg or set V2V_' + name.upper())


def run(args, timeout=120):
    process = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             timeout=timeout, creationflags=0x08000000 if os.name == 'nt' else 0)
    if process.returncode:
        # ffmpeg output can contain paths; no credentials are passed to it.
        raise ValueError('Media processing failed: ' + process.stderr.decode(errors='replace')[-1200:])
    return process.stdout


def probe(path):
    data = json.loads(run([binary('ffprobe'), '-v', 'error', '-show_format',
                           '-show_streams', '-of', 'json', str(path)]))
    stream = next((s for s in data['streams'] if s['codec_type'] == 'video'), None)
    if not stream:
        raise ValueError('The file has no video stream.')
    a, b = stream.get('avg_frame_rate', '0/1').split('/')
    duration = float(data['format'].get('duration', stream.get('duration', 0)))
    if not duration > 0:
        raise ValueError('Could not read the video duration.')
    rotation = next((item.get('rotation', 0) for item in stream.get('side_data_list', [])
                     if 'rotation' in item), stream.get('tags', {}).get('rotate', 0))
    width, height = stream['width'], stream['height']
    if round(float(rotation)) % 180 == 90:
        width, height = height, width  # Match FFmpeg's automatic display rotation.
    return dict(width=width, height=height, duration=duration,
                fps=float(a) / float(b) if float(b) else 0,
                has_audio=any(s['codec_type'] == 'audio' for s in data['streams']))


def frame(path, seconds, dest, duration):
    seconds = finite(seconds, 'Keyframe time')
    if not 0 <= seconds < duration:
        raise ValueError('Keyframe time is outside the video.')
    run([binary('ffmpeg'), '-v', 'error', '-y', '-ss', str(seconds), '-i', str(path),
         '-frames:v', '1', '-q:v', '2', str(dest)])
    with Image.open(dest) as image:
        return dict(width=image.width, height=image.height)


def normalized_image(source, dest):
    with Image.open(source) as image:
        image = ImageOps.exif_transpose(image).convert('RGB')
        image.save(dest)
        return dict(width=image.width, height=image.height)


def crop(source, region, dest):
    with Image.open(source) as image:
        if not isinstance(region, dict):
            raise ValueError('Draw a source character box first.')
        x, y, w, h = [round(finite(region.get(k, 0), k)) for k in ['x', 'y', 'width', 'height']]
        if min(x, y) < 0 or min(w, h) < 8 or x + w > image.width or y + h > image.height:
            raise ValueError('The character box must be inside the image and at least 8×8 pixels.')
        image.crop((x, y, x + w, y + h)).convert('RGB').save(dest)
        return dict(x=x, y=y, width=w, height=h)


def prepare_clip(source, start, end, dest, info, resolution='480p'):
    start, end = finite(start, 'Start time'), finite(end, 'End time')
    length = round(end - start, 6)
    if not 0 <= start < end <= info['duration'] + 0.001 or not 2 <= length <= 15:
        raise ValueError('Select a 2–15 second range within the video.')
    if resolution not in ('source', '480p'):
        raise ValueError('Resolution must be source or 480p')
    if resolution == 'source':
        ow, oh = info['width'], info['height']
        cw, ch = math.ceil(ow / 32) * 32, math.ceil(oh / 32) * 32
        x, y = (cw - ow) // 4 * 2, (ch - oh) // 4 * 2
        filters = f'fps=24,pad={cw}:{ch}:{x}:{y}:black,setsar=1'
    else:
        ow, oh = int(math.floor(info['width'] * 480 / info['height'] / 2 + .5)) * 2, 480
        cw, ch = math.ceil(ow / 32) * 32, 480
        x, y = (cw - ow) // 4 * 2, 0
        filters = ('fps=24,scale=-2:480,pad=ceil(iw/32)*32:480:(ow-iw)/2:0:black,setsar=1')
    run([binary('ffmpeg'), '-v', 'error', '-y', '-ss', str(start), '-i', str(source),
         '-t', str(length), '-map', '0:v:0', '-map', '0:a?', '-vf', filters,
         '-c:v', 'libx264', '-preset', 'fast', '-crf', '18', '-pix_fmt', 'yuv420p',
         '-c:a', 'aac', '-movflags', '+faststart', str(dest)])
    result = probe(dest)
    result['output'] = dict(width=ow, height=oh, x=x, y=y)
    result['source_transform'] = dict(scale_x=ow/info['width'], scale_y=oh/info['height'], x=x, y=y)
    result['resolution'] = resolution
    return result


def hash_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def thumbnail_times(duration, count=None, fps=24):
    duration = finite(duration, 'Video duration')
    if duration <= 0:
        raise ValueError('Video duration must be greater than zero.')
    count = max(8, min(24, math.ceil(duration / .75) + 1)) if count is None else max(2, min(24, int(count)))
    # Seek inside the last video frame rather than at the exclusive EOF timestamp.
    last = max(0, duration - max(.15, 1 / max(1, float(fps))))
    return [round(last * i / (count - 1), 6) for i in range(count)]


def thumbnail(source, seconds, dest):
    run([binary('ffmpeg'), '-v', 'error', '-y', '-ss', str(seconds), '-i', str(source),
         '-frames:v', '1', '-vf', 'scale=192:-2', '-threads', '1', '-q:v', '5',
         '-an', str(dest)], timeout=45)
    with Image.open(dest) as image:
        return dict(width=image.width, height=image.height)

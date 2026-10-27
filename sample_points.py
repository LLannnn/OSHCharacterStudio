"""Character sample validation and labelled Gemini atlases. No model calls."""
import math
from PIL import Image, ImageDraw, ImageOps, ImageFont

MAX_SAMPLES = 6


def points(selection):
    """Legacy single selections remain valid; samples, when present, are authoritative."""
    values = selection.get('samples')
    return values if values is not None else [selection]


def normalize(selection, duration, start=None, end=None, require_crop=False):
    if not isinstance(selection, dict):
        raise ValueError('Each character needs a source sample.')
    values = points(selection)
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_SAMPLES:
        raise ValueError(f'Choose 1–{MAX_SAMPLES} samples per character.')
    result = []
    for value in values:
        if not isinstance(value, dict):
            raise ValueError('Invalid character sample.')
        seconds = float(value.get('time', 0))
        if not math.isfinite(seconds) or not 0 <= seconds < duration:
            raise ValueError('A sample time is outside the source video.')
        if start is not None and not start <= seconds < end:
            raise ValueError('All character samples must be inside the selected timeline range.')
        region = value.get('crop')
        if require_crop and not isinstance(region, dict):
            raise ValueError('Draw a box for every character sample.')
        item = dict(time=seconds, crop=region)
        if value.get('frame_file'):
            item['frame_file'] = value['frame_file']
        result.append(item)
    return dict(result[0], **({'samples': result} if 'samples' in selection or len(result) > 1 else {}))


def content(selection):
    """Frame cache file names are not part of selection equality."""
    return [dict(time=p['time'], crop=p.get('crop')) for p in points(selection)]


def counts(checkpoint):
    return {k: len(points(v)) for k, v in checkpoint.get('selections', {}).items()}


def atlas(images, times, label, dest):
    """Separate cells, stable IDs and source times; never mix A/B or target images."""
    cell, header = 512, 32
    columns = min(2, len(images))
    rows = math.ceil(len(images) / columns)
    sheet = Image.new('RGB', (columns * cell, rows * (cell + header)), '#111a16')
    draw = ImageDraw.Draw(sheet)
    for i, (path, seconds) in enumerate(zip(images, times)):
        x, y = (i % columns) * cell, (i // columns) * (cell + header)
        draw.text((x + 12, y + 6), f'SOURCE {label}{i+1} | {seconds:.3f}s', fill='white', font=ImageFont.load_default(size=18))
        with Image.open(path) as image:
            tile = ImageOps.contain(image.convert('RGB'), (cell - 16, cell - 16))
            sheet.paste(tile, (x + (cell-tile.width)//2, y + header + (cell-tile.height)//2))
    sheet.save(dest)
    return dict(columns=columns, rows=rows, sample_ids=[f'{label}{i+1}' for i in range(len(images))])


def locator_samples(checkpoint, facts, custom=None):
    custom = custom or {}
    result = {}
    for label, selection in checkpoint.get('selections', {}).items():
        descriptions = facts.get('source_samples_' + label, [])
        result[label] = []
        for i, sample in enumerate(points(selection)):
            text = custom.get(label, '').strip() or (descriptions[i] if i < len(descriptions) else '') or facts.get('source_' + label, '')
            result[label].append(dict(sample_id=f'{label}{i+1}', time=sample.get('time', checkpoint['start']),
                                     frame=round((sample.get('time', checkpoint['start'])-checkpoint['start'])*24), description=text.strip()))
    return result

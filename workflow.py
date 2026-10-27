"""Two independent graphs; facts and replacement rules stay separate."""
import copy
import json
import math
import re
from pathlib import Path

TEMPLATES = Path(__file__).parent / 'templates'
FIELDS = ['source_A', 'target_A', 'source_B', 'target_B', 'remove_A', 'remove_B',
          'actions_A', 'actions_B', 'interaction', 'scene', 'shot_structure']
SINGLE_FIELDS = ['source_A', 'target_A', 'remove_A', 'actions_A', 'interaction', 'scene', 'shot_structure']


def character_count(value=2):
    if type(value) is not int or value not in (1, 2):
        raise ValueError('Choose one or two characters.')
    return value


def active_labels(count=2):
    return ['A'] if character_count(count) == 1 else ['A', 'B']


def fact_fields(count=2):
    return SINGLE_FIELDS if character_count(count) == 1 else FIELDS


def parse_facts(value, count=2, sample_counts=None):
    fields = fact_fields(count)
    if isinstance(value, str):
        value = value.strip()
        value = re.sub(r'^```(?:json)?\s*|\s*```$', '', value).strip()
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ValueError('Descriptions must be a JSON object.')
    missing = [k for k in fields if not isinstance(value.get(k), str)
               or not value[k].strip() or 'UNCONFIRMED' in value[k].upper()]
    if missing:
        raise ValueError('Check description fields: ' + ', '.join(missing))
    result = {k: value[k].strip() for k in fields}
    for label in active_labels(count):
        key = 'source_samples_' + label
        expected = (sample_counts or {}).get(label, 1)
        entries = value.get(key)
        if entries is None and expected <= 1:
            continue  # Old saved single-sample facts are still valid.
        if not isinstance(entries, list) or not 1 <= len(entries) <= 6 or any(
                not isinstance(v, str) or not v.strip() or 'UNCONFIRMED' in v.upper() for v in entries):
            raise ValueError('Review the individual source sample descriptions: ' + key)
        if sample_counts and len(entries) != expected:
            raise ValueError(f'{key} must contain {expected} descriptions in atlas order.')
        result[key] = [v.strip() for v in entries]
    return result


def assemble_prompt(facts, count=2):
    facts = parse_facts(facts, count)
    name = 'replacement-template-single.txt' if count == 1 else 'replacement-template.txt'
    text = (TEMPLATES / name).read_text(encoding='utf-8')
    for k in fact_fields(count):
        value = facts[k]
        text = text.replace('{{' + k.upper() + '}}', value.rstrip('.。'))
    if '{{' in text:
        raise ValueError('The prompt template has unfilled fields.')
    return text


def prompt_graph(names, count=2, sample_counts=None):
    labels = active_labels(count)
    node = json.loads((TEMPLATES / 'gemini-node.json').read_text(encoding='utf-8'))
    if count == 1:
        node['inputs']['model.system_prompt'] = (TEMPLATES / 'gemini-single-system.txt').read_text(encoding='utf-8')
        node['inputs']['model.prompt'] = (TEMPLATES / 'gemini-single-request.txt').read_text(encoding='utf-8')
    if sample_counts:
        instruction = ('\nSOURCE SAMPLE ATLASES: source inputs may be labelled atlases or legacy single source crops, not new people. Treat a legacy single crop as sample A1 or B1 for its role. '
            'Every numbered tile for a role shows the SAME selected source person at a different source time. '
            'Inspect every tile independently. Target reference inputs remain separate and must not be used '
            'to locate the source person. Keep the existing base fields for the final replacement prompt. '
            'In addition, return the following JSON arrays of English strings; no other extra fields: ')
        instruction += '; '.join(f'source_samples_{k}: exactly {sample_counts.get(k,1)} descriptions, '
            f'ordered {k}1 through {k}{sample_counts.get(k,1)}' for k in labels)
        instruction += ('. Each entry must describe only the visible source appearance and clothing in '
            'that tile, at most 22 words. Include useful differences in view or visible outfit, '
            'but never permanent left/right screen positions. Do not combine all samples into one entry. '
            'The base source_A/source_B fields summarize stable identifying features across their own tiles. '
            'If any tile cannot be described, mark its entry UNCONFIRMED rather than omit it. '
            'This atlas extension takes precedence over the base field-count wording above.')
        node['inputs']['model.system_prompt'] += instruction
        node['inputs']['model.prompt'] += instruction
    nodes = {}
    roles = [role + '_' + label for label in labels for role in ['source', 'target']]
    for i, role in enumerate(roles, 1):
        nodes[str(i)] = {'class_type': 'LoadImage', 'inputs': {'image': names[role]}}
        node['inputs']['model.images.image_' + str(i)] = [str(i), 0]
    nodes['5'] = {'class_type': 'LoadVideo', 'inputs': {'file': names['clip']}}
    node['inputs']['model.video.video_1'] = ['5', 0]
    nodes['722'] = node
    nodes['802'] = {'class_type': 'SaveText', 'inputs': {
        'text': ['722', 0], 'filename_prefix': 'v2v_app/facts', 'format': 'json'}}
    return nodes


def video_graph(names, prompt, duration, width, height, count=2, strength=1.0):
    active_labels(count)
    if not isinstance(prompt, str) or not prompt.strip() or 'UNCONFIRMED' in prompt.upper():
        raise ValueError('Review the final prompt first.')
    if not 2 <= duration <= 15:
        raise ValueError('H3 requires a 2–15 second clip.')
    nodes = json.loads((TEMPLATES / 'h3-union20.api.json').read_text(encoding='utf-8'))
    nodes = {k: {'class_type': v['class_type'], 'inputs': copy.deepcopy(v['inputs'])}
             for k, v in nodes.items() if isinstance(v, dict) and 'class_type' in v}
    nodes['238']['inputs']['value'] = prompt.strip()
    nodes['232']['inputs']['value'] = duration
    nodes['236']['inputs'].update(width=width, height=height)
    nodes['237']['inputs']['image'] = names['target_A']
    if count == 2:
        nodes['250']['inputs']['image'] = names['target_B']
    else:
        nodes.pop('250')
        nodes['236']['inputs'].pop('ref_images.ref_image_1', None)
    nodes['300']['inputs']['file'] = names['clip']
    if any(not names.get(k) for k in ('control_mask', 'control_pose', 'control_source')):
        raise ValueError('Reviewed masks, DWPose and aligned source video are required.')
    for node_id, role in [('601', 'control_mask'), ('604', 'control_source'), ('700', 'control_pose')]:
        nodes[node_id]['inputs']['file'] = names[role]
    from controls import options
    nodes['172']['inputs']['strength'] = options({'strength': strength})['strength']
    # Preserve the known-good full 20-step non-turbo chain, including INT8 VAE.
    return nodes


def finite(value, name):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(name + ' must be a finite number.')
    return value

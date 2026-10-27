"""Cross-platform, loopback-only two-stage character replacement app."""
import argparse
import copy
import hashlib
import json
import mimetypes
import os
import re
import secrets
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from cloud import Cloud, read_key
from media import binary, crop, frame, hash_file, normalized_image, prepare_clip, probe, run, thumbnail, thumbnail_times
from workflow import FIELDS, assemble_prompt, parse_facts, prompt_graph, video_graph, finite, character_count, active_labels
import controls
import sample_points
from settings import DEFAULT_RESOLUTION

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('V2V_DATA_DIR', ROOT / 'data')).resolve()
LOCK = threading.RLock()
TOKEN = secrets.token_urlsafe(32)
PLANS = {}
MONITORS = set()
TIMELINE_LOCKS = {}
ROLES = ['video', 'target_A', 'target_B']
CONTROL_RUNS = set()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def read_json(path, default=None):
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else copy.deepcopy(default)


def config():
    default = DATA / 'comfy.key'
    return read_json(DATA / 'config.json', {'key_file': str(default)})


def checked_id(value):
    if not re.fullmatch(r'[a-f0-9]{32}', value):
        raise ValueError('Invalid project or job ID.')
    return value


def project_dir(pid):
    path = DATA / 'projects' / checked_id(pid)
    if not (path / 'project.json').is_file():
        raise ValueError('Project does not exist.')
    return path


def project(pid):
    return read_json(project_dir(pid) / 'project.json')


def save_project(p):
    write_json(project_dir(p['id']) / 'project.json', p)


def new_project():
    pid = uuid.uuid4().hex
    p = {'id': pid, 'name': 'Untitled project', 'created': time.time(), 'assets': {},
         'character_count': 1, 'selections': {}, 'facts': {}, 'prompt': '', 'jobs': []}
    write_json(DATA / 'projects' / pid / 'project.json', p)
    return p


def public_config():
    try:
        read_key(config())
        ready = True
    except (ValueError, OSError):
        ready = False
    tools = {}
    for name in ['ffmpeg', 'ffprobe']:
        try:
            binary(name)
            tools[name] = True
        except ValueError:
            tools[name] = False
    return {'key_configured': ready, 'key_file': config().get('key_file', ''),
            'tools': tools, 'fields': FIELDS, 'token': TOKEN, 'control_runtime': controls.runtime(config())}


def path_in_project(pid, relative):
    base = project_dir(pid).resolve()
    path = (base / relative).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        raise ValueError('Project file does not exist.')
    return path


def project_count(p, requested=None):
    count = character_count(p.get('character_count', 2))
    if requested is not None and character_count(requested) != count:
        raise ValueError('The character count changed. Reload the project.')
    return count


def set_character_count(pid, value):
    count = character_count(value)
    p = project(pid)
    old = project_count(p)
    if count != old:
        prepared = p.get('prepared', {})
        for label, selection in prepared.get('selections', {}).items():
            item = p.setdefault('selections', {}).setdefault(label, copy.deepcopy(selection))
            rel = 'prepared/' + prepared['id'] + '/frame_' + label + '.jpg'
            if not item.get('frame_file') and abs(item['time'] - selection['time']) < .0005 and (project_dir(pid) / rel).is_file():
                item['frame_file'] = rel
        # Keep user-written text recoverable, but never carry a two-person prompt
        # into a one-reference submission automatically.
        p.setdefault('mode_drafts', {})[str(old)] = {'facts': p.get('facts', {}), 'prompt': p.get('prompt', '')}
        p.pop('prepared', None)
        p['facts'], p['prompt'] = {}, ''
        p.pop('sample_inputs_changed',None);p.pop('sample_facts_stale',None)
    p['character_count'] = count
    if p.get('editor'):
        p['editor']['character_count'] = count
        p['editor']['selections'] = copy.deepcopy(p.get('selections', {}))
    save_project(p)
    return p


def timeline(pid, video_file):
    p, base = project(pid), project_dir(pid)
    asset = p['assets'].get('video')
    if not asset or asset['file'] != video_file:
        raise ValueError('The video changed. Reload the timeline.')
    source = path_in_project(pid, video_file)
    stamp = f"{video_file}:{source.stat().st_size}:{source.stat().st_mtime_ns}"
    key = hashlib.sha256(stamp.encode()).hexdigest()[:24]
    folder = base / 'timeline' / key
    with LOCK:
        mutex = TIMELINE_LOCKS.setdefault((pid, key), threading.Lock())
    with mutex:
        manifest = read_json(folder / 'manifest.json')
        if manifest and all((base / t['file']).is_file() for t in manifest['thumbnails']):
            return manifest
        folder.mkdir(parents=True, exist_ok=True)
        times = thumbnail_times(asset['info']['duration'], fps=asset['info']['fps'] or 24)
        items = []
        for index, seconds in enumerate(times):
            dest = folder / f'{index:03d}.jpg'
            info = thumbnail(source, seconds, dest)
            items.append({'time': seconds, 'file': str(dest.relative_to(base)), **info})
        manifest = {'video_file': video_file, 'duration': asset['info']['duration'],
                    'fps': asset['info']['fps'], 'thumbnails': items}
        if project(pid)['assets']['video']['file'] != video_file:
            raise ValueError('The video changed. Reload the timeline.')
        write_json(folder / 'manifest.json', manifest)
        return manifest


def save_editor(pid, data):
    # Editable selection state is separate from immutable prepared/job snapshots.
    p = project(pid)
    count = project_count(p, data.get('character_count'))
    labels = active_labels(count)
    video = p['assets'].get('video')
    if not video or video['file'] != data.get('video_file'):
        raise ValueError('The video changed. These selections were not saved.')
    duration = video['info']['duration']
    start, end = finite(data['range']['start'], 'Start time'), finite(data['range']['end'], 'End time')
    if not 0 <= start < end <= duration + .001 or not min(2, duration) - .001 <= end - start <= 15.001:
        raise ValueError('Choose a 2–15 second range inside the video.')
    selected = copy.deepcopy(p.get('selections', {}))
    for label in labels:
        value = sample_points.normalize(data.get('selections', {}).get(label, {}), duration)
        for item in sample_points.points(value):
            if item.get('frame_file'):
                frame_path = path_in_project(pid, item['frame_file'])
                if frame_path.suffix.lower() not in ['.jpg', '.png', '.jpeg']:
                    raise ValueError('Invalid sample frame image.')
        selected[label] = value
    resolution = data.get('resolution', (p['prepared'].get('resolution', '480p') if p.get('prepared') else DEFAULT_RESOLUTION))
    if resolution not in ('source', '480p'):
        raise ValueError('Resolution must be source or 480p.')
    editor = {'range': {'start': start, 'end': end}, 'selections': selected, 'character_count': count,
              'resolution': resolution}
    prepared = p.get('prepared')
    if prepared:
        same_range = abs(prepared['start'] - start) < .0005 and abs(prepared['end'] - end) < .0005
        same_count = prepared.get('character_count', 2) == count
        same_selection = same_count and all(sample_points.content(prepared['selections'][k]) ==
                                            sample_points.content(selected[k]) for k in labels)
        same_resolution = prepared.get('resolution', '480p') == resolution
        if not (same_range and same_count and same_resolution):
            p.pop('prepared', None)
            p.pop('sample_inputs_changed', None)
        else:
            p['sample_inputs_changed'] = not same_selection
    p['editor'] = editor
    p['selections'] = selected
    save_project(p)
    return {'ok': True}


def prepare_sample_images(pid, p, selections, dest, start, end):
    base = project_dir(pid)
    source = path_in_project(pid, p.get('prepared',{}).get('source_file',p['assets']['video']['file']))
    labels = active_labels(project_count(p))
    selected, sample_files, atlases = {}, {}, {}
    for label in labels:
        selection = sample_points.normalize(selections[label], p['assets']['video']['info']['duration'],
                                            start, end, require_crop=True)
        values, images = [], []
        for i, point in enumerate(sample_points.points(selection)):
            frame_name = f'frame_{label}.jpg' if i == 0 else f'frame_{label}_{i+1:02d}.jpg'
            image_name = f'source_{label}_{i+1:02d}.png'
            frame(source, point['time'], dest / frame_name, p['assets']['video']['info']['duration'])
            region = crop(dest / frame_name, point['crop'], dest / image_name)
            values.append(dict(time=point['time'], crop=region,
                               frame_file=str((dest / frame_name).relative_to(base))))
            images.append(dest / image_name)
            sample_files[f'{label}{i+1}'] = str((dest / image_name).relative_to(base))
        selected[label] = dict(values[0], **({'samples': values} if 'samples' in selection else {}))
        atlases[label] = sample_points.atlas(images, [v['time'] for v in values], label, dest / f'source_{label}.png')
    return selected, sample_files, atlases


def update_samples(pid):
    """Refresh source samples only. Preserve the prepared clip, targets, facts and prompt."""
    p, base = project(pid), project_dir(pid)
    old = p.get('prepared')
    if not old:
        raise ValueError('Prepare the video and reference inputs first.')
    if not p.get('sample_inputs_changed'):
        return p
    source = path_in_project(pid, old.get('source_file',p['assets']['video']['file']))
    if old.get('source_hash') and hash_file(source) != old['source_hash']:
        raise ValueError('The source video changed. Prepare the full inputs again.')
    dest = base / 'prepared' / uuid.uuid4().hex
    dest.mkdir(parents=True)
    selected, sample_files, atlases = prepare_sample_images(pid,p,p['selections'],dest,old['start'],old['end'])
    checkpoint = copy.deepcopy(old)
    checkpoint.update(id=dest.name,selections=selected,sample_files=sample_files,atlases=atlases,
                      source_file=old.get('source_file',p['assets']['video']['file']),
                      source_info=old.get('source_info',p['assets']['video']['info']),source_hash=hash_file(source),
                      sample_hashes={k:hash_file(base/v) for k,v in sample_files.items()})
    for label in selected:
        role='source_'+label
        checkpoint['files'][role]=str((dest/(role+'.png')).relative_to(base))
        checkpoint['hashes'][role]=hash_file(base/checkpoint['files'][role])
    facts=copy.deepcopy(p.get('facts',{}))
    for label,selection in selected.items():
        key='source_samples_'+label
        previous=facts.get(key,[])
        by_point={json.dumps(v,sort_keys=True):previous[i] for i,v in enumerate(sample_points.content(old['selections'][label])) if i<len(previous)}
        if facts.get('source_'+label) and (previous or len(sample_points.points(selection))>1):
            facts[key]=[by_point.get(json.dumps(v,sort_keys=True),facts['source_'+label]) for v in sample_points.content(selection)]
    override=p.get('tracking_descriptions',{})
    if override.get('checkpoint_id')==old['id']:
        override['checkpoint_id']=checkpoint['id']
    p.update(prepared=checkpoint,facts=facts,sample_inputs_changed=False,
             sample_facts_stale=bool(facts or p.get('prompt')))
    p.pop('tracking_inputs',None)
    p['selections'].update(selected)
    if p.get('editor'):
        p['editor']['selections']=copy.deepcopy(p['selections'])
    save_project(p)
    write_json(dest/'checkpoint.json',checkpoint)
    return p


def prepare(pid, data):
    p, base = project(pid), project_dir(pid)
    count = project_count(p, data.get('character_count'))
    labels = active_labels(count)
    roles = ['video'] + ['target_' + k for k in labels]
    if any(k not in p['assets'] for k in roles):
        raise ValueError('Upload the video and ' + ('one ' if count == 1 else 'two ') + 'replacement reference image')
    source = path_in_project(pid, p['assets']['video']['file'])
    # A new immutable preparation per checkpoint; running jobs never read later edits.
    dest = base / 'prepared' / uuid.uuid4().hex
    dest.mkdir(parents=True)
    selected, sample_files, atlases = prepare_sample_images(pid,p,data['selections'],dest,float(data['start']),float(data['end']))
    for label in labels:
        normalized_image(path_in_project(pid, p['assets']['target_' + label]['file']), dest / ('target_' + label + '.png'))
    resolution = data.get('resolution', (p['prepared'].get('resolution', '480p') if p.get('prepared') else DEFAULT_RESOLUTION))
    if resolution not in ('source', '480p'):
        raise ValueError('Resolution must be source or 480p.')
    info = prepare_clip(source, data['start'], data['end'], dest / 'clip.mp4', p['assets']['video']['info'], resolution)
    roles = ['clip'] + [kind + '_' + k for k in labels for kind in ['source', 'target']]
    files = {k: str((dest / (k + ('.mp4' if k == 'clip' else '.png'))).relative_to(base)) for k in roles}
    checkpoint = {'id': dest.name, 'start': float(data['start']), 'end': float(data['end']),
                  'duration': round(float(data['end']) - float(data['start']), 6), 'info': info,
                  'files': files, 'selections': selected, 'character_count': count,
                  'hashes': {k: hash_file(base / v) for k, v in files.items()},
                  'source_file': p['assets']['video']['file'], 'source_info': p['assets']['video']['info'],
                  'source_hash': hash_file(source), 'resolution': resolution,
                  'sample_files': sample_files, 'atlases': atlases,
                  'sample_hashes': {k: hash_file(base / v) for k,v in sample_files.items()}}
    p['prepared'] = checkpoint
    p.pop('sample_inputs_changed', None)
    p.pop('sample_facts_stale', None)
    p.setdefault('selections', {}).update(selected)
    p['editor'] = {'range': {'start': checkpoint['start'], 'end': checkpoint['end']},
                   'resolution': resolution, 'character_count': count,
                   'selections': copy.deepcopy(p['selections'])}
    p['facts'], p['prompt'] = {}, ''
    save_project(p)
    write_json(dest / 'checkpoint.json', checkpoint)
    return p


def generation_settings(pid, data):
    p = project(pid)
    if 'tracking_descriptions' in data:
        c=p.get('prepared')
        if not c:raise ValueError('Prepare the character inputs first.')
        descriptions=data['tracking_descriptions']
        if not isinstance(descriptions,dict) or any(not isinstance(v,str) or len(v)>1000 for v in descriptions.values()):
            raise ValueError('Locator descriptions must be text of at most 1,000 characters per role.')
        # Empty means inherit Gemini's current source fact, never override it
        # with an empty string when generation settings were saved early.
        old=p.get('tracking_descriptions',{})
        values=dict(old.get('values',{})) if old.get('checkpoint_id')==c['id'] else {}
        for k in c.get('selections',{}):
            if k in descriptions:values[k]=descriptions[k].strip()
        p['tracking_descriptions']=dict(checkpoint_id=c['id'],values={
            k:v for k,v in values.items() if k in c.get('selections',{}) and isinstance(v,str) and v.strip()})
    p['generation'] = controls.options(data)
    save_project(p)
    return p


def control_state(pid):
    p = project(pid)
    ref = p.get('control_run')
    if not ref:
        return {'status': 'none', 'valid': False}
    folder = project_dir(pid) / 'controls' / checked_id(ref['id'])
    record = read_json(folder / 'control.json', {})
    settings = controls.options(p.get('generation', {}))
    record['valid'] = not p.get('sample_inputs_changed') and bool(p.get('prepared')) and record.get('identity') == controls.identity(
        p['prepared'], settings, tracking_inputs(p,p['prepared']))
    warning = controls.warning_signature(record.get('quality', {}))
    record['tracking_warnings_accepted'] = bool(warning and record.get('approved') and record.get('accepted_tracking_warnings') == warning)
    if warning and record.get('approved') and not record['tracking_warnings_accepted']:
        record['approved'] = False
    record['progress'] = read_json(folder / 'progress.json', {})
    if record.get('status') == 'running' and (pid, ref['id']) not in CONTROL_RUNS:
        record.update(status='interrupted', error='Local preparation was interrupted. Prepare the controls again.')
    return record


def tracking_inputs(p,c):
    value = p.get('tracking_inputs', {})
    review=copy.deepcopy({k:value.get(k,[]) for k in ('anchors','decisions','ignored_boundaries')}) if value.get('checkpoint_id') == c['id'] else {'anchors':[],'decisions':[],'ignored_boundaries':[]}
    override=p.get('tracking_descriptions',{})
    custom=override.get('values',{}) if override.get('checkpoint_id')==c['id'] else {}
    source={k:(custom.get(k,'').strip() or p.get('facts',{}).get('source_'+k,''))
            for k in c.get('selections',{})}
    descriptions={k:v.strip() for k,v in source.items() if k in c.get('selections',{}) and isinstance(v,str) and v.strip()}
    if descriptions:
        if c.get('start') is not None:
            review['samples'] = sample_points.locator_samples(c, p.get('facts', {}), custom)
        from text_locator import MODEL, REVISION
        review['descriptions']=descriptions
        review['locator']={'model':MODEL,'revision':REVISION}
    return review


def correction_context(pid,cid):
    p=project(pid);state=control_state(pid)
    if state.get('id') != cid or not state.get('valid') or state.get('status') not in ('needs_input','completed'):
        raise ValueError('Tracking results changed. Refresh and review the current result.')
    folder=project_dir(pid)/'controls'/checked_id(cid)
    request=read_json(folder/'request.json')
    return p,state,folder,request


def control_timeline(pid,cid):
    """Local overlay thumbnails in clip time, bound to this control record."""
    p,state,folder,request=correction_context(pid,cid)
    source=path_in_project(pid,state['previews']['mask'])
    stamp=f'{source.stat().st_size}:{source.stat().st_mtime_ns}'
    target=folder/'timeline'
    with LOCK:mutex=TIMELINE_LOCKS.setdefault((pid,cid),threading.Lock())
    with mutex:
        manifest=read_json(target/'manifest.json')
        if manifest and manifest.get('version')==2 and manifest.get('stamp')==stamp and all(path_in_project(pid,t['file']).is_file() for t in manifest['thumbnails']):
            return manifest
        target.mkdir(exist_ok=True)
        fps=state.get('manifest',{}).get('fps',24)
        count=state.get('manifest',{}).get('source_frames') or max(s['end'] for s in state['quality']['shots'])
        items=[]
        for i in range(min(12,count)):
            index=min(count-1,int((i+.5)*count/min(12,count)))
            dest=target/f'{i:03d}.jpg'
            info=thumbnail(source,index/fps,dest)
            items.append(dict(time=index/fps,file=str(dest.relative_to(project_dir(pid))),**info))
        manifest=dict(version=2,stamp=stamp,thumbnails=items,duration=count/fps,fps=fps,
                      source_start=request['checkpoint']['start'],source_end=request['checkpoint'].get('end',request['checkpoint']['start']+count/fps),control_id=cid)
        write_json(target/'manifest.json',manifest)
        return manifest


def save_tracking_correction(pid,data):
    with LOCK:
        p,state,folder,request=correction_context(pid,data['control_id'])
        issue=next((i for i in state.get('quality',{}).get('issues',[]) if i['id']==data['issue_id']),None)
        if not issue:
            raise ValueError('This review item changed. Refresh the results.')
        label=data.get('label')
        if label not in issue['labels']:
            raise ValueError('Choose a role belonging to this review item.')
        c=request['checkpoint'];review=tracking_inputs(p,c);action=data.get('action')
        if action=='merge':
            if issue['kind']!='anchor' or issue.get('boundary_reason')!='candidate_cut':
                raise ValueError('Only a candidate hard cut can be marked as a continuous shot.')
            review['ignored_boundaries']=sorted(set(review['ignored_boundaries']+[issue['shot_start']]))
        elif action in ('absent','accept'):
            if action=='accept' and issue['kind']!='overlap':
                raise ValueError('Confirm that this role is absent or occluded in this range.')
            review['decisions']=[d for d in review['decisions'] if d['issue_id']!=issue['id']]+[
                dict(issue_id=issue['id'],kind=action,label=label,start=issue['start'],end=issue['end'])]
        else:
            raise ValueError('Invalid review action.')
        p['tracking_inputs']=dict(checkpoint_id=c['id'],**review);save_project(p)
    return {'ok':True}


def start_controls(pid):
    with LOCK:
        p = project(pid)
        if p.get('sample_inputs_changed'):
            p = update_samples(pid)
        c = copy.deepcopy(p.get('prepared'))
        if not c:
            raise ValueError('Prepare and review the video and character inputs first.')
        if c.get('resolution', '480p') not in ('source', '480p'):
            raise ValueError('Prepare inputs again with a supported output resolution.')
        if any(k[0] == pid for k in CONTROL_RUNS):
            raise ValueError('Local control preparation is already running for this project.')
        settings = controls.options(p.get('generation', {}))
        source_file = c.get('source_file', p['assets']['video']['file'])
        source = path_in_project(pid, source_file)
        for role, rel in c['files'].items():
            if hash_file(path_in_project(pid, rel)) != c['hashes'][role]:
                raise ValueError('Prepared inputs changed. Prepare them again.')
        source_hash = hash_file(source)
        if c.get('source_hash', source_hash) != source_hash:
            raise ValueError('The source video changed. Prepare the inputs again.')
        cid = uuid.uuid4().hex
        folder = project_dir(pid) / 'controls' / cid
        folder.mkdir(parents=True)
        review=tracking_inputs(p,c)
        if any(not review.get('descriptions',{}).get(k) for k in c.get('selections',{})):
            raise ValueError('Provide a source locator description for each role. Saved Gemini source_A / source_B descriptions can be reused.')
        previous=p.get('control_run',{}).get('id')
        record = dict(id=cid, status='running', checkpoint_id=c['id'], created=time.time(),
                      identity=controls.identity(c, settings,review), settings=settings, approved=False)
        write_json(folder/'control.json', record)
        p['control_run'] = {'id': cid}
        save_project(p)
        request = dict(clip=str(path_in_project(pid,c['files']['clip'])), source=str(source),
                       source_info=c.get('source_info',p['assets']['video']['info']),
                       source_hash=source_hash, checkpoint=c, settings=settings,review=review,
                       reuse_from=str(project_dir(pid)/'controls'/checked_id(previous)) if previous else None)
        cfg = config()
        CONTROL_RUNS.add((pid,cid))

    def worker():
        try:
            manifest = controls.execute(request, folder, cfg)
            files = {role:str((folder/name).relative_to(project_dir(pid))) for role,name in manifest['files'].items()}
            previews = {role:str((folder/name).relative_to(project_dir(pid))) for role,name in manifest['previews'].items()}
            record.update(status='needs_input' if manifest.get('requires_input') else 'completed',
                          files=files, hashes=manifest['hashes'], previews=previews,
                          manifest=manifest, quality=manifest.get('quality',{}), finished=time.time())
        except Exception as e:
            record.update(status='failed', error=str(e)[:1500], finished=time.time())
        finally:
            with LOCK:
                write_json(folder/'control.json',record)
                CONTROL_RUNS.discard((pid,cid))
    threading.Thread(target=worker, daemon=True).start()
    return control_state(pid)


def checked_controls(pid, reviewed=False):
    state = control_state(pid)
    if not state.get('valid') or state.get('status') not in ('completed', 'needs_input'):
        raise ValueError('Prepare masks and DWPose controls for the current inputs first.')
    required = {'control_mask', 'control_pose', 'control_source'}
    if not required.issubset(state.get('files', {})) or not required.issubset(state.get('hashes', {})):
        raise ValueError('Complete mask, DWPose and source files are required. Prepare the controls again.')
    if reviewed and not state.get('approved'):
        raise ValueError('Review and approve the mask and pose previews first.')
    warning = controls.warning_signature(state.get('quality', {}))
    if reviewed and warning and state.get('accepted_tracking_warnings') != warning:
        raise ValueError('Explicitly accept the current tracking warnings before submitting.')
    for role, rel in state['files'].items():
        if hash_file(path_in_project(pid, rel)) != state['hashes'][role]:
            raise ValueError('Control files changed. Prepare and review them again.')
    return state


def approve_controls(pid, cid, approved=True, accept_tracking_warnings=False):
    with LOCK:
        state = checked_controls(pid)
        if state['id'] != cid:
            raise ValueError('Controls changed. Review the latest previews.')
        folder = project_dir(pid)/'controls'/checked_id(cid)
        record = read_json(folder/'control.json')
        warning = controls.warning_signature(state.get('quality', {}))
        if approved and warning and accept_tracking_warnings is not True:
            raise ValueError('Confirm that you want to continue despite the tracking warnings.')
        record.update(approved=approved, reviewed=time.time() if approved else None,
                      accepted_tracking_warnings=warning if approved else None)
        write_json(folder/'control.json',record)
    return control_state(pid)


def quote(kind):
    # Historical, disclosed values; no fabricated fixed total or zero-cost claim.
    return ('Gemini 3.1 Flash-Lite: one request, up to 2,048 output tokens. Historical node quote: '
            '$0.00025/1K input tokens and $0.0015/1K output tokens; Cloud GPU runtime may also be charged. '
            if kind == 'prompt' else
            'MiniMax H3 ref2va: one video generation, full 20 steps, no Partner Node fee. ') + (
            'Historical Cloud GPU quote: 0.266 credits per active second (workspace record, 2026-10-25). '
            'Check the platform for current prices. Final total unknown. No automatic retries.')


def make_plan(pid, kind, prompt='', requested_count=None):
    if kind not in ['prompt', 'video']:
        raise ValueError('Invalid stage.')
    p = project(pid)
    if kind == 'prompt' and p.get('sample_inputs_changed'):
        with LOCK:p = update_samples(pid)
    count = project_count(p, requested_count)
    c = p.get('prepared')
    if not c:
        raise ValueError('Complete the local input review first.')
    if c.get('resolution', '480p') not in ('source', '480p'):
        raise ValueError('Prepare inputs again with a supported output resolution.')
    if c.get('character_count', 2) != count:
        raise ValueError('The character count changed. Prepare the inputs again.')
    c = copy.deepcopy(c)
    generation = controls.options(p.get('generation', {}))
    control = None
    if kind == 'video':
        control = checked_controls(pid, reviewed=True)
        c['files'].update(control['files'])
        c['hashes'].update(control['hashes'])
    names = {k: Path(v).name for k, v in c['files'].items()}
    graph = prompt_graph(names, count, sample_points.counts(c)) if kind == 'prompt' else video_graph(
        names, prompt, c['duration'], c['info']['width'], c['info']['height'], count=count, strength=generation['strength'])
    if kind == 'video':
        p['prompt'] = prompt.strip()
        save_project(p)
    snapshot = {'project_id': pid, 'kind': kind, 'checkpoint': copy.deepcopy(c),
                'prompt': prompt.strip(), 'graph': graph, 'character_count': count,
                'generation': generation, 'control_id': control['id'] if control else None,
                'tracking_warning_signature': controls.warning_signature(control.get('quality', {})) if control else None,
                'tracking_warnings': copy.deepcopy(control.get('quality', {}).get('issues', [])) if control else []}
    fingerprint = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
    plan_id = uuid.uuid4().hex
    with LOCK:
        # Plans expire and are single-use. Confirmation binds exactly this graph/media.
        PLANS[plan_id] = {'snapshot': snapshot, 'fingerprint': fingerprint,
                          'created': time.time(), 'consumed': False}
    detail = (' V2: Union 2.0 INT8 + EdgeTAM masks + DWPose, strength '+str(generation['strength'])+'.'
              if kind == 'video' and control else '')
    if kind == 'video' and c.get('resolution') == 'source':
        detail += ' Original-resolution generation uses a larger canvas and may take longer and cost more.'
    return {'id': plan_id, 'fingerprint': fingerprint, 'kind': kind, 'cost': quote(kind)+detail,
            'graph': graph, 'checkpoint': c, 'character_count': count, 'generation': generation,
            'tracking_warnings': snapshot['tracking_warnings']}


def job_path(pid, jid):
    return project_dir(pid) / 'jobs' / checked_id(jid)


def update_job(pid, jid, **values):
    with LOCK:
        path = job_path(pid, jid) / 'job.json'
        j = read_json(path)
        j.update(values, updated=time.time())
        write_json(path, j)
        return j


def accept_plan(plan_id, fingerprint, accepted):
    with LOCK:
        plan = PLANS.get(plan_id)
        if not accepted or not plan or plan['consumed'] or plan['fingerprint'] != fingerprint:
            raise ValueError('Confirmation is invalid. Review this submission again.')
        if time.time() - plan['created'] > 900:
            raise ValueError('Confirmation expired after 15 minutes. Review again.')
        s = plan['snapshot']
        p = project(s['project_id'])
        if p.get('sample_inputs_changed'):
            raise ValueError('Character samples changed. Review a new submission plan first.')
        for old in p['jobs']:
            j = read_json(job_path(p['id'], old) / 'job.json')
            if j['status'] in ['uploading', 'submitting', 'running', 'polling_paused', 'submission_unknown']:
                raise ValueError('This project has an unfinished job. Check it before creating another paid submission.')
        # Recheck immutable checkpoint identity and content before spending.
        if p.get('prepared', {}).get('id') != s['checkpoint']['id']:
            raise ValueError('The video or character selections changed. Review the inputs again.')
        if project_count(p) != s.get('character_count', 2):
            raise ValueError('The character count changed. Review the inputs again.')
        if s['kind'] == 'video':
            if controls.options(p.get('generation', {})) != s.get('generation', controls.options({})):
                raise ValueError('Generation settings changed. Review again.')
            if s.get('control_id'):
                current_control = checked_controls(p['id'], reviewed=True)
                if current_control['id'] != s['control_id'] or controls.warning_signature(current_control.get('quality', {})) != s.get('tracking_warning_signature'):
                    raise ValueError('Controls or tracking warnings changed. Review this submission again.')
        for role, rel in s['checkpoint']['files'].items():
            if hash_file(path_in_project(p['id'], rel)) != s['checkpoint']['hashes'][role]:
                raise ValueError('Input files changed. Prepare them again.')
        read_key(config())  # Fail before consuming confirmation when unconfigured.
        plan['consumed'] = True
        jid = uuid.uuid4().hex
        j = {'id': jid, 'project_id': p['id'], 'kind': s['kind'], 'status': 'uploading',
             'created': time.time(), 'cost_disclosure': quote(s['kind']), 'outputs': [],
             'checkpoint_id': s['checkpoint']['id'], 'character_count': s.get('character_count', 2),
             'generation_version': s.get('generation', {}).get('generation_version', 'v2')}
        write_json(job_path(p['id'], jid) / 'job.json', j)
        write_json(job_path(p['id'], jid) / 'snapshot.json', s)
        p['jobs'].append(jid)
        save_project(p)
        threading.Thread(target=submit_worker, args=(p['id'], jid, copy.deepcopy(s)), daemon=True).start()
        return j


def submit_worker(pid, jid, s):
    cloud = None
    submitting = False
    try:
        cloud = Cloud(config())
        count = character_count(s.get('character_count', 2))
        kinds = ['source', 'target'] if s['kind'] == 'prompt' else ['target']
        roles = ['clip'] + [kind + '_' + k for k in active_labels(count) for kind in kinds]
        if s['kind'] == 'video':
            roles += ['control_mask', 'control_pose', 'control_source']
        names = {}
        for role in roles:
            source = path_in_project(pid, s['checkpoint']['files'][role])
            if hash_file(source) != s['checkpoint']['hashes'][role]:
                raise ValueError('Submission inputs changed. Review them again before generation.')
            update_job(pid,jid,upload_role=role)
            names[role] = cloud.upload(source)
        graph = prompt_graph(names, count, sample_points.counts(s['checkpoint'])) if s['kind'] == 'prompt' else video_graph(
            names, s['prompt'], s['checkpoint']['duration'], s['checkpoint']['info']['width'],
            s['checkpoint']['info']['height'], count=count, strength=s['generation']['strength'])
        expected = copy.deepcopy(s['graph'])
        for node in expected.values():
            for key,value in list(node['inputs'].items()):
                if key in ('file','image') and isinstance(value,str):
                    for role,name in names.items():
                        if value == Path(s['checkpoint']['files'][role]).name:
                            node['inputs'][key] = name
                            break
        if graph != expected:
            raise ValueError('The workflow differs from the confirmed plan. Review again before submission.')
        write_json(job_path(pid, jid) / 'submission.api.json', graph)
        write_json(job_path(pid, jid) / 'upload-mapping.json', names)
        update_job(pid, jid, status='submitting')
        submitting = True
        prompt_id = cloud.submit(graph, jid)  # Exactly one POST. Never retry.
        submitting = False
        update_job(pid, jid, status='running', prompt_id=prompt_id)
        guarded_monitor(pid, jid, cloud)
    except Exception as e:
        error = cloud.safe_error(e) if cloud else 'Could not read the Cloud configuration.'
        # A lost submit response is ambiguous: block automatic resubmission.
        update_job(pid, jid, status='submission_unknown' if submitting else 'failed', error=error)
    finally:
        if cloud:
            cloud.close()


def guarded_monitor(pid, jid, cloud):
    key = (pid, jid)
    with LOCK:
        if key in MONITORS:
            return
        MONITORS.add(key)
    try:
        monitor(pid, jid, cloud)
    finally:
        with LOCK:
            MONITORS.discard(key)


def monitor(pid, jid, cloud):
    j = read_json(job_path(pid, jid) / 'job.json')
    prompt_id = j['prompt_id']
    for _ in range(720):
        try:
            history = cloud.history(prompt_id)
            status = history.get('status', {})
            complete = status.get('completed', False) if isinstance(status, dict) else False
            state = str(status.get('status_str', '') if isinstance(status, dict) else status).lower()
            if state in ['error', 'failed', 'cancelled']:
                # Save only execution error detail, never the raw prompt history.
                errors = [cloud.safe_error(m[1].get('exception_message', 'Cloud execution failed.'))
                          for m in status.get('messages', []) if len(m) > 1 and m[0] == 'execution_error']
                update_job(pid, jid, status='failed', error='; '.join(errors) or 'Cloud execution failed.')
                return
            if complete or state in ['success', 'completed']:
                update_job(pid, jid, remote_completed=True)
                finish_job(pid, jid, history.get('outputs', {}), cloud)
                return
            remote = cloud.status(prompt_id)
            remote_state = str(remote.get('status', '')).lower()
            if remote_state in ['failed', 'error', 'cancelled']:
                update_job(pid, jid, status='failed', error='Cloud job status: ' + remote_state)
                return
            update_job(pid, jid, remote_status=remote_state or state or 'queued', last_poll_error='')
        except Exception as e:
            update_job(pid, jid, last_poll_error=cloud.safe_error(e))
        time.sleep(5)
    update_job(pid, jid, status='polling_paused', error='Polling stopped. The Cloud job may still be running; resume polling the same job.')


def finish_job(pid, jid, outputs, cloud):
    folder = job_path(pid, jid)
    j = read_json(folder / 'job.json')
    count = character_count(j.get('character_count', 2))
    if j['kind'] == 'prompt':
        raw = outputs.get('802', {}).get('text', [])
        text = '\n'.join(raw) if isinstance(raw, list) else str(raw)
        (folder / 'raw-gemini.txt').write_text(text, encoding='utf-8')
        try:
            facts = parse_facts(text, count, sample_points.counts(read_json(folder / 'snapshot.json')['checkpoint']))
            final = assemble_prompt(facts, count=count)
        except (ValueError, TypeError) as e:
            update_job(pid, jid, status='needs_review', error=str(e), raw_text=text)
            return
        write_json(folder / 'facts.json', facts)
        (folder / 'prompt.txt').write_text(final, encoding='utf-8')
        with LOCK:
            p = project(pid)
            if p.get('prepared', {}).get('id') == j['checkpoint_id'] and project_count(p) == count:
                p.update(facts=facts, prompt=final, sample_facts_stale=False)
                save_project(p)
        update_job(pid, jid, status='completed', facts=facts, prompt=final)
    else:
        entries = []
        # Only SaveVideo 192 belongs to this stage's output contract.
        out = outputs.get('192', {})
        for value in out.values():
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict) and item.get('filename') and Path(item['filename']).suffix.lower() in ['.mp4', '.webm', '.mov', '.mkv']:
                        if item not in entries:
                            entries.append(item)
        if not entries:
            raise ValueError('The job ended without a SaveVideo 192 output. Check the same job in Cloud.')
        paths = []
        for i, item in enumerate(entries):
            dest = folder / ('output_' + str(i) + Path(item['filename']).suffix.lower())
            cloud.download(item, dest)
            paths.append(str(dest.relative_to(project_dir(pid))))
        # Preserve raw generation. Review copy has the selected source duration/audio.
        snapshot = read_json(folder / 'snapshot.json')
        clip = path_in_project(pid, snapshot['checkpoint']['files']['clip'])
        review = folder / 'review.mp4'
        output = snapshot['checkpoint']['info'].get('output')
        filters = (['-vf', f"crop={output['width']}:{output['height']}:{output['x']}:{output['y']}:exact=1"]
                   if output else [])
        pixel_format = 'yuv444p' if output and (output['width']%2 or output['height']%2) else 'yuv420p'
        run([binary('ffmpeg'), '-v', 'error', '-y', '-i', str(project_dir(pid) / paths[0]),
             '-i', str(clip), '-t', str(snapshot['checkpoint']['duration']), '-map', '0:v:0',
             '-map', '1:a?', *filters, '-c:v', 'libx264', '-crf', '18', '-pix_fmt', pixel_format,
             '-c:a', 'aac', '-movflags', '+faststart', str(review)], timeout=600)
        update_job(pid, jid, status='completed', outputs=paths, review=str(review.relative_to(project_dir(pid))))


class Handler(BaseHTTPRequestHandler):
    server_version = 'V2V/0.1'

    def log_message(self, *args):
        pass  # No request or credential logging.

    def send_json(self, value, status=200):
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def safe_host(self):
        host = self.headers.get('Host', '')
        return host in [f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}']

    def do_GET(self):
        try:
            if not self.safe_host():
                raise ValueError('Only local access is allowed.')
            url = urlparse(self.path)
            if url.path == '/api/config':
                return self.send_json(public_config())
            if url.path == '/api/projects':
                items = [read_json(p) for p in (DATA / 'projects').glob('*/project.json')]
                return self.send_json(sorted(items, key=lambda p: p['created'], reverse=True))
            if url.path.startswith('/api/controls/'):
                return self.send_json(control_state(url.path.rsplit('/', 1)[1]))
            if url.path.startswith('/api/project/'):
                return self.send_json(project(url.path.rsplit('/', 1)[1]))
            if url.path.startswith('/api/job/'):
                _, _, _, pid, jid = url.path.split('/')
                return self.send_json(read_json(job_path(pid, jid) / 'job.json'))
            if url.path.startswith('/files/'):
                _, _, pid, rel = url.path.split('/', 3)
                return self.serve_file(path_in_project(pid, unquote(rel)))
            relative = 'index.html' if url.path == '/' else unquote(url.path.lstrip('/'))
            base = ROOT / 'web'
            path = (base / relative).resolve()
            if not path.is_relative_to(base) or not path.is_file():
                return self.send_json({'error': 'Not found'}, 404)
            return self.serve_file(path)
        except (ValueError, OSError, KeyError, TypeError) as e:
            self.send_json({'error': str(e)}, 400)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def serve_file(self, path):
        size = path.stat().st_size
        start, end = 0, size - 1
        requested = self.headers.get('Range')
        if requested:
            match = re.fullmatch(r'bytes=(\d+)-(\d*)', requested)
            if not match:
                self.send_error(416)
                return
            start = int(match[1]); end = min(size - 1, int(match[2])) if match[2] else size - 1
            if start > end:
                self.send_error(416)
                return
        self.send_response(206 if requested else 200)
        self.send_header('Content-Type', mimetypes.guess_type(path)[0] or 'application/octet-stream')
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Content-Length', str(end - start + 1))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        if requested:
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        self.end_headers()
        with path.open('rb') as f:
            f.seek(start)
            remaining = end - start + 1
            while remaining:
                chunk = f.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def do_POST(self):
        try:
            if not self.safe_host() or self.headers.get('X-V2V-Token') != TOKEN:
                return self.send_json({'error': 'Invalid local session.'}, 403)
            origin = self.headers.get('Origin')
            if origin and origin not in [f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}']:
                return self.send_json({'error': 'Cross-site requests are not allowed.'}, 403)
            url = urlparse(self.path)
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 <= length <= 512 * 1024 * 1024:
                raise ValueError('The maximum file size is 512 MB.')
            if url.path == '/api/upload':
                return self.upload(url, length)
            if length > 1024 * 1024:
                raise ValueError('The JSON request is too large.')
            data = json.loads(self.rfile.read(length) or '{}')
            if url.path == '/api/project':
                return self.send_json(new_project())
            if url.path == '/api/config':
                value = config()
                value['key_file'] = str(Path(data['key_file']).expanduser())
                for key in ('control_python','control_aux','control_pose_model','control_extra_site'):
                    if key in data:
                        value[key] = str(data[key]).strip()
                read_key(value)
                write_json(DATA / 'config.json', value)
                return self.send_json(public_config())
            pid = data.get('project_id', '')
            if url.path == '/api/generation':
                with LOCK:
                    return self.send_json(generation_settings(pid, data))
            if url.path == '/api/samples/update':
                with LOCK:return self.send_json(update_samples(pid))
            if url.path == '/api/controls/start':
                return self.send_json(start_controls(pid))
            if url.path == '/api/controls/approve':
                return self.send_json(approve_controls(pid, data['control_id'], data.get('approved', True) is True,
                                                        data.get('accept_tracking_warnings') is True))
            if url.path == '/api/controls/timeline':
                return self.send_json(control_timeline(pid,data['control_id']))
            if url.path == '/api/controls/correct':
                return self.send_json(save_tracking_correction(pid,data))
            if url.path == '/api/controls/reset':
                with LOCK:
                    p=project(pid)
                    p.pop('tracking_inputs',None)
                    save_project(p)
                return self.send_json({'ok':True})
            if url.path == '/api/characters':
                with LOCK:
                    return self.send_json(set_character_count(pid, data['character_count']))
            if url.path == '/api/timeline':
                return self.send_json(timeline(pid, data['video_file']))
            if url.path == '/api/editor':
                with LOCK:
                    return self.send_json(save_editor(pid, data))
            if url.path == '/api/frame':
                p = project(pid); base = project_dir(pid)
                filename = 'frames/' + uuid.uuid4().hex + '.jpg'
                (base / 'frames').mkdir(exist_ok=True)
                info = frame(path_in_project(pid, p['assets']['video']['file']), data['time'],
                             base / filename, p['assets']['video']['info']['duration'])
                return self.send_json({'file': filename, 'info': info})
            if url.path == '/api/prepare':
                with LOCK:
                    return self.send_json(prepare(pid, data))
            if url.path == '/api/assemble':
                with LOCK:
                    p = project(pid)
                    count = project_count(p, data.get('character_count'))
                    facts = parse_facts(data['facts'], count, sample_points.counts(p.get('prepared', {})));  text = assemble_prompt(facts, count=count)
                    p.update(facts=facts, prompt=text, sample_facts_stale=False); save_project(p)
                return self.send_json({'facts': facts, 'prompt': text})
            if url.path == '/api/save-prompt':
                with LOCK:
                    p = project(pid); project_count(p, data.get('character_count')); p['prompt'] = str(data['prompt']); save_project(p)
                return self.send_json(p)
            if url.path == '/api/plan':
                return self.send_json(make_plan(pid, data['kind'], data.get('prompt', ''), data.get('character_count')))
            if url.path == '/api/submit':
                return self.send_json(accept_plan(data['plan_id'], data['fingerprint'], data.get('accepted') is True))
            if url.path == '/api/resume':
                with LOCK:
                    j = read_json(job_path(pid, data['job_id']) / 'job.json')
                    if j['status'] not in ['polling_paused', 'running', 'failed'] or not j.get('prompt_id'):
                        raise ValueError('This record has no Cloud job ID to resume polling.')
                    if (pid, j['id']) in MONITORS:
                        return self.send_json({'ok': True, 'already_monitoring': True})
                    # Read-only recovery after process restart; never submit again.
                    update_job(pid, j['id'], status='running')
                    def resume():
                        c = Cloud(config())
                        try:
                            guarded_monitor(pid, j['id'], c)
                        except Exception as e:
                            update_job(pid, j['id'], status='polling_paused', error=c.safe_error(e))
                        finally:
                            c.close()
                    threading.Thread(target=resume, daemon=True).start()
                return self.send_json({'ok': True})
            return self.send_json({'error': 'Not found'}, 404)
        except (ValueError, KeyError, OSError, TypeError) as e:
            self.send_json({'error': str(e)}, 400)
        except Exception:
            self.send_json({'error': 'Local processing failed. Check the inputs and dependencies. Cloud submissions are never retried automatically.'}, 500)

    def upload(self, url, length):
        args = parse_qs(url.query)
        pid, role = args['project_id'][0], args['role'][0]
        p, base = project(pid), project_dir(pid)
        if role not in ROLES:
            raise ValueError('Invalid upload role.')
        ext = Path(args.get('name', ['file'])[0]).suffix.lower()
        allowed = ['.mp4', '.mov', '.webm', '.mkv', '.m4v'] if role == 'video' else ['.png', '.jpg', '.jpeg', '.webp']
        if ext not in allowed:
            raise ValueError('Unsupported file type.')
        folder = base / 'assets'; folder.mkdir(exist_ok=True)
        dest = folder / (uuid.uuid4().hex + ext)
        with dest.open('wb') as f:
            left = length
            while left:
                chunk = self.rfile.read(min(left, 1024 * 1024))
                if not chunk:
                    raise ValueError('Upload interrupted.')
                f.write(chunk); left -= len(chunk)
        if role == 'video':
            info = probe(dest)
        else:
            output = dest.with_suffix('.png')
            info = normalized_image(dest, output)
            if output != dest:
                dest.unlink()
            dest = output
        with LOCK:
            p = project(pid)
            p['assets'][role] = {'file': str(dest.relative_to(base)), 'name': args.get('name', ['file'])[0], 'info': info}
            if role == 'video' or role in ['target_' + k for k in active_labels(project_count(p))]:
                p.pop('prepared', None); p['facts'], p['prompt'] = {}, ''
                p.pop('sample_inputs_changed',None);p.pop('sample_facts_stale',None)
            if role == 'video':
                p['selections'] = {}
                p.pop('editor', None)
            save_project(p)
        self.send_json(p)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    DATA.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    server.daemon_threads = True
    url = f'http://127.0.0.1:{server.server_port}'
    print('V2V App: ' + url + '\nLocal media processing; Cloud jobs require explicit confirmation.')
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()

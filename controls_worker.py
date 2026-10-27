"""Offline local EdgeTAM + DWPose worker, launched with a model-capable Python.

Initial boxes originate from user crops. New shots/losses may be located from
saved source descriptions using cached Grounding DINO. No Cloud calls,
grey reference fill, handpicked points, or automatic model installation.
"""
import contextlib
import io
import json
import math
from pathlib import Path
from sample_points import points
import subprocess
import sys
import time


def save(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def geometry(info, source_info):
    if 'source_transform' in info:
        return info['source_transform']
    # Existing 480p checkpoints: yuv420 pad offsets are chroma-aligned.
    w = int(math.floor(source_info['width'] * 480 / source_info['height'] / 2 + .5)) * 2
    return dict(scale_x=w/source_info['width'], scale_y=480/source_info['height'],
                x=(info['width']-w)//4*2, y=0)


def padded_length(duration):
    frames = max(5, round(duration * 24))
    return frames + (5 - frames % 17) % 17


def main(path):
    request = json.loads(Path(path).read_text())
    out = Path(path).parent
    started = time.monotonic()

    def progress(stage, detail):
        save(out/'progress.json', dict(stage=stage, detail=detail, elapsed=round(time.monotonic()-started, 1)))

    extra = request['runtime'].get('control_extra_site')
    if extra:
        sys.path.insert(0, extra)
    sys.path.insert(0, str(Path(request['runtime']['control_aux'])/'src'))
    import cv2
    import numpy as np
    import torch
    from transformers import EdgeTamVideoModel, Sam2VideoProcessor
    from custom_controlnet_aux.dwpose.wholebody import Wholebody
    from custom_controlnet_aux.dwpose import draw_poses
    from custom_controlnet_aux.dwpose.types import Keypoint, BodyResult, PoseResult
    from edge_tracker import _device, _track_one
    from controls import EDGE_MODEL, EDGE_REVISION
    from media import hash_file

    cv2.setNumThreads(4)
    torch.set_grad_enabled(False)
    ff = request['ffmpeg']
    clip, source = request['clip'], request['source']
    info, source_info = request['checkpoint']['info'], request['source_info']
    w, h = info['width'], info['height']
    tr = geometry(info, source_info)
    proxy_h = min(480, h)
    proxy_w = max(2, int(math.floor(w * proxy_h / h / 2 + .5))*2)
    progress('loading', 'Loading the clip and cached local models.')
    decode = subprocess.run([ff, '-v', 'error', '-i', clip, '-vf', f'scale={proxy_w}:{proxy_h}',
                             '-an', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'],
                            capture_output=True, check=True, timeout=180)
    video = np.frombuffer(decode.stdout, np.uint8).reshape(-1, proxy_h, proxy_w, 3).copy()
    n = len(video)
    if not n:
        raise ValueError('The video contains no readable frames.')
    from tracking_review import scenes, segment_track, analyze, signature, VERSION
    review = request.get('review',{'anchors':[],'decisions':[]})
    shots = scenes(video,review.get('ignored_boundaries',[]))
    anchors = {}
    for label, selection in request['checkpoint']['selections'].items():
        anchors[label] = []
        for i, point in enumerate(points(selection)):
            box = point['crop']
            xyxy = [box['x'],box['y'],box['x']+box['width'],box['y']+box['height']]
            index = min(n-1,max(0,round((point['time']-request['checkpoint']['start'])*24)))
            anchors[label].append(dict(frame=index,box=xyxy,initial=True,restart=0,sample_id=f'{label}{i+1}'))
    for anchor in review['anchors']:
        if anchor['label'] in anchors:
            anchors[anchor['label']].append(anchor.copy())
    for label, items in anchors.items():
        for item in items:
            x1,y1,x2,y2=item['box']
            item['box']=[max(0.,(x1*tr['scale_x']+tr['x'])*proxy_w/w),
                         max(0.,(y1*tr['scale_y']+tr['y'])*proxy_h/h),
                         min(float(proxy_w),(x2*tr['scale_x']+tr['x'])*proxy_w/w),
                         min(float(proxy_h),(y2*tr['scale_y']+tr['y'])*proxy_h/h)]
    prompts = {label:[(p['frame'],p['box']) for p in items] for label,items in anchors.items()}
    cache=None;cache_plans=None
    clip_hash=hash_file(Path(clip))
    old=request.get('reuse_from')
    if old:
        old=Path(old)
        try:
            previous=json.loads((old/'tracking-plan.json').read_text())
            old_decisions={signature(d) for d in previous.get('decisions',[])}
            new_decisions={signature(d) for d in review['decisions']}
            if previous['version']==VERSION and previous['clip_hash']==clip_hash and old_decisions<=new_decisions:
                with np.load(old/'tracking.npz') as cached:
                    cache={k:cached[k].copy() for k in cached.files}
                cache_plans=previous['plans']
        except (OSError,ValueError,KeyError):
            pass
    model = EdgeTamVideoModel.from_pretrained(EDGE_MODEL, revision=EDGE_REVISION, local_files_only=True).eval()
    processor = Sam2VideoProcessor.from_pretrained(EDGE_MODEL, revision=EDGE_REVISION, local_files_only=True)
    device = _device('auto')
    descriptions=review.get('descriptions',{})
    auto_anchors=[];detections=[];locator=None
    recovery=[];detection_cache={}
    def apply_absences():
        for decision in review['decisions']:
            if decision['kind']=='absent' and decision['label'] in raw:
                raw[decision['label']][decision['start']:decision['end']]=False
    try:
        model.to(device)
        progress('tracking',f'Tracking {len(shots)} candidate shots independently on {device}.')
        def infer(segment,prompt):
            return _track_one(model,processor,segment,prompt,device).numpy()>0
        raw,plans,session_count=segment_track(video,shots,anchors,infer,cache,cache_plans)
    except (RuntimeError, NotImplementedError):
        if device != 'mps':
            raise
        device = 'cpu'
        model.to(device)
        progress('tracking','This operation is unsupported on MPS; continuing shot tracking on CPU.')
        raw,plans,session_count=segment_track(video,shots,anchors,infer,cache,cache_plans)
    apply_absences()
    # Track existing user seeds first. Their independent masks protect a valid
    # role while another role is reacquired; no shared EdgeTAM object session.
    if descriptions and analyze(raw,shots,anchors,review['decisions'])['requires_input']:
        from text_locator import Locator,relocate
        progress('locating','Locating source people by short descriptions, assigning distinct candidates and preserving valid tracks.')
        try:locator=Locator()
        except OSError as e:
            raise ValueError('Grounding DINO is not cached. Follow the setup instructions to download it locally.') from e
        sample_total=max((len(v) for v in review.get('samples',{}).values()),default=1)
        for attempt in range(3):
            for sample_index in range(sample_total):
                q=analyze(raw,shots,anchors,review['decisions'])
                if not q['requires_input']:break
                progress('locating',f'Local recovery {attempt+1}/3, source sample {sample_index+1}/{sample_total}; timeline-local samples first')
                found,log=relocate(video,shots,anchors,descriptions,locator,q['issues'],review['decisions'],
                                   known=raw,pass_index=attempt,samples=review.get('samples'),sample_index=sample_index,detection_cache=detection_cache)
                detections.extend(log)
                recovery.append(dict(pass_index=attempt,sample_index=sample_index,issues_before=len(q['issues']),
                                     anchors_added=len(found),sampled_frames=sorted(set(e['frame'] for e in log))))
                if not found:continue
                auto_anchors.extend(found)
                raw,plans,calls=segment_track(video,shots,anchors,infer,raw,plans);session_count+=calls
                apply_absences()
            if not analyze(raw,shots,anchors,review['decisions'])['requires_input']:break
        # A repair can expose a new issue late in the last pass. Exhaust any
        # still-untried source sample for that role/shot before requiring review.
        bound=max(1,sample_total*len(anchors)*len(shots))
        for _ in range(bound):
            q=analyze(raw,shots,anchors,review['decisions'])
            if not q['requires_input']:break
            tried={(e['label'],e['shot_start'],e['source_sample']) for e in detections
                   if e.get('attempted_for')==e['label']}
            for label,items in anchors.items():
                for point in items:
                    if point.get('initial'):
                        shot=next((s for s in shots if s['start']<=point['frame']<s['end']),None)
                        if shot:tried.add((label,shot['start'],point['sample_id']))
            found,log=relocate(video,shots,anchors,descriptions,locator,q['issues'],review['decisions'],
                               known=raw,pass_index=2,samples=review.get('samples'),untried=tried,detection_cache=detection_cache)
            if not log:break
            detections.extend(log)
            recovery.append(dict(pass_index=3,exhaustion=True,issues_before=len(q['issues']),
                                 anchors_added=len(found),sampled_frames=sorted(set(e['frame'] for e in log))))
            if found:
                auto_anchors.extend(found)
                raw,plans,calls=segment_track(video,shots,anchors,infer,raw,plans);session_count+=calls
                apply_absences()
        del locator
    for decision in review['decisions']:
        if decision['kind']=='absent' and decision['label'] in raw:
            raw[decision['label']][decision['start']:decision['end']]=False
    quality=analyze(raw,shots,anchors,review['decisions'])
    quality['auto_anchors']=auto_anchors
    quality['descriptions']=descriptions
    quality['recovery']=recovery
    quality['source_samples']=review.get('samples',{})
    for issue in quality['issues']:
        tested={}
        for label in issue['labels']:
            ids={e.get('source_sample') for e in detections if e['label']==label and
                 e.get('attempted_for')==label and issue['shot_start']<=e['frame']<issue['shot_end']}
            ids.update(p.get('sample_id') for p in anchors[label] if p.get('initial') and
                       issue['shot_start']<=p['frame']<issue['shot_end'])
            tested[label]=sorted(v for v in ids if v)
        issue['tested_samples']=tested
        expected={k:{p['sample_id'] for p in review.get('samples',{}).get(k,[])} for k in issue['labels']}
        issue['samples_exhausted']=all(expected[k].issubset(tested[k]) for k in expected)
    if descriptions:
        quality['note']='Source descriptions locate people after cuts and losses. Uncertain matches pause for review. Check the preview; boxes and tracks do not guarantee identity.'
        for issue in quality['issues']:
            issue['reason']='Different frames and appearance queries did not resolve this role. Other valid tracks are preserved. Edit the source description or confirm absence / occlusion.'
    save(out/'text-locations.json',dict(descriptions=descriptions,anchors=auto_anchors,detections=detections,
                                      locator=review.get('locator'),source_samples=review.get('samples',{}),recovery=recovery,cloud_calls=0))
    save(out/'tracking-plan.json',dict(version=VERSION,clip_hash=clip_hash,plans=plans,
                                     decisions=review['decisions'],shots=shots,local_sessions=session_count,
                                     descriptions=descriptions,auto_anchors=auto_anchors))
    save(out/'tracking-review.json',quality)
    del model, processor
    if hasattr(torch, 'mps') and device == 'mps':
        torch.mps.empty_cache()
    grow = request['settings']['grow_px']
    masks, boxes, presence = {}, {}, {}
    for label, values in raw.items():
        masks[label] = np.zeros_like(values)
        boxes[label] = []
        for i, mask in enumerate(values):
            ys, xs = np.where(mask)
            if not len(xs):
                boxes[label].append(None)
                continue
            x1, x2 = max(0,int(xs.min())-grow), min(proxy_w,int(xs.max())+1+grow)
            y1, y2 = max(0,int(ys.min())-grow), min(proxy_h,int(ys.max())+1+grow)
            boxes[label].append([x1,y1,x2,y2])
            masks[label][i,y1:y2,x1:x2] = True
        present = np.flatnonzero(values.reshape(n,-1).any(axis=1))
        presence[label] = dict(nonempty_frames=len(present), first_seconds=float(present[0]/24) if len(present) else None, last_seconds=float(present[-1]/24) if len(present) else None)
    union = np.logical_or.reduce(list(masks.values()))
    np.savez_compressed(out/'tracking.npz', **raw)
    save(out/'tracking-boxes.json', dict(prompts=prompts, boxes=boxes, presence=presence,
                                       device=device, proxy_size=[proxy_w,proxy_h], source_transform=tr))
    if quality['requires_input']:
        progress('needs_input','Some shots remain unresolved. Check source descriptions or confirm normal absence before preparing DWPose.')
        # Encode only a proxy overlay; no control files are considered ready.
        with (out/'preview-encode.log').open('w') as log:
            encoder=subprocess.Popen([ff,'-v','error','-y','-f','rawvideo','-pix_fmt','rgb24',
                '-s',f'{proxy_w}x{proxy_h}','-r','24','-i','-','-an','-c:v','libx264',
                '-crf','18','-threads','1','-pix_fmt','yuv420p','-movflags','+faststart',
                str(out/'mask-overlay.mp4')],stdin=subprocess.PIPE,stderr=log)
            try:
                for i in range(n):
                    overlay=video[i].copy()
                    for label,color in [('A',[255,51,51]),('B',[51,128,255])]:
                        if label in masks:
                            selected=masks[label][i]
                            overlay[selected]=(overlay[selected]*.55+np.array(color)*.45).astype(np.uint8)
                    encoder.stdin.write(overlay.tobytes())
                encoder.stdin.close()
                if encoder.wait(timeout=120):raise ValueError('Could not encode the tracking review preview.')
            finally:
                if encoder.poll() is None:encoder.kill()
                encoder.wait()
        save(out/'manifest.json',dict(requires_input=True,quality=quality,files={},hashes={},
             previews={'mask':'mask-overlay.mp4'},presence=presence,source_frames=n,
             fps=24,width=w,height=h,cloud_calls=0,elapsed=round(time.monotonic()-started,2)))
        return
    if not union.any():
        raise ValueError('No character masks were found. Check that the selected people appear in this clip.')
    progress('pose', 'Loading DWPose and extracting body poses from original-resolution person regions.')
    pose_model = Wholebody(None, request['runtime']['control_pose_model'], torchscript_device='cpu')
    target_frames = padded_length(request['checkpoint']['duration'])
    processes = []
    log = (out/'ffmpeg.log').open('w')

    def encoder(name, width, height, lossless=False):
        codec = ['-c:v','libx264rgb','-crf','0','-pix_fmt','rgb24'] if lossless else ['-c:v','libx264','-crf','18','-pix_fmt','yuv420p']
        p = subprocess.Popen([ff,'-v','error','-y','-f','rawvideo','-pix_fmt','rgb24','-s',f'{width}x{height}',
                              '-r','24','-i','-','-an',*codec,'-threads','1','-movflags','+faststart',str(out/name)],
                             stdin=subprocess.PIPE, stderr=log)
        processes.append(p)
        return p

    mask_out = encoder('mask.mp4', w, h, True)
    pose_out = encoder('pose.mp4', w, h, True)
    mask_preview = encoder('mask-overlay.mp4', proxy_w, proxy_h)
    pose_preview = encoder('pose-overlay.mp4', proxy_w, proxy_h)
    sw, sh = source_info['width'], source_info['height']
    decoder = subprocess.Popen([ff,'-v','error','-ss',str(request['checkpoint']['start']),'-i',source,
                                '-t',str(request['checkpoint']['duration']),'-vf','fps=24','-an',
                                '-f','rawvideo','-pix_fmt','rgb24','-'], stdout=subprocess.PIPE, stderr=log)
    pose_counts = {label:0 for label in prompts}
    entries = []
    last_mask, last_pose = None, None
    try:
        for i in range(n):
            data = bytearray()
            while len(data) < sw*sh*3:
                chunk = decoder.stdout.read(sw*sh*3-len(data))
                if not chunk:
                    break
                data.extend(chunk)
            if len(data) != sw*sh*3:
                raise ValueError('Source and prepared clip frame counts differ. Prepare the inputs again.')
            original = np.frombuffer(data,np.uint8).reshape(sh,sw,3)
            canvas = np.zeros((h,w,3),np.uint8)
            frame_entry = dict(frame=i, roles={})
            for label, sequence in boxes.items():
                box = sequence[i]
                if box is None:
                    continue
                x1,y1,x2,y2 = box
                # Proxy → padded canvas → original pixels.
                ox1=max(0, int(math.floor((x1*w/proxy_w-tr['x'])/tr['scale_x'])))
                oy1=max(0, int(math.floor((y1*h/proxy_h-tr['y'])/tr['scale_y'])))
                ox2=min(sw, int(math.ceil((x2*w/proxy_w-tr['x'])/tr['scale_x'])))
                oy2=min(sh, int(math.ceil((y2*h/proxy_h-tr['y'])/tr['scale_y'])))
                if ox2-ox1 < 24 or oy2-oy1 < 48:
                    frame_entry['roles'][label] = {'status':'crop_too_small'}
                    continue
                with contextlib.redirect_stdout(io.StringIO()):
                    poses = Wholebody.format_result(pose_model(original[oy1:oy2,ox1:ox2].copy()))
                joints = []
                if poses:
                    joints = [None if k is None else Keypoint((float(k.x)+ox1)*tr['scale_x']+tr['x'],
                              (float(k.y)+oy1)*tr['scale_y']+tr['y'], float(k.score), k.id)
                              for k in poses[0].body.keypoints]
                    person = draw_poses([PoseResult(BodyResult(joints),None,None,None)], h,w,
                                        draw_body=True,draw_hand=False,draw_face=False)
                    valid = cv2.resize(masks[label][i].astype(np.uint8),(w,h),interpolation=cv2.INTER_NEAREST)>0
                    person[~valid] = 0
                    canvas = np.maximum(canvas, person)
                    if person.any():
                        pose_counts[label] += 1
                frame_entry['roles'][label] = {'joints': [None if k is None else [float(k.x),float(k.y),float(k.score)] for k in joints]}
            mask = cv2.resize(union[i].astype(np.uint8),(w,h),interpolation=cv2.INTER_NEAREST)*255
            mask_rgb = np.repeat(mask[...,None],3,axis=2)
            if i < target_frames:
                mask_out.stdin.write(mask_rgb.tobytes());pose_out.stdin.write(canvas.tobytes())
                last_mask,last_pose = mask_rgb,canvas
            overlay = video[i].copy()
            for label, color in [('A',[255,51,51]),('B',[51,128,255])]:
                if label in masks:
                    selected=masks[label][i]
                    overlay[selected]=(overlay[selected]*.55+np.array(color)*.45).astype(np.uint8)
            small_pose = cv2.resize(canvas,(proxy_w,proxy_h),interpolation=cv2.INTER_AREA)
            pose_overlay = video[i].copy();visible=small_pose.any(axis=-1);pose_overlay[visible]=small_pose[visible]
            mask_preview.stdin.write(overlay.tobytes());pose_preview.stdin.write(pose_overlay.tobytes())
            entries.append(frame_entry)
            if i%12 == 0:
                progress('pose', f'DWPose frame {i+1}/{n}; A red / B blue masks, standard OpenPose skeleton colors.')
        for i in range(max(0,target_frames-n)):
            mask_out.stdin.write(last_mask.tobytes());pose_out.stdin.write(last_pose.tobytes())
        for p in processes:
            p.stdin.close()
        for p in processes:
            if p.wait(timeout=180):
                raise ValueError('Control video encoding failed. Check the local FFmpeg log.')
        if decoder.wait(timeout=30):
            raise ValueError('Could not read the source video.')
    finally:
        for p in processes+[decoder]:
            if p.poll() is None:
                p.kill()
            p.wait()
        log.close()
    if any(not pose_counts[k] for k in pose_counts if raw[k].any()):
        raise ValueError('At least one role has no usable poses. Choose a clearer keyframe or a larger source box.')
    save(out/'pose-keypoints.json', entries)
    progress('encoding', 'Aligning control frames and preparing source video context.')
    subprocess.run([ff,'-v','error','-y','-i',clip,'-vf',f'tpad=stop_mode=clone:stop={target_frames}',
                    '-frames:v',str(target_frames),'-an','-c:v','libx264','-crf','0','-pix_fmt','yuv420p',
                    '-movflags','+faststart',str(out/'context.mp4')], check=True, capture_output=True, timeout=180)
    files = {'control_mask':'mask.mp4', 'control_pose':'pose.mp4', 'control_source':'context.mp4'}
    from media import probe
    for role,name in files.items():
        actual = probe(out/name)
        if (actual['width'],actual['height']) != (w,h) or abs(actual['fps']-24)>.01 or abs(actual['duration']-target_frames/24)>.05:
            raise ValueError('Control video dimensions, frame counts or frame rates differ.')
    manifest = dict(requires_input=False, quality=quality, files=files, hashes={k:hash_file(out/v) for k,v in files.items()},
                    previews={'mask':'mask-overlay.mp4','pose':'pose-overlay.mp4'},
                    presence=presence, pose_nonempty_frames=pose_counts, frames=target_frames,
                    source_frames=n, width=w, height=h, fps=24, mask_polarity='white=regenerate',
                    source_transform=tr, proxy_size=[proxy_w,proxy_h],
                    pose_source_resolution=[sw,sh], edge_model=EDGE_MODEL, edge_revision=EDGE_REVISION,
                    pose_model_sha256=hash_file(Path(request['runtime']['control_pose_model'])),
                    settings=request['settings'], cloud_calls=0, elapsed=round(time.monotonic()-started,2))
    save(out/'manifest.json', manifest)
    progress('completed', 'Controls are ready. Review the mask and pose previews, then approve them.')


if __name__ == '__main__':
    try:
        main(sys.argv[1])
    except Exception as error:
        folder = Path(sys.argv[1]).parent
        save(folder/'failure.json', {'error':f'{type(error).__name__}: {str(error)[:1200]}'})
        raise

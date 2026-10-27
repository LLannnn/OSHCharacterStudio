"""Offline description grounding. Source descriptions only; no provider calls."""
import math
import re
import itertools

MODEL = 'IDEA-Research/grounding-dino-tiny'
REVISION = 'a2bb814dd30d776dcf7e30523b00659f4f141c71'
PERSON = re.compile(r'\b(woman|women|man|men|male|female|person|people|individual|human|adult|child|girl|boy|lady|gentleman|driver|singer|drummer)\b')
CLOTHING = re.compile(r'\b(beret|hat|cap|shirt|tshirt|t-shirt|top|dress|jacket|coat|sweater|hoodie|trousers|pants|skirt|suit|vest|scarf)\b')


def overlap(a, b):
    area = max(0,min(a[2],b[2])-max(a[0],b[0])) * max(0,min(a[3],b[3])-max(a[1],b[1]))
    aa=max(0,a[2]-a[0])*max(0,a[3]-a[1]);bb=max(0,b[2]-b[0])*max(0,b[3]-b[1])
    return area/max(1,aa+bb-area), area/max(1,aa)


def ranked_candidates(detections, width, height):
    """Reject ambiguous people; detected clothing must belong to the candidate.

    Grounding DINO's maximum token score can rank a blond man above a woman
    described as wearing a beret. A hat-only box must not seed a person either.
    These conservative checks are candidate selection, not identity recognition.
    """
    people=[];attributes={}
    for d in detections:
        b=d.get('box',[]);score=d.get('score',0);label=d.get('text','').lower()
        if len(b)!=4 or not all(isinstance(x,(float,int)) and math.isfinite(x) for x in [*b,score]):continue
        if min(b[2]-b[0],b[3]-b[1])<8:continue
        if PERSON.search(label) and score>=.32:
            people.append(dict(box=[max(0,min(width,b[0])),max(0,min(height,b[1])),
                                    max(0,min(width,b[2])),max(0,min(height,b[3]))],score=score,text=label))
        elif score>=.30:
            for term in CLOTHING.findall(label):attributes.setdefault(term,[]).append(d)
    # Lesser attributes can be missed or detected on a neighbouring person.
    # Use the strongest grounded garment evidence to disambiguate the person.
    strongest=max((d['score'] for ds in attributes.values() for d in ds),default=0)
    attributes={k:ds for k,ds in attributes.items() if max(d['score'] for d in ds)>=strongest-.1}
    candidates=[]
    for p in sorted(people,key=lambda x:-x['score']):
        # Strong garment evidence must lie inside this person.
        if any(not any(overlap(d['box'],p['box'])[1]>=.65 for d in ds) for ds in attributes.values()):continue
        if any(overlap(p['box'],other['box'])[0]>.65 for other in candidates):continue
        candidates.append(p)
    return candidates[:4]


def location_queries(description):
    """Derive short visual noun phrases, without a language-model call.

    Keep source facts unchanged. Drop scene/action clauses from locator-only
    queries so a car or a neighbouring hairstyle does not dominate detection.
    """
    text=description.strip().rstrip(' .')
    person=PERSON.search(text.lower())
    clothing=re.search(r'\b(?:wearing|dressed in|in)\s+(.+)',text,re.I)
    queries=[]
    if person and clothing:
        outfit=re.split(r',|;|\b(?:sitting|standing|driving|walking|holding|singing|looking)\b',clothing.group(1),maxsplit=1,flags=re.I)[0].strip()
        outfit=re.sub(r'[\"\']','',outfit)
        if CLOTHING.search(outfit.lower()):
            first=re.split(r'\s+and\s+',outfit,maxsplit=1,flags=re.I)[0]
            queries.extend(['a '+person.group()+' wearing '+first,'a '+person.group()+' wearing '+outfit])
    queries.append(text)
    return list(dict.fromkeys(q for q in queries if q))


def assign_roles(pools):
    """Joint one-to-one assignment, retaining alternatives instead of vetoing both.

    Scores are ranking heuristics, not calibrated identity probabilities. If
    near-optimal assignments disagree about a role, leave that role unresolved.
    Already tracked roles can be locked to their independent mask for this frame.
    """
    labels=list(pools)
    options=[ps if ps and ps[0].get('locked') else [None,*ps] for ps in pools.values()]
    possibilities=[]
    for values in itertools.product(*options):
        chosen=[p for p in values if p]
        if any(overlap(a['box'],b['box'])[0]>.65 for i,a in enumerate(chosen) for b in chosen[i+1:]):continue
        score=sum(p['score']+.03*(len(p.get('queries',[]))-1) for p in chosen)
        possibilities.append((score,values))
    if not possibilities:return {k:None for k in labels}
    best=max(s for s,_ in possibilities)
    near=[values for s,values in possibilities if best-s<.08]
    result={}
    for i,label in enumerate(labels):
        candidates=[v[i] for v in near]
        if all(candidates) and all(overlap(candidates[0]['box'],p['box'])[0]>.65 for p in candidates[1:]):
            result[label]=max(candidates,key=lambda p:p['score'])
        else:result[label]=None
    return result


class Locator:
    def __init__(self):
        import torch
        from transformers import AutoProcessor, AutoModelForZeroShotObjectDetection
        torch.set_num_threads(4)
        self.processor=AutoProcessor.from_pretrained(MODEL,revision=REVISION,local_files_only=True)
        self.model=AutoModelForZeroShotObjectDetection.from_pretrained(MODEL,revision=REVISION,local_files_only=True).eval()

    def __call__(self,image,description):
        import torch
        from PIL import Image
        image=Image.fromarray(image)
        inputs=self.processor(images=image,text=description.rstrip(' .')+'.',return_tensors='pt')
        if inputs.input_ids.shape[-1]>256:
            raise ValueError('The locator description is too long. Keep source appearance and clothing; shorten action and scene details.')
        with torch.inference_mode():outputs=self.model(**inputs)
        r=self.processor.post_process_grounded_object_detection(outputs,inputs.input_ids,
              threshold=.25,text_threshold=.25,target_sizes=[image.size[::-1]])[0]
        return [dict(box=b.tolist(),score=float(s),text=t) for b,s,t in
                zip(r['boxes'],r['scores'],r.get('text_labels',r.get('labels',[])))]


def relocate(video, shots, anchors, descriptions, detect, issues=None, decisions=(), known=None, pass_index=0, samples=None, sample_index=None, untried=None, detection_cache=None):
    """Return added anchors and an auditable detection log; never invent absence."""
    entries=[];added=[];h,w=video.shape[1:3];cache=detection_cache if detection_cache is not None else {}
    import numpy as np
    issues=issues or []
    tasks=[]
    for shot in shots:
        if shot['black']:continue
        for label in anchors:
            if not any(shot['start']<=p['frame']<shot['end'] for p in anchors[label]):
                tasks.append((label,shot['start'],shot['end'],shot))
    for issue in issues:
        if issue['kind'] in ('missing','overlap'):
            shot=next(s for s in shots if s['start']==issue['shot_start'])
            for label in issue['labels']:tasks.append((label,issue['start'],issue['end'],shot))
    fractions=[(.25,.5,.75),(.1,.4,.6,.9),(.05,.2,.8,.95)][min(pass_index,2)]
    tasks=list({(label,a,b):(label,a,b,shot) for label,a,b,shot in tasks}.values())
    frame_results={}

    def ordered_samples(role, a, b, shot):
        values=(samples or {}).get(role) or [dict(sample_id=role+'1',frame=-1,description=descriptions.get(role,''))]
        # Prefer explicit samples in this review interval, then this shot, then
        # other samples inside the selected source timeline. Never use target images.
        return sorted(values,key=lambda v:(0 if a<=v.get('frame',-1)<b else
            1 if shot['start']<=v.get('frame',-1)<shot['end'] else 2,
            abs(v.get('frame',-1)-(a+b-1)/2),v.get('sample_id','')))

    def locate_frame(frame, selected_role, sample, shot):
        result_key=(frame,selected_role,sample['sample_id'])
        if result_key in frame_results:return frame_results[result_key]
        pools={};logs={}
        for role in anchors:
            boxes=[]
            # A valid independent mask is not revoked by another role's text.
            bad=any(role in i['labels'] and i['start']<=frame<i['end'] for i in issues)
            if known is not None and role in known and not bad:
                ys,xs=np.where(known[role][frame])
                if len(xs):boxes=[dict(box=[int(xs.min()),int(ys.min()),int(xs.max())+1,int(ys.max())+1],score=1.,locked=True,queries=[])]
            pools[role]=boxes;logs[role]=[]

        def query(role,text):
            key=(frame,text)
            if key not in cache:cache[key]=detect(video[frame],text)
            ds=cache[key];logs[role].append(dict(query=text,detections=ds))
            for candidate in ranked_candidates(ds,w,h):
                same=next((p for p in pools[role] if overlap(p['box'],candidate['box'])[0]>.65),None)
                if same:
                    same['queries']=list(dict.fromkeys([*same['queries'],text]))
                    if candidate['score']>same['score']:same.update(box=candidate['box'],score=candidate['score'],text=candidate['text'])
                else:pools[role].append(dict(candidate,queries=[text]))
            pools[role].sort(key=lambda p:-p['score']);pools[role]=pools[role][:4]

        chosen_samples={k:(sample if k==selected_role else ordered_samples(k,shot['start'],shot['end'],shot)[0]) for k in anchors}
        variants={k:([] if any(d['kind']=='absent' and d['label']==k and d['start']<=frame<d['end'] for d in decisions)
                     else location_queries(chosen_samples[k].get('description',''))) for k in anchors}
        for role in anchors:
            if not pools[role] and variants[role]:query(role,variants[role][0])
        assigned=assign_roles(pools)
        if any(assigned.get(role) is None and variants[role] for role in anchors):
            for role in anchors:
                if pools[role] and pools[role][0].get('locked'):continue
                for text in variants[role][1:]:query(role,text)
            assigned=assign_roles(pools)
        for role in anchors:
            entries.append(dict(label=role,frame=frame,description=descriptions.get(role,''),
                candidate=assigned.get(role),candidates=pools[role],queries=logs[role],pass_index=pass_index,
                source_sample=chosen_samples[role].get('sample_id'),source_sample_frame=chosen_samples[role].get('frame'),
                attempted_for=selected_role,shot_start=shot['start'],
                reason='Preserved independent tracking.' if pools[role] and pools[role][0].get('locked') else
                       'Assigned a distinct person.' if assigned.get(role) else 'Ambiguous or missing candidate; other valid tracks are preserved.'))
        frame_results[result_key]=assigned
        return assigned

    for label,a,b,shot in tasks:
        desc=descriptions.get(label,'').strip()
        if not desc or any(d['kind']=='absent' and d['label']==label and d['start']<=a and d['end']>=b for d in decisions):continue
        frames=sorted(set(min(b-1,a+int((b-a)*f)) for f in fractions))
        ordered=ordered_samples(label,shot['start'],shot['end'],shot)
        attempts=ordered if sample_index is None else ordered[sample_index:sample_index+1]
        if untried is not None:
            attempts=[v for v in attempts if (label,shot['start'],v['sample_id']) not in untried]
        for sample in attempts:
            accepted=[]
            for frame in frames:
                candidate=locate_frame(frame,label,sample,shot).get(label)
                if candidate:accepted.append((frame,candidate))
            if not accepted:continue
            consistent=[(f,p) for f,p in accepted if len(frames)==1 or
                        any(g!=f and overlap(p['box'],q['box'])[0]>.2 for g,q in accepted)]
            if not consistent:continue
            frame,candidate=max(consistent,key=lambda x:x[1]['score'])
            if any(p['frame']==frame and p.get('auto') and p.get('source_sample')==sample['sample_id'] for p in anchors[label]):continue
            anchor=dict(label=label,frame=frame,box=candidate['box'],restart=a,auto=True,
                        score=candidate['score'],description=sample.get('description',desc),source_sample=sample['sample_id'])
            anchors[label][:]=[p for p in anchors[label] if not (p.get('auto') and p.get('restart')==a)]
            anchors[label].append(anchor);added.append(anchor)
            break
    return added,entries


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description='Prepare the optional local text locator cache')
    parser.add_argument('--download',action='store_true',help='Explicitly download the pinned official model (no inference)')
    args=parser.parse_args()
    if not args.download:parser.error('Use --download to prepare the cache; normal app inference stays offline')
    from huggingface_hub import snapshot_download
    print(snapshot_download(MODEL,revision=REVISION,allow_patterns=['*.json','*.txt','model.safetensors']))

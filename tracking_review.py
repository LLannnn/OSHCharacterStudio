"""Heuristic shot boundaries and explicit human correction checkpoints.

No identity recognition claims: cuts and empty masks are review candidates.
All frame ranges are [start,end), in the prepared 24fps clip.
"""
import hashlib
import json

VERSION = 5


def runs(values):
    start = None
    for i, value in enumerate(list(values)+[False]):
        if value and start is None:
            start = i
        elif not value and start is not None:
            yield start, i
            start = None


def scenes(video,ignored_boundaries=()):
    import numpy as np
    n,h,w = video.shape[:3]
    sample = video[:,h//8:7*h//8: max(1,h//48),w//8:7*w//8:max(1,w//64)].astype(np.float32)
    brightness = sample.mean(axis=(1,2,3))
    dark = (brightness < 18) & (np.percentile(sample,90,axis=(1,2,3)) < 25)
    # Isolated dark frames are not enough to declare a black transition.
    black = np.zeros(n,bool)
    for a,b in runs(dark):
        if b-a>=2:
            black[a:b]=True
    change = np.r_[0,np.abs(sample[1:]-sample[:-1]).mean(axis=(1,2,3))]
    cuts = [i for i in range(1,n) if change[i]>32 and not black[i] and not black[i-1]
            and all(abs(i-b)>=6 for b in ignored_boundaries)]
    # Suppress adjacent peaks from a single candidate transition.
    selected=[]
    for i in cuts:
        if not selected or i-selected[-1]>=6:
            selected.append(i)
        elif change[i]>change[selected[-1]]:
            selected[-1]=i
    boundaries={0,n,*selected}
    for a,b in runs(black):boundaries.update([a,b])
    points=sorted(boundaries)
    shots=[dict(start=a,end=b,black=bool(black[a:b].all()),reason='black' if black[a:b].all()
                else ('start' if a==0 else 'black_end' if black[a-1] else 'candidate_cut'))
           for a,b in zip(points,points[1:])]
    return shots


def signature(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


def segment_track(video, shots, anchors, infer, cached=None, cache_plan=None):
    """Independent sessions per shot; corrective anchors only overwrite their tail.

    infer(segment, [(local_frame, box)]) -> binary masks. Stable segments may be
    copied from the previous run, avoiding another local inference for them.
    """
    import numpy as np
    n,h,w=video.shape[:3]
    raw={};plans={};calls=0
    for label,items in anchors.items():
        raw[label]=np.zeros((n,h,w),bool);plans[label]={}
        for shot in shots:
            a,b=shot['start'],shot['end']
            if shot['black']:continue
            prompts=sorted([p for p in items if a<=p['frame']<b],key=lambda x:(x.get('restart',a),x['frame']))
            key=f'{a}:{b}';entry={'points':prompts};plans[label][key]=entry
            old=(cache_plan or {}).get(label,{}).get(key)
            reusable=cached is not None and label in cached and cached[label].shape==raw[label].shape
            if reusable and old==entry:
                raw[label][a:b]=cached[label][a:b];continue
            if not prompts:continue
            seeds=[p for p in prompts if p.get('initial')]
            seed=seeds[0] if seeds else prompts[0]
            corrections=[p for p in prompts if p not in seeds] if seeds else prompts[1:]
            prior=old.get('points',[]) if isinstance(old,dict) else []
            if reusable and prior and prompts[:len(prior)]==prior:
                raw[label][a:b]=cached[label][a:b]
                corrections=[p for p in corrections if p not in prior]
            elif len(seeds)>1:
                # Each sample gets its own EdgeTAM session, never a joint prompt.
                # Prefer the nearest source-time sample for each output frame;
                # a nonempty alternative may recover an empty nearest track.
                best=np.full(b-a,float('inf'))
                for point in sorted(seeds,key=lambda p:p['frame']):
                    tracked=infer(video[a:b],[(point['frame']-a,point['box'])]);calls+=1
                    present=tracked.reshape(b-a,-1).any(axis=1)
                    distance=np.abs(np.arange(a,b)-point['frame'])
                    take=present & (distance<best)
                    raw[label][a:b][take]=tracked[take];best[take]=distance[take]
            else:
                raw[label][a:b]=infer(video[a:b],[(seed['frame']-a,seed['box'])]);calls+=1
            for i,p in enumerate(corrections):
                left=max(a,p.get('restart',p['frame']))
                right=min(b,corrections[i+1].get('restart',b) if i+1<len(corrections) else b)
                if not left<=p['frame']<right:
                    continue
                raw[label][left:right]=infer(video[left:right],[(p['frame']-left,p['box'])]);calls+=1
    return raw,plans,calls


def analyze(raw,shots,anchors,decisions=(),fps=24):
    import numpy as np
    issues=[]
    handled={d['issue_id'] for d in decisions}

    def issue(kind,labels,a,b,shot,reason):
        ident=f'{kind}:{"+".join(labels)}:{a}:{b}'
        if ident in handled:return
        if len(labels)==1 and any(d['kind']=='absent' and d['label']==labels[0]
                                  and d['start']<=a and d['end']>=b for d in decisions):return
        suggested=min(shot['end']-1,max(a,(a+b-1)//2))
        issues.append(dict(id=ident,kind=kind,labels=labels,start=a,end=b,
                           shot_start=shot['start'],shot_end=shot['end'],frame=suggested,
                           seconds=suggested/fps,reason=reason,boundary_reason=shot.get('reason','')))
    for label,mask in raw.items():
        present=mask.reshape(len(mask),-1).any(axis=1)
        for shot in shots:
            a,b=shot['start'],shot['end']
            if shot['black']:continue
            if not any(a<=p['frame']<b for p in anchors[label]):
                issue('anchor',[label],a,b,shot,'Confirm the person in this new shot, or mark them absent.')
                continue
            for x,y in runs(~present[a:b]):
                if y-x>=6:
                    issue('missing',[label],a+x,a+y,shot,'The mask is empty for several frames. Check tracking loss, absence or occlusion.')
    labels=list(raw)
    if len(labels)==2:
        a,b=(raw[k] for k in labels)
        union=(a|b).sum(axis=(1,2));intersection=(a&b).sum(axis=(1,2))
        overlap=np.divide(intersection,union,out=np.zeros_like(union,dtype=float),where=union>0)>.8
        for shot in shots:
            if shot['black']:continue
            for x,y in runs(overlap[shot['start']:shot['end']]):
                if y-x>=6:
                    issue('overlap',labels,shot['start']+x,shot['start']+y,shot,'The two tracks overlap heavily and may follow the same person.')
    return dict(version=VERSION,shots=shots,issues=issues,requires_input=bool(issues),
                note='Cuts and tracking loss are detected heuristically. Confirm normal absence and occlusion. Passing review does not guarantee identity or generation quality.')

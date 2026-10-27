"""Pure tracking/repair tests: no network, model or paid calls."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import app
import controls
from tracking_review import scenes,segment_track,analyze


class TrackingReviewTests(unittest.TestCase):
    def video(self):
        v=np.full((60,32,48,3),170,np.uint8);v[20:30]=0;v[30:]=[80,130,180]
        return v

    def infer(self,clip,prompts):
        mask=np.zeros(clip.shape[:3],bool)
        x1,y1,x2,y2=map(int,prompts[0][1]);mask[:,y1:y2,x1:x2]=True
        return mask

    def test_black_separates_sessions_and_requires_new_role_box(self):
        video=self.video();shots=scenes(video)
        self.assertEqual([(s['start'],s['end'],s['black'])for s in shots],[(0,20,False),(20,30,True),(30,60,False)])
        anchors={'A':[dict(frame=10,box=[3,4,16,24],initial=True,restart=0)]}
        raw,plan,calls=segment_track(video,shots,anchors,self.infer)
        q=analyze(raw,shots,anchors)
        self.assertEqual(calls,1);self.assertFalse(raw['A'][20:].any())
        self.assertEqual([(i['kind'],i['start'],i['end'])for i in q['issues']],[('anchor',30,60)])
        # A new box is the same role, on the other side of the black transition.
        anchors['A'].append(dict(frame=45,box=[20,4,35,24],restart=30))
        repaired,_,calls=segment_track(video,shots,anchors,self.infer,raw,plan)
        self.assertEqual(calls,1);self.assertTrue(np.array_equal(raw['A'][:20],repaired['A'][:20]))
        self.assertFalse(repaired['A'][20:30].any());self.assertTrue(repaired['A'][30:].any())
        self.assertFalse(analyze(repaired,shots,anchors)['requires_input'])

    def test_same_shot_repair_only_recomputes_lost_tail(self):
        video=np.full((100,32,48,3),120,np.uint8);shots=scenes(video)
        first=dict(frame=10,box=[2,4,15,24],initial=True,restart=0)
        anchors={'A':[first]};raw,plan,_=segment_track(video,shots,anchors,self.infer)
        anchors['A'].append(dict(frame=70,box=[22,4,40,24],restart=60))
        calls=[]
        def inferred(v,p):calls.append(len(v));return self.infer(v,p)
        new,_,count=segment_track(video,shots,anchors,inferred,raw,plan)
        self.assertEqual(count,1);self.assertEqual(calls,[40]);self.assertTrue(np.array_equal(new['A'][:60],raw['A'][:60]))
        self.assertTrue(new['A'][60:,4:24,22:40].all());self.assertFalse(new['A'][60:,4:24,2:15].any())

    def test_user_can_dismiss_false_cut_without_bridging_black_frames(self):
        v=np.full((40,32,48,3),80,np.uint8);v[20:]=180
        self.assertEqual(len(scenes(v)),2)
        self.assertEqual(len(scenes(v,[20])),1)
        video=self.video()
        self.assertEqual([(s['start'],s['end'],s['black'])for s in scenes(video,[20,30])],
                         [(0,20,False),(20,30,True),(30,60,False)])

    def test_normal_exit_is_reviewable_not_forced_to_have_mask(self):
        video=np.full((30,32,48,3),120,np.uint8);shots=scenes(video)
        raw={'A':np.zeros(video.shape[:3],bool)};raw['A'][:15,4:20,3:14]=True
        anchors={'A':[dict(frame=8,box=[3,4,14,20])]}
        issue=analyze(raw,shots,anchors)['issues'][0]
        self.assertEqual(issue['kind'],'missing')
        decision=dict(issue_id=issue['id'],kind='absent',label='A',start=15,end=30)
        self.assertFalse(analyze(raw,shots,anchors,[decision])['requires_input'])
        self.assertFalse(raw['A'][15:].any())

    def test_overlap_uses_raw_pixel_masks_not_intersecting_bounding_boxes(self):
        video=np.full((20,32,48,3),120,np.uint8);shots=scenes(video)
        a=np.zeros(video.shape[:3],bool);a[:,2:28,3:8]=True;a[:,23:28,3:35]=True
        b=np.zeros_like(a);b[:,2:20,18:35]=True
        anchors={k:[dict(frame=5,box=[0,0,40,30])]for k in ['A','B']}
        self.assertFalse(analyze({'A':a,'B':b},shots,anchors)['requires_input'])
        self.assertEqual(analyze({'A':a,'B':a.copy()},shots,anchors)['issues'][0]['kind'],'overlap')

    def test_absence_decision_preserves_prompt_and_checkpoint_and_rejects_stale_review(self):
        with tempfile.TemporaryDirectory() as temp:
            old=app.DATA;app.DATA=Path(temp)
            try:
                p=app.new_project();pid=p['id'];p.update(prompt='keep my reviewed prompt')
                c={'id':'checkpoint','start':5.0,'end':9.0,'duration':4,'selections':{},'files':{},'hashes':{}}
                p['prepared']=c;p['generation']=controls.options({'generation_version':'v2'})
                cid='c'*32;folder=app.project_dir(pid)/'controls'/cid;folder.mkdir(parents=True)
                p['control_run']={'id':cid};app.save_project(p)
                issue=dict(id='anchor:A:48:96',kind='anchor',labels=['A'],start=48,end=96,shot_start=48,shot_end=96,frame=70)
                record=dict(id=cid,status='needs_input',identity=controls.identity(c,p['generation']),quality={'issues':[issue]})
                app.write_json(folder/'control.json',record);app.write_json(folder/'request.json',dict(checkpoint=c,source_info=dict(width=1920,height=1080)))
                data=dict(control_id=cid,issue_id=issue['id'],label='A',action='absent')
                with self.assertRaises(ValueError):app.save_tracking_correction(pid,dict(data,action='rebox'))
                with self.assertRaises(ValueError):app.save_tracking_correction(pid,dict(data,label='B'))
                app.save_tracking_correction(pid,data)
                new=app.project(pid);self.assertEqual(new['prompt'],p['prompt']);self.assertEqual(new['prepared'],c)
                self.assertEqual(new['tracking_inputs']['decisions'][0]['kind'],'absent')
                self.assertEqual(new['tracking_inputs']['decisions'][0]['start'],48)
                self.assertFalse(app.control_state(pid)['valid'])
                with self.assertRaises(ValueError):app.save_tracking_correction(pid,data)
                with self.assertRaises(ValueError):app.checked_controls(pid,reviewed=True)
            finally:app.DATA=old


if __name__=='__main__':unittest.main()

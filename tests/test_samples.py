"""Multi-sample input/recovery contracts. Local only; no provider or model calls."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from PIL import Image
import app
import controls
import media
import sample_points
import workflow
from text_locator import relocate
from tracking_review import segment_track, analyze


class SampleTests(unittest.TestCase):
    def test_legacy_normalization_bounds_and_all_sample_equality(self):
        old=dict(time=1,crop=dict(x=0,y=0,width=20,height=30))
        self.assertEqual(sample_points.normalize(old,4),old)
        multi=dict(old,samples=[old,dict(old,time=2)])
        result=sample_points.normalize(multi,4,0,3,True)
        self.assertEqual(len(sample_points.content(result)),2)
        for value in [dict(multi,samples=[]),dict(multi,samples=[old]*7),dict(multi,samples=[old,dict(old,time=3.5)])]:
            with self.assertRaises(ValueError):sample_points.normalize(value,4,0,3,True)
        with self.assertRaises(ValueError):sample_points.normalize(dict(multi,samples=[old,dict(time=2)]),4,0,3,True)

    def test_atlas_keeps_roles_and_numbered_views_separate(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)
            for name,color in [('one','red'),('two','blue')]:Image.new('RGB',(60,90),color).save(p/(name+'.png'))
            manifest=sample_points.atlas([p/'one.png',p/'two.png'],[1.25,3.5],'A',p/'atlas.png')
            self.assertEqual(manifest['sample_ids'],['A1','A2'])
            with Image.open(p/'atlas.png') as im:
                self.assertEqual(im.size,(1024,544))
                self.assertEqual(im.getpixel((256,288)),(255,0,0));self.assertEqual(im.getpixel((768,288)),(0,0,255))

    def test_every_sample_needs_an_individual_fact_and_prompt_uses_stable_summary(self):
        facts={k:'observed '+k for k in workflow.SINGLE_FIELDS}
        with self.assertRaises(ValueError):workflow.parse_facts(facts,1,{'A':2})
        facts['source_samples_A']=['front view pink dress','back view pink beret']
        parsed=workflow.parse_facts(facts,1,{'A':2})
        self.assertEqual(parsed['source_samples_A'],facts['source_samples_A'])
        text=workflow.assemble_prompt(parsed,count=1)
        self.assertIn('observed source_A',text);self.assertNotIn('back view pink beret',text)
        bad=dict(facts,source_samples_A=['only one'])
        with self.assertRaises(ValueError):workflow.parse_facts(bad,1,{'A':2})
        names=dict(clip='clip.mp4',source_A='atlas-A.png',target_A='target.png')
        g=workflow.prompt_graph(names,1,{'A':2})
        self.assertEqual(sum(n['class_type']=='GeminiNodeV3' for n in g.values()),1)
        self.assertIn('source_samples_A: exactly 2',g['722']['inputs']['model.system_prompt'])
        self.assertEqual(g['1']['inputs']['image'],'atlas-A.png')
        self.assertFalse(any('Sampler' in n['class_type'] for n in g.values()))

    def test_each_human_sample_has_independent_tam_session_and_empty_first_can_fall_back(self):
        video=np.ones((50,16,24,3),np.uint8)
        shots=[dict(start=0,end=50,black=False)]
        anchors={'A':[dict(frame=5,box=[1,1,5,8],initial=True,sample_id='A1'),dict(frame=35,box=[12,2,20,14],initial=True,sample_id='A2')]}
        calls=[]
        def infer(v,prompts):
            calls.append(prompts);self.assertEqual(len(prompts),1)
            mask=np.zeros(v.shape[:3],bool)
            if prompts[0][0]==35:mask[:,2:14,12:20]=True
            return mask
        raw,plan,count=segment_track(video,shots,anchors,infer)
        self.assertEqual(count,2);self.assertEqual(len(calls),2)
        self.assertTrue(raw['A'].reshape(50,-1).any(axis=1).all())
        self.assertFalse(analyze(raw,shots,anchors)['requires_input'])
        _,_,cached=segment_track(video,shots,anchors,infer,raw,plan);self.assertEqual(cached,0)

    def test_recovery_tries_timeline_local_sample_first_then_other_samples(self):
        video=np.ones((80,32,48,3),np.uint8)
        shots=[dict(start=0,end=40,black=False),dict(start=40,end=80,black=False)]
        samples={'A':[dict(sample_id='A1',frame=70,description='woman in black'),dict(sample_id='A2',frame=10,description='woman in pink')]}
        anchors={'A':[]};calls=[]
        def detect(v,text):
            calls.append(text)
            return [dict(box=[3,3,24,30],score=.8,text='woman')] if 'black' in text else []
        added,logs=relocate(video,shots[:1],anchors,{'A':'woman'},detect,samples=samples)
        self.assertEqual(added[0]['source_sample'],'A1')
        rolelogs=[e for e in logs if e['label']=='A']
        self.assertEqual(rolelogs[0]['source_sample'],'A2')
        self.assertEqual({e['source_sample'] for e in rolelogs},{'A1','A2'})
        anchors={'A':[]}
        added,logs=relocate(video,shots[:1],anchors,{'A':'woman'},lambda v,t:[],samples=samples)
        self.assertEqual(added,[])
        self.assertEqual({e['source_sample'] for e in logs},{'A1','A2'})

    def test_worker_sample_index_does_not_retest_first_sample_instead_of_second(self):
        video=np.ones((40,32,48,3),np.uint8);shots=[dict(start=0,end=40,black=False)]
        samples={'A':[dict(sample_id='A1',frame=3,description='woman in black'),dict(sample_id='A2',frame=30,description='woman in pink')]}
        seen=[]
        for index in range(2):
            _,log=relocate(video,shots,{'A':[]},{'A':'woman'},lambda v,t:[],samples=samples,sample_index=index)
            seen.extend(e['source_sample'] for e in log)
        self.assertEqual(set(seen),{'A1','A2'})

    def test_identical_detector_inputs_are_cached_without_skipping_sample_audits(self):
        video=np.ones((40,32,48,3),np.uint8);shots=[dict(start=0,end=40,black=False)]
        samples={'A':[dict(sample_id='A1',frame=5,description='woman'),dict(sample_id='A2',frame=30,description='woman')]}
        cache={};calls=[];records=[]
        def detect(v,t):calls.append(t);return []
        for i in range(2):
            _,log=relocate(video,shots,{'A':[]},{'A':'woman'},detect,samples=samples,sample_index=i,detection_cache=cache)
            records.extend(log)
        self.assertEqual(len(calls),3)
        self.assertEqual({e['source_sample'] for e in records},{'A1','A2'})

    def test_final_exhaustion_pass_tries_only_remaining_source_samples(self):
        video=np.ones((40,32,48,3),np.uint8);shots=[dict(start=0,end=40,black=False)]
        samples={'A':[dict(sample_id='A1',frame=5,description='woman in black'),dict(sample_id='A2',frame=30,description='woman in pink')]}
        calls=[]
        def detect(v,t):calls.append(t);return []
        _,log=relocate(video,shots,{'A':[]},{'A':'woman'},detect,samples=samples,untried={('A',0,'A2')})
        self.assertEqual({e['source_sample'] for e in log},{'A1'})
        calls.clear()
        _,log=relocate(video,shots,{'A':[]},{'A':'woman'},detect,samples=samples,untried={('A',0,'A1'),('A',0,'A2')})
        self.assertEqual(log,[]);self.assertEqual(calls,[])

    def test_additional_point_keeps_video_checkpoint_and_marks_sample_revision(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(app,'DATA',Path(folder)):
                p=app.new_project();pid=p['id'];base=app.project_dir(pid)
                point=dict(time=.5,crop=dict(x=0,y=0,width=20,height=30))
                p['assets']['video']=dict(file='clip.mp4',info=dict(duration=4))
                p['selections']={'A':point}
                p['prepared']=dict(id='checkpoint',start=0,end=3,selections={'A':point},character_count=1,resolution='480p')
                app.save_project(p)
                payload=dict(video_file='clip.mp4',character_count=1,range=dict(start=0,end=3),selections={'A':dict(point,samples=[point])})
                app.save_editor(pid,payload);self.assertIn('prepared',app.project(pid))
                payload['selections']['A']['samples'].append(dict(point,time=2))
                app.save_editor(pid,payload);self.assertIn('prepared',app.project(pid))
                self.assertTrue(app.project(pid)['sample_inputs_changed'])
                self.assertEqual(len(app.project(pid)['selections']['A']['samples']),2)

    def test_real_multi_sample_preparation_and_snapshot_bound_parse(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(app,'DATA',Path(folder)):
                p=app.new_project();pid=p['id'];base=app.project_dir(pid)
                src=base/'source.mp4'
                media.run([media.binary('ffmpeg'),'-v','error','-y','-f','lavfi','-i','testsrc2=s=320x180:r=24:d=3','-c:v','libx264','-pix_fmt','yuv420p',str(src)])
                Image.new('RGB',(40,60),'green').save(base/'target.png')
                p['assets']=dict(video=dict(file=src.name,info=media.probe(src)),target_A=dict(file='target.png'));app.save_project(p)
                point=dict(time=.25,crop=dict(x=10,y=10,width=60,height=80))
                selection=dict(point,samples=[point,dict(point,time=1.5)])
                p=app.prepare(pid,dict(start=0,end=2,character_count=1,selections={'A':selection},resolution='480p'))
                c=p['prepared'];self.assertEqual(sample_points.counts(c),{'A':2})
                self.assertEqual(c['atlases']['A']['sample_ids'],['A1','A2'])
                self.assertEqual(len(c['sample_hashes']),2)
                with Image.open(base/c['files']['source_A']) as im:self.assertEqual(im.size,(1024,544))
                facts={k:'observed '+k for k in workflow.SINGLE_FIELDS}
                facts['source_samples_A']=['front view woman','back view woman']
                p['facts']=facts;app.save_project(p)
                inputs=app.tracking_inputs(p,c)
                self.assertEqual([s['description'] for s in inputs['samples']['A']],facts['source_samples_A'])
                old=controls.identity(c,controls.options({}),inputs)
                facts['source_samples_A'][1]='side view woman';p['facts']=facts
                self.assertNotEqual(old,controls.identity(c,controls.options({}),app.tracking_inputs(p,c)))
                p['prompt']='Keep this user-edited prompt exactly.';app.save_project(p)
                original_atlas=(base/c['files']['source_A']).read_bytes()
                stale_plan=app.make_plan(pid,'prompt')
                updated=copy.deepcopy(p['selections']);updated['A']['samples'].append(dict(point,time=1.75))
                app.save_editor(pid,dict(video_file=src.name,character_count=1,range=dict(start=0,end=2),selections=updated,resolution='480p'))
                self.assertEqual(app.project(pid)['prompt'],p['prompt'])
                with patch.object(app,'read_key',return_value='unused'):
                    with self.assertRaises(ValueError):app.accept_plan(stale_plan['id'],stale_plan['fingerprint'],True)
                with patch.object(app,'prepare_clip',side_effect=AssertionError('Do not remake the clip')),patch.object(app,'Cloud',side_effect=AssertionError('No Cloud calls')):
                    after=app.update_samples(pid)
                new=after['prepared']
                self.assertEqual(after['prompt'],p['prompt']);self.assertTrue(after['sample_facts_stale'])
                self.assertFalse(after['sample_inputs_changed']);self.assertNotEqual(new['id'],c['id'])
                self.assertEqual(new['files']['clip'],c['files']['clip'])
                self.assertEqual(new['files']['target_A'],c['files']['target_A'])
                self.assertEqual((base/c['files']['source_A']).read_bytes(),original_atlas)
                self.assertEqual(after['facts']['source_samples_A'],['front view woman','side view woman',facts['source_A']])
                self.assertEqual(app.tracking_inputs(after,new)['samples']['A'][2]['description'],facts['source_A'])
                reordered=copy.deepcopy(after['selections'])
                reordered['A']['samples']=[reordered['A']['samples'][2],reordered['A']['samples'][0]]
                app.save_editor(pid,dict(video_file=src.name,character_count=1,range=dict(start=0,end=2),selections=reordered,resolution='480p'))
                final=app.update_samples(pid)
                self.assertEqual(final['facts']['source_samples_A'],[facts['source_A'],'front view woman'])
                self.assertEqual(final['prompt'],p['prompt'])



if __name__=='__main__':unittest.main()

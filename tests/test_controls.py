"""No paid calls. Graph routing, stale review, upload binding and real media geometry."""
import copy
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch

import app
import controls
import controls_worker
import media
import workflow


class ControlsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.old=app.DATA;app.DATA=Path(self.temp.name)
        self.p=app.new_project();self.pid=self.p['id'];self.base=app.project_dir(self.pid)
        self.names={k:k+'.mp4' for k in ['clip','source_A','target_A','target_B','control_mask','control_pose','control_source']}
        self.c={'id':'prepared-test','character_count':1,'duration':7,'start':0,'info':{'width':864,'height':480},'files':{},'hashes':{}}
        for role in self.names:
            if role.startswith('control_'):continue
            f=self.base/self.names[role];f.write_bytes(role.encode())
            self.c['files'][role]=f.name;self.c['hashes'][role]=media.hash_file(f)
        self.p['prepared']=self.c;app.save_project(self.p)

    def tearDown(self):
        app.DATA=self.old;self.temp.cleanup()

    def ready(self):
        app.generation_settings(self.pid,{'generation_version':'v2'})
        cid='a'*32;folder=self.base/'controls'/cid;folder.mkdir(parents=True)
        files={};hashes={}
        for role in ['control_mask','control_pose','control_source']:
            f=folder/(role+'.mp4');f.write_bytes(role.encode());files[role]=str(f.relative_to(self.base));hashes[role]=media.hash_file(f)
        rec=dict(id=cid,status='completed',approved=False,files=files,hashes=hashes,
                 identity=controls.identity(self.c,controls.options({'generation_version':'v2'})))
        app.write_json(folder/'control.json',rec)
        p=app.project(self.pid);p['control_run']={'id':cid};app.save_project(p)
        return cid,folder

    def test_runtime_readiness_requires_actual_model_paths(self):
        with patch.object(controls,'ROOT',self.base),patch.dict(os.environ,{},clear=True):
            self.assertFalse(controls.runtime({})['paths_ready'])
            aux=self.base/'aux';(aux/'src/custom_controlnet_aux/dwpose').mkdir(parents=True)
            python=self.base/'model-python';python.write_bytes(b'fixture')
            pose=self.base/'pose.onnx';pose.write_bytes(b'fixture')
            config=dict(control_python=str(python),control_aux=str(aux),control_pose_model=str(pose))
            self.assertTrue(controls.runtime(config)['paths_ready'])
            self.assertFalse(controls.runtime(dict(config,control_aux=str(self.base)))['paths_ready'])
            self.assertFalse(controls.runtime(dict(config,control_pose_model=str(self.base)))['paths_ready'])

    def test_release_patches_both_model_consumers_and_requires_all_controls(self):
        graph=workflow.video_graph(self.names,'prompt',7,864,480,count=1)
        for k in ['224','226']:self.assertEqual(graph[k]['inputs']['model'],['172',0])
        self.assertEqual(graph['173']['inputs']['name'],controls.UNION_MODEL)
        self.assertEqual(graph['172']['inputs']['strength'],1)
        self.assertEqual(graph['172']['inputs']['mask'],['606',0])
        self.assertEqual(graph['172']['inputs']['source_video'],['605',0])
        self.assertEqual(graph['172']['inputs']['control_video'],['701',0])
        self.assertNotIn('250',graph)
        self.assertFalse(any('Gemini' in n['class_type'] for n in graph.values()))
        with self.assertRaises(ValueError):workflow.video_graph({'target_A':'a','clip':'c'},'p',7,864,480,count=1)

    def test_only_v2_and_bbox_are_fixed(self):
        self.assertEqual(controls.options({}),dict(generation_version='v2',mask_mode='bbox',grow_px=16,strength=1.0))
        for data in [dict(generation_version='v1'),dict(mask_mode='segment'),dict(grow_px=-1),dict(grow_px=65),dict(grow_px=True),dict(strength=float('nan')),dict(strength=True)]:
            with self.assertRaises(ValueError):app.generation_settings(self.pid,data)
        self.assertEqual(app.project(self.pid).get('generation'),None)
        selected=app.generation_settings(self.pid,dict(grow_px=10,strength=.8))
        self.assertEqual(selected['generation'],dict(generation_version='v2',mask_mode='bbox',grow_px=10,strength=.8))
        graph=workflow.video_graph(self.names,'prompt',7,1920,1088,count=1,strength=.8)
        self.assertEqual(graph['172']['inputs']['strength'],.8)

    def test_review_required_invalidates_when_pixels_or_settings_change(self):
        cid,folder=self.ready()
        with self.assertRaises(ValueError):app.make_plan(self.pid,'video','prompt')
        app.approve_controls(self.pid,cid)
        plan=app.make_plan(self.pid,'video','prompt')
        self.assertIn('172',plan['graph'])
        app.approve_controls(self.pid,cid,False)
        with self.assertRaises(ValueError):app.make_plan(self.pid,'video','prompt')
        app.approve_controls(self.pid,cid)
        (folder/'control_mask.mp4').write_bytes(b'changed')
        with self.assertRaises(ValueError):app.make_plan(self.pid,'video','prompt')

    def test_changed_control_approval_after_confirmation_blocks_submit(self):
        cid,_=self.ready();app.approve_controls(self.pid,cid)
        plan=app.make_plan(self.pid,'video','prompt')
        app.approve_controls(self.pid,cid,False)
        with patch.object(app,'read_key',return_value='fixture'),patch.object(threading.Thread,'start'):
            with self.assertRaises(ValueError):app.accept_plan(plan['id'],plan['fingerprint'],True)
        self.assertEqual(app.project(self.pid)['jobs'],[])

    def test_review_timeline_uses_overlay_clip_clock_caches_thumbs_and_rejects_stale(self):
        cid,folder=self.ready()
        record=app.read_json(folder/'control.json')
        (folder/'overlay.mp4').write_bytes(b'local overlay')
        record.update(previews={'mask':str((folder/'overlay.mp4').relative_to(self.base))},
                      manifest={'fps':24,'source_frames':133})
        app.write_json(folder/'control.json',record)
        app.write_json(folder/'request.json',{'checkpoint':dict(self.c,start=5.523)})
        calls=[]
        def thumb(source,seconds,dest):
            calls.append(seconds);dest.write_bytes(b'jpeg');return {'width':192,'height':108}
        with patch.object(app,'thumbnail',side_effect=thumb):
            data=app.control_timeline(self.pid,cid)
            self.assertEqual(len(calls),12)
            self.assertEqual(data['source_start'],5.523)
            self.assertEqual(data['duration'],133/24)
            self.assertTrue(all(0<=s<=132/24 for s in calls))
            self.assertEqual(app.control_timeline(self.pid,cid),data)
            self.assertEqual(len(calls),12)
            p=app.project(self.pid);p['prepared']['id']='changed-checkpoint';app.save_project(p)
            with self.assertRaises(ValueError):app.control_timeline(self.pid,cid)

    def test_unsupported_checkpoint_resolution_is_rejected(self):
        p=app.project(self.pid);p['prepared']['resolution']='unsupported';app.save_project(p)
        with self.assertRaises(ValueError):app.make_plan(self.pid,'prompt')
        with self.assertRaises(ValueError):app.make_plan(self.pid,'video','prompt')
        with self.assertRaises(ValueError):app.start_controls(self.pid)

    def test_native_checkpoint_and_expansion_changes_follow_review_gate(self):
        self.c['resolution']='source'
        self.c['info'].update(width=1920,height=1088)
        p=app.project(self.pid);p['prepared']=self.c;app.save_project(p)
        cid,_=self.ready();app.approve_controls(self.pid,cid)
        plan=app.make_plan(self.pid,'video','prompt')
        self.assertEqual(plan['graph']['236']['inputs']['width'],1920)
        self.assertEqual(plan['graph']['236']['inputs']['height'],1088)
        self.assertIn('larger canvas',plan['cost'])
        app.generation_settings(self.pid,dict(grow_px=10,strength=.8))
        self.assertFalse(app.control_state(self.pid)['valid'])
        with self.assertRaises(ValueError):app.make_plan(self.pid,'video','prompt')

    def test_settings_preserve_manual_other_role_and_blank_reverts_to_auto(self):
        p=app.project(self.pid);p['prepared']['selections']={'A':{},'B':{}}
        p['facts']={'source_A':'original woman','source_B':'original man'}
        p['tracking_descriptions']={'checkpoint_id':self.c['id'],'values':{'A':'','B':''}}
        app.save_project(p)
        p=app.generation_settings(self.pid,{'generation_version':'v2','tracking_descriptions':{'A':'manual woman'}})
        self.assertEqual(p['tracking_descriptions']['values'],{'A':'manual woman'})
        p=app.generation_settings(self.pid,{'generation_version':'v2','tracking_descriptions':{'B':'manual man'}})
        self.assertEqual(p['tracking_descriptions']['values'],{'A':'manual woman','B':'manual man'})
        p=app.generation_settings(self.pid,{'generation_version':'v2','tracking_descriptions':{'A':'  '}})
        self.assertEqual(p['tracking_descriptions']['values'],{'B':'manual man'})
        self.assertEqual(app.tracking_inputs(p,p['prepared'])['descriptions'],{'A':'original woman','B':'manual man'})

    def test_prompt_branch_ignores_controls_and_submission_uploads_reviewed_v2(self):
        cid,_=self.ready();app.approve_controls(self.pid,cid)
        prompt=app.make_plan(self.pid,'prompt')
        self.assertFalse(any(n['class_type'] in ['ModelPatchLoader','MiniMaxH3FunControlNetApply'] for n in prompt['graph'].values()))
        app.generation_settings(self.pid,dict(strength=.8))
        plan=app.make_plan(self.pid,'video','prompt')
        self.assertEqual(plan['graph']['172']['inputs']['strength'],.8)
        uploaded=[];submitted=[]
        class FakeCloud:
            def __init__(self,c):pass
            def upload(self,path):uploaded.append(path.name);return 'cloud-'+path.name
            def submit(self,graph,jid):submitted.append(graph);return 'fake-cloud-id'
            def close(self):pass
            def safe_error(self,e):return str(e)
        with patch.object(app,'read_key',return_value='fixture'),patch.object(threading.Thread,'start'):
            job=app.accept_plan(plan['id'],plan['fingerprint'],True)
        snap=app.read_json(app.job_path(self.pid,job['id'])/'snapshot.json')
        with patch.object(app,'Cloud',FakeCloud),patch.object(app,'guarded_monitor'):
            app.submit_worker(self.pid,job['id'],snap)
        self.assertEqual(len(submitted),1);self.assertEqual(len(uploaded),5)
        self.assertEqual(submitted[0]['172']['inputs']['strength'],.8)
        self.assertEqual(submitted[0]['601']['inputs']['file'],'cloud-control_mask.mp4')
        self.assertEqual(submitted[0]['700']['inputs']['file'],'cloud-control_pose.mp4')
        self.assertEqual(submitted[0]['604']['inputs']['file'],'cloud-control_source.mp4')

    def test_1080p_input_exports_480p_and_restores_unpadded_geometry(self):
        if not shutil.which('ffmpeg'):self.skipTest('ffmpeg required')
        src=self.base/'original.mp4'
        media.run([media.binary('ffmpeg'),'-v','error','-y','-f','lavfi','-i','testsrc2=s=1920x1080:r=24:d=2','-c:v','libx264','-preset','ultrafast','-threads','2',str(src)])
        info=media.probe(src)
        result=media.prepare_clip(src,0,2,self.base/'clip-real.mp4',info)
        self.assertEqual((result['width'],result['height']),(864,480))
        self.assertEqual(result['output'],dict(width=854,height=480,x=4,y=0))
        self.assertEqual(controls_worker.geometry(result,info),result['source_transform'])
        with self.assertRaises(ValueError):media.prepare_clip(src,0,2,self.base/'bad.mp4',info,'unsupported')
        for resolution in ('480p','source'):
            with self.subTest(resolution=resolution):
                clip=self.base/('clip-'+resolution+'.mp4')
                result=media.prepare_clip(src,0,2,clip,info,resolution)
                if resolution=='source':
                    self.assertEqual((result['width'],result['height']),(1920,1088))
                    self.assertEqual(result['output'],dict(width=1920,height=1080,x=0,y=4))
                c=copy.deepcopy(self.c);c.update(info=result,duration=2,resolution=resolution);c['files']['clip']=clip.name
                jid=('b' if resolution=='480p' else 'c')*32;folder=app.job_path(self.pid,jid);folder.mkdir(parents=True)
                app.write_json(folder/'job.json',dict(id=jid,kind='video',character_count=1))
                app.write_json(folder/'snapshot.json',{'checkpoint':c})
                class FakeCloud:
                    def download(self,item,dest):shutil.copy(clip,dest)
                app.finish_job(self.pid,jid,{'192':{'video':[dict(filename='result.mp4')]}},FakeCloud())
                out=media.probe(folder/'review.mp4')
                expected=(854,480) if resolution=='480p' else (1920,1080)
                self.assertEqual((out['width'],out['height']),expected)
                self.assertAlmostEqual(out['duration'],2,places=2)



if __name__=='__main__':unittest.main()

import copy
import json
import sys
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch
import tempfile

import httpx
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import controls
import cloud
import media
import workflow


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.old_data = app.DATA
        app.DATA = self.base
        app.PLANS.clear()
        self.p = app.new_project()
        # Existing regression fixtures represent legacy two-character projects.
        self.p['character_count'] = 2
        app.save_project(self.p)
        self.folder = app.project_dir(self.p['id'])

    def tearDown(self):
        app.DATA = self.old_data
        self.temp.cleanup()

    def facts(self):
        return {k: 'observed ' + k for k in workflow.FIELDS}

    def checkpoint(self):
        files = {}
        for role in ['source_A', 'target_A', 'source_B', 'target_B']:
            f = self.folder / (role + '.png')
            Image.new('RGB', (80, 96), (200, 30, 50)).save(f)
            files[role] = f.name
        (self.folder / 'clip.mp4').write_bytes(b'fake-clip')
        files['clip'] = 'clip.mp4'
        c = {'id': 'c1', 'duration': 7, 'info': {'width': 864, 'height': 480},
             'files': files, 'hashes': {k: media.hash_file(self.folder / v) for k, v in files.items()}}
        self.p['prepared'] = c
        app.save_project(self.p)
        return c

    def ready_controls(self):
        p=app.project(self.p['id']);c=p['prepared'];cid='c'*32
        folder=self.folder/'controls'/cid;folder.mkdir(parents=True,exist_ok=True)
        files={}
        for role in ['control_mask','control_pose','control_source']:
            f=folder/(role+'.mp4');f.write_bytes(role.encode());files[role]=str(f.relative_to(self.folder))
        record=dict(id=cid,status='completed',approved=True,files=files,
                    hashes={k:media.hash_file(self.folder/v) for k,v in files.items()},
                    identity=controls.identity(c,controls.options({}),app.tracking_inputs(p,c)))
        app.write_json(folder/'control.json',record)
        p['control_run']={'id':cid};app.save_project(p)

    def test_no_h3_or_hidden_extra_step_in_prompt_graph(self):
        names = {k: k + '.png' for k in ['source_A','target_A','source_B','target_B','clip']}
        graph = workflow.prompt_graph(names)
        self.assertEqual(len(graph), 7)
        self.assertEqual([v['class_type'] for v in graph.values()].count('GeminiNodeV3'), 1)
        self.assertFalse(any('MiniMax' in v['class_type'] or 'Sampler' in v['class_type'] for v in graph.values()))
        for i, role in enumerate(['source_A','target_A','source_B','target_B'], 1):
            self.assertEqual(graph[str(i)]['inputs']['image'], names[role])
            self.assertEqual(graph['722']['inputs']['model.images.image_' + str(i)], [str(i),0])

    def test_video_retains_successful_baseline_core_and_has_no_gemini(self):
        names = dict(clip='new.mp4', target_A='newA.png', target_B='newB.png',control_mask='mask.mp4',control_source='context.mp4',control_pose='pose.mp4')
        graph = workflow.video_graph(names, 'reviewed prompt', 7, 864, 480)
        baseline = json.loads((workflow.TEMPLATES / 'h3-union20.api.json').read_text())
        for k in ['219','220','223','224','225','226','227','228','229','241','242','243','244','245','246']:
            self.assertEqual(graph[k]['inputs'], baseline[k]['inputs'], k)
        self.assertFalse(any('Gemini' in v['class_type'] for v in graph.values()))
        self.assertEqual(graph['237']['inputs']['image'], names['target_A'])
        self.assertEqual(graph['250']['inputs']['image'], names['target_B'])
        self.assertEqual(graph['238']['inputs']['value'], 'reviewed prompt')

    def test_unconfirmed_missing_fields_and_empty_prompt_are_blocked(self):
        with self.assertRaises(ValueError): workflow.assemble_prompt({})
        f = self.facts();f['target_B'] = 'UNCONFIRMED missing image'
        with self.assertRaises(ValueError): workflow.assemble_prompt(f)
        with self.assertRaises(ValueError): workflow.video_graph({}, ' ', 7, 864, 480)
        final = workflow.assemble_prompt(self.facts())
        self.assertNotIn('{{', final)
        self.assertIn('observed target_B', final)

    def test_crop_exact_pixels_and_out_of_bounds(self):
        image = Image.new('RGB', (200,100))
        for x in range(200):
            for y in range(100): image.putpixel((x,y),(x,y,2))
        source=self.folder/'frame.png';image.save(source)
        dest=self.folder/'crop.png'
        media.crop(source,dict(x=75,y=20,width=30,height=45),dest)
        with Image.open(dest) as result:
            self.assertEqual(result.size,(30,45));self.assertEqual(result.getpixel((0,0)),(75,20,2))
        with self.assertRaises(ValueError): media.crop(source,dict(x=150,y=20,width=968,height=1070),dest)
        with self.assertRaises(ValueError): media.crop(source,dict(x=0,y=0,width=float('nan'),height=20),dest)

    def test_single_use_confirmation_and_active_job_dedup(self):
        self.checkpoint()
        plan=app.make_plan(self.p['id'],'prompt')
        with patch.object(app, 'read_key', return_value='test-only'), patch.object(threading.Thread,'start'):
            with self.assertRaises(ValueError): app.accept_plan(plan['id'],plan['fingerprint'],False)
            job=app.accept_plan(plan['id'],plan['fingerprint'],True)
            self.assertEqual(job['status'],'uploading')
            with self.assertRaises(ValueError): app.accept_plan(plan['id'],plan['fingerprint'],True)
            self.ready_controls()
            second=app.make_plan(self.p['id'],'video','reviewed prompt')
            with self.assertRaises(ValueError): app.accept_plan(second['id'],second['fingerprint'],True)

    def test_stale_or_changed_input_never_submits(self):
        self.checkpoint();plan=app.make_plan(self.p['id'],'prompt')
        (self.folder/'target_A.png').write_bytes(b'changed')
        with patch.object(threading.Thread,'start') as thread:
            with self.assertRaises(ValueError): app.accept_plan(plan['id'],plan['fingerprint'],True)
            thread.assert_not_called()
        self.checkpoint();plan=app.make_plan(self.p['id'],'prompt')
        p=app.project(self.p['id']);p['prepared']['id']='new';app.save_project(p)
        with self.assertRaises(ValueError):app.accept_plan(plan['id'],plan['fingerprint'],True)

    def test_secret_not_in_export_and_no_post_retry(self):
        key='test-secret-123456';path=self.base/'key';path.write_text(key)
        calls=[]
        def handler(request):
            calls.append(request)
            if request.url.path=='/api/prompt':
                payload=json.loads(request.content)
                self.assertEqual(payload['extra_data']['api_key_comfy_org'],key)
                self.assertEqual(set(payload['prompt']),{'802'})
                return httpx.Response(503,json={'error':{'message':'downstream unavailable'}})
            return httpx.Response(200,json={})
        c=cloud.Cloud({'key_file':str(path)},transport=httpx.MockTransport(handler))
        graph={'802':{'class_type':'SaveText','inputs':{'text':'hello'}}}
        with self.assertRaises(ValueError):c.submit(graph,'test-client')
        self.assertEqual(len(calls),1)
        self.assertNotIn(key,json.dumps(graph));self.assertNotIn(key,c.safe_error('oops '+key))
        c.close()

    def test_prompt_worker_persists_exact_facts_but_never_starts_h3(self):
        self.checkpoint()
        plan=app.make_plan(self.p['id'],'prompt')
        facts=self.facts()
        calls=[]
        class FakeCloud:
            def __init__(self, config): pass
            def upload(self,path): calls.append(('upload',path.name));return 'remote-'+path.name
            def submit(self,graph,client_id): calls.append(('submit',copy.deepcopy(graph)));return 'cloud-prompt-id'
            def history(self,prompt_id): return {'status':{'completed':True,'status_str':'success'},'outputs':{'802':{'text':[json.dumps(facts)]}}}
            def safe_error(self,e): return str(e)
            def close(self): pass
        with patch.object(app,'read_key',return_value='test-only'),patch.object(threading.Thread,'start'):
            j=app.accept_plan(plan['id'],plan['fingerprint'],True)
        with patch.object(app,'Cloud',FakeCloud):
            app.submit_worker(self.p['id'],j['id'],app.PLANS[plan['id']]['snapshot'])
        result=app.read_json(app.job_path(self.p['id'],j['id'])/'job.json')
        self.assertEqual(result['status'],'completed')
        self.assertEqual(result['facts'],facts)
        self.assertEqual(len([c for c in calls if c[0]=='submit']),1)
        self.assertEqual(len([c for c in calls if c[0]=='upload']),5)
        self.assertFalse(any('Sampler' in n['class_type'] for n in calls[-1][1].values()))
        saved=app.project(self.p['id'])
        self.assertEqual(saved['prompt'],workflow.assemble_prompt(facts))
        self.assertEqual(len(saved['jobs']),1)

    def test_real_local_ffmpeg_range_frame_and_24fps_preparation(self):
        source=self.folder/'source.mp4'
        media.run([media.binary('ffmpeg'),'-v','error','-y','-f','lavfi','-i',
                   'testsrc2=size=640x360:rate=30:duration=4','-c:v','libx264','-pix_fmt','yuv420p',str(source)])
        info=media.probe(source)
        result=media.prepare_clip(source,1,3,self.folder/'prepared.mp4',info)
        self.assertEqual((result['width'],result['height'],result['fps']),(864,480,24))
        self.assertAlmostEqual(result['duration'],2,places=1)
        self.assertEqual(media.frame(source,2,self.folder/'frame.jpg',info['duration']),dict(width=640,height=360))
        with self.assertRaises(ValueError):media.prepare_clip(source,2,7,self.folder/'bad.mp4',info)

    def test_http_token_and_range_access(self):
        server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        url='http://127.0.0.1:'+str(server.server_port)
        try:
            with httpx.Client(base_url=url) as client:
                c=client.get('/api/config').json()
                self.assertNotIn('api_key',c)
                self.assertEqual(client.post('/api/project',json={}).status_code,403)
                self.assertEqual(client.post('/api/project',json={},headers={'X-V2V-Token':app.TOKEN,'Origin':'https://elsewhere.example'}).status_code,403)
                self.assertEqual(client.post('/api/project',json={},headers={'X-V2V-Token':app.TOKEN}).status_code,200)
                (self.folder/'tiny.mp4').write_bytes(b'0123456789')
                r=client.get('/files/'+self.p['id']+'/tiny.mp4',headers={'Range':'bytes=2-5'})
                self.assertEqual(r.status_code,206);self.assertEqual(r.content,b'2345')
                self.assertEqual(client.get('/api/config',headers={'Host':'foreign.example'}).status_code,400)
        finally:
            server.shutdown();server.server_close()

    def test_timeline_local_cache_and_stale_asset_protection(self):
        source=self.folder/'source.mp4'
        media.run([media.binary('ffmpeg'),'-v','error','-y','-f','lavfi','-i',
                   'testsrc2=size=320x180:rate=24:duration=2','-c:v','libx264','-pix_fmt','yuv420p',str(source)])
        p=app.project(self.p['id']);p['assets']['video']={'file':'source.mp4','info':media.probe(source)};app.save_project(p)
        first=app.timeline(self.p['id'],'source.mp4')
        self.assertTrue(8<=len(first['thumbnails'])<=24)
        for item in first['thumbnails']:
            self.assertGreaterEqual(item['time'],0);self.assertLess(item['time'],2)
            with Image.open(self.folder/item['file']) as image:self.assertEqual(image.width,192)
        with patch.object(app,'thumbnail',side_effect=AssertionError('cache must avoid ffmpeg')):
            self.assertEqual(app.timeline(self.p['id'],'source.mp4'),first)
        with self.assertRaises(ValueError):app.timeline(self.p['id'],'old-video.mp4')

    def test_editor_persistence_invalidates_only_changed_preparation(self):
        self.checkpoint()
        p=app.project(self.p['id']);p['assets']['video']={'file':'clip.mp4','info':{'duration':9.05}}
        selected={'A':{'time':1,'crop':{'x':0,'y':0,'width':20,'height':20}},
                  'B':{'time':.5,'crop':{'x':0,'y':0,'width':20,'height':20}}}
        p['prepared'].update(start=0,end=7,selections=copy.deepcopy(selected));app.save_project(p)
        data={'video_file':'clip.mp4','range':{'start':0,'end':7},'selections':selected}
        app.save_editor(p['id'],data)
        self.assertIn('prepared',app.project(p['id']))
        data['range']={'start':1.01,'end':3.01};app.save_editor(p['id'],data)
        result=app.project(p['id']);self.assertNotIn('prepared',result)
        self.assertEqual(result['editor']['range'],{'start':1.01,'end':3.01})
        self.assertEqual(result['selections']['B']['time'],.5)
        # Out-of-range role times are retained for visible review, never silently moved.
        bad=copy.deepcopy(data);bad['video_file']='another.mp4'
        with self.assertRaises(ValueError):app.save_editor(p['id'],bad)
        bad=copy.deepcopy(data);bad['selections']['A']['time']=12
        with self.assertRaises(ValueError):app.save_editor(p['id'],bad)

    def test_single_graphs_have_exactly_two_gemini_images_and_one_h3_reference(self):
        names={'clip':'clip.mp4','source_A':'source.png','target_A':'target.png'}
        graph=workflow.prompt_graph(names,1)
        self.assertEqual(len(graph),5)
        self.assertEqual(sum(n['class_type']=='LoadImage' for n in graph.values()),2)
        inputs=graph['722']['inputs']
        self.assertEqual([k for k in inputs if k.startswith('model.images.')],['model.images.image_1','model.images.image_2'])
        self.assertIn('SEVEN',inputs['model.system_prompt'])
        names.update(control_mask='mask.mp4',control_source='context.mp4',control_pose='pose.mp4')
        video=workflow.video_graph(names,'single test',7,864,480,count=1)
        self.assertNotIn('250',video)
        self.assertEqual([k for k in video['236']['inputs'] if k.startswith('ref_images.')],['ref_images.ref_image_0'])
        self.assertEqual(sum(n['class_type']=='LoadImage' for n in video.values()),1)
        baseline=json.loads((workflow.TEMPLATES/'h3-union20.api.json').read_text())
        for k in ['219','220','223','224','225','226','227','228','229','241','242','243','244','245','246']:
            self.assertEqual(video[k]['inputs'],baseline[k]['inputs'])

    def test_single_facts_need_no_b_fields_or_second_picture(self):
        facts={k:'observed '+k for k in workflow.SINGLE_FIELDS}
        self.assertEqual(workflow.parse_facts(facts,1),facts)
        text=workflow.assemble_prompt(facts,count=1)
        self.assertIn('Keep all other people unchanged',text)
        self.assertNotIn('Picture 2',text);self.assertNotIn('{{',text)
        with self.assertRaises(ValueError):workflow.parse_facts(facts,2)
        bad=dict(facts,source_A='UNCONFIRMED missing source')
        with self.assertRaises(ValueError):workflow.parse_facts(bad,1)

    def test_single_prepare_with_no_b_asset_or_selection(self):
        source=self.folder/'source.mp4'
        media.run([media.binary('ffmpeg'),'-v','error','-y','-f','lavfi','-i',
                   'testsrc2=size=320x180:rate=24:duration=3','-c:v','libx264','-pix_fmt','yuv420p',str(source)])
        Image.new('RGB',(40,70),'red').save(self.folder/'target.png')
        p=app.project(self.p['id']);p['character_count']=1
        p['assets']={'video':{'file':'source.mp4','info':media.probe(source)},'target_A':{'file':'target.png'}};app.save_project(p)
        selected={'A':{'time':.5,'crop':{'x':10,'y':10,'width':60,'height':100}}}
        p=app.prepare(p['id'],{'character_count':1,'start':0,'end':2,'selections':selected})
        self.assertEqual(set(p['prepared']['files']),{'clip','source_A','target_A'})
        self.assertEqual(set(p['prepared']['selections']),{'A'})
        self.assertEqual(p['prepared']['character_count'],1)
        plan=app.make_plan(p['id'],'prompt')
        self.assertEqual(plan['character_count'],1)
        self.assertEqual(sum(n['class_type']=='LoadImage' for n in plan['graph'].values()),2)
        with self.assertRaises(ValueError):app.prepare(p['id'],{'character_count':2})

    def test_mode_change_preserves_b_inputs_and_invalidates_stale_prompt(self):
        self.checkpoint();p=app.project(self.p['id'])
        selected={'A':{'time':1,'crop':{'x':0,'y':0,'width':20,'height':20}},'B':{'time':.5,'crop':{'x':2,'y':2,'width':30,'height':30}}}
        p['selections']=copy.deepcopy(selected);p['assets']['target_B']={'file':'target_B.png'}
        p['prepared'].update(start=0,end=7,selections=copy.deepcopy(selected))
        folder=self.folder/'prepared/c1';folder.mkdir(parents=True);Image.new('RGB',(80,96)).save(folder/'frame_B.jpg')
        p['editor']={'range':{'start':0,'end':7},'selections':copy.deepcopy(selected)}
        p['prompt']='user edited two-character prompt';app.save_project(p)
        single=app.set_character_count(p['id'],1)
        self.assertNotIn('prepared',single);self.assertEqual(single['prompt'],'')
        self.assertEqual(single['mode_drafts']['2']['prompt'],p['prompt'])
        self.assertEqual(single['selections']['B']['crop'],selected['B']['crop'])
        self.assertTrue((self.folder/single['selections']['B']['frame_file']).is_file())
        dual=app.set_character_count(p['id'],2)
        self.assertEqual(dual['assets']['target_B'],p['assets']['target_B'])
        self.assertEqual(dual['selections']['B'],single['selections']['B'])

    def test_old_confirmation_is_rejected_after_count_switch(self):
        self.checkpoint();plan=app.make_plan(self.p['id'],'prompt')
        app.set_character_count(self.p['id'],1)
        with patch.object(threading.Thread,'start') as start:
            with self.assertRaises(ValueError):app.accept_plan(plan['id'],plan['fingerprint'],True)
            start.assert_not_called()

    def test_single_worker_uses_snapshot_count_even_if_project_mode_changes(self):
        self.checkpoint();p=app.project(self.p['id']);p['character_count']=1;p['prepared']['character_count']=1
        p['prepared']['files']={k:v for k,v in p['prepared']['files'].items() if k in ['clip','source_A','target_A']};app.save_project(p)
        plan=app.make_plan(p['id'],'prompt');facts={k:'observed '+k for k in workflow.SINGLE_FIELDS};calls=[]
        class FakeCloud:
            def __init__(self,config):pass
            def upload(self,path):calls.append(('upload',path.name));return path.name
            def submit(self,graph,client_id):calls.append(('submit',graph));return 'single-cloud-id'
            def history(self,prompt_id):return {'status':{'completed':True},'outputs':{'802':{'text':[json.dumps(facts)]}}}
            def safe_error(self,e):return str(e)
            def close(self):pass
        with patch.object(app,'read_key',return_value='test'),patch.object(threading.Thread,'start'):
            job=app.accept_plan(plan['id'],plan['fingerprint'],True)
        app.set_character_count(p['id'],2)
        with patch.object(app,'Cloud',FakeCloud):app.submit_worker(p['id'],job['id'],app.PLANS[plan['id']]['snapshot'])
        result=app.read_json(app.job_path(p['id'],job['id'])/'job.json')
        self.assertEqual(result['status'],'completed');self.assertEqual(result['character_count'],1)
        self.assertEqual(set(result['facts']),set(workflow.SINGLE_FIELDS))
        self.assertEqual(len([x for x in calls if x[0]=='upload']),3)
        self.assertEqual(sum(n['class_type']=='LoadImage' for n in calls[-1][1].values()),2)
        self.assertEqual(app.project(p['id'])['prompt'],'')

    def test_single_video_worker_uploads_one_reference_and_required_controls(self):
        self.checkpoint();p=app.project(self.p['id']);p['character_count']=1;p['prepared']['character_count']=1
        p['prepared']['files']={k:v for k,v in p['prepared']['files'].items() if k in ['clip','source_A','target_A']};app.save_project(p)
        with self.assertRaises(ValueError):app.make_plan(p['id'],'video','single prompt',requested_count=2)
        self.ready_controls()
        plan=app.make_plan(p['id'],'video','single prompt',requested_count=1);calls=[]
        class FakeCloud:
            def __init__(self,config):pass
            def upload(self,path):calls.append(('upload',path.name));return path.name
            def submit(self,graph,client_id):calls.append(('submit',graph));return 'video-id'
            def safe_error(self,e):return str(e)
            def close(self):pass
        with patch.object(app,'read_key',return_value='test'),patch.object(threading.Thread,'start'):
            job=app.accept_plan(plan['id'],plan['fingerprint'],True)
        with patch.object(app,'Cloud',FakeCloud),patch.object(app,'guarded_monitor'):
            app.submit_worker(p['id'],job['id'],app.PLANS[plan['id']]['snapshot'])
        self.assertEqual([x[1] for x in calls if x[0]=='upload'],['clip.mp4','target_A.png','control_mask.mp4','control_pose.mp4','control_source.mp4'])
        self.assertNotIn('250',calls[-1][1])
        self.assertEqual(len([x for x in calls if x[0]=='submit']),1)

    def test_editor_ignores_inactive_b_and_rejects_stale_count(self):
        p=app.project(self.p['id']);p['character_count']=1;p['assets']['video']={'file':'clip.mp4','info':{'duration':9}}
        p['selections']['B']={'time':.5,'crop':{'x':1,'y':2,'width':20,'height':20}};app.save_project(p)
        data={'video_file':'clip.mp4','character_count':1,'range':{'start':0,'end':7},'selections':{'A':{'time':1,'crop':None},'B':{'time':999,'frame_file':'missing.jpg'}}}
        app.save_editor(p['id'],data)
        self.assertEqual(app.project(p['id'])['selections']['B'],p['selections']['B'])
        data['character_count']=2
        with self.assertRaises(ValueError):app.save_editor(p['id'],data)

    def test_new_single_and_legacy_dual_defaults_and_count_validation(self):
        self.assertEqual(app.new_project()['character_count'],1)
        self.assertEqual(app.project_count({}),2)
        for value in [0,3,True,'1',None]:
            with self.assertRaises(ValueError):app.set_character_count(self.p['id'],value)


if __name__=='__main__':
    unittest.main()

"""Real FFmpeg worker export with deterministic stub trackers/poses; no models/network."""
from collections import namedtuple
import json
from pathlib import Path
import shutil
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import numpy as np
import controls_worker
import media


class WarningWorkerTests(unittest.TestCase):
    def test_partial_tracking_gap_exports_all_controls_without_inventing_masks(self):
        if not shutil.which('ffmpeg'):
            self.skipTest('ffmpeg required')
        with tempfile.TemporaryDirectory() as td:
            folder=Path(td);video=folder/'source.mp4'
            media.run([media.binary('ffmpeg'),'-v','error','-y','-f','lavfi','-i',
                       'color=c=gray:s=64x96:r=24:d=2','-c:v','libx264','-pix_fmt','yuv420p',str(video)])
            info=media.probe(video);info['source_transform']=dict(scale_x=1,scale_y=1,x=0,y=0)
            pose_file=folder/'fixture.onnx';pose_file.write_bytes(b'not a real model')
            checkpoint=dict(start=0,duration=2,info=info,selections={'A':dict(time=.2,crop=dict(x=8,y=8,width=40,height=72))})
            request=dict(runtime=dict(control_extra_site='',control_aux=str(folder/'aux'),control_pose_model=str(pose_file)),
                         ffmpeg=media.binary('ffmpeg'),clip=str(video),source=str(video),source_info=info,
                         checkpoint=checkpoint,settings=dict(grow_px=0),review=dict(anchors=[],decisions=[]))
            path=folder/'request.json';path.write_text(json.dumps(request))
            cv=types.ModuleType('cv2');cv.setNumThreads=lambda n:None
            cv.INTER_NEAREST=0;cv.INTER_AREA=1
            def resize(image,size,interpolation=0):
                self.assertEqual((image.shape[1],image.shape[0]),size)
                return image.copy()
            cv.resize=resize
            torch=types.ModuleType('torch');torch.set_grad_enabled=lambda v:None
            model=types.SimpleNamespace();model.eval=lambda:model;model.to=lambda device:model
            factory=types.SimpleNamespace(from_pretrained=lambda *a,**kw:model)
            transformers=types.ModuleType('transformers');transformers.EdgeTamVideoModel=factory;transformers.Sam2VideoProcessor=factory
            edge=types.ModuleType('edge_tracker');edge._device=lambda choice:'cpu'
            def track(model,processor,frames,prompt,device):
                masks=np.zeros(frames.shape[:3],dtype=np.float32);masks[:,8:80,8:48]=1;masks[12:20]=0
                return types.SimpleNamespace(numpy=lambda:masks)
            edge._track_one=track
            keypoint=namedtuple('Keypoint','x y score id');body=namedtuple('BodyResult','keypoints');pose=namedtuple('PoseResult','body face hand1 hand2')
            aux=types.ModuleType('custom_controlnet_aux');aux.__path__=[]
            dw=types.ModuleType('custom_controlnet_aux.dwpose');dw.__path__=[]
            whole=types.ModuleType('custom_controlnet_aux.dwpose.wholebody')
            class Wholebody:
                def __init__(self,*args,**kwargs):pass
                def __call__(self,image):return [pose(body([keypoint(12,16,1,0)]),None,None,None)]
                format_result=staticmethod(lambda value:value)
            whole.Wholebody=Wholebody
            def draw(poses,h,w,**kwargs):
                image=np.zeros((h,w,3),np.uint8);image[10:70,10:40]=[0,255,0];return image
            dw.draw_poses=draw
            dt=types.ModuleType('custom_controlnet_aux.dwpose.types');dt.Keypoint=keypoint;dt.BodyResult=body;dt.PoseResult=pose
            modules={'cv2':cv,'torch':torch,'transformers':transformers,'edge_tracker':edge,
                     'custom_controlnet_aux':aux,'custom_controlnet_aux.dwpose':dw,
                     'custom_controlnet_aux.dwpose.wholebody':whole,'custom_controlnet_aux.dwpose.types':dt}
            original_path=sys.path[:]
            try:
                with patch.dict(sys.modules,modules):controls_worker.main(path)
            finally:sys.path[:]=original_path
            manifest=json.loads((folder/'manifest.json').read_text())
            self.assertTrue(manifest['requires_input'])
            self.assertEqual(set(manifest['files']),{'control_mask','control_pose','control_source'})
            self.assertEqual(set(manifest['previews']),{'mask','pose'})
            self.assertEqual(manifest['pose_nonempty_frames']['A'],40)
            self.assertEqual(manifest['frames'],56)
            self.assertEqual(manifest['quality']['issues'][0]['kind'],'missing')
            for name in manifest['files'].values():
                output=media.probe(folder/name)
                self.assertEqual((output['width'],output['height']),(64,96))
                self.assertAlmostEqual(output['duration'],56/24,places=2)
            raw=np.load(folder/'tracking.npz')['A']
            self.assertFalse(raw[12:20].any());self.assertTrue(raw[:12].any())
            decoded=media.run([media.binary('ffmpeg'),'-v','error','-i',str(folder/'mask.mp4'),'-an','-f','rawvideo','-pix_fmt','rgb24','-'])
            masks=np.frombuffer(decoded,np.uint8).reshape(-1,96,64,3)
            self.assertFalse(masks[12:20].any());self.assertTrue(masks[:12].any())


if __name__=='__main__':unittest.main()

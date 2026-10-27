"""No model/network: wrong-person, ambiguity, absence and stale-text guards."""
import copy
import unittest

import numpy as np
import app
import controls
from text_locator import ranked_candidates,relocate,assign_roles,location_queries


def detection(box,score=.8,text='woman'):
    return dict(box=box,score=score,text=text)


class TextLocatorTests(unittest.TestCase):
    def test_hat_does_not_seed_a_person_and_clothing_disambiguates_blond_man(self):
        # Real diagnostic: full-text grounding gave the male a higher person
        # token score; the described beret belongs to the woman on the right.
        ds=[detection([79,63,484,455],.616,'blonde woman'),
            detection([421,26,854,455],.397,'blonde woman'),
            detection([435,26,774,283],.674,'a pink beret'),
            detection([79,250,484,455],.463,'pink collared top')]
        person=assign_roles({'A':ranked_candidates(ds,864,480)})['A']
        self.assertEqual(person['box'],[421,26,854,455])
        self.assertEqual(ranked_candidates(ds[2:3],864,480),[])

    def test_similar_people_and_nan_are_not_auto_assigned(self):
        ds=[detection([2,2,20,28],.8),detection([28,2,46,28],.77)]
        self.assertIsNone(assign_roles({'A':ranked_candidates(ds,48,32)})['A'])
        self.assertEqual(ranked_candidates([detection([0,0,float('nan'),20])],48,32),[])

    def test_derives_locator_only_phrase_and_does_not_search_black(self):
        v=np.ones((40,32,48,3),np.uint8)*120
        shots=[dict(start=0,end=10,black=False),dict(start=10,end=20,black=True),dict(start=20,end=40,black=False)]
        anchors={'A':[dict(frame=5,box=[2,2,20,28],initial=True)]};calls=[]
        def detect(image,description):
            calls.append(description);return [detection([26,2,46,28])]
        added,log=relocate(v,shots,anchors,{'A':'woman in pink beret'},detect)
        self.assertEqual(calls,['a woman wearing pink beret']*3)
        self.assertEqual(len(added),1);self.assertEqual(added[0]['restart'],20)
        self.assertEqual(added[0]['description'],'woman in pink beret')
        self.assertTrue(added[0]['auto']);self.assertTrue(all(x['frame']>=20 for x in log))

    def test_roles_cannot_automatically_bind_to_the_same_person(self):
        v=np.ones((20,32,48,3),np.uint8);shots=[dict(start=0,end=20,black=False)]
        anchors={'A':[],'B':[]}
        added,_=relocate(v,shots,anchors,{'A':'woman','B':'man'},lambda v,t:[detection([2,2,24,30])])
        self.assertEqual(added,[])
        self.assertEqual(anchors,{'A':[],'B':[]})

    def test_joint_assignment_uses_B_alternative_instead_of_rejecting_correct_A(self):
        woman=[30,2,48,30];man=[2,2,20,30]
        pools={'A':[detection(woman,.79)],'B':[detection(woman,.54,'man'),detection(man,.36,'man')]}
        chosen=assign_roles(pools)
        self.assertEqual(chosen['A']['box'],woman);self.assertEqual(chosen['B']['box'],man)
        self.assertEqual(assign_roles(dict(reversed(list(pools.items())))),chosen)
        pools['B']=pools['B'][:1]
        chosen=assign_roles(pools)
        self.assertEqual(chosen['A']['box'],woman);self.assertIsNone(chosen['B'])

    def test_existing_A_mask_cannot_be_revoked_by_Bs_higher_detection_score(self):
        woman=[30,2,48,30];man=[2,2,20,30]
        fixed=dict(detection(woman,1),locked=True)
        chosen=assign_roles({'A':[fixed],'B':[detection(woman,.99),detection(man,.4)]})
        self.assertEqual(chosen['A']['box'],woman);self.assertEqual(chosen['B']['box'],man)

    def test_short_visual_queries_preserve_source_and_keep_full_text_as_fallback(self):
        text='the blonde woman with a braided hairstyle wearing a pink beret and a pink dress, driving a car'
        queries=location_queries(text)
        self.assertEqual(queries[0],'a woman wearing a pink beret')
        self.assertEqual(queries[-1],text)
        self.assertNotIn('driving',queries[0]);self.assertNotIn('braided',queries[0])

    def test_automatic_retests_try_new_frames_rather_than_repeat_same_failure(self):
        v=np.ones((100,32,48,3),np.uint8);shots=[dict(start=0,end=100,black=False)]
        selections=[]
        for attempt in range(3):
            _,log=relocate(v,shots,{'A':[]},{'A':'woman'},lambda v,t:[],pass_index=attempt)
            selections.append(set(e['frame'] for e in log))
        self.assertEqual([len(s) for s in selections],[3,4,4])
        self.assertFalse(selections[0]&selections[1]);self.assertFalse(selections[1]&selections[2])

    def test_new_frame_pass_can_recover_when_first_pass_finds_nothing(self):
        v=np.zeros((100,32,48,3),np.uint8)
        for i in range(100):v[i,0,0,0]=i
        shots=[dict(start=0,end=100,black=False)];anchors={'A':[]}
        def detect(image,text):
            return [detection([2,2,20,30])] if int(image[0,0,0]) in [10,40] else []
        first,_=relocate(v,shots,anchors,{'A':'woman'},detect,pass_index=0)
        self.assertEqual(first,[])
        second,_=relocate(v,shots,anchors,{'A':'woman'},detect,pass_index=1)
        self.assertEqual(len(second),1)
        self.assertIn(second[0]['frame'],[10,40])

    def test_no_detection_never_invents_absence_or_box_and_preserves_user_seed(self):
        v=np.ones((20,32,48,3),np.uint8);shots=[dict(start=0,end=10,black=False),dict(start=10,end=20,black=False)]
        anchors={'A':[dict(frame=5,box=[2,2,20,28],initial=True)]};old=copy.deepcopy(anchors)
        added,log=relocate(v,shots,anchors,{'A':'woman'},lambda v,t:[])
        self.assertEqual(added,[]);self.assertEqual(anchors,old)
        self.assertTrue(log);self.assertTrue(all(x['candidate'] is None for x in log))

    def test_only_source_facts_are_used_and_description_edit_invalidates_controls(self):
        c=dict(id='cp',selections={'A':{},'B':{}})
        p=dict(facts=dict(source_A='original woman',target_A='replacement man',source_B='original man'))
        r=app.tracking_inputs(p,c)
        self.assertEqual(r['descriptions'],{'A':'original woman','B':'original man'})
        settings=controls.options({});old=controls.identity(c,settings,r)
        p['tracking_descriptions']=dict(checkpoint_id='cp',values={'A':'woman in pink','B':'man in pink'})
        self.assertNotEqual(old,controls.identity(c,settings,app.tracking_inputs(p,c)))
        p['tracking_descriptions']['checkpoint_id']='older'
        self.assertEqual(old,controls.identity(c,settings,app.tracking_inputs(p,c)))

    def test_blank_overrides_inherit_each_source_and_auto_fields_follow_new_facts(self):
        c=dict(id='cp',selections={'A':{},'B':{}})
        p=dict(facts=dict(source_A='woman in pink',source_B='man in patterned pink',target_A='replacement'),
               tracking_descriptions=dict(checkpoint_id='cp',values={'A':'','B':'  '}))
        self.assertEqual(app.tracking_inputs(p,c)['descriptions'],{'A':'woman in pink','B':'man in patterned pink'})
        p['tracking_descriptions']['values']['A']='manual source A'
        self.assertEqual(app.tracking_inputs(p,c)['descriptions'],{'A':'manual source A','B':'man in patterned pink'})
        p['facts']['source_B']='new observed man'
        self.assertEqual(app.tracking_inputs(p,c)['descriptions']['B'],'new observed man')
        p['facts']={}
        self.assertEqual(app.tracking_inputs(p,c)['descriptions'],{'A':'manual source A'})


if __name__=='__main__':unittest.main()

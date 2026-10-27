const assert=require('node:assert/strict');
const {normalizeRange,moveRange,inputRange,frameTime,rangeFrameTime}=require('../web/timeline.js');

const check=(duration,r)=>{
  assert(r.start>=-.001&&r.end<=duration+.001);
  assert(r.end-r.start>=Math.min(2,duration)-.0011);
  assert(r.end-r.start<=Math.min(15,duration)+.0011);
};
let range=normalizeRange(9.05,0,7);
assert.deepEqual(range,{start:0,end:7});
assert.deepEqual(moveRange(9.05,range,'move',4),{start:2.05,end:9.05});
assert.deepEqual(moveRange(9.05,range,'start',6.9),{start:5,end:7});
assert.deepEqual(moveRange(9.05,range,'end',.4),{start:0,end:2});
assert.deepEqual(moveRange(120,{start:10,end:17},'end',90),{start:10,end:25});
assert.deepEqual(moveRange(120,{start:10,end:25},'start',0),{start:10,end:25});
assert.deepEqual(inputRange(9.05,.5,7.5,'start'),{start:.5,end:7.5});
assert.deepEqual(inputRange(9.05,6,7,'start'),{start:5,end:7});
assert.deepEqual(inputRange(120,40,47,'start'),{start:40,end:47});
assert(frameTime(9.05,9.05,23.976)<9.05);
assert(rangeFrameTime(0,{start:2,end:7},9.05,24)>=2);
assert(rangeFrameTime(9,{start:2,end:7},9.05,24)<7);
for(const duration of [.5,1.2,2,3.3333,9.05,15,60,300]){
  for(let i=-10;i<50;i++){
    const r=normalizeRange(duration,i*.37,i*.37+7);check(duration,r);
    for(const value of [-10,0,1.001,2.001,duration/2,duration,duration+20]){
      for(const part of ['start','end','move'])check(duration,moveRange(duration,r,part,value));
    }
  }
}
console.log('Timeline interval/marker tests passed (10,080 boundary combinations).');

// Exercise the actual pointer handlers and callbacks with a tiny DOM fixture.
// Video decoding/cache is tested with real FFmpeg in test_app.py.
const nodes=new Map();
function node(id){
  if(!nodes.has(id))nodes.set(id,{style:{},attrs:{},handlers:{},classList:{toggle(){}},
    addEventListener(name,fn){this.handlers[name]=fn;},setPointerCapture(){},
    getBoundingClientRect(){return {left:0,width:1000};},
    setAttribute(k,v){this.attrs[k]=String(v);},querySelector(){return {textContent:''};}});
  return nodes.get(id);
}
global.document={getElementById:node};
delete require.cache[require.resolve('../web/timeline.js')];require('../web/timeline.js');
const changes=[],picks=[],activations=[];
const view=new global.VideoTimeline({range:(r,commit)=>changes.push([{...r},commit]),
  pick:(label,t)=>picks.push([label,t]),activate:l=>activations.push(l),preview(){},error(e){throw e;}});
view.duration=9.05;view.range={start:0,end:7};view.markers={A:{time:1,crop:{}},B:{time:.5,crop:{}}};
const event=x=>({clientX:x,pointerId:1,preventDefault(){}});
node('timelineRangeEnd').handlers.pointerdown(event(700));
node('timelineRangeEnd').handlers.pointermove(event(700-1000/9.05));
node('timelineRangeEnd').handlers.pointerup();
assert.deepEqual(view.range,{start:0,end:6});assert.equal(changes.at(-1)[1],true);
view.range={start:0,end:7};
node('timelineRangeMove').handlers.pointerdown(event(300));
node('timelineRangeMove').handlers.pointermove(event(900));
node('timelineRangeMove').handlers.pointerup();
assert.deepEqual(view.range,{start:2.05,end:9.05});
view.range={start:0,end:7};
node('timelineMarkerA').handlers.pointerdown(event(100));
node('timelineMarkerA').handlers.pointermove(event(100+1000/9.05));
node('timelineMarkerA').handlers.pointerup();
assert.deepEqual(picks,[['A',2]]);assert.equal(view.markers.B.time,.5);
node('timelineMarkerB').handlers.pointerdown(event(50));node('timelineMarkerB').handlers.pointerup();
assert.deepEqual(activations,['B']);assert.equal(picks.length,1);
view.range={start:0,end:7};node('timelineRangeStart').handlers.pointerdown(event(0));
node('timelineRangeStart').handlers.pointermove(event(200));node('timelineRangeStart').handlers.pointercancel();
assert.deepEqual(view.range,{start:0,end:7});
view.update({A:{time:1,crop:{}},B:{time:99,crop:{}}},'A',1);
assert.equal(node('timelineWarning').hidden,true);
const previousPicks=picks.length;view.pick('B',1);assert.equal(picks.length,previousPicks);
view.update({A:{time:1,crop:{}},B:{time:99,crop:{}}},'A',2);
assert.equal(node('timelineWarning').hidden,false);
console.log('Pointer interaction tests passed: edge trim, region move/clamp, A-only marker pick, marker activation, drag cancellation.');

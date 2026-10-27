const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../web/app.js'),'utf8');
const helpers=source.slice(source.indexOf('function samplePoint('),source.indexOf('function renderSampleList('));
const context={sampleIndices:{A:1,B:0},selections:{A:{time:2,crop:{x:20},samples:[{time:1,crop:{x:10}},{time:2,crop:{x:20}}]},B:{time:3,crop:{x:30}}}};
vm.runInNewContext(helpers+'\nsyncSamples();',context);
context.selections.A.crop={x:99};context.selections.A.time=2.5;
vm.runInNewContext('syncSamples();',context);
assert.equal(context.selections.A.samples[0].crop.x,10);
assert.equal(context.selections.A.samples[1].crop.x,99);
assert.equal(context.selections.A.samples[1].time,2.5);
vm.runInNewContext('initializeSampleEditors();',context);
assert.equal(context.sampleIndices.A,0);assert.equal(context.selections.A.time,1);
assert.equal(context.selections.A.samples[1].time,2.5);
const dirtyFunction=source.match(/function markDirty\([^)]*\) \{[\s\S]*?\n\}/)[0];
const nodes={};
const state={dirty:false,sampleDirty:false,current:{prepared:{id:'existing'},prompt:'keep prompt'},
 trackingTimeline:{clear(){}},$:id=>nodes[id]||(nodes[id]={hidden:false,checked:true,disabled:false,textContent:''}),
 sampleText:en=>en,renderSampleNotice(){},scheduleEditorSave(){}};
vm.runInNewContext(dirtyFunction+"\nmarkDirty('samples');",state);
assert.equal(state.dirty,false);assert.equal(state.sampleDirty,true);
assert.equal(state.current.prepared.id,'existing');assert.equal(state.current.prompt,'keep prompt');
assert.equal(nodes.generateVideo.disabled,true);
vm.runInNewContext("markDirty('inputs');",state);
assert.equal(state.dirty,true);assert.equal(state.sampleDirty,false);
console.log('Multi-sample editor passed: independent points, reload selection, prompt retention and stale control gate.');

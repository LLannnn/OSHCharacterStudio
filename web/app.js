const $ = id => document.getElementById(id);
let token='', current=null, active='A', dirty=true, pendingPlan=null, polling=null;
const frames={A:null,B:null};
let selections={A:{time:0,crop:null},B:{time:0,crop:null}};
let drag=null, sampleDirty=false;
let editorTimer=null,editorPromise=Promise.resolve();
const frameRequests={A:0,B:0};
const pendingFrames={A:false,B:false};
function roleCount(){return current?.character_count??2;}
function activeRoles(){return roleCount()===1?['A']:['A','B'];}
const timelineView=new VideoTimeline({
  load:(project_id,video_file)=>api('/api/timeline',{project_id,video_file}),
  error:e=>notify(e.message,true),
  preview:seconds=>{const video=$('sourceVideo');video.pause();if(video.readyState>=1)video.currentTime=seconds;},
  activate:label=>selectRole(label),
  selectSample:(label,index)=>selectSample(label,index).catch(e=>notify(e.message,true)),
  range:(range,commit)=>{
    const changed=Number($('rangeStart').value)!==range.start||Number($('rangeEnd').value)!==range.end;
    $('rangeStart').value=range.start;$('rangeEnd').value=range.end;
    if(changed)markDirty();
    if(commit)scheduleEditorSave();
  },
  pick:(label,seconds)=>{selectRole(label);getFrame(seconds,label).catch(e=>notify(e.message,true));}
});

const sampleIndices={A:0,B:0};
function samplePoint(value){return {time:value.time,crop:value.crop,...(value.frame_file?{frame_file:value.frame_file}:{})};}
function sampleList(label){const s=selections[label];return s.samples||[samplePoint(s)];}
function syncSample(label){const s=selections[label];if(s.samples)s.samples[sampleIndices[label]]=samplePoint(s);}
function syncSamples(){for(const label of ['A','B'])syncSample(label);}
function initializeSampleEditors(){
  for(const label of ['A','B']){sampleIndices[label]=0;const s=selections[label];if(s.samples)selections[label]={...s.samples[0],samples:s.samples,sample_index:0};}
}
function renderSampleList(){
  const list=$('sampleList');list.replaceChildren();
  const values=sampleList(active);
  $('sampleHeading').textContent='Source samples '+active+` · ${values.length}/6`;
  $('addSample').disabled=values.length>=6||!current?.assets.video||pendingFrames[active];
  $('removeSample').disabled=values.length<=1||pendingFrames[active];
  values.forEach((point,index)=>{
    const button=document.createElement('button');button.className='sample-card';button.classList.toggle('active',index===sampleIndices[active]);
    const text=document.createElement('span');text.textContent=`${active}${index+1} · ${point.time.toFixed(3)}s · `+(point.crop?'Box set':'Draw box');
    if(point.frame_file){const image=document.createElement('img');image.src=fileUrl(point.frame_file);image.alt='';button.append(image);}
    button.append(text);const label=active;button.onclick=action(()=>selectSample(label,index));list.append(button);
  });
}
async function selectSample(label,index){
  syncSample(label);const values=sampleList(label),point=values[index];if(!point)return;
  sampleIndices[label]=index;selections[label]={...point,samples:values,sample_index:index};
  frameRequests[label]++;pendingFrames[label]=false;frames[label]=null;selectRole(label);renderSelection();
  if(!point.frame_file)await getFrame(point.time,label);
}
$('addSample').onclick=action(async()=>{
  if(pendingFrames[active])throw new Error('Wait for the current frame to load.');
  syncSample(active);const label=active,values=sampleList(label).slice();
  if(values.length>=6)throw new Error('At most six samples per character.');
  const seconds=$('sourceVideo').currentTime,start=Number($('rangeStart').value),end=Number($('rangeEnd').value);
  if(seconds<start||seconds>=end)throw new Error('Move the video playhead inside the selected range first.');
  values.push({time:seconds,crop:null});selections[label].samples=values;
  await selectSample(label,values.length-1);markDirty('samples');renderSelection();
});
$('removeSample').onclick=action(async()=>{
  syncSample(active);const label=active,values=sampleList(label).slice();if(values.length<=1)return;
  values.splice(sampleIndices[label],1);sampleIndices[label]=0;selections[label]={...values[0],samples:values,sample_index:0};
  await selectSample(label,0);markDirty('samples');renderSelection();
});

async function api(path, data, method='POST') {
  const options={method,headers:{'X-V2V-Token':token}};
  if (data!==undefined) {options.headers['Content-Type']='application/json';options.body=JSON.stringify(data);}
  const response=await fetch(path,options);
  const result=await response.json();
  if (!response.ok) throw new Error(result.error||`HTTP ${response.status}`);
  return result;
}
function notify(message,error=false) {
  $('toast').textContent=message;$('toast').className=error?'error':'';$('toast').hidden=false;
  clearTimeout(notify.timer);notify.timer=setTimeout(()=>$('toast').hidden=true,error?11000:4500);
}
function action(fn) {return async event=>{try{document.body.classList.add('busy');await fn(event);}catch(e){notify(e.message,true);}finally{document.body.classList.remove('busy');}};}
for(const label of document.querySelectorAll('label')){
  const input=label.querySelector('input[type="file"]');
  if(input){label.setAttribute('role','button');label.tabIndex=0;label.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();input.click();}});}
}
function fileUrl(file) {return `/files/${current.id}/${file.split('/').map(encodeURIComponent).join('/')}`;}
function markDirty(kind='inputs') {
  if(kind==='samples'&&current?.prepared&&!dirty){sampleDirty=true;current.sample_inputs_changed=true;}
  else{dirty=true;sampleDirty=false;}
  trackingTimeline.clear();$('prepareState').textContent=sampleDirty?'Samples changed; prompt retained.':'Inputs changed. Prepare them again.';
  $('checkpointPanel').hidden=true;$('trackingIssues').hidden=true;$('controlPreviews').hidden=true;$('controlReviewed').checked=false;
  $('generateVideo').disabled=true;renderSampleNotice();scheduleEditorSave();
}
function renderSampleNotice(){
  const notice=$('sampleChangeNotice');
  notice.hidden=!sampleDirty&&!current?.sample_facts_stale;
  notice.textContent=sampleDirty?'Character samples changed. The existing prompt is retained. Local tracking will update the sample atlases automatically; regenerating descriptions is recommended, not required.':'Character samples changed since the saved descriptions. The existing prompt can still be used; regenerating descriptions is recommended.';
  $('prepareInputs').textContent=sampleDirty?'Update character samples locally':'Prepare and review inputs';
}
async function updateSampleInputs(){
  if(!sampleDirty&&!current.sample_inputs_changed)return;
  if(activeRoles().some(k=>pendingFrames[k]))throw new Error('Wait for sample frames to load.');
  syncSamples();if(!validRange())throw new Error('Choose a valid timeline range first.');
  if(await saveEditorNow()===false)throw new Error('Sample edits were not saved. Retry the local update.');
  const factsUnedited=$('factsText').value.trim()===JSON.stringify(current.facts||{},null,2).trim();
  const pid=current.id,p=await api('/api/samples/update',{project_id:pid});if(current.id!==pid)return;
  current=p;sampleDirty=false;dirty=false;renderCheckpoint();renderSampleNotice();
  if(factsUnedited&&Object.keys(p.facts||{}).length)$('factsText').value=JSON.stringify(p.facts,null,2);
  $('prepareState').textContent='Samples updated locally. Existing prompt retained.';
}

function editorPayload(){syncSamples();return {project_id:current.id,video_file:current.assets.video.file,character_count:roleCount(),resolution:$('outputResolution').value,range:{start:Number($('rangeStart').value),end:Number($('rangeEnd').value)},selections:structuredClone(selections)};}
function renderMode(){
  const single=roleCount()===1;
  document.body.classList.toggle('single-character',single);
  for(const id of ['selectB','characterBCard','timelineTrackB','timelinePickB'])$(id).hidden=single;
  $('modeSingle').classList.toggle('active',single);$('modeDouble').classList.toggle('active',!single);
  $('modeSingle').setAttribute('aria-pressed',single);$('modeDouble').setAttribute('aria-pressed',!single);
  $('characterModeHint').textContent=single?'Replace character A and keep other people unchanged.':'Replace both characters using A → A and B → B.';
  $('mappingTag').textContent=single?'A → A':'A → A · B → B';
  $('mappingHint').textContent=single?'Upload one replacement reference for A.':'Your A/B pairing determines each replacement.';
  $('inputCheckpointHint').textContent='Prepare the clip and '+(single?'two':'four')+' input images locally, then review what will be sent.';
  $('videoGenerationHint').textContent='Use the selected resolution and control settings with the clip, '+(single?'one':'two')+' replacement reference(s), reviewed prompt, masks and DWPose controls.';
  $('timelineHelp').textContent='Click or drag thumbnails to preview. Drag the edges to trim or the green center to move the range. Drag '+(single?'A':'A / B')+' markers to change keyframes.';
  $('factsText').placeholder='Paste Gemini base descriptions and per-sample arrays.';
  timelineView.update(selections,active,roleCount());
}
async function changeRoleCount(count){
  if(count===roleCount())return;
  await saveEditorNow();
  const response=await api('/api/characters',{project_id:current.id,character_count:count});
  current=response;selections=structuredClone({A:response.selections.A||{time:0,crop:null},B:response.selections.B||{time:0,crop:null}});
  initializeSampleEditors();
  pendingPlan=null;$('factsText').value='';$('promptText').value='';$('finalPrompt').value='';updateWordCount();
  $('resultPanel').hidden=true;
  if(count===1){frameRequests.B++;pendingFrames.B=false;if(active==='B')active='A';}
  renderMode();selectRole(active);markDirty();renderSelection();
  refreshJobs().catch(e=>notify(e.message,true));
  notify('Switched to '+(count===1?'one character':'two characters')+'. Prepare inputs again. Existing B selections are retained.');
}
function validRange(){
  const start=parseFloat($('rangeStart').value),end=parseFloat($('rangeEnd').value),duration=current?.assets.video?.info.duration;
  return Number.isFinite(start)&&Number.isFinite(end)&&start>=0&&end<=duration+.001&&end-start>=Math.min(2,duration)-.000001&&end-start<=15.000001;
}
function scheduleEditorSave(){
  if(!current?.assets.video)return;
  clearTimeout(editorTimer);$('editorSaveStatus').textContent='Saving selections…';
  editorTimer=setTimeout(()=>saveEditorNow(),400);
}
function saveEditorNow(){
  clearTimeout(editorTimer);editorTimer=null;
  if(!current?.assets.video)return editorPromise;
  if(!validRange()){$('editorSaveStatus').textContent='Choose a valid 2–15 second range inside the video.';return editorPromise;}
  const payload=editorPayload();
  editorPromise=editorPromise.catch(()=>{}).then(()=>api('/api/editor',payload)).then(()=>{if(current.id===payload.project_id)$('editorSaveStatus').textContent='Range and character selections saved locally.';return true;}).catch(e=>{if(current.id===payload.project_id){$('editorSaveStatus').textContent='Selections not saved: '+e.message;notify(e.message,true);}return false;});
  return editorPromise;
}
function selectionText(label) {
  const s=selections[label];
  return s?.crop?`${s.time.toFixed(3)}s · ${s.crop.width} × ${s.crop.height}`:frames[label]?`${s.time.toFixed(3)}s · Draw a character box`:'Choose a source character';
}
function renderSelection() {
  syncSamples();
  $('selectionA').textContent=selectionText('A');$('selectionB').textContent=selectionText('B');
  renderSampleList();
  timelineView.update(selections,active,roleCount());
}
function resetFrames() {frames.A=frames.B=null;frameRequests.A++;frameRequests.B++;pendingFrames.A=pendingFrames.B=false;}
async function refreshProjectList() {
  const projects=await api('/api/projects',undefined,'GET');
  const select=$('projectList');select.replaceChildren();
  for (const p of projects) {const opt=document.createElement('option');opt.value=p.id;opt.textContent=(p.assets.video?.name||'Untitled project')+' · '+new Date(p.created*1000).toLocaleTimeString('en-US',{hour:'2-digit',minute:'2-digit'});select.append(opt);}
  if (current) select.value=current.id;
}
function displayProject(p) {
  clearTimeout(editorTimer);editorTimer=null;
  current=p;localStorage.setItem('frame-project',p.id);dirty=!p.prepared;sampleDirty=!!p.sample_inputs_changed;active='A';resetFrames();
  $('resultPanel').hidden=true;$('resultLinks').replaceChildren();$('resultVideo').removeAttribute('src');
  selections=structuredClone({A:p.editor?.selections.A||p.selections.A||{time:0,crop:null},B:p.editor?.selections.B||p.selections.B||{time:0,crop:null}});
  $('outputResolution').value=p.editor?.resolution??(p.prepared?(p.prepared.resolution||'480p'):'source');
  initializeSampleEditors();
  renderMode();
  renderGeneration();refreshControls().catch(e=>notify(e.message,true));
  if (p.assets.video) {
    $('sourceVideo').src=fileUrl(p.assets.video.file);$('sourceVideo').hidden=false;$('videoEmpty').hidden=true;
    const i=p.assets.video.info;$('videoMeta').textContent=`${p.assets.video.name} · ${i.width} × ${i.height} · ${i.fps.toFixed(3)}fps · ${i.duration.toFixed(3)}s`;
    $('rangeStart').value=p.editor?.range.start??p.prepared?.start??0;$('rangeEnd').value=p.editor?.range.end??p.prepared?.end??Math.min(7,i.duration);
  } else {$('sourceVideo').removeAttribute('src');$('sourceVideo').hidden=true;$('videoEmpty').hidden=false;$('videoMeta').textContent='No video loaded';}
  for (const label of ['A','B']) {
    const a=p.assets['target_'+label];$('target'+label+'Image').hidden=!a;$('target'+label+'Empty').hidden=!!a;
    if(a)$('target'+label+'Image').src=fileUrl(a.file);
    $('target'+label+'Name').textContent=a?.name||'Identity and outfit reference';
  }
  $('factsText').value=Object.keys(p.facts||{}).length?JSON.stringify(p.facts,null,2):'';
  $('promptText').value=p.prompt||'';updateWordCount();renderSelection();selectRole('A');
  $('checkpointPanel').hidden=!p.prepared||sampleDirty;
  renderSampleNotice();
  if (p.prepared&&!sampleDirty)renderCheckpoint();
  for(const label of ['A','B']){
    const file=selections[label].frame_file||(p.prepared?.selections?.[label]?`prepared/${p.prepared.id}/frame_${label}.jpg`:null);
    if(!file)continue;
    selections[label].frame_file=file;
    const image=new Image(),request=++frameRequests[label];image.onload=()=>{if(current.id!==p.id||request!==frameRequests[label])return;frames[label]=image;renderSelection();if(active===label)drawFrame();};image.src=fileUrl(file);
  }
  $('editorSaveStatus').textContent='';
  timelineView.reset();
  if(p.assets.video){const pid=p.id;timelineView.load(p,{start:Number($('rangeStart').value),end:Number($('rangeEnd').value)},selections,file=>`/files/${pid}/${file.split('/').map(encodeURIComponent).join('/')}`);}
  $('prepareState').textContent=p.prepared?'Inputs ready. Generate descriptions or paste a prompt.':'Prepare local inputs first.';
  refreshJobs().catch(e=>notify(e.message,true));
}
async function upload(file,role) {
  if (!file)return;
  await saveEditorNow();notify('Importing locally: '+file.name+'…');
  const url='/api/upload?'+new URLSearchParams({project_id:current.id,role,name:file.name});
  const response=await fetch(url,{method:'POST',headers:{'X-V2V-Token':token},body:file});
  const p=await response.json();if(!response.ok)throw new Error(p.error);
  // Keep unsaved A/B selections when only reference images changed.
  if(role!=='video')p.selections=selections;
  displayProject(p);await refreshProjectList();notify('Import complete.');
}
function selectRole(label) {
  if(!activeRoles().includes(label))label='A';
  active=label;$('selectA').classList.toggle('active',label==='A');$('selectB').classList.toggle('active',label==='B');
  $('frameTime').value=selections[label]?.time??0;
  timelineView.update(selections,active,roleCount());
  if(current?.assets.video&&frames[label])timelineView.preview(selections[label].time);
  if(!frames[label]&&!pendingFrames[label]&&selections[label]?.frame_file){
    const pid=current.id,file=selections[label].frame_file,request=++frameRequests[label],image=new Image();
    image.onload=()=>{if(current.id!==pid||frameRequests[label]!==request)return;frames[label]=image;renderSelection();if(active===label)drawFrame();};
    image.src=fileUrl(file);
  }
  drawFrame();renderSampleList();
}
async function getFrame(seconds,label=active) {
  if(!activeRoles().includes(label))throw new Error('This mode does not use character '+label);
  if(!current.assets.video)throw new Error('Upload a video first.');
  if(!validRange())throw new Error('Choose a valid replacement range first.');
  const start=Number($('rangeStart').value),end=Number($('rangeEnd').value);
  if(!Number.isFinite(seconds)||seconds<start-.001||seconds>=end)throw new Error('Choose a character keyframe inside the replacement range.');
  const pid=current.id,videoFile=current.assets.video.file,request=++frameRequests[label];
  const same=Math.abs(selections[label].time-Number(seconds))<.0005;
  pendingFrames[label]=true;if(!same)markDirty('samples');
  timelineView.preview(seconds);timelineView.update({...selections,[label]:{time:seconds,crop:same?selections[label].crop:null}},active,roleCount());
  if(active===label){$('frameTime').value=seconds;$('cropInfo').textContent='Loading character '+label+' at '+seconds.toFixed(3)+'s frame…';}
  try{
    const data=await api('/api/frame',{project_id:pid,time:seconds});
    if(current.id!==pid||current.assets.video.file!==videoFile||request!==frameRequests[label])return;
    const image=new Image();await new Promise((resolve,reject)=>{image.onload=resolve;image.onerror=()=>reject(new Error('Could not load the keyframe.'));image.src=fileUrl(data.file);});
    if(current.id!==pid||current.assets.video.file!==videoFile||request!==frameRequests[label])return;
    frames[label]=image;selections[label]={...selections[label],time:Number(seconds),crop:same?selections[label].crop:null,frame_file:data.file};syncSample(label);
    if(!same)markDirty('samples');else scheduleEditorSave();
    if(active===label)$('frameTime').value=seconds;
    renderSelection();if(active===label)drawFrame();
    if(!same)notify('Character '+label+' moved to '+seconds.toFixed(3)+'s. Draw a new box for this character.');
  }finally{if(request===frameRequests[label]){pendingFrames[label]=false;timelineView.update(selections,active,roleCount());}}
}
function drawFrame() {
  const canvas=$('cropCanvas'),image=frames[active];
  canvas.hidden=!image;$('cropEmpty').hidden=!!image;
  if(!image){$('cropInfo').textContent='Load a keyframe, then draw a character box.';return;}
  canvas.width=image.naturalWidth;canvas.height=image.naturalHeight;
  const ctx=canvas.getContext('2d');ctx.drawImage(image,0,0);
  const r=selections[active]?.crop;
  if(r){
    ctx.fillStyle='#0009';ctx.fillRect(0,0,canvas.width,canvas.height);
    ctx.drawImage(image,r.x,r.y,r.width,r.height,r.x,r.y,r.width,r.height);
    ctx.strokeStyle=active==='A'?'#b6e6b4':'#a9c7ed';ctx.lineWidth=Math.max(2,canvas.width/500);ctx.strokeRect(r.x,r.y,r.width,r.height);
    for(const [x,y]of[[r.x,r.y],[r.x+r.width,r.y],[r.x,r.y+r.height],[r.x+r.width,r.y+r.height]]){ctx.fillStyle=ctx.strokeStyle;const d=Math.max(5,canvas.width/150);ctx.fillRect(x-d/2,y-d/2,d,d);}
    for(const[k,id]of[['x','cropX'],['y','cropY'],['width','cropW'],['height','cropH']])$(id).value=r[k];
    $('cropInfo').textContent=`Character ${active} · ${selections[active].time.toFixed(3)}s · Source image ${canvas.width} × ${canvas.height} · Box ${r.x}, ${r.y}, ${r.width}, ${r.height}`;
  }else {$('cropInfo').textContent=`Character ${active} · ${selections[active].time.toFixed(3)}s · Source image ${canvas.width} × ${canvas.height} · Draw a new box`;for(const id of ['cropX','cropY','cropW','cropH'])$(id).value='';}
}
function point(event){const c=$('cropCanvas'),r=c.getBoundingClientRect();return {x:Math.round(Math.max(0,Math.min(c.width,(event.clientX-r.left)*c.width/r.width))),y:Math.round(Math.max(0,Math.min(c.height,(event.clientY-r.top)*c.height/r.height)))};}
$('cropCanvas').addEventListener('pointerdown',event=>{drag=point(event);$('cropCanvas').setPointerCapture(event.pointerId);});
$('cropCanvas').addEventListener('pointermove',event=>{if(!drag)return;const p=point(event);selections[active].crop={x:Math.min(p.x,drag.x),y:Math.min(p.y,drag.y),width:Math.abs(p.x-drag.x),height:Math.abs(p.y-drag.y)};drawFrame();});
$('cropCanvas').addEventListener('pointerup',()=>{if(!drag)return;drag=null;const r=selections[active].crop;if(!r||Math.min(r.width,r.height)<8){selections[active].crop=null;notify('Character boxes must be at least 8×8 pixels.',true);}markDirty('samples');renderSelection();drawFrame();});
$('cropCanvas').addEventListener('pointercancel',()=>{drag=null;});
function renderCheckpoint(){
  const grid=$('checkpointImages');grid.replaceChildren();
  const names={source_A:'Source A',target_A:'Replacement A',source_B:'Source B',target_B:'Replacement B'};
  for(const role of activeRoles().flatMap(label=>['source_'+label,'target_'+label])){const fig=document.createElement('figure'),img=document.createElement('img'),cap=document.createElement('figcaption');img.src=fileUrl(current.prepared.files[role]);img.alt=names[role];cap.textContent=names[role]+(role.startsWith('source_')&&current.prepared.atlases?.[role.slice(-1)]?' · '+'sample atlas':'');const link=document.createElement('a');link.href=img.src;link.target='_blank';link.rel='noopener';link.append(img);fig.append(link,cap);grid.append(fig);}
  $('preparedVideo').src=fileUrl(current.prepared.files.clip);$('checkpointPanel').hidden=false;
}
function updateWordCount(){$('promptCount').textContent=($('promptText').value.trim().match(/\S+/g)||[]).length+' words';}
async function savePrompt(){const pid=current.id,p=await api('/api/save-prompt',{project_id:pid,prompt:$('promptText').value,character_count:roleCount()});if(current.id===pid){current=p;fillTrackingDescriptions();}}
function setStep(n){
  $('prepareSection').hidden=n!==1;$('generateSection').hidden=n!==2;
  $('step1').classList.toggle('active',n===1);$('step2').classList.toggle('active',n===2);
  if(n===2){fillTrackingDescriptions();$('finalPrompt').value=$('promptText').value;renderVideoSummary();refreshControls().catch(e=>notify(e.message,true));}
}
function renderVideoSummary(){
  const box=$('videoSummary');box.replaceChildren();
  if(dirty||!current.prepared){const p=document.createElement('p');p.textContent='Return to step 1 and prepare the clip and character inputs.';box.append(p);return;}
  const first=document.createElement('div'),v=document.createElement('video'),p=document.createElement('p');v.controls=true;v.src=fileUrl(current.prepared.files.clip);const info=current.prepared.info,o=info.output||info;p.textContent=`${current.prepared.start.toFixed(3)}–${current.prepared.end.toFixed(3)}s · Output ${o.width}×${o.height} · 24fps · H3 canvas ${info.width}×${info.height}`;first.append(v,p);box.append(first);
  for(const label of activeRoles()){const d=document.createElement('div'),i=document.createElement('img'),t=document.createElement('p');i.src=fileUrl(current.prepared.files['target_'+label]);i.alt='Replacement reference '+label;t.textContent='Reference '+label+' → Source '+label;d.append(i,t);box.append(d);}
}
async function openPlan(kind){
  if(sampleDirty)await updateSampleInputs();
  if(activeRoles().some(k=>pendingFrames[k]))throw new Error('Wait for the character keyframe to load.');
  if(dirty||!current.prepared)throw new Error('Return to step 1 to prepare and review the inputs.');
  const prompt=kind==='video'?$('finalPrompt').value:'';
  if(kind==='video')await saveGeneration();
  pendingPlan=await api('/api/plan',{project_id:current.id,kind,prompt,character_count:roleCount()});
  $('costText').textContent=pendingPlan.cost;
  $('confirmTitle').textContent=kind==='prompt'?'Generate descriptions only':'Generate replacement video only';
  const count=pendingPlan.character_count;
  $('confirmInputs').textContent=(count===1?'One character: ':'Two characters: ')+(kind==='prompt'?`1 clip + ${count*2} ordered character images. Generates descriptions only.`:`1 clip + ${count} reference images + reviewed prompt, masks and DWPose controls. Union 2.0 video generation only.`);
  $('confirmTrackingWarning').hidden=!pendingPlan.tracking_warnings?.length;
  $('confirmTrackingWarning').textContent=pendingPlan.tracking_warnings?.length?`You accepted ${pendingPlan.tracking_warnings.length} unresolved tracking warning(s). Missing frames may remain unchanged or be replaced incorrectly. Add samples and retry, or split into shorter replacement tasks if needed. This submission uses the available controls.`:'';
  $('graphPreview').textContent=JSON.stringify(pendingPlan.graph,null,2);
  $('acceptCost').checked=false;$('confirmSubmit').disabled=true;$('confirmDialog').showModal();
}
function download(name,text,type='application/json'){const a=document.createElement('a');const url=URL.createObjectURL(new Blob([text],{type}));a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),500);}
let controlPolling=null,controlRecord=null,descriptionsDirty=false,descriptionEditSerial=0;
const descriptionEdits=new Set();
const trackingTimeline=new TrackingTimeline($('trackingTimeline'),{
  load:(project_id,control_id)=>api('/api/controls/timeline',{project_id,control_id}),
  seek:(time,video)=>{const pose=$('posePreview');if(video===$('maskPreview')&&pose.readyState>=1){pose.pause();pose.currentTime=time;}},
  issue:issue=>{for(const node of $('trackingIssueList').children)node.classList.toggle('selected',node.dataset.issueId===issue.id);}
});
function generationPayload(){const data={project_id:current.id,grow_px:Number($('controlGrow').value),strength:Number($('controlStrength').value)};if(current.prepared&&descriptionsDirty)data.tracking_descriptions=Object.fromEntries(activeRoles().filter(k=>descriptionEdits.has(k)).map(k=>[k,$('trackingDescription'+k).value]));return data;}
function fillTrackingDescriptions(){
  if(descriptionsDirty)return;
  const override=current.tracking_descriptions;
  const descriptions=current.prepared&&override?.checkpoint_id===current.prepared.id?override.values:null;
  for(const k of ['A','B'])$('trackingDescription'+k).value=descriptions?.[k]?.trim()||current.facts?.['source_'+k]?.trim()||'';
}
function renderGeneration(){
  const settings=current.generation||{};
  $('controlGrow').value=settings.grow_px??16;$('controlStrength').value=settings.strength??1;
  descriptionsDirty=false;
  descriptionEdits.clear();
  fillTrackingDescriptions();
  $('trackingDescriptionBLabel').hidden=roleCount()===1;

}
async function saveGeneration(){
  const pid=current.id,serial=descriptionEditSerial,r=await api('/api/generation',generationPayload());
  if(current.id===pid){current.generation=r.generation;current.tracking_descriptions=r.tracking_descriptions;current.facts=r.facts;pendingPlan=null;if(serial===descriptionEditSerial)renderGeneration();}
}
function controlAvailability(record,inputsChanged=false){
  const current=!inputsChanged&&record.valid;
  const ready=!!(current&&['completed','needs_input'].includes(record.status)&&['control_mask','control_pose','control_source'].every(k=>record.files?.[k])&&record.previews?.mask&&record.previews?.pose);
  const warnings=!!(current&&(record.quality?.requires_input||record.quality?.issues?.length));
  return {ready,warnings,needsReview:!!(current&&(record.status==='needs_input'||warnings))};
}
function warningSummary(record){
  const issues=record.quality?.issues||[],fps=record.manifest?.fps||24;
  return issues.length?issues.map(i=>`${i.labels.join('/')} · ${(i.start/fps).toFixed(2)}–${(i.end/fps).toFixed(2)}s`).join('; '):'Some tracking ranges remain uncertain.';
}
async function refreshControls(){
  clearTimeout(controlPolling);if(!current)return;
  const pid=current.id,r=await api('/api/controls/'+pid,undefined,'GET');if(current.id!==pid)return;
  const previous=controlRecord;controlRecord=r;
  const {ready,warnings,needsReview}=controlAvailability(r,dirty||sampleDirty||descriptionsDirty);
  const needsInput=needsReview;
  if(previous?.id!==r.id||JSON.stringify(previous?.quality?.issues)!==JSON.stringify(r.quality?.issues)||!warnings||!ready)$('acceptTrackingWarnings').checked=false;
  if(r.tracking_warnings_accepted)$('acceptTrackingWarnings').checked=true;
  $('trackingIssues').hidden=!needsInput;
  renderTrackingIssues(needsInput?r:null);
  $('trackingWarning').hidden=!warnings;
  $('trackingWarningSummary').textContent=warningSummary(r)+(ready?' Available mask and pose files are ready.':' Complete control files are not available yet; prepare local controls again.');
  $('acceptTrackingWarnings').disabled=!ready;
  $('controlPreviews').hidden=!ready;$('controlReviewed').checked=ready&&r.approved;
  $('controlReviewed').disabled=!ready||(warnings&&!$('acceptTrackingWarnings').checked);
  $('prepareControls').disabled=r.status==='running'||dirty||!current.prepared;
  const text=r.status==='running'?(r.progress.detail||'Preparing locally. Please wait…'):
    r.status==='failed'||r.status==='interrupted'?(r.error||'Local preparation failed.'):
    needsInput&&r.approved?'Tracking warnings accepted. Controls approved; review and submit the video when ready.':
    needsInput?`Some tracking ranges need review. Add samples and retry, split into shorter replacement tasks, or accept the red warning to continue${ready?' with the available controls':'; prepare complete controls first'}.`:
    ready?(r.approved?'Controls approved. Review and submit the video when ready.':'Controls ready. Play both previews and approve the character tracking and poses.'):
    descriptionsDirty?'Locator descriptions changed. Save them or prepare local controls.':
    dirty||!current.prepared?'Return to step 1 and prepare video and character inputs.':'Controls are not ready for the current inputs. Prepare them locally.';
  $('controlStatus').textContent=text;$('controlStatus').classList.toggle('error',warnings||['failed','interrupted'].includes(r.status));
  $('generateVideo').disabled=!ready||!r.approved||(warnings&&!$('acceptTrackingWarnings').checked);
  if(ready){
    for(const [id,role] of [['maskPreview','mask'],['posePreview','pose']]){const url=fileUrl(r.previews[role]);if($(id).getAttribute('src')!==url)$(id).src=url;}
    $('controlPresence').textContent=Object.entries(r.manifest.presence).map(([label,p])=>`${label}: tracked ${p.nonempty_frames} frames${p.nonempty_frames?`, clip ${p.first_seconds.toFixed(2)}–${p.last_seconds.toFixed(2)}s`:''}; valid pose ${r.manifest.pose_nonempty_frames[label]} frames`).join('; ')+`. Automatic source locations: ${r.quality?.auto_anchors?.length||0}. Undetected frames have empty masks. Check crossings, entrances, exits and small people.`;
    const box=$('controlDownloads');box.replaceChildren();for(const [role,name] of [['control_mask','Mask video (white = redraw)'],['control_pose','DWPose control video']]){const a=document.createElement('a');a.href=fileUrl(r.files[role]);a.download='';a.textContent=name;box.append(a);}
  }
  if(ready||needsInput){
    const video=needsInput?$('trackingReviewVideo'):$('maskPreview');
    trackingTimeline.load(current,r,video,rel=>`/files/${pid}/${rel.split('/').map(encodeURIComponent).join('/')}`);
  }else trackingTimeline.clear();
  if(r.status==='running')controlPolling=setTimeout(()=>refreshControls().catch(e=>notify(e.message,true)),2500);
}
for(const id of ['controlGrow','controlStrength'])$(id).onchange=action(async()=>{await saveGeneration();await refreshControls();});
$('outputResolution').onchange=()=>{markDirty();notify('Resolution changed. Prepare the video and character inputs again. Existing jobs are unchanged.');};
for(const k of ['A','B']){
  $('trackingDescription'+k).oninput=()=>{descriptionEditSerial++;descriptionEdits.add(k);descriptionsDirty=true;trackingTimeline.clear();$('generateVideo').disabled=true;$('controlReviewed').checked=false;$('controlPreviews').hidden=true;$('trackingIssues').hidden=true;$('controlStatus').textContent='Locator descriptions changed. Save them or prepare local controls.';pendingPlan=null;};
}
$('saveTrackingDescriptions').onclick=action(async()=>{await saveGeneration();await refreshControls();notify('Locator descriptions saved. Local preparation will reuse them.');});
async function prepareLocalControls(){await updateSampleInputs();await saveGeneration();await api('/api/controls/start',{project_id:current.id});await refreshControls();notify('Independent local tracking and automatic source location started.');}
$('prepareControls').onclick=action(prepareLocalControls);
$('retryTracking').onclick=action(prepareLocalControls);
$('controlReviewed').onchange=action(async()=>{await api('/api/controls/approve',{project_id:current.id,control_id:controlRecord.id,approved:$('controlReviewed').checked,accept_tracking_warnings:$('acceptTrackingWarnings').checked});await refreshControls();});
$('acceptTrackingWarnings').onchange=action(async()=>{const accepted=$('acceptTrackingWarnings').checked;if(!accepted&&controlRecord?.approved){await api('/api/controls/approve',{project_id:current.id,control_id:controlRecord.id,approved:false});await refreshControls();}$('controlReviewed').checked=accepted&&!!controlRecord?.approved;$('controlReviewed').disabled=!accepted;$('generateVideo').disabled=true;});
function renderTrackingIssues(record){
  const list=$('trackingIssueList');list.replaceChildren();if(!record)return;
  const url=fileUrl(record.previews.mask);if($('trackingReviewVideo').getAttribute('src')!==url)$('trackingReviewVideo').src=url;
  for(const issue of record.quality.issues){
    const card=document.createElement('div');card.className='tracking-issue';card.dataset.issueId=issue.id;
    const title=document.createElement('b'),text=document.createElement('p'),actions=document.createElement('div');actions.className='issue-actions';
    title.textContent=`Character ${issue.labels.join(' / ')} · Clip ${(issue.start/24).toFixed(3)}–${(issue.end/24).toFixed(3)}s`;
    text.textContent=issue.reason;card.append(title,text,actions);
    const locate=document.createElement('button');locate.textContent='Preview this range';locate.onclick=()=>trackingTimeline.seek(issue.seconds,issue);actions.append(locate);
    for(const label of issue.labels){
      const absent=document.createElement('button');absent.textContent=issue.kind==='anchor'?label+' is absent from this shot':label+' is absent / occluded normally';
      absent.onclick=action(()=>applyTrackingDecision(issue,label,'absent'));actions.append(absent);
    }
    if(issue.kind==='overlap'){const accept=document.createElement('button');accept.textContent='Confirm normal overlap';accept.onclick=action(()=>applyTrackingDecision(issue,issue.labels[0],'accept'));actions.append(accept);}
    if(issue.kind==='anchor'&&issue.boundary_reason==='candidate_cut'){const merge=document.createElement('button');merge.textContent='Continuous shot: reuse previous tracking';merge.onclick=action(()=>applyTrackingDecision(issue,issue.labels[0],'merge'));actions.append(merge);}
    list.append(card);
  }
}
async function applyTrackingDecision(issue,label,choice){
  const pid=current.id,cid=controlRecord.id;
  await api('/api/controls/correct',{project_id:pid,control_id:cid,issue_id:issue.id,label,action:choice});
  await api('/api/controls/start',{project_id:pid});await refreshControls();
  notify('Decision saved. Rebuilding and checking local tracks.');
}
$('resetTracking').onclick=action(async()=>{await api('/api/controls/reset',{project_id:current.id});await refreshControls();notify('Tracking decisions cleared. Descriptions, source selections and prompt are retained. Prepare controls again.');});
const statusNames={uploading:'Uploading inputs',submitting:'Submitting',running:'Queued / running',completed:'Completed · review needed',failed:'Failed',submission_unknown:'Submission unknown · check Cloud history',polling_paused:'Polling paused',needs_review:'Descriptions need review'};
async function refreshJobs(){
  if(!current)return;
  const pid=current.id;
  const latest=await api('/api/project/'+pid,undefined,'GET');if(current.id!==pid)return;current.jobs=latest.jobs;
  if(latest.prepared?.id===current.prepared?.id&&latest.character_count===current.character_count){
    current.facts=latest.facts;current.sample_facts_stale=latest.sample_facts_stale;renderSampleNotice();current.tracking_descriptions=latest.tracking_descriptions;fillTrackingDescriptions();
  }
  const box=$('jobList');box.replaceChildren();let running=false,shownResult=false;
  for(const id of [...current.jobs].reverse()){
    const j=await api(`/api/job/${pid}/${id}`,undefined,'GET');if(current.id!==pid)return;
    const row=document.createElement('div');row.className='job';
    const d=document.createElement('div'),title=document.createElement('div'),small=document.createElement('small');title.className='job-title';title.textContent=(j.kind==='prompt'?'01 · Gemini descriptions':'02 · H3 video · '+(j.generation_version||'v2').toUpperCase())+' · '+new Date(j.created*1000).toLocaleString('en-US');small.textContent=j.prompt_id?'Cloud job: '+j.prompt_id:'Upload / submission record (no Cloud ID yet): '+j.id;d.append(title,small);
    if(j.error){const error=document.createElement('p');error.className='job-error';error.textContent=j.error;d.append(error);}
    const controls=document.createElement('div'),s=document.createElement('span');s.className='status';s.textContent=statusNames[j.status]||j.status;controls.append(s);
    if(j.prompt_id){const link=document.createElement('a');link.textContent=' Cloud ';link.href='https://cloud.comfy.org/';link.target='_blank';link.rel='noreferrer';controls.append(link);}
    if(j.kind==='prompt'&&j.status==='completed'){
      const load=document.createElement('button');load.textContent='Load descriptions and prompt';load.disabled=(j.character_count??2)!==roleCount();load.title=load.disabled?'Switch to the same character count as this job.':'';load.onclick=()=>{if((j.character_count??2)!==roleCount()){notify('Switch to the same character count as this job first.',true);return;}$('factsText').value=JSON.stringify(j.facts,null,2);$('promptText').value=j.prompt;updateWordCount();setStep(1);notify('Loaded. Review the pairing, actions and scene.');};controls.append(load);
    }
    if(j.status==='needs_review'&&j.raw_text){const b=document.createElement('button');b.textContent='View raw descriptions';b.onclick=()=>{$('factsText').value=j.raw_text;$('factsEditor').hidden=false;setStep(1);};controls.append(b);}
    if(j.prompt_id&&['polling_paused','failed','running'].includes(j.status)){const b=document.createElement('button');b.textContent='Resume polling this job';b.onclick=action(async()=>{await api('/api/resume',{project_id:current.id,job_id:j.id});await refreshJobs();});controls.append(b);}
    if(!shownResult&&j.kind==='video'&&(j.character_count??2)===roleCount()&&j.status==='completed'&&j.review){shownResult=true;$('resultPanel').hidden=false;if($('resultVideo').getAttribute('src')!==fileUrl(j.review)){$('resultVideo').src=fileUrl(j.review);const links=$('resultLinks');links.replaceChildren();for(const [name,path]of[['Download video',j.review],...(j.outputs||[]).map((p,i)=>['Raw output '+(i+1),p])]){const a=document.createElement('a');a.href=fileUrl(path);a.textContent=name;a.download='';links.append(a);}}}
    row.append(d,controls);box.append(row);
    if(['uploading','submitting','running'].includes(j.status))running=true;
    if(j.status==='completed'&&j.kind==='prompt'&&(j.character_count??2)===roleCount()&&!$('promptText').value.trim()&&j.checkpoint_id===current.prepared?.id){$('factsText').value=JSON.stringify(j.facts,null,2);$('promptText').value=j.prompt;updateWordCount();}
  }
  if(!current.jobs.length)box.textContent='No Cloud jobs yet. Uploading, keyframes and boxes are processed locally.';
  clearTimeout(polling);if(running)polling=setTimeout(()=>refreshJobs().catch(e=>notify(e.message,true)),5000);
}

$('newProject').onclick=action(async()=>{await saveEditorNow();displayProject(await api('/api/project',{}));await refreshProjectList();setStep(1);});
$('modeSingle').onclick=action(()=>changeRoleCount(1));$('modeDouble').onclick=action(()=>changeRoleCount(2));
$('projectList').onchange=action(async()=>{const selected=$('projectList').value;await saveEditorNow();displayProject(await api('/api/project/'+selected,undefined,'GET'));setStep(1);});
for(const id of ['videoUpload','videoUpload2'])$(id).onchange=action(e=>upload(e.target.files[0],'video'));
for(const label of ['A','B']){
  $('target'+label+'Upload').onchange=action(e=>upload(e.target.files[0],'target_'+label));
  $('select'+label).onclick=() => selectRole(label);
  $('edit'+label).onclick=()=>{selectRole(label);$('frameTime').focus();};
}
for(const [id,part]of[['rangeStart','start'],['rangeEnd','end']]){
  $(id).oninput=()=>{if(!current?.assets.video)return;if(validRange())timelineView.setRange(Number($('rangeStart').value),Number($('rangeEnd').value));markDirty();};
  $(id).onchange=()=>{
    if(!current?.assets.video)return;
    const start=parseFloat($('rangeStart').value),end=parseFloat($('rangeEnd').value);
    if(!Number.isFinite(start)||!Number.isFinite(end))return;
    const range=timelineView.setRange(start,end,part);$('rangeStart').value=range.start;$('rangeEnd').value=range.end;markDirty();
  };
}
$('sourceVideo').addEventListener('timeupdate',()=>timelineView.follow($('sourceVideo').currentTime));
$('getFrame').onclick=action(()=>getFrame(Number($('frameTime').value)));
$('captureCurrent').onclick=action(async()=>{$('frameTime').value=$('sourceVideo').currentTime.toFixed(3);await getFrame(Number($('frameTime').value));});
$('applyCrop').onclick=()=>{if(!frames[active])return notify('Load a keyframe first.',true);selections[active].crop={x:Number($('cropX').value),y:Number($('cropY').value),width:Number($('cropW').value),height:Number($('cropH').value)};markDirty('samples');renderSelection();drawFrame();};
$('prepareInputs').onclick=action(async()=>{
  if(sampleDirty){await updateSampleInputs();await refreshControls();notify('Samples updated; video, references and prompt retained.');return;}
  if(activeRoles().some(k=>pendingFrames[k]))throw new Error('Wait for the character keyframe to load.');
  syncSamples();
  if(activeRoles().some(k=>sampleList(k).some(p=>!p.crop)))throw new Error(roleCount()===1?'Load a keyframe and draw a box for A.':'Load keyframes and draw boxes for A and B.');
  await saveEditorNow();
  notify('Preparing the clip and character images locally…');
  const p=await api('/api/prepare',{project_id:current.id,start:Number($('rangeStart').value),end:Number($('rangeEnd').value),character_count:roleCount(),resolution:$('outputResolution').value,selections});
  current=p;dirty=false;sampleDirty=false;renderSampleNotice();$('promptText').value='';$('factsText').value='';updateWordCount();renderCheckpoint();renderGeneration();await refreshControls();$('prepareState').textContent='Inputs ready. Review the '+(roleCount()===1?'two':'four')+' images.';notify('Local inputs are ready.');
});
$('generatePrompt').onclick=action(()=>openPlan('prompt'));
$('generateVideo').onclick=action(()=>openPlan('video'));
$('toggleFacts').onclick=()=>$('factsEditor').hidden=!$('factsEditor').hidden;
$('assemblePrompt').onclick=action(async()=>{const pid=current.id,r=await api('/api/assemble',{project_id:pid,facts:$('factsText').value,character_count:roleCount()});if(current.id!==pid)return;current.facts=r.facts;current.sample_facts_stale=false;renderSampleNotice();$('promptText').value=r.prompt;fillTrackingDescriptions();updateWordCount();notify('Prompt assembled locally.');});
$('promptText').oninput=updateWordCount;
$('copyPrompt').onclick=action(async()=>{await navigator.clipboard.writeText($('promptText').value);notify('Copied.');});
$('savePrompt').onclick=action(async()=>{await savePrompt();notify('Prompt saved.');});
$('goVideo').onclick=action(async()=>{if(sampleDirty)await updateSampleInputs();if(dirty||!current.prepared)throw new Error('Prepare and review the inputs first.');if(!$('promptText').value.trim())throw new Error('Generate or paste a prompt first.');await savePrompt();setStep(2);window.scrollTo({top:0,behavior:'smooth'});});
$('step1').onclick=()=>{if(!$('generateSection').hidden)$('promptText').value=$('finalPrompt').value;setStep(1);updateWordCount();};
$('step2').onclick=()=>setStep(2);
$('acceptCost').onchange=()=>$('confirmSubmit').disabled=!$('acceptCost').checked;
$('cancelSubmit').onclick=()=>$('confirmDialog').close();
$('exportGraph').onclick=()=>pendingPlan&&download(`v2v-${pendingPlan.kind}-prepared.api.json`,JSON.stringify(pendingPlan.graph,null,2));
$('confirmSubmit').onclick=action(async()=>{
  $('confirmSubmit').disabled=true;
  const j=await api('/api/submit',{plan_id:pendingPlan.id,fingerprint:pendingPlan.fingerprint,accepted:$('acceptCost').checked});
  $('confirmDialog').close();pendingPlan=null;notify('One job created. Submissions are not retried automatically.');await refreshJobs();
});
$('refreshJobs').onclick=action(refreshJobs);
$('settingsOpen').onclick=()=>$('settingsDialog').showModal();$('settingsClose').onclick=()=>$('settingsDialog').close();
function displayConfig(c){token=c.token;$('keyFile').value=c.key_file;$('connection').textContent=c.key_configured?'Comfy key configured':'Key not configured';$('connection').classList.toggle('ready',c.key_configured);$('toolStatus').textContent=`FFmpeg ${c.tools.ffmpeg?'✓':'Not found'} · FFprobe ${c.tools.ffprobe?'✓':'Not found'}`;for(const [id,key]of[['controlPython','control_python'],['controlAux','control_aux'],['controlPoseModel','control_pose_model'],['controlExtraSite','control_extra_site']])$(id).value=c.control_runtime?.[key]||'';}
$('saveSettings').onclick=action(async()=>{const value={key_file:$('keyFile').value};for(const [id,key]of[['controlPython','control_python'],['controlAux','control_aux'],['controlPoseModel','control_pose_model'],['controlExtraSite','control_extra_site']])value[key]=$(id).value;displayConfig(await api('/api/config',value));$('settingsDialog').close();notify('Local configuration paths saved.');});

(async()=>{
  try{
    displayConfig(await api('/api/config',undefined,'GET'));
    const list=await api('/api/projects',undefined,'GET');
    const existing=list.find(p=>p.id===localStorage.getItem('frame-project'))||list[0];
    displayProject(existing||await api('/api/project',{}));await refreshProjectList();
  }catch(e){notify(e.message,true);}
})();

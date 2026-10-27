(function(root){
  'use strict';
  const clamp=(x,a,b)=>Math.max(a,Math.min(b,x));
  const round=x=>Math.round(x*1000)/1000;
  function limits(duration){return {min:Math.min(2,duration),max:Math.min(15,duration)};}
  function normalizeRange(duration,start,end){
    const l=limits(duration);
    if(!Number.isFinite(duration)||duration<=0)throw new Error('Invalid video duration.');
    start=Number.isFinite(start)?start:0;end=Number.isFinite(end)?end:Math.min(7,duration);
    start=clamp(start,0,duration-l.min);end=clamp(end,start+l.min,Math.min(duration,start+l.max));
    return {start:round(start),end:round(end)};
  }
  function moveRange(duration,range,part,value){
    const r=normalizeRange(duration,range.start,range.end),l=limits(duration);
    if(part==='start')r.start=clamp(value,Math.max(0,r.end-l.max),r.end-l.min);
    else if(part==='end')r.end=clamp(value,r.start+l.min,Math.min(duration,r.start+l.max));
    else if(part==='move'){const length=r.end-r.start;r.start=clamp(value,0,duration-length);r.end=r.start+length;}
    return {start:round(r.start),end:round(r.end)};
  }
  function inputRange(duration,start,end,part){
    const l=limits(duration);
    if(part==='start'){end=clamp(end,l.min,duration);start=clamp(start,Math.max(0,end-l.max),end-l.min);}
    else{start=clamp(start,0,duration-l.min);end=clamp(end,start+l.min,Math.min(duration,start+l.max));}
    return {start:round(start),end:round(end)};
  }
  function frameTime(time,duration,fps=24){return round(clamp(time,0,Math.max(0,duration-Math.max(.04,1/(fps||24)))));}
  function rangeFrameTime(time,range,duration,fps=24){return frameTime(clamp(time,range.start,Math.max(range.start,range.end-1/(fps||24))),duration,fps);}
  const model={normalizeRange,moveRange,inputRange,frameTime,rangeFrameTime};
  if(typeof module!=='undefined'&&module.exports)module.exports=model;
  if(typeof document==='undefined')return;

  class VideoTimeline{
    constructor(options){
      this.options=options;this.duration=0;this.fps=24;this.range={start:0,end:0};this.markers={};this.time=0;this.active='A';this.labels=['A','B'];this.drag=null;this.request=0;
      this.el=id=>document.getElementById(id);
      const film=this.el('timelineFilm');
      film.addEventListener('pointerdown',e=>{if(!this.duration)return;e.preventDefault();film.setPointerCapture(e.pointerId);const thumb=e.target.closest('.timeline-thumb');this.drag={part:'scrub',x:e.clientX};this.preview(thumb?Number(thumb.dataset.time):this.at(e));});
      film.addEventListener('pointermove',e=>{if(this.drag?.part==='scrub'){if(Math.abs(e.clientX-this.drag.x)>3)this.preview(this.at(e));}});
      film.addEventListener('pointerup',()=>{this.drag=null;});film.addEventListener('pointercancel',()=>{this.drag=null;});
      film.addEventListener('pointerleave',()=>{this.el('timelineHover').hidden=true;});
      for(const [id,part] of [['timelineRangeStart','start'],['timelineRangeEnd','end'],['timelineRangeMove','move'],['timelineMarkerA','A'],['timelineMarkerB','B']]){
        const node=this.el(id);
        node.addEventListener('pointerdown',e=>{if(!this.duration)return;e.preventDefault();node.setPointerCapture(e.pointerId);this.drag={part,x:e.clientX,range:{...this.range},marker:this.markers[part]?.time||0,moved:false};});
        node.addEventListener('pointermove',e=>this.pointerMove(e));
        node.addEventListener('pointerup',()=>this.pointerEnd());node.addEventListener('pointercancel',()=>this.pointerCancel());
        node.addEventListener('keydown',e=>this.key(e,part));
      }
      for(const label of ['A','B'])this.el('timelinePick'+label).onclick=()=>this.pick(label,this.time);
    }
    reset(){this.request++;this.duration=0;this.drag=null;this.el('timelinePanel').hidden=true;this.el('timelineFilm').replaceChildren();this.el('timelineHover').hidden=true;}
    async load(project,range,markers,fileUrl){
      const id=++this.request;const video=project.assets.video;
      if(!video){this.reset();return;}
      this.duration=video.info.duration;this.fps=video.info.fps||24;this.range=normalizeRange(this.duration,range.start,range.end);this.markers=structuredClone(markers);this.time=0;this.fileUrl=fileUrl;
      this.labels=project.character_count===1?['A']:['A','B'];
      this.el('timelinePanel').hidden=false;this.el('timelineStatus').textContent='Loading local thumbnails…';this.el('timelineFilm').replaceChildren();this.render();
      const ruler=this.el('timelineRuler');ruler.replaceChildren();
      for(let i=0;i<=5;i++){const tick=document.createElement('span');tick.textContent=(this.duration*i/5).toFixed(1)+'s';ruler.append(tick);}
      try{
        const data=await this.options.load(project.id,video.file);
        if(id!==this.request)return;
        for(const item of data.thumbnails){
          const button=document.createElement('button'),image=document.createElement('img');button.className='timeline-thumb';button.dataset.time=item.time;button.setAttribute('aria-label','Preview '+item.time.toFixed(3)+' seconds');button.title=item.time.toFixed(3)+'s';image.src=fileUrl(item.file);image.alt='';image.draggable=false;button.append(image);
          button.onclick=e=>{if(e.detail===0)this.preview(item.time);};
          button.onpointerenter=()=>this.hover(item,button);button.onfocus=()=>this.hover(item,button);button.onblur=()=>{this.el('timelineHover').hidden=true;};
          this.el('timelineFilm').append(button);
        }
        this.el('timelineStatus').textContent=data.thumbnails.length+' sampled frames · '+this.duration.toFixed(3)+'s';
      }catch(e){if(id!==this.request)return;this.el('timelineStatus').textContent='Could not load thumbnails. Select the project again to retry.';this.options.error(e);}
    }
    hover(item,node){
      if(this.drag)return;
      const box=this.el('timelineHover'),panel=this.el('timelinePanel'),r=node.getBoundingClientRect(),p=panel.getBoundingClientRect();
      box.querySelector('img').src=this.fileUrl(item.file);box.querySelector('span').textContent=item.time.toFixed(3)+'s · Click to preview';box.hidden=false;
      box.style.left=clamp(r.left-p.left+r.width/2-115,0,Math.max(0,p.width-box.offsetWidth))+'px';
    }
    at(e){const r=this.el('timelineSurface').getBoundingClientRect();return clamp((e.clientX-r.left)/r.width,0,1)*this.duration;}
    preview(time){this.time=frameTime(time,this.duration,this.fps);this.renderPlayhead();this.options.preview(this.time);}
    follow(time){if(!this.duration||this.drag)return;this.time=frameTime(time,this.duration,this.fps);this.renderPlayhead();}
    setRange(start,end,part){const r=part?inputRange(this.duration,start,end,part):normalizeRange(this.duration,start,end);this.range=r;this.render();return r;}
    update(markers,active,count=2){this.markers=structuredClone(markers);this.active=active;this.labels=count===1?['A']:['A','B'];this.renderMarkers();}
    pick(label,time){
      if(!this.duration||!this.labels.includes(label))return;
      if(time<this.range.start-.001||time>=this.range.end){this.options.error(new Error('Choose a character keyframe inside the green replacement range.'));return;}
      const t=rangeFrameTime(time,this.range,this.duration,this.fps);this.options.pick(label,t);
    }
    pointerMove(e){
      const d=this.drag;if(!d||d.part==='scrub')return;
      const width=this.el('timelineSurface').getBoundingClientRect().width,delta=(e.clientX-d.x)/width*this.duration;d.moved=d.moved||Math.abs(e.clientX-d.x)>2;
      if(['A','B'].includes(d.part)){
        const t=rangeFrameTime(d.marker+delta,this.range,this.duration,this.fps);
        this.markers[d.part]={...this.markers[d.part],time:t};this.renderMarkers();this.preview(t);
      }else{
        const value=(d.part==='end'?d.range.end:d.range.start)+delta;
        this.range=moveRange(this.duration,d.range,d.part,value);this.render();this.options.range(this.range,false);
      }
    }
    pointerEnd(){
      const d=this.drag;if(!d)return;this.drag=null;
      if(['A','B'].includes(d.part)){if(d.moved)this.pick(d.part,this.markers[d.part].time);else this.options.activate(d.part);}
      else if(d.part!=='scrub')this.options.range(this.range,true);
    }
    pointerCancel(){const d=this.drag;this.drag=null;if(!d)return;if(['A','B'].includes(d.part)){this.markers[d.part]={...this.markers[d.part],time:d.marker};this.renderMarkers();}else if(d.part!=='scrub'){this.range=d.range;this.render();this.options.range(this.range,true);}}
    key(e,part){
      if(!this.duration||!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;
      e.preventDefault();const step=e.shiftKey?1:1/this.fps,delta=e.key==='ArrowLeft'?-step:step;
      if(['A','B'].includes(part)){
        let t=(this.markers[part]?.time||0)+delta;if(e.key==='Home')t=this.range.start;if(e.key==='End')t=this.range.end-1/this.fps;
        this.pick(part,rangeFrameTime(t,this.range,this.duration,this.fps));
      }else{
        let value=(part==='end'?this.range.end:this.range.start)+delta;
        if(e.key==='Home')value=0;if(e.key==='End')value=this.duration;
        this.range=moveRange(this.duration,this.range,part,value);this.render();this.options.range(this.range,true);
      }
    }
    renderPlayhead(){this.el('timelinePlayhead').style.left=(this.duration?this.time/this.duration*100:0)+'%';this.el('timelineCurrentTime').textContent=this.time.toFixed(3)+'s';}
    renderMarkers(){
      const invalid=[];
      for(const label of this.labels){
        const m=this.markers[label]||{time:0,crop:null},node=this.el('timelineMarker'+label),percent=this.duration?m.time/this.duration*100:0;
        node.style.left=percent+'%';node.classList.toggle('edge-start',percent<8);node.classList.toggle('edge-end',percent>90);node.classList.toggle('unset',!m.crop);node.classList.toggle('active',label===this.active);
        const outside=[m,...(m.samples||[])].some(p=>p.time<this.range.start-.001||p.time>=this.range.end);node.classList.toggle('outside',outside);if(outside)invalid.push(label);
        node.querySelector('small').textContent=m.time.toFixed(2)+'s';node.setAttribute('aria-valuenow',m.time);node.setAttribute('aria-valuemax',this.duration);node.setAttribute('aria-valuetext',m.time.toFixed(3)+' seconds'+(m.crop?', box selected':', box needed'));node.title='Character '+label+' · '+m.time.toFixed(3)+'s · Drag to change frame';
      }
      this.renderSampleMarkers();
      this.el('timelineWarning').hidden=!invalid.length;this.el('timelineWarning').textContent='Character '+invalid.join(', ')+' keyframes are outside this range. Drag their markers to choose new frames.';
    }
    renderSampleMarkers(){
      for(const node of this.sampleMarks||[])node.remove();this.sampleMarks=[];
      for(const label of this.labels){
        const selected=this.markers[label],samples=selected?.samples||[];
        if(samples.length<2)continue;
        const parent=this.el('timelineMarker'+label).parentElement;
        samples.forEach((sample,index)=>{
          if(index===(selected.sample_index||0))return;
          const node=document.createElement('button');node.className='sample-dot '+label.toLowerCase();node.textContent=label+(index+1);
          node.style.left=Math.max(1,Math.min(97,sample.time/this.duration*100))+'%';
          node.title=`${label}${index+1} · ${sample.time.toFixed(3)}s`;node.setAttribute('aria-label',node.title);
          node.onclick=()=>this.options.selectSample?.(label,index);parent.append(node);this.sampleMarks.push(node);
        });
      }
    }
    render(){
      if(!this.duration)return;
      const a=this.range.start/this.duration*100,b=this.range.end/this.duration*100;
      this.el('timelineRange').style.left=a+'%';this.el('timelineRange').style.width=(b-a)+'%';this.el('timelineLeftShade').style.width=a+'%';this.el('timelineRightShade').style.width=(100-b)+'%';
      const l=limits(this.duration);
      for(const [id,value,min,max]of[['timelineRangeStart',this.range.start,Math.max(0,this.range.end-l.max),this.range.end-l.min],['timelineRangeEnd',this.range.end,this.range.start+l.min,Math.min(this.duration,this.range.start+l.max)],['timelineRangeMove',this.range.start,0,this.duration-(this.range.end-this.range.start)]]){const n=this.el(id);n.setAttribute('aria-valuenow',value);n.setAttribute('aria-valuemin',round(min));n.setAttribute('aria-valuemax',round(max));n.setAttribute('aria-valuetext',value.toFixed(3)+' seconds');}
      this.el('timelineRangeMove').textContent=(this.range.end-this.range.start).toFixed(2)+'s · Drag to move';this.renderPlayhead();this.renderMarkers();
    }
  }
  root.VideoTimeline=VideoTimeline;root.TimelineMath=model;
})(typeof window!=='undefined'?window:globalThis);

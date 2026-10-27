(function(root){
  'use strict';
  const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
  function bounds(start,end,frames){
    const a=clamp(start,0,frames),b=clamp(end,a,frames);
    return {left:a/frames*100,width:(b-a)/frames*100};
  }
  function playTime(value,frames,fps=24){return clamp(Math.round(value*fps),0,Math.max(0,frames-1))/fps;}
  if(typeof module!=='undefined'&&module.exports)module.exports={bounds,playTime};
  if(typeof document==='undefined')return;
  class TrackingTimeline{
    constructor(host,options){this.host=host;this.options=options;this.key=null;this.pending=null;this.request=0;this.video=null;}
    clear(){this.request++;this.key=null;this.host.hidden=true;this.video=null;this.pending=null;this.cursor=null;this.data=null;}
    async load(project,record,video,fileUrl){
      const key=project.id+':'+record.id;this.video=video;
      if(this.key===key){this.host.hidden=false;return;}
      this.key=key;this.pending=null;const request=++this.request;this.host.hidden=false;this.host.textContent='Loading the tracking review timeline…';
      try{
        const data=await this.options.load(project.id,record.id);
        if(request!==this.request)return;
        this.data=data;this.record=record;this.fileUrl=fileUrl;this.render();
      }catch(e){if(request!==this.request)return;this.key=null;this.host.textContent='Could not load the timeline: '+e.message;}
    }
    seek(seconds,issue){
      if(!this.data||!this.video)return;
      const time=playTime(seconds,Math.round(this.data.duration*this.data.fps),this.data.fps);
      this.video.pause();this.pending=time;
      if(this.video.readyState>=1){this.video.currentTime=time;this.pending=null;}
      this.follow(time);this.options.seek?.(time,this.video);if(issue)this.options.issue(issue);
    }
    follow(time){
      if(!this.cursor)return;
      time=playTime(time,Math.round(this.data.duration*this.data.fps),this.data.fps);
      this.cursor.style.left=clamp(time/this.data.duration*100,0,100)+'%';
      this.slider.value=time;this.current.textContent=`Clip ${time.toFixed(3)}s · Source ${this.sourceTime(time).toFixed(3)}s`;
      for(const button of this.host.querySelectorAll('[data-start]'))button.classList.toggle('at-time',time>=Number(button.dataset.start)&&time<Number(button.dataset.end));
    }
    sourceTime(time){return Math.min(this.data.source_end??Infinity,this.data.source_start+time);}
    render(){
      const host=this.host;host.replaceChildren();
      const header=document.createElement('div');header.className='review-timeline-header';
      const title=document.createElement('b');title.textContent='Tracking review timeline';this.current=document.createElement('span');header.append(title,this.current);host.append(header);
      const hint=document.createElement('p');hint.className='hint';hint.textContent='Click or drag thumbnails to preview; click a marker to jump. Yellow: review needed. Grey: black frames. Green: automatically recovered.';host.append(hint);
      const surface=document.createElement('div');surface.className='review-timeline-surface';host.append(surface);
      const film=document.createElement('div');film.className='review-film';film.setAttribute('aria-label','Tracking preview thumbnails');surface.append(film);
      for(const item of this.data.thumbnails){
        const button=document.createElement('button'),image=document.createElement('img');image.src=this.fileUrl(item.file);image.alt='';image.draggable=false;button.append(image);
        button.setAttribute('aria-label',`Tracking preview ${item.time.toFixed(3)} seconds`);button.title=`Clip ${item.time.toFixed(3)}s / Source ${this.sourceTime(item.time).toFixed(3)}s`;
        button.onclick=e=>{if(e.detail===0)this.seek(item.time);};film.append(button);
      }
      let dragging=false;
      const scrub=e=>{const r=film.getBoundingClientRect();this.seek(clamp((e.clientX-r.left)/r.width,0,1)*this.data.duration);};
      film.onpointerdown=e=>{if(e.button!==0)return;dragging=true;film.setPointerCapture(e.pointerId);scrub(e);};
      film.onpointermove=e=>{if(dragging)scrub(e);};film.onpointerup=()=>{dragging=false;};film.onpointercancel=()=>{dragging=false;};
      const frames=Math.round(this.data.duration*this.data.fps),fps=this.data.fps;
      const lane=(name)=>{const row=document.createElement('div');row.className='review-lane';row.setAttribute('aria-label',name);surface.append(row);return row;};
      const bar=(row,start,end,name,kind,issue)=>{
        const button=document.createElement('button'),pos=bounds(start,end,frames);button.className='review-range '+kind;button.textContent=name;button.title=`${name} · Clip ${(start/fps).toFixed(3)}–${(end/fps).toFixed(3)}s · Source ${this.sourceTime(start/fps).toFixed(3)}–${this.sourceTime(end/fps).toFixed(3)}s`;
        button.setAttribute('aria-label',button.title);button.dataset.start=start/fps;button.dataset.end=end/fps;button.style.left=pos.left+'%';button.style.width=pos.width+'%';button.onclick=()=>this.seek(start/fps,issue);row.append(button);
      };
      const quality=this.record.quality||{},shots=quality.shots||[];
      const shotLane=lane('Shot boundaries');
      shots.forEach((s,i)=>bar(shotLane,s.start,s.end,s.black?'Black frames':'Shot '+(i+1),s.black?'black':'shot'));
      const labels=Object.keys(this.record.manifest.presence||{});
      for(const label of labels){
        const row=lane('Character '+label+' tracking markers');
        for(const a of quality.auto_anchors||[]){if(a.label!==label)continue;const s=shots.find(s=>a.frame>=s.start&&a.frame<s.end);if(s)bar(row,a.restart,s.end,label+' recovered','recovered');}
        for(const issue of quality.issues||[]){if(issue.labels.includes(label))bar(row,issue.start,issue.end,label+' needs review','problem',issue);}
        if(!row.children.length){const text=document.createElement('span');text.className='review-lane-empty';text.textContent=label+': no pending markers';row.append(text);}
      }
      this.cursor=document.createElement('div');this.cursor.className='review-playhead';surface.append(this.cursor);
      const ruler=document.createElement('div');ruler.className='review-ruler';for(let i=0;i<=5;i++){const tick=document.createElement('span');tick.textContent=(this.data.duration*i/5).toFixed(2)+'s';ruler.append(tick);}host.append(ruler);
      this.slider=document.createElement('input');this.slider.type='range';this.slider.min=0;this.slider.max=(frames-1)/fps;this.slider.step=1/fps;this.slider.setAttribute('aria-label','Tracking preview position');this.slider.oninput=()=>this.seek(Number(this.slider.value));host.append(this.slider);
      const video=this.video;
      video.ontimeupdate=()=>{if(this.video===video)this.follow(video.currentTime);};
      video.onloadedmetadata=()=>{if(this.video===video&&this.pending!==null){video.currentTime=this.pending;this.pending=null;}};
      this.follow(video.currentTime||0);
    }
  }
  root.TrackingTimeline=TrackingTimeline;
})(typeof window!=='undefined'?window:globalThis);

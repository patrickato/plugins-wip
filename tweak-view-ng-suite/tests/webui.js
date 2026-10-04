const CSRF=document.querySelector('meta[name=csrf_token]').content;
let S=null,selected=null,drag=null;
let snap=true, overlayZones=true, warnOverlap=false;

const api=async(path,method='GET',body=null)=>{
  let o={method,headers:{'X-CSRFToken':CSRF}};
  if(body!==null){o.headers['Content-Type']='application/json';o.body=JSON.stringify(body)}
  let r=await fetch('/plugins/tweak_view_ng/'+path,o);
  let ct=r.headers.get('content-type')||'';
  if(!ct.includes('application/json')){
    let t=await r.text();
    if(r.status===400&&/csrf/i.test(t))throw Error('Session expired — reload the page (Ctrl-Shift-R) and try again.');
    if(r.status===401||r.status===403)throw Error('Not authorized — reload the page and sign in again.');
    throw Error('Server returned a non-JSON '+r.status+' response — try reloading the page.')
  }
  let j=await r.json();if(!r.ok)throw Error(j.error||r.statusText);return j
};

function msg(t,bad=false){let e=document.getElementById('status');e.textContent=t;e.className=bad?'err':'muted';if(!bad&&/✓/.test(t)){let sv=document.getElementById('saved');if(sv){sv.textContent='saved ✓';sv.className='ok'}}}

async function refresh(){
  try{
    S=await api('api/state');
    document.getElementById('screen').textContent=`${S.screen.width}×${S.screen.height} • Pwn ${S.pwnagotchi_version}`;
    renderList();renderEditor();renderProfiles();scale();
    msg(`undo ${S.history.undo} • redo ${S.history.redo}${S.pending_missing.length?' • pending '+S.pending_missing.join(', '):''}`)
  }catch(e){msg(e.message,true)}
}

// --- helpers for strip membership + "moved from default" ---
function lineY(name){let e=S&&S.elements[name];if(!e)return null;let xy=e.properties.xy;if(!xy)return null;if(!Array.isArray(xy))xy=String(xy).split(',').map(Number);return xy[1]}
function topLineY(){let y=lineY('line1');return y==null?Math.round(S.screen.height*0.12):y}
function botLineY(){let y=lineY('line2');return y==null?Math.round(S.screen.height*0.88):y}
function isEdited(n){return S&&S.configured&&S.configured.edits&&(n in S.configured.edits)}

function renderList(){
  if(!S)return;
  let q=document.getElementById('search').value.toLowerCase(),ul=document.getElementById('elements');
  ul.innerHTML='';
  Object.entries(S.elements).filter(([n])=>n.toLowerCase().includes(q)).forEach(([n,e])=>{
    let li=document.createElement('li');
    li.className='el'+(n===selected?' sel':'');
    li.innerHTML=`<b>${esc(n)}</b>${isEdited(n)?'<span class=dot title="moved from default">●</span>':''}<span class=type>${esc(e.type)}</span>`;
    li.setAttribute('data-help','Select this element, then drag it on the preview, nudge it, align it, or edit its properties on the right. A ● means it has been moved from its default.');
    li.onclick=()=>{selected=n;renderList();renderEditor();drawBoxes()};
    ul.appendChild(li)
  })
}

function esc(s){return String(s).replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}

function renderEditor(){
  let ed=document.getElementById('editor'),t=document.getElementById('title');
  ed.innerHTML='';
  if(!S||!selected||!S.elements[selected]){t.textContent='Select an element';return}
  let e=S.elements[selected];
  t.innerHTML=esc(selected)+' · '+esc(e.type)+(isEdited(selected)?' <span class=dot title="moved from default">●</span>':'');
  e.editable.forEach(k=>{
    let v=e.properties[k],row=document.createElement('div');row.className='row';
    let label=document.createElement('label');label.textContent=k;
    let input;
    if(['font','text_font','label_font','alt_font'].includes(k)){
      input=document.createElement('select');
      S.fonts.forEach(f=>{let o=document.createElement('option');o.value=f;o.textContent=f;if(f===v)o.selected=true;input.appendChild(o)})
    }else if(k==='wrap'){input=document.createElement('input');input.type='checkbox';input.checked=!!v}
    else{input=document.createElement('input');input.value=Array.isArray(v)?v.join(','):v??'';if(k==='xy')input.dataset.xy='1'}
    input.id='p_'+k;row.append(label,input);ed.appendChild(row)
  })
}

function readProps(){let e=S.elements[selected],p={};e.editable.forEach(k=>{let i=document.getElementById('p_'+k);if(!i)return;p[k]=k==='wrap'?i.checked:i.value});return p}

async function apply(){if(!selected)return;try{await api('api/update','POST',{element:selected,properties:readProps()});await refresh();reloadPreview();msg('applied ✓')}catch(e){msg(e.message,true)}}
async function revertEl(){if(!selected)return;try{await api('api/revert','POST',{element:selected});await refresh();reloadPreview();msg('reverted ✓')}catch(e){msg(e.message,true)}}
async function resetAll(){if(!confirm('Reset the active profile?'))return;try{await api('api/reset','POST',{});await refresh();reloadPreview()}catch(e){msg(e.message,true)}}
async function undo(){try{await api('api/undo','POST',{});await refresh();reloadPreview()}catch(e){msg(e.message,true)}}
async function redo(){try{await api('api/redo','POST',{});await refresh();reloadPreview()}catch(e){msg(e.message,true)}}
function addShape(type){let name=prompt('Shape name');if(!name)return;api('api/add_shape','POST',{name,type,properties:{xy:[5,5,40,25],color:255,width:1}}).then(()=>{selected=name;refresh();reloadPreview()}).catch(e=>msg(e.message,true))}

// --- fine 1px nudge pad (touch-friendly twin of the arrow keys) ---
function nudge(dx,dy){
  if(!selected||!S||!S.elements[selected]){msg('select an element first',true);return}
  let xy=S.elements[selected].properties.xy;
  if(xy==null){msg('this element has no position to move',true);return}
  if(!Array.isArray(xy))xy=String(xy).split(',');
  let a=xy.map(Number);
  a[0]+=dx;a[1]+=dy;if(a.length>=4){a[2]+=dx;a[3]+=dy}
  let i=document.getElementById('p_xy');if(i)i.value=a.join(',');
  apply()
}

// --- auto-align (server does the math) ---
async function alignEl(edge){if(!selected){msg('select an element first',true);return}try{await api('api/align','POST',{element:selected,edge});await refresh();reloadPreview();msg('aligned '+edge+' ✓')}catch(e){msg(e.message,true)}}
function otherNames(){return S?Object.keys(S.elements).filter(n=>n!==selected).sort():[]}
async function matchCoord(axis){
  if(!selected){msg('select an element first',true);return}
  let opts=otherNames();if(!opts.length){msg('no other elements',true);return}
  let target=prompt('Match '+axis.toUpperCase()+' of which element?\n\n'+opts.join(', '));
  if(!target)return; target=target.trim();
  if(!S.elements[target]){msg('no element named '+target,true);return}
  try{await api('api/match','POST',{element:selected,target,axis});await refresh();reloadPreview();msg('matched '+axis.toUpperCase()+' of '+target+' ✓')}catch(e){msg(e.message,true)}
}
async function stackElements(){
  let all=S?Object.keys(S.elements).sort():[];
  let pick=prompt('Stack which elements down a column?\nComma-separated names (top-to-bottom order is auto):\n\n'+all.join(', '),selected||'');
  if(!pick)return;
  let names=pick.split(',').map(x=>x.trim()).filter(Boolean);
  if(names.length<2){msg('name at least 2 elements',true);return}
  let bad=names.filter(n=>!S.elements[n]);if(bad.length){msg('unknown: '+bad.join(', '),true);return}
  try{await api('api/stack','POST',{elements:names});await refresh();reloadPreview();msg('stacked '+names.length+' ✓')}catch(e){msg(e.message,true)}
}
async function renameProfile(){
  let cur=S&&S.active_profile;if(!cur)return;
  if(cur==='default'){msg("can't rename the default profile",true);return}
  let nn=prompt('Rename profile "'+cur+'" to:');if(!nn)return;
  try{await api('api/profile','POST',{op:'rename',name:cur,new:nn.trim()});selected=null;await refresh();reloadPreview();msg('renamed ✓')}catch(e){msg(e.message,true)}
}
async function deleteProfile(){
  let cur=S&&S.active_profile;if(!cur)return;
  if(cur==='default'){msg("can't delete the default profile",true);return}
  if(!confirm('Delete profile "'+cur+'"? This cannot be undone from here.'))return;
  try{await api('api/profile','POST',{op:'delete',name:cur});selected=null;await refresh();reloadPreview();msg('deleted ✓')}catch(e){msg(e.message,true)}
}
// --- help mode: the ? button turns on hover tooltips over every control ---
let helpMode=false;
function toggleHelp(){
  helpMode=!helpMode;
  document.getElementById('helpBtn').classList.toggle('on',helpMode);
  document.body.classList.toggle('help-on',helpMode);
  document.getElementById('help').style.display=helpMode?'block':'none';
  if(!helpMode)hideHelpBubble()
}
function showHelpBubble(el){
  let bub=document.getElementById('helpbubble');
  bub.textContent=el.getAttribute('data-help');
  bub.style.display='block';
  let r=el.getBoundingClientRect();
  let left=Math.max(8,Math.min(r.left,window.innerWidth-bub.offsetWidth-10));
  let top=r.bottom+6;
  if(top+bub.offsetHeight>window.innerHeight-6)top=Math.max(6,r.top-bub.offsetHeight-6);
  bub.style.left=left+'px';bub.style.top=top+'px'
}
function hideHelpBubble(){let b=document.getElementById('helpbubble');if(b)b.style.display='none'}
document.addEventListener('mouseover',ev=>{
  if(!helpMode)return;
  let el=ev.target.closest('[data-help]');
  if(el)showHelpBubble(el);else hideHelpBubble()
});
function markSaved(){let e=document.getElementById('saved');if(e){e.textContent='saved ✓';e.className='ok'}}

function renderProfiles(){
  let s=document.getElementById('profile');s.innerHTML='';
  S.profiles.forEach(n=>{let o=document.createElement('option');o.value=n;o.textContent=n;o.selected=n===S.active_profile;s.appendChild(o)});
  s.onchange=()=>api('api/profile','POST',{name:s.value}).then(()=>{selected=null;refresh();reloadPreview()}).catch(e=>msg(e.message,true))
}
function newProfile(){let n=prompt('New profile name');if(!n)return;api('api/profile','POST',{name:n}).then(()=>{selected=null;refresh();reloadPreview()}).catch(e=>msg(e.message,true))}
async function exportCfg(){let d=await api('api/export');let a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(d,null,2)],{type:'application/json'}));a.download='tweak_view_ng.json';a.click();URL.revokeObjectURL(a.href)}
function importCfg(inp){let f=inp.files[0];if(!f)return;let r=new FileReader();r.onload=()=>{try{let d=JSON.parse(r.result);api('api/import','POST',d).then(()=>{selected=null;refresh();reloadPreview()}).catch(e=>msg(e.message,true))}catch(e){msg('invalid JSON',true)}};r.readAsText(f)}
function reloadPreview(){let im=document.getElementById('preview');im.src='/ui?t='+Date.now()}

function toggleSnap(){snap=!snap;document.getElementById('snapBtn').classList.toggle('on',snap);msg('snap '+(snap?'on':'off'))}
function toggleZones(){overlayZones=!overlayZones;document.getElementById('zoneBtn').classList.toggle('on',overlayZones);drawBoxes()}

function scale(){
  if(!S)return;
  let im=document.getElementById('preview'),frame=document.getElementById('frame'),
    maxW=Math.max(250,document.querySelector('.stage').clientWidth-40),
    maxH=Math.max(150,document.querySelector('.stage').clientHeight-40),
    sc=Math.min(maxW/S.screen.width,maxH/S.screen.height,4);
  if(window.innerWidth<850)sc=Math.min((window.innerWidth-38)/S.screen.width,3);
  sc=Math.max(.5,sc);
  frame.style.width=(S.screen.width*sc)+'px';frame.style.height=(S.screen.height*sc)+'px';
  im.style.width='100%';im.style.height='100%';
  drawBoxes()
}

function boxRect(xy){let x=xy[0]||0,y=xy[1]||0,w=xy.length>=4?Math.max(4,(xy[2]-x)):18,h=xy.length>=4?Math.max(4,(xy[3]-y)):12;return {x,y,w,h}}

function drawBoxes(){
  if(drag)return;            // never rebuild the overlay mid-drag (would kill the live box)
  let ov=document.getElementById('overlay');ov.innerHTML='';
  if(!S)return;
  // safe-zone shading for top/bottom strips
  if(overlayZones){
    let ty=topLineY(),by=botLineY();
    let zt=document.createElement('div');zt.className='zone';zt.style.left='0';zt.style.top='0';zt.style.width='100%';zt.style.height=(ty/S.screen.height*100)+'%';ov.appendChild(zt);
    let zb=document.createElement('div');zb.className='zone';zb.style.left='0';zb.style.top=(by/S.screen.height*100)+'%';zb.style.width='100%';zb.style.height=((S.screen.height-by)/S.screen.height*100)+'%';ov.appendChild(zb)
  }
  Object.entries(S.elements).forEach(([n,e])=>{
    let xy=e.properties.xy;if(!xy)return;
    if(!Array.isArray(xy))xy=String(xy).split(',').map(Number);
    let r=boxRect(xy);
    let b=document.createElement('div');
    b.className='box'+(n===selected?' sel':'')+(isEdited(n)?' edited':'');
    b.style.left=(r.x/S.screen.width*100)+'%';b.style.top=(r.y/S.screen.height*100)+'%';
    b.style.width=(r.w/S.screen.width*100)+'%';b.style.height=(r.h/S.screen.height*100)+'%';
    b.title=n;
    let lbl=document.createElement('span');lbl.className='blabel';lbl.textContent=n;b.appendChild(lbl);
    b.onpointerdown=ev=>startDrag(ev,n,xy);
    b.onclick=()=>{selected=n;renderList();renderEditor();drawBoxes()};
    b.dataset.name=n;
    ov.appendChild(b)
  })
}

function applySnap(a){
  if(!snap)return a;
  let th=3;
  let edges=[0,S.screen.width-1];let ey=[0,S.screen.height-1,topLineY(),botLineY()];
  // snap x to screen edges
  edges.forEach(E=>{if(Math.abs(a[0]-E)<=th)a[0]=E});
  // snap y to edges + divider lines
  ey.forEach(E=>{if(Math.abs(a[1]-E)<=th)a[1]=E});
  return a
}

function crosses(a){
  // warn only on a real problem: pushed off a screen edge, or a box clearly
  // straddling a divider line (center on the far side), not merely touching a
  // line it legitimately sits against.
  let r=boxRect(a),ty=topLineY(),by=botLineY(),W=S.screen.width,H=S.screen.height;
  if(r.x<0||r.y<0||r.x+r.w>W||r.y+r.h>H)return true;
  let h=(a.length>=4?r.h:10),cy=r.y+h/2,tol=2;
  // straddling line1: top above it AND bottom well below it
  if(r.y<ty-tol && (r.y+h)>ty+tol)return true;
  if(r.y<by-tol && (r.y+h)>by+tol)return true;
  if(warnOverlap){
    for(let [n,e] of Object.entries(S.elements)){
      if(n===drag.n)continue;let oxy=e.properties.xy;if(!oxy)continue;
      if(!Array.isArray(oxy))oxy=String(oxy).split(',').map(Number);
      let o=boxRect(oxy);
      if(r.x< o.x+o.w && r.x+r.w> o.x && r.y< o.y+o.h && r.y+r.h> o.y)return true
    }
  }
  return false
}

function liveBox(a){
  if(!drag)return;
  let b=[...document.querySelectorAll('.box')].find(x=>x.dataset.name===drag.n);
  if(!b)return;
  let r=boxRect(a);
  b.style.left=(r.x/S.screen.width*100)+'%';b.style.top=(r.y/S.screen.height*100)+'%';
  b.classList.toggle('warn',crosses(a))
}

function startDrag(ev,n,xy){
  ev.preventDefault();
  selected=n;renderList();renderEditor();
  // mark the current box selected WITHOUT rebuilding the overlay (rebuilding
  // would detach the element being dragged and kill the drag).
  document.querySelectorAll('.box').forEach(b=>b.classList.toggle('sel',b.dataset.name===n));
  let frame=document.getElementById('frame').getBoundingClientRect();
  drag={id:ev.pointerId,n,xy:[...xy],sx:ev.clientX,sy:ev.clientY,fw:frame.width,fh:frame.height};
  let el=ev.currentTarget||ev.target;
  try{el.setPointerCapture(ev.pointerId)}catch(e){}
  el.onpointermove=moveDrag;el.onpointerup=endDrag;el.onpointercancel=endDrag
}

function moveDrag(ev){
  if(!drag)return;
  let dx=Math.round((ev.clientX-drag.sx)*S.screen.width/drag.fw),
      dy=Math.round((ev.clientY-drag.sy)*S.screen.height/drag.fh),
      a=[...drag.xy];
  a[0]+=dx;a[1]+=dy;if(a.length>=4){a[2]+=dx;a[3]+=dy}
  a=applySnap(a);
  drag.cur=a;
  let i=document.getElementById('p_xy');if(i)i.value=a.join(',');
  liveBox(a)   // Feature 1: box follows cursor live
}

function endDrag(ev){if(!drag)return;drag=null;document.querySelectorAll('.box.warn').forEach(b=>b.classList.remove('warn'));apply()}

// --- Feature: arrow-key nudge ---
window.addEventListener('keydown',ev=>{
  if(!selected||!S||!S.elements[selected])return;
  if(['INPUT','SELECT','TEXTAREA'].includes((ev.target.tagName||'')))return;
  let d={ArrowLeft:[-1,0],ArrowRight:[1,0],ArrowUp:[0,-1],ArrowDown:[0,1]}[ev.key];
  if(!d)return;
  ev.preventDefault();
  let step=ev.shiftKey?10:1;
  let i=document.getElementById('p_xy');if(!i)return;
  let a=String(i.value).split(',').map(Number);
  a[0]+=d[0]*step;a[1]+=d[1]*step;if(a.length>=4){a[2]+=d[0]*step;a[3]+=d[1]*step}
  i.value=a.join(',');
  apply()
});

window.addEventListener('resize',scale);
document.getElementById('preview').onload=()=>{if(drag)return;scale();drawBoxes()};
refresh();
setInterval(()=>{if(!drag)reloadPreview()},7000);

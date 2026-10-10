const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const stateLabels = {watched:'Vu',unwatched:'Non vu',in_progress:'En cours',unknown:'État non renseigné'};
const kindLabels = {movie:'Film',tvshow:'Série',episode:'Épisode'};
const baseURL = new URL('.', window.location.href);
const url = (path) => new URL(path.replace(/^\/+/, ''), baseURL).href;
const state = {csrf:null,role:'read',view:localStorage.getItem('kodi_library_view') || 'grid',order:'desc',offset:0,limit:60,recent:false,facets:null,players:[],seq:0,controller:null,detail:null};
let toastTimer;
function toast(message) { $('toast').textContent=message; $('toast').hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('toast').hidden=true,5500); }
function error(message) { $('error-banner').textContent=message; $('error-banner').hidden=false; }
async function api(path, options={}) {
  const headers=new Headers(options.headers || {});
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type','application/json');
  if (state.csrf && options.method && options.method !== 'GET') headers.set('X-CSRF-Token',state.csrf);
  const response=await fetch(url(`api/v1/${path}`),{...options,headers,credentials:'same-origin'});
  if (response.status===204) return null;
  const value=await response.json();
  if (!response.ok) {
    if (response.status===401 && path!=='auth/session') showLogin();
    throw new Error(value.error?.message || `Erreur ${response.status}`);
  }
  return value;
}
function showLogin() { $('app').hidden=true; $('login').hidden=false; }
function date(value) { if (!value) return '—'; const d=new Date(value); return Number.isNaN(d.getTime()) ? '—' : d.toLocaleDateString('fr-FR'); }
function badges(item) { return item.source_types.map(s=>`<span class="source-badge ${s==='clz'?'physical':''}">${s==='clz'?'COLLECTION':'KODI'}</span>`).join(''); }
function poster(item, detail=false) {
  const progress=item.total_seconds>0 ? Math.min(100,100*item.resume_seconds/item.total_seconds):0;
  return `<div class="poster"><span class="poster-placeholder" aria-hidden="true">${esc(item.title.slice(0,2).toUpperCase())}</span>${item.artwork.poster?`<img src="${esc(url(item.artwork.poster))}" alt="" ${detail?'':'loading="lazy"'}>`:''}${!detail&&item.rating?`<span class="rating">★ ${Number(item.rating).toFixed(1)}</span>`:''}${!detail&&item.watch_status==='watched'?'<span class="state" title="Vu">✓</span>':''}${!detail&&item.watch_status==='in_progress'?'<span class="state progress" title="En cours">◐</span>':''}${progress>0&&item.watch_status==='in_progress'?`<span class="progress-track"><span style="width:${progress}%"></span></span>`:''}</div>`;
}
function card(item) {
  const meta=[item.year,kindLabels[item.kind],...item.formats].filter(Boolean);
  return `<button class="media-card" data-media="${esc(item.id)}" aria-label="${esc(item.title)}, ${esc(stateLabels[item.watch_status])}">${poster(item)}<span><span class="card-title">${esc(item.title)}</span><span class="card-meta">${meta.map(v=>`<span>${esc(v)}</span>`).join('<span aria-hidden="true">·</span>')}</span><span class="card-meta">${badges(item)}</span></span>${state.view==='list'?`<span class="list-status">${esc(stateLabels[item.watch_status])}<br>${item.rating?`★ ${Number(item.rating).toFixed(1)} · `:''}${date(item.date_added)}</span>`:''}</button>`;
}
function attachImageFallbacks(root) { root.querySelectorAll('img').forEach(img=>{img.addEventListener('error',()=>img.classList.add('failed'),{once:true});}); }
function currentParams() {
  const params=new URLSearchParams({limit:state.limit,offset:state.offset,sort:$('sort').value,order:state.order});
  for (const [id,name] of [['search','q'],['kind','kind'],['source-type','source_type'],['genre','genre'],['format','format'],['collection','collection'],['watch-status','watch_status']]) if ($(id).value) params.set(name,$(id).value);
  if (state.recent) params.set('recent_days','30');
  return params;
}
function updateFilterState() {
  let count=['search','kind','source-type','genre','format','collection','watch-status'].filter(id=>$(id).value).length + Number(state.recent);
  $('filter-count').textContent=count || '';
  $('reset-filters').hidden=!count;
  document.querySelectorAll('[data-quick]').forEach(b=>b.classList.toggle('active', b.dataset.quick==='recent'?state.recent:b.dataset.quick==='all'?!state.recent&&!$('watch-status').value:!state.recent&&b.dataset.quick===$('watch-status').value));
}
async function loadMedia() {
  const seq=++state.seq;
  state.controller?.abort(); state.controller=new AbortController();
  $('results').setAttribute('aria-busy','true'); $('error-banner').hidden=true;
  updateFilterState();
  try {
    const page=await api(`media?${currentParams()}`,{signal:state.controller.signal});
    if (seq!==state.seq) return;
    $('result-count').textContent=`${page.total.toLocaleString('fr-FR')} résultat${page.total>1?'s':''}${state.recent?' · ajoutés depuis 30 jours':''}`;
    $('results').className=state.view==='grid'?'poster-grid':'list-results';
    if (!page.items.length) {
      const empty=!state.facets?.sources.length || state.facets.sources.every(s=>!s.item_count);
      $('results').innerHTML=`<div class="empty-state"><span class="empty-icon" aria-hidden="true">▦</span><h2>${empty?'Votre bibliothèque commence ici.':'Aucun titre ne correspond.'}</h2><p>${empty?'Connectez la base Kodi ou importez un export CLZ Movies.':'Modifiez les filtres ou essayez un autre mot.'}</p>${state.role==='admin'&&empty?'<button class="primary" data-action="connections">Ajouter une source →</button>':'<button class="secondary" data-action="reset">Effacer les filtres</button>'}</div>`;
    } else $('results').innerHTML=page.items.map(card).join('');
    attachImageFallbacks($('results'));
    $('pagination').hidden=page.total<=state.limit;
    $('previous-page').disabled=state.offset===0; $('next-page').disabled=!page.has_more;
    $('page-info').textContent=`${state.offset+1}–${state.offset+page.items.length} / ${page.total}`;
  } catch (e) { if (e.name!=='AbortError'&&seq===state.seq) error(e.message); }
  finally { if (seq===state.seq) $('results').setAttribute('aria-busy','false'); }
}
function fillSelect(id, values, label) {
  const selected=$(id).value;
  $(id).innerHTML=`<option value="">${label}</option>`+values.map(v=>`<option value="${esc(v)}">${esc(v)}</option>`).join('');
  if (values.includes(selected)) $(id).value=selected;
}
async function refresh() {
  const [facets,status,players]=await Promise.all([api('facets'),api('status'),api('players')]);
  state.facets=facets; state.players=players;
  fillSelect('genre',facets.genres,'Tous les genres'); fillSelect('format',facets.formats,'Tous les formats'); fillSelect('collection',facets.collections,'Toutes les collections');
  $('catalogue-status').textContent=`${status.works.toLocaleString('fr-FR')} titres · ${status.editions.toLocaleString('fr-FR')} fichiers et éditions · ${status.sources.length} source${status.sources.length>1?'s':''}`;
  const latest=status.sources.map(s=>s.last_sync).filter(Boolean).sort().at(-1);
  $('sync-status').textContent=latest?`Dernière mise à jour : ${date(latest)}`:'Aucune source importée';
  await loadMedia();
}
async function initialize() {
  try {
    const session=await api('auth/session'); state.csrf=session.csrf_token; state.role=session.role;
    $('login').hidden=true; $('app').hidden=false; $('connections-button').hidden=state.role!=='admin'; $('logout-button').hidden=!state.csrf;
    setView(state.view,false); await refresh();
  } catch (e) { if ($('app').hidden) showLogin(); else error(e.message); }
}
function reset() { ['search','kind','source-type','genre','format','collection','watch-status'].forEach(id=>$(id).value=''); state.recent=false; state.offset=0; loadMedia(); }
function setView(view, reload=true) { state.view=view; localStorage.setItem('kodi_library_view',view); $('grid-view').setAttribute('aria-pressed',view==='grid'); $('list-view').setAttribute('aria-pressed',view==='list'); if (reload) loadMedia(); }
function showDialog(id) { if (!$(id).open) $(id).showModal(); }
function editionMarkup(e) {
  const m=e.metadata;
  const fields=[['Format',m.formats?.join(', ')],['Édition',m.edition||m.version],['Code-barres',m.barcode],['Disques',m.disc_count],['Quantité',m.quantity],['Coffret',m.boxset],['Collection',m.collections?.join(', ')],['Régions',m.regions?.join(', ')],['Audio',m.audio_tracks?.join(', ')],['Sous-titres',m.subtitles?.join(', ')],['Dernier visionnage',e.last_played?date(e.last_played):null],['Historique CLZ',m.seen_dates?.map(date).join(', ')],['Lectures',e.play_count],['Reprise',e.resume_seconds?`${Math.floor(e.resume_seconds/60)} min`:null],['Fichier',m.file],['Ajout',e.date_added?date(e.date_added):null],['Identifiants',Object.entries(e.identifiers).map(([k,v])=>`${k}: ${v}`).join(' · ')]];
  const label=e.source_type==='clz'?(m.formats?.join(' / ')||'Édition physique'):(m.version||'Fichier Kodi');
  return `<details class="edition"><summary><b>${esc(label)}</b> <span class="muted">· ${esc(e.source_name)}</span></summary><dl class="edition-grid">${fields.filter(([,v])=>v!==null&&v!==undefined&&v!=='').map(([k,v])=>`<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl></details>`;
}
async function openDetail(id) {
  try {
    const item=await api(`media/${encodeURIComponent(id)}`); state.detail=item;
    const playablePlayers=state.players.filter(p=>item.editions.some(e=>e.source_type==='kodi'&&e.source_id===p.source_id));
    const playable=item.kind!=='tvshow'&&playablePlayers.length&&state.role==='admin';
    $('detail-content').innerHTML=`<div class="detail-layout"><div class="detail-poster">${poster(item,true)}</div><div class="detail-intro"><p class="eyebrow">${esc(kindLabels[item.kind])}</p><div class="detail-heading"><h2>${esc(item.title)}</h2>${item.original_title&&item.original_title!==item.title?`<p class="original-title">${esc(item.original_title)}</p>`:''}</div><div class="detail-info">${item.year?`<span>${item.year}</span>`:''}${item.runtime_minutes?`<span>${item.runtime_minutes} min</span>`:''}${item.rating?`<span class="score">★ ${Number(item.rating).toFixed(1)}</span>`:''}${item.user_rating?`<span>Votre note : ${item.user_rating}/10</span>`:''}</div><span class="status-chip ${esc(item.watch_status)}">${esc(stateLabels[item.watch_status])}</span><div class="card-meta">${badges(item)}</div></div><div class="detail-body"><div class="genre-chips">${item.genres.map(g=>`<button data-genre="${esc(g)}">${esc(g)}</button>`).join('')}</div><p class="synopsis">${esc(item.plot||'Synopsis non renseigné.')}</p>${item.date_added||item.last_played?`<p class="hint">${item.date_added?`Ajouté le ${date(item.date_added)}`:''}${item.last_played?` · Dernier visionnage le ${date(item.last_played)}`:''}</p>`:''}${playable?`<section class="detail-section"><p class="section-label">LECTURE SUR KODI</p><div class="play-row"><select id="play-target" aria-label="Instance Kodi">${playablePlayers.map(p=>`<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('')}</select><button id="play-button" class="primary">▶ Lire sur Kodi</button></div><label class="play-options"><input id="play-resume" type="checkbox" checked>Reprendre au point de lecture</label><p class="hint">L’instance Kodi sélectionnée doit être démarrée.</p></section>`:''}${item.kind==='tvshow'?'<section class="detail-section"><p class="section-label">ÉPISODES KODI</p><div id="episode-list" class="episode-links">Chargement…</div></section>':''}<section class="detail-section"><p class="section-label">FICHIERS & ÉDITIONS · ${item.editions.length}</p>${item.editions.map(editionMarkup).join('')}</section></div></div>`;
    attachImageFallbacks($('detail-content')); showDialog('detail-dialog');
    if (item.kind==='tvshow') {
      const episodes=await api(`media?kind=episode&parent_id=${encodeURIComponent(item.id)}&sort=title&order=asc&limit=200`);
      if (state.detail?.id===item.id) {
        const ordered=episodes.items.sort((a,b)=>(a.season-b.season)||(a.episode-b.episode));
        $('episode-list').innerHTML=ordered.length?ordered.map(e=>`<button data-media="${esc(e.id)}">S${String(e.season||0).padStart(2,'0')} E${String(e.episode||0).padStart(2,'0')} · ${esc(e.title)} <span class="muted">· ${esc(stateLabels[e.watch_status])}</span></button>`).join(''):'Aucun épisode Kodi importé pour cette série.';
        if (episodes.has_more) $('episode-list').insertAdjacentHTML('beforeend','<p class="hint">Les 200 premiers épisodes sont affichés. Retrouvez les autres dans le catalogue avec le filtre Épisodes.</p>');
      }
    }
  } catch(e) {toast(e.message);}
}
async function pollJob(job) {
  const target=$('job-status'); if (target) target.hidden=false;
  for(let i=0;i<600;i++) {
    const value=await api(`jobs/${encodeURIComponent(job.id)}`);
    if (target) target.textContent=value.status==='pending'?'Import en attente…':'Synchronisation en cours…';
    if(value.status==='failed') throw new Error(value.error||'Import échoué.');
    if(value.status==='completed') {
      toast(`${value.result.imported} entrées importées${value.result.matching_conflicts?` · ${value.result.matching_conflicts} rapprochements ambigus conservés séparément`:''}.`);
      await refresh(); if($('connections-dialog').open) await connections(); return;
    }
    await new Promise(resolve=>setTimeout(resolve,1000));
  }
  toast('L’import continue en arrière-plan. Consultez les connexions pour son résultat.');
}
async function connections() {
  try {
    const sources=await api('sources'); state.facets.sources=sources; state.players=await api('players');
    const clz=sources.filter(s=>s.type==='clz'); const kodi=sources.filter(s=>s.type==='kodi');
    $('connection-content').innerHTML=`<h3 class="connection-subheading">Sources du catalogue</h3>${sources.map(s=>`<div class="connection-card"><div><strong>${esc(s.name)}</strong><p>${s.type==='kodi'?'Kodi / MariaDB':'CLZ Movies'} · ${s.item_count} entrées${s.last_sync?` · ${date(s.last_sync)}`:''}${!s.enabled?' · Désactivée':''}</p>${s.last_error?`<p class="error-text">${esc(s.last_error)}</p>`:''}</div><div class="connection-actions">${s.type==='kodi'?`<button data-sync="${esc(s.id)}">Synchroniser</button><button data-edit-source="${esc(s.id)}">Modifier</button>`:''}<button data-remove-source="${esc(s.id)}">Retirer</button></div></div>`).join('')||'<p class="hint">Aucune source configurée.</p>'}<details><summary class="text-button">+ Connecter une base Kodi</summary><form id="kodi-form" class="connection-form"><input type="hidden" name="id"><label>Nom<input name="name" required placeholder="Kodi — NAS"></label><label>Hôte MariaDB<input name="host" required placeholder="192.168.1.10"></label><label>Port<input name="port" type="number" min="1" max="65535" value="3306" required></label><label>Base MyVideos<input name="database" placeholder="Automatique, ou MyVideos…"></label><label>Utilisateur<input name="username" required autocomplete="off" placeholder="kodi_library"></label><label>Mot de passe<input name="password" type="password" autocomplete="new-password"></label><label class="check-label wide"><input name="tls" type="checkbox">Connexion MariaDB avec TLS</label><p class="hint wide">Un compte disposant uniquement du droit SELECT suffit. La bibliothèque reste consultable quand Kodi est arrêté.</p><button class="primary wide" type="submit">Enregistrer et synchroniser</button></form></details><h3 class="connection-subheading">Importer CLZ Movies</h3><form id="clz-form" class="upload-row"><select name="source_id" aria-label="Collection CLZ">${clz.map(s=>`<option value="${esc(s.id)}">${esc(s.name)}</option>`).join('')}<option value="">Nouvelle collection…</option></select><input name="file" type="file" accept=".xml,application/xml,text/xml" required aria-label="Export XML CLZ Movies"><button class="secondary" type="submit">Importer l’export XML</button><p class="hint">Un nouvel import remplace les données de cette collection et conserve les données Kodi associées.</p></form><div id="job-status" class="job-status" role="status" hidden></div><h3 class="connection-subheading">Instances Kodi pour la lecture</h3>${state.players.map(p=>`<div class="connection-card"><div><strong>${esc(p.name)}</strong><p>${esc(p.host)}:${p.port}</p></div><div class="connection-actions"><button data-edit-player="${esc(p.id)}">Modifier</button><button data-remove-player="${esc(p.id)}">Retirer</button></div></div>`).join('')||'<p class="hint">Configurez-les lorsque vous souhaitez lancer un média à distance.</p>'}${kodi.length?`<details><summary class="text-button">+ Ajouter une instance Kodi</summary><form id="player-form" class="connection-form"><input type="hidden" name="id"><label>Nom<input name="name" required placeholder="Kodi — Salon"></label><label>Base partagée<select name="source_id">${kodi.map(s=>`<option value="${esc(s.id)}">${esc(s.name)}</option>`).join('')}</select></label><label>Hôte Kodi<input name="host" required placeholder="192.168.1.20"></label><label>Port HTTP<input name="port" type="number" value="8080" min="1" max="65535" required></label><label>Utilisateur HTTP<input name="username" autocomplete="off"></label><label>Mot de passe HTTP<input name="password" type="password" autocomplete="new-password"></label><label class="check-label wide"><input name="tls" type="checkbox">HTTPS sur Kodi</label><p class="hint wide">Cette instance doit utiliser la même base MyVideos que la source choisie. Activez le contrôle distant HTTP dans Kodi.</p><button type="submit" class="primary wide">Enregistrer l’instance</button></form></details>`:''}`;
    showDialog('connections-dialog');
  } catch(e) {toast(e.message);}
}
function fillForm(id,data) {
  const form=$(id); form.closest('details').open=true;
  for(const [key,value] of Object.entries(data)) {
    const el=form.elements.namedItem(key); if(!el) continue;
    if(el.type==='checkbox') el.checked=Boolean(value); else el.value=value??'';
  }
  form.scrollIntoView({block:'nearest',behavior:'smooth'});
}
document.addEventListener('submit',async event=>{
  const form=event.target;
  if(!['login-form','kodi-form','clz-form','player-form'].includes(form.id)) return;
  event.preventDefault(); const button=form.querySelector('button[type="submit"]'); button.disabled=true;
  try {
    const data=new FormData(form);
    if(form.id==='login-form') {
      await api('auth/session',{method:'POST',body:JSON.stringify({token:data.get('token')})}); form.reset(); $('login-error').textContent=''; await initialize();
    } else if(form.id==='kodi-form') {
      const body={name:data.get('name'),type:'kodi',config:{host:data.get('host'),port:Number(data.get('port')),username:data.get('username'),password:data.get('password')||null,database:data.get('database'),tls:data.has('tls')}};
      const id=data.get('id'); const source=await api(id?`sources/${id}`:'sources',{method:id?'PUT':'POST',body:JSON.stringify(body)});
      await pollJob(await api(`sources/${source.id}/sync`,{method:'POST'}));
    } else if(form.id==='clz-form') {
      let id=data.get('source_id');
      if(!id) { const name=prompt('Nom de cette collection','Collection Blu-ray'); if(!name) return; const source=await api('sources',{method:'POST',body:JSON.stringify({name,type:'clz'})}); id=source.id; }
      const body=new FormData(); body.set('source_id',id); body.set('file',data.get('file'));
      await pollJob(await api('imports/clz',{method:'POST',body}));
    } else {
      const id=data.get('id'); const body={name:data.get('name'),source_id:data.get('source_id'),host:data.get('host'),port:Number(data.get('port')),username:data.get('username'),password:data.get('password')||null,tls:data.has('tls')};
      await api(id?`players/${id}`:'players',{method:id?'PUT':'POST',body:JSON.stringify(body)}); toast('Instance enregistrée.'); await connections();
    }
  } catch(e) {if(form.id==='login-form') $('login-error').textContent=e.message; else toast(e.message);}
  finally {button.disabled=false;}
});
document.addEventListener('click',async event=>{
  const button=event.target.closest('button'); if(!button) return;
  if(button.dataset.close) $(button.dataset.close).close();
  if(button.dataset.media) openDetail(button.dataset.media);
  if(button.dataset.genre) {$('genre').value=button.dataset.genre; $('detail-dialog').close(); state.offset=0; loadMedia();}
  if(button.dataset.action==='connections') connections(); if(button.dataset.action==='reset') reset();
  if(button.dataset.quick) {state.recent=button.dataset.quick==='recent'; $('watch-status').value=['all','recent'].includes(button.dataset.quick)?'':button.dataset.quick; state.offset=0; if(state.recent){$('sort').value='date_added';state.order='desc';$('sort-order').textContent='↓ Décroissant';}loadMedia();}
  try {
    if(button.dataset.sync) {button.disabled=true; await pollJob(await api(`sources/${button.dataset.sync}/sync`,{method:'POST'}));}
    if(button.dataset.removeSource && confirm('Retirer cette source et ses données importées ?')) {await api(`sources/${button.dataset.removeSource}`,{method:'DELETE'});await refresh();await connections();}
    if(button.dataset.removePlayer && confirm('Retirer cette instance Kodi ?')) {await api(`players/${button.dataset.removePlayer}`,{method:'DELETE'});await connections();}
    if(button.dataset.editSource) {const s=state.facets.sources.find(s=>s.id===button.dataset.editSource); fillForm('kodi-form',{id:s.id,name:s.name,...s.config});}
    if(button.dataset.editPlayer) {const p=state.players.find(p=>p.id===button.dataset.editPlayer); fillForm('player-form',p);}
    if(button.id==='play-button') {button.disabled=true; await api(`players/${$('play-target').value}/play`,{method:'POST',body:JSON.stringify({media_id:state.detail.id,resume:$('play-resume').checked})});toast('Lecture envoyée à Kodi.');}
  }catch(e){toast(e.message);}finally{button.disabled=false;}
});
$('connections-button').addEventListener('click',connections);
$('logout-button').addEventListener('click',async()=>{try{await api('auth/session',{method:'DELETE'});showLogin();}catch(e){toast(e.message);}});
$('grid-view').addEventListener('click',()=>setView('grid')); $('list-view').addEventListener('click',()=>setView('list'));
$('toggle-filters').addEventListener('click',()=>{const hidden=!$('filters').hidden;$('filters').hidden=hidden;$('toggle-filters').setAttribute('aria-expanded',!hidden);});
$('reset-filters').addEventListener('click',reset);
let searchTimer; $('search').addEventListener('input',()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{state.offset=0;loadMedia();},250);});
['kind','source-type','genre','format','collection','watch-status','sort'].forEach(id=>$(id).addEventListener('change',()=>{state.offset=0;loadMedia();}));
$('sort-order').addEventListener('click',()=>{state.order=state.order==='asc'?'desc':'asc';$('sort-order').textContent=state.order==='asc'?'↑ Croissant':'↓ Décroissant';state.offset=0;loadMedia();});
$('previous-page').addEventListener('click',()=>{state.offset=Math.max(0,state.offset-state.limit);loadMedia();window.scrollTo({top:0,behavior:'smooth'});});
$('next-page').addEventListener('click',()=>{state.offset+=state.limit;loadMedia();window.scrollTo({top:0,behavior:'smooth'});});
document.addEventListener('keydown',event=>{if(event.key==='/'&&!['INPUT','SELECT','TEXTAREA'].includes(event.target.tagName)&&!$('app').hidden&&!document.querySelector('dialog[open]')){event.preventDefault();$('search').focus();}});
for(const dialog of document.querySelectorAll('dialog')) dialog.addEventListener('click',event=>{if(event.target===dialog){const r=dialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)dialog.close();}});
initialize();

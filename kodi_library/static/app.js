const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const stateLabels = {watched:'Vu',unwatched:'Non vu',in_progress:'En cours',unknown:'État non renseigné'};
const kindLabels = {movie:'Film',tvshow:'Série',episode:'Épisode'};
const baseURL = new URL('.', window.location.href);
const url = (path) => new URL(path.replace(/^\/+/, ''), baseURL).href;
const preferenceKey = `kodi_library_preferences_v1:${baseURL.pathname}`;
function readStorage(key) { try { return localStorage.getItem(key); } catch { return null; } }
function writeStorage(key,value) { try { localStorage.setItem(key,value); } catch { /* Browsing also works when storage is unavailable. */ } }
let preferences;
try { preferences=JSON.parse(readStorage(preferenceKey)) || {}; } catch { preferences={}; }
if(typeof preferences!=='object'||Array.isArray(preferences)) preferences={};
const filterIds = ['search','kind','source-type','genre','collection','watch-status','sort'];
let filtersRestored=false;
const savedView = ['grid','list'].includes(preferences.view)?preferences.view:readStorage('kodi_library_view');
const canonicalFormat = value => /^4k(?:\s+uhd)?$/i.test(String(value).trim())?'4K':String(value).trim();
function formatCategory(label) {
  if (/^(dolby atmos|dolby truehd|dolby digital|dts(?:[-:]|$)|aac|flac|pcm|lpcm|ac3|eac3|mp3|opus)/i.test(label)) return 'audio';
  if (/^(blu-ray|dvd|hd dvd|uhd blu-ray|ultra hd blu-ray)/i.test(label)) return 'disc';
  return 'video';
}
function formatSelects() { return {video:'video-format',audio:'audio-format',disc:'disc-format'}; }
function updateFormatSelections() {
  const selected=new Set(state.formats);
  const values=[...new Set((state.facets?.formats||[]).map(canonicalFormat))].sort((a,b)=>a.localeCompare(b,'fr',{numeric:true}));
  for(const [category,id] of Object.entries(formatSelects())) {
    const labels={video:'+ Format vidéo',audio:'+ Format audio',disc:'+ Support'};
    $(id).innerHTML='<option value="">'+labels[category]+'</option>'+values
      .filter(v=>formatCategory(v)===category&&!selected.has(v))
      .map(v=>`<option value="${esc(v)}">${esc(v)}</option>`).join('');
  }
  $('selected-formats').innerHTML=state.formats.map(v=>`<button type="button" class="selected-format" data-remove-format="${esc(v)}" aria-label="Retirer ${esc(v)}">${esc(v)} <span aria-hidden="true">×</span></button>`).join('');
  $('selected-formats').hidden=!state.formats.length;
}
const state = {csrf:null,role:'read',view:savedView==='list'?'list':'grid',order:preferences.order==='asc'?'asc':'desc',limit:60,recent:preferences.recent===true,formats:[],facets:null,players:[],tmdbAvailable:false,jobs:[],activityPollBusy:false,activityWasRunning:false,seq:0,controller:null,detail:null,detailSeq:0,personalSeq:0,personalPending:new Set(),enrichingIds:new Set(),playbackDraft:null,playbackMediaId:null,listId:typeof preferences.listId==='string'?preferences.listId:null,listItems:[],listRevision:0,listSort:['position','title','year','date_added','rating','user_rating'].includes(preferences.listSort)?preferences.listSort:'position',listOrder:preferences.listOrder==='desc'?'desc':'asc',listTotal:0,listSeq:0,items:[],ids:new Set(),nextOffset:0,total:0,hasMore:false,loading:false,loadError:false};
function savePreferences() {
  if(!filtersRestored)return;
  writeStorage(preferenceKey,JSON.stringify({filters:Object.fromEntries(filterIds.map(id=>[id,$(id).value])),formats:state.formats,order:state.order,recent:state.recent,view:state.view,filtersHidden:$('filters').hidden,listId:state.listId,listSort:state.listSort,listOrder:state.listOrder}));
}
function restoreFilters() {
  if(filtersRestored)return;
  for(const id of filterIds){
    const value=preferences.filters?.[id];
    if(typeof value==='string'&&(id==='search'||[...$(id).options].some(option=>option.value===value)))$(id).value=value;
  }
  const previousFormat=preferences.filters?.format;
  const savedFormats=Array.isArray(preferences.formats)?preferences.formats:
    typeof previousFormat==='string'&&previousFormat?[previousFormat]:[];
  const available=new Set((state.facets?.formats||[]).map(canonicalFormat));
  state.formats=[...new Set(savedFormats.filter(v=>typeof v==='string').map(canonicalFormat))]
    .filter(v=>available.has(v)).slice(0,20);
  updateFormatSelections();
  $('sort-order').textContent=state.order==='asc'?'↑ Croissant':'↓ Décroissant';
  $('filters').hidden=preferences.filtersHidden===true;
  $('toggle-filters').setAttribute('aria-expanded',!$('filters').hidden);
  filtersRestored=true;
}
const moreObserver = 'IntersectionObserver' in window ? new IntersectionObserver(entries=>{
  if(entries.some(entry=>entry.isIntersecting)&&!state.loading&&!state.loadError&&state.hasMore&&!$('app').hidden&&!document.querySelector('dialog[open]')) loadMedia({append:true});
},{rootMargin:'600px 0px'}) : null;
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
function showLogin() { ++state.seq; state.controller?.abort(); state.loading=false; moreObserver?.disconnect(); $('app').hidden=true; $('login').hidden=false; }
function date(value) { if (!value) return '—'; const d=new Date(value); return Number.isNaN(d.getTime()) ? '—' : d.toLocaleDateString('fr-FR'); }
function badges(item) { return item.source_types.map(s=>`<span class="source-badge ${s==='clz'?'physical':''}">${s==='clz'?'COLLECTION':'KODI'}</span>`).join(''); }
function poster(item, detail=false) {
  const progress=item.total_seconds>0 ? Math.min(100,100*item.resume_seconds/item.total_seconds):0;
  return `<div class="poster"><span class="poster-placeholder" aria-hidden="true">${esc(item.title.slice(0,2).toUpperCase())}</span>${item.artwork.poster?`<img src="${esc(url(item.artwork.poster))}" alt="" ${detail?'':'loading="lazy"'}>`:''}${!detail&&item.rating?`<span class="rating">★ ${Number(item.rating).toFixed(1)}</span>`:''}${!detail&&item.watch_status==='watched'?'<span class="state" title="Vu">✓</span>':''}${!detail&&item.watch_status==='in_progress'?'<span class="state progress" title="En cours">◐</span>':''}${progress>0&&item.watch_status==='in_progress'?`<span class="progress-track"><span style="width:${progress}%"></span></span>`:''}</div>`;
}
const formatLogos = {'4K':['4k','video'],'4K UHD':['4k','video'],'8K':['8k','video'],'1080p':['1080p','video'],'720p':['720p','video'],'3D':['3d','video'],'Blu-ray 3D':['3d','disc'],'Blu-ray':['bluray','disc'],'DVD':['dvd','disc'],'Dolby Vision':['vision','hdr'],'HDR10':['hdr10','hdr'],'HDR10+':['hdr10plus','hdr'],'HLG':['hlg','hdr'],'Dolby Atmos':['atmos','audio'],'Dolby TrueHD':['truehd','audio'],'DTS-HD MA':['dtshd','audio'],'DTS-HD':['dtshd','audio'],'DTS:X':['dtsx','audio'],'DTS':['dts','audio'],'Dolby Digital':['dd','audio'],'Dolby Digital+':['ddplus','audio']};
function formatBadges(values, compact=false, provenance=null) {
  const seen=new Set(); const labels=values.map(canonicalFormat).filter(label=>{const key=formatLogos[label]?.[0]||label;if(seen.has(key))return false;seen.add(key);return true;});
  const visible=compact?labels.slice(0,4):labels;
  return visible.map(label=>{
    const logo=formatLogos[label];
    const sources=provenance?.get(label);
    const description=sources?.length?`${label} · ${sources.join(', ')}`:label;
    return logo?`<span class="format-badge ${logo[1]}" title="${esc(description)}" aria-label="${esc(description)}"><svg aria-hidden="true" viewBox="0 0 112 36"><use href="${esc(url('static/format-logos.svg'))}#${logo[0]}"></use></svg></span>`:`<span class="format-chip" title="${esc(description)}">${esc(label)}</span>`;
  }).join('')+(compact&&labels.length>4?`<span class="format-overflow" title="${esc(labels.slice(4).join(', '))}">+${labels.length-4}</span>`:'');
}
function detailFormatBadges(item) {
  // The detail must reflect editions, never an outdated aggregated works.formats.
  const origins=new Map();
  for(const edition of item.editions||[]) {
    const source=edition.source_type==='clz'?'Collection physique':'Kodi';
    const name=edition.source_name?`${source} (${edition.source_name})`:source;
    for(const raw of edition.metadata?.formats||[]) {
      const label=canonicalFormat(raw);
      if(!origins.has(label))origins.set(label,[]);
      if(!origins.get(label).includes(name))origins.get(label).push(name);
    }
  }
  return formatBadges([...origins.keys()].sort((a,b)=>a.localeCompare(b,'fr')),false,origins);
}
const personCategories = {cast:'Casting',director:'Réalisation',writer:'Scénario',producer:'Production',composer:'Musique · Composition',photography:'Photographie',crew:'Autres fonctions'};
function castMarkup(people=[]) {
  const members=list=>`<ul class="cast-grid">${list.map(member=>{
    const links=member.links||{};const link=links.tmdb||links.imdb||links.imdb_search||`https://www.themoviedb.org/search/person?query=${encodeURIComponent(member.name)}`;
    return `<li class="cast-member"><a class="person-photo" href="${esc(link)}" target="_blank" rel="noopener noreferrer" aria-label="Voir la fiche de ${esc(member.name)}"><span class="cast-initials" aria-hidden="true">${esc(member.name.split(/\s+/).slice(0,2).map(word=>word[0]).join(''))}</span>${member.thumbnail?`<img src="${esc(url(member.thumbnail))}" alt="" loading="lazy">`:''}</a><span><a class="person-name" href="${esc(link)}" target="_blank" rel="noopener noreferrer"><strong>${esc(member.name)} ↗</strong></a>${member.role?`<small>${esc(member.role)}</small>`:''}<span class="person-links">${Object.entries(links).map(([site,link])=>`<a href="${esc(link)}" target="_blank" rel="noopener noreferrer">${site==='imdb_search'?'Recherche IMDb':site.toUpperCase()} ↗</a>`).join(' · ')}</span></span></li>`;
  }).join('')}</ul>`;
  return Object.entries(personCategories).map(([category,label])=>{
    const group=people.filter(p=>(p.category||'cast')===category); if(!group.length)return '';
    return `<section class="detail-section credits-section"><p class="section-label">${label.toUpperCase()} · ${group.length}</p>${members(group.slice(0,8))}${group.length>8?`<details class="cast-more"><summary>Voir les ${group.length-8} autres personnes</summary>${members(group.slice(8))}</details>`:''}</section>`;
  }).join('');
}
function actionIcon(name) {
  const icons={
    star:'<path d="m12 2.5 2.9 5.9 6.5.9-4.7 4.6 1.1 6.5-5.8-3.1-5.8 3.1 1.1-6.5-4.7-4.6 6.5-.9z"/>',
    youtube:'<rect x="1" y="4" width="22" height="16" rx="5" fill="#ff3838"/><path d="m10 8 6 4-6 4z" fill="white"/>',
    watch:'<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
    watched:'<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/><path d="m16 20 2 2 4-5"/>',
    refresh:'<path d="M20 7v5h-5M4 17v-5h5"/><path d="M6.1 6.1A8 8 0 0 1 20 12M4 12a8 8 0 0 0 13.9 5.9"/>'
  };
  const filled=['star','youtube'].includes(name);
  return `<svg aria-hidden="true" viewBox="0 0 24 24" fill="${filled?'currentColor':'none'}" ${filled?'':'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"'}>${icons[name]||''}</svg>`;
}
function personalActions(item) {
  const writable=state.role==='admin'&&!state.personalPending.has(item.id);
  const value=item.user_rating;
  return `<div class="rating-control"><button id="rating-button" class="action-button rating-button ${value!=null?'rated':''}" aria-label="Votre note : ${value!=null?esc(value)+'/10':'non renseignée'}${state.role==='admin'?', modifier':''}" aria-haspopup="dialog" aria-controls="rating-popover" aria-expanded="false" ${writable?'':'disabled'}>${actionIcon('star')}<span class="rating-value">${value!=null?esc(value):'—'}</span></button><div id="rating-popover" class="rating-popover" popover="manual" role="dialog" aria-label="Modifier votre note" hidden><div class="rating-popover-heading"><strong>Votre note</strong><output id="rating-preview">${value!=null?esc(value):'—'}<small> / 10</small></output></div><div class="rating-stars" role="group" aria-label="Choisir une note sur dix">${Array.from({length:10},(_,i)=>`<button class="rating-choice" data-rating="${i+1}" aria-label="Donner la note de ${i+1} sur 10" aria-pressed="${value===i+1}" ${writable?'':'disabled'}><span class="rating-glyph">${actionIcon('star')}<span class="rating-choice-fill">${actionIcon('star')}</span></span><small>${i+1}</small></button>`).join('')}</div><label class="rating-slider-label">Par demi-point<input id="rating-slider" type="range" min="0" max="10" step="0.5" value="${value??0}" aria-label="Votre note par demi-point" ${writable?'':'disabled'}></label><button class="text-button rating-reset" data-rating-reset ${writable?'':'disabled'}>Utiliser la note des sources</button><p class="hint">Cliquez sur une étoile pour enregistrer.</p></div></div>`;
}
function watchMarkup(item) {
  const watched=item.watch_status==='watched';
  const label=state.role==='admin'?`${stateLabels[item.watch_status]} · Marquer comme ${watched?'non vu':'vu'}`:`${stateLabels[item.watch_status]} · Accès en lecture seule`;
  return `<button id="watch-button" class="status-chip watch-button ${esc(item.watch_status)}" aria-label="${esc(label)}" aria-pressed="${watched}" title="${esc(label)}" data-tooltip="${esc(label)}" ${state.role==='admin'&&!state.personalPending.has(item.id)?'':'disabled'}>${actionIcon(watched?'watched':'watch')}<span class="watch-state">${esc(stateLabels[item.watch_status])}</span></button>`;
}
function detailActions(item) {
  const trailers=[...new Set(item.trailers||[])];
  const link=trailers[0]||'https://www.youtube.com/results?search_query='+encodeURIComponent(item.title+' '+(item.year||'')+' bande annonce');
  const multiple=trailers.length>1;
  const label=multiple?`Choisir une bande-annonce parmi ${trailers.length}`:trailers.length?'Voir la bande-annonce sur YouTube':'Rechercher une bande-annonce sur YouTube';
  const attributes=multiple?'id="open-trailers" aria-haspopup="dialog" aria-controls="trailers-dialog"':`id="trailer-link" href="${esc(link)}" target="_blank" rel="noopener noreferrer"`;
  const trailerButton=`<${multiple?'button':'a'} ${attributes} class="action-button trailer-button" aria-label="${label}" title="${label}">${actionIcon('youtube')}<span>Bande-annonce${multiple?` · ${trailers.length}`:''}</span></${multiple?'button':'a'}>`;
  const playable=item.kind!=='tvshow'&&item.editions.some(e=>e.source_type==='kodi');
  return `<div class="detail-actions compact-actions">${trailerButton}${personalActions(item)}${playable?`<button id="open-playback" class="action-button kodi-button" title="Lire sur Kodi" data-tooltip="Lire sur Kodi" aria-label="Lire sur Kodi" aria-haspopup="dialog" aria-controls="play-dialog"><img src="${esc(url('static/kodi-logo.svg'))}" alt="" width="27" height="27"></button>`:''}<span id="personal-status" class="visually-hidden" role="status" aria-live="polite"></span></div>`;
}
function openTrailers() {
  hideRatingPopover();
  const item=state.detail;if(!item)return;
  $('trailers-content').innerHTML=`<p class="play-media-title">${esc(item.title)}</p><div class="trailer-choices">${[...new Set(item.trailers||[])].map((link,index)=>`<a class="external-button" data-trailer href="${esc(link)}" target="_blank" rel="noopener noreferrer">${actionIcon('youtube')}<span>Bande-annonce ${index+1}<small>YouTube</small></span><span aria-hidden="true">↗</span></a>`).join('')}</div>`;
  showDialog('trailers-dialog');
}
function metadataAction(item) {
  if(item.kind==='episode')return '';
  const configured=item.tmdb_available;
  const label=configured?'Rafraîchir les métadonnées TMDB':'Configurer TMDB dans les paramètres';
  return `<div class="detail-tools"><button ${configured?'id="refresh-metadata"':'data-action="connections"'} class="action-button tmdb-button ${state.enrichingIds.has(item.id)?'is-loading':''}" title="${label}" data-tooltip="${label}" aria-label="${label}" ${state.role==='admin'&&!state.enrichingIds.has(item.id)?'':'disabled'}><img src="${esc(url('static/tmdb-logo.svg'))}" alt="" width="27" height="27">${configured?`<span class="refresh-mark">${actionIcon('refresh')}</span>`:''}</button></div>`;
}
let ratingCloseTimer;
function positionRatingPopover() {
  const popup=$('rating-popover'),button=$('rating-button');if(!popup||popup.hidden||!button)return;
  const rect=button.getBoundingClientRect(),gap=10,edge=12;
  popup.style.left=`${Math.max(edge,Math.min(rect.left,innerWidth-popup.offsetWidth-edge))}px`;
  const below=rect.bottom+gap;
  const top=below+popup.offsetHeight<=innerHeight-edge?below:Math.max(edge,rect.top-popup.offsetHeight-gap);
  popup.style.top=`${top}px`;
}
function showRatingPopover(focus=false) {
  clearTimeout(ratingCloseTimer);
  const popup=$('rating-popover'),button=$('rating-button');if(!popup||!button||button.disabled)return;
  popup.hidden=false;
  if(popup.showPopover&&!popup.matches(':popover-open'))popup.showPopover();
  popup.dataset.open='true';button.setAttribute('aria-expanded','true');positionRatingPopover();
  if(focus)$('rating-slider').focus({preventScroll:true});
}
function hideRatingPopover() {
  clearTimeout(ratingCloseTimer);
  const popup=$('rating-popover');
  if(popup){if(popup.hidePopover&&popup.matches(':popover-open'))popup.hidePopover();popup.hidden=true;delete popup.dataset.open;}
  $('rating-button')?.setAttribute('aria-expanded','false');
}
function scheduleRatingClose() {
  clearTimeout(ratingCloseTimer);
  ratingCloseTimer=setTimeout(()=>{if(!$('rating-popover')?.contains(document.activeElement))hideRatingPopover();},180);
}
function previewRating(value) {
  const output=$('rating-preview');if(output)output.innerHTML=`${value!=null?esc(value):'—'}<small> / 10</small>`;
  document.querySelectorAll('[data-rating]').forEach(button=>{
    const number=Number(button.dataset.rating);
    button.style.setProperty('--star-fill',`${Math.min(100,Math.max(0,((value??0)-number+1)*100))}%`);
  });
  $('rating-slider')?.setAttribute('aria-valuetext',`${value??0} sur 10`);
}
function syncPersonalControls() {
  const item=state.detail;if(!item)return;
  const busy=state.personalPending.has(item.id),writable=state.role==='admin'&&!busy;
  const rating=$('rating-button'),watch=$('watch-button');
  if(rating){rating.disabled=!writable;rating.classList.toggle('rated',item.user_rating!=null);rating.querySelector('.rating-value').textContent=item.user_rating??'—';rating.setAttribute('aria-label',`Votre note : ${item.user_rating!=null?item.user_rating+'/10':'non renseignée'}${state.role==='admin'?', modifier':''}`);}
  if(watch){
    const watched=item.watch_status==='watched';
    const label=state.role==='admin'?`${stateLabels[item.watch_status]} · Marquer comme ${watched?'non vu':'vu'}`:`${stateLabels[item.watch_status]} · Accès en lecture seule`;
    watch.disabled=!writable;watch.className=`status-chip watch-button ${item.watch_status}`;watch.setAttribute('aria-pressed',watched);watch.setAttribute('aria-label',label);watch.title=label;watch.dataset.tooltip=label;
    watch.innerHTML=`${actionIcon(watched?'watched':'watch')}<span class="watch-state">${esc(stateLabels[item.watch_status])}</span>`;
  }
  document.querySelectorAll('[data-rating],[data-rating-reset],#rating-slider').forEach(control=>control.disabled=!writable);
  document.querySelectorAll('[data-rating]').forEach(button=>button.setAttribute('aria-pressed',Number(button.dataset.rating)===item.user_rating));
  if($('rating-slider'))$('rating-slider').value=item.user_rating??0;
  previewRating(item.user_rating);
  if($('personal-status')&&busy)$('personal-status').textContent='Enregistrement…';
}
async function savePersonal(values) {
  const item=state.detail;if(!item||state.role!=='admin'||state.personalPending.has(item.id))return;
  const id=item.id,seq=state.detailSeq;
  const previous={personal:{...item.personal},user_rating:item.user_rating,watch_status:item.watch_status};
  state.personalPending.add(id);++state.personalSeq;hideRatingPopover();
  item.personal={...item.personal,...values};
  if(values.user_rating!=null)item.user_rating=values.user_rating;
  if(typeof values.watched==='boolean')item.watch_status=values.watched?'watched':'unwatched';
  syncPersonalControls();
  let saved=false;
  try {
    const response=await api(`media/${encodeURIComponent(id)}/personal`,{method:'PATCH',body:JSON.stringify(values)});
    saved=true;
    if(seq===state.detailSeq&&state.detail?.id===id){Object.assign(state.detail,{personal:response.personal,user_rating:response.user_rating,watch_status:response.watch_status});if($('personal-status'))$('personal-status').textContent='Modification enregistrée.';}
  }catch(e){
    if(seq===state.detailSeq&&state.detail?.id===id){Object.assign(state.detail,previous);if($('personal-status'))$('personal-status').textContent='Échec de l’enregistrement : '+e.message;}
    toast(e.message);
  }finally{state.personalPending.delete(id);if(seq===state.detailSeq&&state.detail?.id===id)syncPersonalControls();}
  if(saved){await loadMedia();if($('lists-dialog').open)await loadList();}
}
function metadataMarkup(item) {
  if(item.kind==='episode')return '';
  return `<span id="enrichment-status" class="enrichment-caption" role="status">${state.enrichingIds.has(item.id)?'Récupération des personnes, portraits et bandes-annonces…':item.enriched_at?`Métadonnées TMDB mises en cache le ${date(item.enriched_at)}.`:item.tmdb_available?'Les métadonnées TMDB sont récupérées à l’ouverture de cette fiche.':'TMDB n’est pas configuré. Les métadonnées affichées proviennent de Kodi ou CLZ.'}</span>`;
}
function detailDatesMarkup(item) {
  const dates=[];
  if(item.date_added)dates.push(`Ajouté le ${date(item.date_added)}`);
  if(item.last_played)dates.push(`Dernier visionnage le ${date(item.last_played)}`);
  const metadata=metadataMarkup(item);
  if(!dates.length&&!metadata)return '';
  return `<p class="hint detail-timestamps">${dates.map(esc).join(' · ')}${dates.length&&metadata?' · ':''}${metadata}</p>`;
}
function playbackMarkup(item) {
  const players=state.players.filter(p=>item.editions.some(e=>e.source_type==='kodi'&&e.source_id===p.source_id));
  let controls;
  if(state.role!=='admin')controls='<p class="hint">Un accès administrateur est nécessaire pour lancer la lecture.</p>';
  else if(!players.length)controls='<p class="hint">Ajoutez une instance Kodi associée à la source de ce fichier.</p><button class="secondary" data-action="connections">Configurer une instance Kodi</button>';
  else controls=`<label>Instance Kodi<select id="play-target">${players.map(p=>`<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('')}</select></label><label>Fichier ou version<select id="play-edition" aria-label="Fichier ou version Kodi"></select></label><label class="play-options"><input id="play-resume" type="checkbox" checked>Reprendre au point de lecture</label><button id="play-button" class="primary">▶ Lire sur Kodi</button><p id="play-status" class="hint play-status" role="status" aria-live="polite"></p>`;
  return `<p class="play-media-title">${esc(item.title)}</p><div class="playback-controls">${controls}</div><p class="hint">Kodi lit le fichier avec ses propres accès au NAS.</p>`;
}
function savePlaybackDraft() {
  if(!$('play-target'))return;
  state.playbackDraft={mediaId:state.playbackMediaId,target:$('play-target').value,edition:$('play-edition').value,resume:$('play-resume').checked};
}
function openPlayback() {
  hideRatingPopover();
  const item=state.detail;if(!item)return;
  const draft=state.playbackDraft?.mediaId===item.id?state.playbackDraft:null;
  state.playbackMediaId=item.id;
  $('play-content').innerHTML=playbackMarkup(item);
  if(draft&&$('play-target')&&[...$('play-target').options].some(o=>o.value===draft.target))$('play-target').value=draft.target;
  updatePlayEdition();
  if(draft&&$('play-edition')){
    if([...$('play-edition').options].some(o=>o.value===draft.edition))$('play-edition').value=draft.edition;
    $('play-resume').checked=draft.resume;
  }
  showDialog('play-dialog');
}
function card(item) {
  const meta=[item.year,kindLabels[item.kind]].filter(Boolean);
  return `<button class="media-card" data-media="${esc(item.id)}" aria-label="${esc(item.title)}, ${esc(stateLabels[item.watch_status])}">${poster(item)}<span><span class="card-title">${esc(item.title)}</span><span class="card-meta">${meta.map(v=>`<span>${esc(v)}</span>`).join('<span aria-hidden="true">·</span>')}</span><span class="format-badges compact">${formatBadges(item.formats,true)}</span><span class="card-meta">${badges(item)}</span></span>${state.view==='list'?`<span class="list-status">${esc(stateLabels[item.watch_status])}<br>${item.rating?`★ ${Number(item.rating).toFixed(1)} · `:''}${date(item.date_added)}</span>`:''}</button>`;
}
function attachImageFallbacks(root) { root.querySelectorAll('img').forEach(img=>{img.addEventListener('error',()=>img.classList.add('failed'),{once:true});}); }
function currentParams(offset=0) {
  const params=new URLSearchParams({limit:state.limit,offset,sort:$('sort').value,order:state.order});
  for (const [id,name] of [['search','q'],['kind','kind'],['source-type','source_type'],['genre','genre'],['collection','collection'],['watch-status','watch_status']]) if ($(id).value) params.set(name,$(id).value);
  for (const value of state.formats) params.append('format',value);
  if (state.recent) params.set('recent_days','30');
  return params;
}
function updateFilterState() {
  let count=['search','kind','source-type','genre','collection','watch-status'].filter(id=>$(id).value).length + state.formats.length + Number(state.recent);
  $('filter-count').textContent=count || '';
  $('reset-filters').hidden=!count;
  document.querySelectorAll('[data-quick]').forEach(b=>b.classList.toggle('active', b.dataset.quick==='recent'?state.recent:b.dataset.quick==='all'?!state.recent&&!$('watch-status').value:!state.recent&&b.dataset.quick===$('watch-status').value));
}
function observeMore() {
  moreObserver?.disconnect();
  if(!state.loading&&!state.loadError&&state.hasMore&&!$('app').hidden&&!document.querySelector('dialog[open]')) moreObserver?.observe($('load-more-trigger'));
}
function updateLoadingState() {
  $('pagination').hidden=!state.items.length&&!state.loadError;
  $('page-info').textContent=`${state.items.length.toLocaleString('fr-FR')} / ${state.total.toLocaleString('fr-FR')} titres affichés`;
  $('load-more').hidden=!state.hasMore&&!state.loadError;
  $('load-more').disabled=state.loading;
  $('load-more').textContent=state.loadError?'Réessayer':'Charger la suite';
  $('load-more-status').textContent=state.loading?'Chargement de la suite…':state.loadError?'Le chargement a échoué. Vous pouvez réessayer.':!state.hasMore&&state.items.length?'Tous les résultats sont affichés.':'';
}
function renderCards(items, append=false) {
  const root=$('results'); root.className=state.view==='grid'?'poster-grid':'list-results';
  const template=document.createElement('template'); template.innerHTML=items.map(card).join('');
  attachImageFallbacks(template.content);
  if(!append) root.replaceChildren();
  root.append(template.content);
}
async function loadMedia({append=false}={}) {
  if(append&&(state.loading||!state.hasMore)) return;
  if(!append)savePreferences();
  if(!append) {
    ++state.seq; state.controller?.abort(); state.items=[]; state.ids=new Set(); state.nextOffset=0; state.total=0; state.hasMore=false;
    $('results').replaceChildren();
  }
  const seq=state.seq; const offset=append?state.nextOffset:0;
  state.controller=new AbortController(); state.loading=true; state.loadError=false;
  moreObserver?.disconnect();
  $('results').dataset.appending=String(append);
  $('results').setAttribute('aria-busy','true'); $('error-banner').hidden=true;
  updateLoadingState();
  updateFilterState();
  try {
    const page=await api(`media?${currentParams(offset)}`,{signal:state.controller.signal});
    if (seq!==state.seq) return;
    const added=page.items.filter(item=>!state.ids.has(item.id));
    for(const item of added) state.ids.add(item.id);
    state.items.push(...added); state.total=page.total; state.nextOffset=offset+page.items.length;
    state.hasMore=page.has_more&&page.items.length>0;
    $('result-count').textContent=`${page.total.toLocaleString('fr-FR')} résultat${page.total>1?'s':''}${state.recent?' · ajoutés depuis 30 jours':''}`;
    if (!append&&!page.items.length) {
      const empty=!state.facets?.sources.length || state.facets.sources.every(s=>!s.item_count);
      $('results').innerHTML=`<div class="empty-state"><span class="empty-icon" aria-hidden="true">▦</span><h2>${empty?'Votre bibliothèque commence ici.':'Aucun titre ne correspond.'}</h2><p>${empty?'Connectez la base Kodi ou importez un export CLZ Movies.':'Modifiez les filtres ou essayez un autre mot.'}</p>${state.role==='admin'&&empty?'<button class="primary" data-action="connections">Ajouter une source →</button>':'<button class="secondary" data-action="reset">Effacer les filtres</button>'}</div>`;
    } else renderCards(added,append);
  } catch (e) { if (e.name!=='AbortError'&&seq===state.seq) {state.loadError=true;error(e.message);} }
  finally { if (seq===state.seq) {state.loading=false;$('results').setAttribute('aria-busy','false');updateLoadingState();observeMore();} }
}
function fillSelect(id, values, label) {
  const selected=$(id).value;
  $(id).innerHTML=`<option value="">${label}</option>`+values.map(v=>`<option value="${esc(v)}">${esc(v)}</option>`).join('');
  if (values.includes(selected)) $(id).value=selected;
}
async function refresh() {
  const [facets,status,players]=await Promise.all([api('facets'),api('status'),api('players')]);
  state.facets=facets; state.players=players;
  state.tmdbAvailable=status.tmdb_available===true;
  fillSelect('genre',facets.genres,'Tous les genres'); fillSelect('collection',facets.collections,'Toutes les collections');
  if(filtersRestored){
    const available=new Set(facets.formats.map(canonicalFormat));
    state.formats=state.formats.filter(v=>available.has(v));
    updateFormatSelections();
  }
  restoreFilters();
  $('catalogue-status').textContent=`${status.works.toLocaleString('fr-FR')} titres · ${status.editions.toLocaleString('fr-FR')} fichiers et éditions · ${status.sources.length} source${status.sources.length>1?'s':''}`;
  const latest=status.sources.map(s=>s.last_sync).filter(Boolean).sort().at(-1);
  $('sync-status').textContent=latest?`Dernière mise à jour : ${date(latest)}`:'Aucune source importée';
  await loadMedia();
}
// Keep the activity indicator in the main header, including automated jobs
// started by the server scheduler and jobs launched by another client.
const jobPhaseLabels={
  connecting:'Connexion à MariaDB',loading:'Lecture des métadonnées Kodi',parsing_clz:'Analyse du fichier CLZ',
  movies:'Films',series:'Séries',episodes:'Épisodes',
  verifying_library:'Contrôle de la vidéothèque Kodi',
  publishing:'Mise à jour du catalogue',completed:'Terminé',failed:'Échec'
};
function jobProgressLabel(job) {
  const phase=jobPhaseLabels[job.progress_phase]||'Préparation';
  const current=Math.max(0,Number(job.progress_current)||0);
  const total=Math.max(0,Number(job.progress_total)||0);
  const overall=Number(job.overall_percent);
  const overallText=Number.isFinite(overall)&&job.progress_phase!=='parsing_clz'?' · ≈ '+overall+' % global':'';
  return phase+(total>0?' · '+Math.min(current,total).toLocaleString('fr-FR')+'/'+total.toLocaleString('fr-FR'):'…')+overallText;
}
function jobProgressMarkup(job) {
  const percent=Number(job.overall_percent);
  const showProgress=Number.isFinite(percent)&&job.progress_phase!=='parsing_clz';
  return '<div class="sync-phase"><span class="activity-spinner" aria-hidden="true"></span><span>'+esc(jobProgressLabel(job))+'</span></div>'+
    (showProgress?'<progress class="sync-progress" value="'+Math.max(0,Math.min(percent,100))+'" max="100"></progress>':'');
}
function renderActivity() {
  const active=state.jobs.filter(j=>['pending','running'].includes(j.status));
  const indicator=$('activity-indicator');
  indicator.hidden=!active.length || $('app').hidden;
  if(!active.length)return;
  const sources=new Map((state.facets?.sources||[]).map(s=>[s.id,s]));
  const kinds=new Set(active.map(j=>sources.get(j.source_id)?.type||'kodi'));
  const label=kinds.size>1?'Synchronisation / import en cours'
    :kinds.has('clz')?'Import CLZ en cours':'Synchronisation Kodi en cours';
  const first=active[0];
  const overall=Number(first.overall_percent);
  const showProgress=Number.isFinite(overall)&&first.progress_phase!=='parsing_clz';
  const percent=showProgress?' · ≈'+overall+' %':'';
  $('activity-label').textContent=active.length>1?`${active.length} tâches en cours`:label+percent;
  $('activity-progress').hidden=!showProgress||active.length!==1;
  if(showProgress&&active.length===1){
    $('activity-progress').max=100;
    $('activity-progress').value=Math.max(0,Math.min(overall,100));
  }
  indicator.title=active.map(j=>`${sources.get(j.source_id)?.name||'Source'} : ${j.status}`).join(' · ');
}
async function pollActivity() {
  if(state.activityPollBusy||$('app').hidden)return;
  state.activityPollBusy=true;
  try {
    const recent=await api('jobs');
    const oldActive=state.jobs.filter(j=>['pending','running'].includes(j.status));
    const finished=oldActive.filter(j=>!recent.some(current=>current.id===j.id&&['pending','running'].includes(current.status)));
    state.jobs=recent;
    renderActivity();
    if(finished.length) {
      const completed=finished.filter(j=>recent.find(r=>r.id===j.id)?.status==='completed');
      const failed=finished.filter(j=>recent.find(r=>r.id===j.id)?.status==='failed');
      if(completed.length) {
        await refresh();
        if($('connections-dialog').open) await connections();
        toast(`${completed.length} synchronisation(s) / import(s) terminé(s). Catalogue actualisé.`);
      }
      if(failed.length) toast('Une synchronisation ou un import a échoué. Consultez les paramètres.');
    }
  } catch(e) {
    // Transient network issues must not leave a stale running badge forever.
    if(e?.status===401)showLogin();
  } finally { state.activityPollBusy=false; }
}
$('activity-indicator').addEventListener('click',()=>connections());
setInterval(()=>{if(!$('app').hidden)pollActivity();},3000);
async function initialize() {
  try {
    const session=await api('auth/session'); state.csrf=session.csrf_token; state.role=session.role;
    $('login').hidden=true; $('app').hidden=false; $('connections-button').hidden=state.role!=='admin'; $('logout-button').hidden=session.access_method!=='session';
    $('create-list-button').hidden=state.role!=='admin';
    setView(state.view,false); await refresh(); await pollActivity();
  } catch (e) { if ($('app').hidden) showLogin(); else error(e.message); }
}
function reset() { ['search','kind','source-type','genre','collection','watch-status'].forEach(id=>$(id).value=''); state.formats=[]; updateFormatSelections(); state.recent=false; loadMedia(); }
function setView(view, render=true) { state.view=view; writeStorage('kodi_library_view',view);savePreferences(); $('grid-view').setAttribute('aria-pressed',view==='grid'); $('list-view').setAttribute('aria-pressed',view==='list'); if (render&&state.items.length) {renderCards(state.items);observeMore();} }
let pagePosition=null;
function lockPage() {
  if(pagePosition)return;
  pagePosition={x:window.scrollX,y:window.scrollY};
  document.body.style.top=`-${pagePosition.y}px`;
  document.body.style.left=`-${pagePosition.x}px`;
  document.documentElement.classList.add('modal-open');
  moreObserver?.disconnect();
}
function releasePage() {
  if(document.querySelector('dialog[open]'))return;
  if(pagePosition){
    const position=pagePosition;pagePosition=null;
    document.documentElement.classList.remove('modal-open');
    document.body.style.top='';document.body.style.left='';
    window.scrollTo(position.x,position.y);
  }
  observeMore();
}
function showDialog(id) { if (!$(id).open) {lockPage();$(id).showModal();} }
function editionMarkup(e) {
  const m=e.metadata;
  const fields=[['Format',m.formats?.join(', ')],['Vidéo',m.video_streams?.map(v=>[v.width&&v.height?`${v.width} × ${v.height}`:'',v.codec,v.hdr_type,v.hdr_detail,v.stereo_mode].filter(Boolean).join(' · ')).join(' / ')],['Édition',m.edition||m.version],['Code-barres',m.barcode],['Disques',m.disc_count],['Quantité',m.quantity],['Coffret',m.boxset],['Collection',m.collections?.join(', ')],['Régions',m.regions?.join(', ')],['Audio',m.audio_tracks?.join(', ')],['Sous-titres',m.subtitles?.join(', ')],['Dernier visionnage',e.last_played?date(e.last_played):null],['Historique CLZ',m.seen_dates?.map(date).join(', ')],['Lectures',e.play_count],['Reprise',e.resume_seconds?`${Math.floor(e.resume_seconds/60)} min`:null],['Fichier',m.file],['Ajout',e.date_added?date(e.date_added):null],['Identifiants',Object.entries(e.identifiers).map(([k,v])=>`${k}: ${v}`).join(' · ')]];
  const label=e.source_type==='clz'?(m.formats?.join(' / ')||'Édition physique'):(m.version||'Fichier Kodi');
  return `<details class="edition"><summary><b>${esc(label)}</b> <span class="muted">· ${esc(e.source_name)}</span></summary><div class="format-badges edition-formats">${formatBadges(m.formats||[])}</div><dl class="edition-grid">${fields.filter(([,v])=>v!==null&&v!==undefined&&v!=='').map(([k,v])=>`<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl></details>`;
}
function tmdbAttribution(item) {
  if(!item.enriched_at)return '';
  return `<section class="detail-section credits-attribution"><p class="section-label">CRÉDITS DES MÉTADONNÉES</p><a href="https://www.themoviedb.org/" target="_blank" rel="noopener noreferrer"><img width="80" src="${esc(url('static/tmdb-logo.svg'))}" alt="TMDB"></a><p class="hint">This product uses the TMDB API but is not endorsed or certified by TMDB.</p></section>`;
}
function updatePlayEdition() {
  const target=$('play-target'); const selector=$('play-edition');if(!target||!selector)return;
  const previous=selector.value;
  const player=state.players.find(p=>p.id===target.value);if(!player)return;
  const editions=state.detail.editions.filter(e=>e.source_type==='kodi'&&e.source_id===player.source_id).sort((a,b)=>Number(!a.metadata.is_default)-Number(!b.metadata.is_default));
  selector.innerHTML=editions.map(e=>`<option value="${esc(e.id)}">${esc(e.metadata.version||e.metadata.file?.split('/').pop()||'Fichier Kodi')}${e.metadata.is_default?' · Par défaut':''}</option>`).join('');
  if([...selector.options].some(option=>option.value===previous))selector.value=previous;
}
function renderDetail(item) {
  const previousScroll=state.detail?.id===item.id?$('detail-dialog').scrollTop:0;
  hideRatingPopover();
  state.detail=item;
  $('detail-content').innerHTML=`${metadataAction(item)}<div class="detail-layout"><div class="detail-poster">${poster(item,true)}</div><div class="detail-intro"><p class="eyebrow">${esc(kindLabels[item.kind])}</p><div class="detail-heading"><h2>${esc(item.title)}</h2>${item.original_title&&item.original_title!==item.title?`<p class="original-title">${esc(item.original_title)}</p>`:''}</div><div class="detail-info">${item.year?`<span>${item.year}</span>`:''}${item.runtime_minutes?`<span>${item.runtime_minutes} min</span>`:''}${item.rating?`<span class="score" title="Note publique : ${Number(item.rating).toFixed(1)}/10">★ ${Number(item.rating).toFixed(1)}</span>`:''}</div>${detailActions(item)}<div class="format-badges" aria-label="Formats disponibles selon les éditions">${detailFormatBadges(item)}</div><div class="detail-meta-row">${watchMarkup(item)}<div class="card-meta">${badges(item)}</div></div>${item.source_types.length===1&&item.source_types[0]==='clz'?'<p class="hint">Cette fiche provient uniquement de CLZ. Aucun fichier Kodi ne lui est actuellement associé dans le catalogue.</p>':''}<button id="add-to-list" class="secondary" ${state.role==='admin'?'':'disabled title="Un accès administrateur est nécessaire pour modifier les listes."'}>+ Ajouter à une liste</button>${item.lists?.length?`<p class="hint">Listes : ${item.lists.map(l=>`<button class="text-button" data-open-list="${esc(l.id)}">${esc(l.name)} ↗</button>`).join(' · ')}</p>`:''}</div><div class="detail-body"><div class="genre-chips">${item.genres.map(g=>`<button data-genre="${esc(g)}">${esc(g)}</button>`).join('')}</div><p class="synopsis">${esc(item.plot||'Synopsis non renseigné.')}</p>${detailDatesMarkup(item)}${castMarkup(item.people||item.cast)}${item.kind==='tvshow'?'<section class="detail-section"><p class="section-label">ÉPISODES KODI</p><div id="episode-list" class="episode-links">Chargement…</div></section>':''}<section class="detail-section"><p class="section-label">FICHIERS & ÉDITIONS · ${item.editions.length}</p>${item.editions.map(editionMarkup).join('')}${state.role==='admin'&&item.kind==='movie'?'<button id="diagnose-kodi" class="secondary" type="button">Vérifier la présence dans la vidéothèque</button><div id="kodi-diagnostic" class="metadata-status" role="status" aria-live="polite" hidden></div>':''}</section>${tmdbAttribution(item)}</div></div>`;
  attachImageFallbacks($('detail-content'));syncPersonalControls();$('detail-dialog').scrollTop=previousScroll;
}
async function openDetail(id) {
  const seq=++state.detailSeq;
  try {
    const item=await api(`media/${encodeURIComponent(id)}`); if(seq!==state.detailSeq)return;
    renderDetail(item); showDialog('detail-dialog');
    if(item.tmdb_available&&state.role==='admin'&&item.kind!=='episode'&&(!item.enriched_at||Date.now()-Date.parse(item.enriched_at)>30*86400000)) enrichDetail(false);
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
async function diagnoseKodi() {
  const item=state.detail;
  const display=$('kodi-diagnostic');
  const button=$('diagnose-kodi');
  if(!item||!display||!button)return;
  display.hidden=false;
  display.textContent='Contrôle du parcours de vidéothèque via les tables MariaDB…';
  button.disabled=true;
  try {
    const data=await api('media/'+encodeURIComponent(item.id)+'/diagnostics');
    if(state.detail?.id!==item.id||!$('kodi-diagnostic'))return;
    const paragraphs=[];
    for(const source of data.sources){
      paragraphs.push('<p><strong>'+esc(source.source_name)+'</strong> ('+(source.source_type==='clz'?'CLZ':'Kodi')+')</p>');
      if(source.source_type==='clz'){
        paragraphs.push('<p class="hint">Édition CLZ toujours présente : cette fiche est conservée.</p>');
        continue;
      }
      paragraphs.push('<p class="hint">Base configurée : '+esc(source.configured_database||'Automatique')+' · Base interrogée : <strong>'+esc(source.queried_database||'non vérifiée')+'</strong></p>');
      if(source.library_rule)paragraphs.push('<p class="hint">Critère de la vidéothèque : <strong>'+esc(source.library_rule)+'</strong></p>');
      if(source.error)paragraphs.push('<p class="error-text">Échec du contrôle : '+esc(source.error)+'</p>');
      for(const edition of source.editions){
        const status=edition.in_library===true?'PRÉSENT dans la vidéothèque Kodi (SQL)':
          edition.in_library===false?'ABSENT du parcours Kodi (version par défaut non trouvée)':
          edition.exists_in_movie===true?'Présent dans movie (schéma ancien ou contrôle incomplet)':
          edition.exists_in_movie===false?'ABSENT de movie':'Non vérifié';
        paragraphs.push('<p class="'+(edition.in_library===false||edition.exists_in_movie===false?'error-text':'hint')+'">'+esc(edition.external_key)+' · idMovie='+esc(edition.kodi_id??'—')+' · '+esc(status)+'</p>');
        const links=edition.sql_links;
        if(links){
          paragraphs.push('<p class="hint">Fichier par défaut (ID '+esc(links.default_file_id??'—')+') : '+(links.file_row_exists?'ligne files présente':'<strong>ligne files absente</strong>')+' · '+(links.path_row_exists?'chemin SQL présent':'<strong>chemin SQL absent</strong>')+'</p>');
          if(links.file)paragraphs.push('<p class="hint">Chemin enregistré : <code>'+esc(links.file)+'</code></p>');
          if(links.excluded_from_scan)paragraphs.push('<p class="hint">Répertoire exclu du scan (path.exclude)</p>');
          if(links.scan_disabled)paragraphs.push('<p class="hint">Mise à jour du répertoire désactivée (path.noUpdate)</p>');
          if(links.versions?.length)paragraphs.push('<p class="hint">Versions dans videoversion : '+links.versions.map(v=>'ID '+esc(v.file_id??'—')+' ('+(v.file_row_exists?'lien valide':'fichier SQL absent')+')').join(' · ')+'</p>');
        }
      }
      paragraphs.push('<p class="hint">La présence dans files/path atteste un lien SQL, pas l’existence physique du fichier NAS. exclude/noUpdate contrôlent le scan, ce ne sont pas des indicateurs de suppression.</p>');
    }
    $('kodi-diagnostic').innerHTML=paragraphs.join('')||'<p>Aucune édition répertoriée.</p>';
  }catch(e){
    if(state.detail?.id===item.id&&$('kodi-diagnostic')) $('kodi-diagnostic').textContent=e.message;
  }finally{
    if(state.detail?.id===item.id&&$('diagnose-kodi')) $('diagnose-kodi').disabled=false;
  }
}
document.addEventListener('click',event=>{
  if(event.target.closest('#diagnose-kodi'))diagnoseKodi();
});
async function pollJob(job) {
  state.jobs=[job,...state.jobs.filter(j=>j.id!==job.id)];renderActivity();
  const target=$('job-status'); if (target) target.hidden=false;
  for(let i=0;i<600;i++) {
    const value=await api(`jobs/${encodeURIComponent(job.id)}`);
    state.jobs=[value,...state.jobs.filter(j=>j.id!==value.id)];renderActivity();
    if(target && ['pending','running'].includes(value.status)){
      target.innerHTML=jobProgressMarkup(value);
    }
    if(value.status==='failed') throw new Error(value.error||'Import échoué.');
    if(value.status==='completed') {
      const result=value.result||{};
      const details=[`${result.imported||0} entrées synchronisées`];
      if(result.database) details.unshift(`Base Kodi : ${result.database}`);
      if(result.created_editions) details.push(`${result.created_editions} nouvelles éditions`);
      if(result.updated_editions) details.push(`${result.updated_editions} éditions reconnues`);
      if(result.unchanged_editions) details.push(`${result.unchanged_editions} éditions inchangées (sans réindexation)`);
      if(result.rebuilt_works!=null) details.push(`${result.rebuilt_works} fiches réindexées`);
      if(result.library_verified)details.push(`Parcours SQL Kodi vérifié : ${result.library_movie_count} films · ${result.library_excluded_editions} éditions écartées`);

      if(result.removed_editions) details.push(`${result.removed_editions} éditions retirées de cette source`);
      else if(result.database) details.push('Aucune suppression Kodi détectée');
      if(result.removed_works) details.push(`${result.removed_works} œuvres supprimées du catalogue`);
      if(result.orphaned_movie_view_rows) details.push(`${result.orphaned_movie_view_rows} lignes obsolètes dans movie_view ignorées`);
      if(result.movie_table_count!=null) details.push(`${result.movie_table_count} films présents dans movie`);
      if(result.sqlite_publish_ms!=null) details.push(`Publication SQLite : ${result.sqlite_publish_ms} ms`);
      if(result.sql_view_fetch_ms) details.push(`Lecture vues SQL : ${Object.entries(result.sql_view_fetch_ms).map(([kind,ms])=>kind+' '+ms+' ms').join(', ')}`);
      if(result.kept_clz_works) details.push(`${result.kept_clz_works} œuvres conservées via CLZ`);
      if(result.kept_other_sources) details.push(`${result.kept_other_sources} œuvres conservées par une autre source`);
      if(result.matching_conflicts) details.push(`${result.matching_conflicts} rapprochements ambigus conservés séparément`);
      const summary=details.join(' · ')+'.';
      const removedTitles=result.removed_titles||[];
      const more=Math.max(0,(result.removed_titles_total||removedTitles.length)-removedTitles.length);
      const removalReport=removedTitles.length
        ? `\nRetirés de cette source : ${removedTitles.join(' · ')}${more?` · et ${more} autre(s)`:''}.`
        : '';
      const report=summary+removalReport+(result.database&&result.removed_editions===0?
        '\nSi un film supprimé de Kodi reste affiché, vérifiez que la base MyVideos indiquée est bien celle utilisée par Kodi et que la fiche ne provient pas de CLZ.':'');
      if(target) target.textContent=report;
      toast(summary);
      await refresh();
      if($('connections-dialog').open) {
        await connections();
        const status=$('job-status');
        if(status) {status.hidden=false;status.textContent=report;}
      }
      return;
    }
    await new Promise(resolve=>setTimeout(resolve,1000));
  }
  toast('L’import continue en arrière-plan. Consultez les paramètres pour son résultat.');
}
async function connections() {
  try {
    const [sources,players,status,devices]=await Promise.all([api('sources'),api('players'),api('status'),api('pairing/devices')]);state.facets.sources=sources;state.players=players;state.tmdbAvailable=status.tmdb_available===true;
    const clz=sources.filter(s=>s.type==='clz'); const kodi=sources.filter(s=>s.type==='kodi');
    const sourceJobs=new Map();
    for(const job of state.jobs)if(!sourceJobs.has(job.source_id))sourceJobs.set(job.source_id,job);
    $('connection-content').innerHTML=`<h3 class="connection-subheading">Appairage de l'application iOS</h3><section class="metadata-status"><p>Générez un code à 8 chiffres, valable 5 minutes et utilisable une seule fois. Il remplace la saisie du jeton API dans l'application.</p><label>Autorisations du nouvel appareil <select id="pairing-role"><option value="admin">Administrateur</option><option value="read">Lecture seule</option></select></label><p><button id="pairing-start" class="primary" type="button">Générer un code d'appairage</button></p><p id="pairing-code" class="pairing-code" role="status" aria-live="polite"></p><p class="hint">Dans iOS : sélectionnez le serveur détecté, puis recopiez le code. Un autre code invalide le précédent. Privilégiez HTTPS.</p>${devices.length?`<p class="hint">Appareils appairés</p><div class="paired-devices">${devices.map(device=>`<div class="connection-card"><span><strong>${esc(device.name)}</strong> · ${device.role==='admin'?'Administrateur':'Lecture seule'}</span><button type="button" class="secondary" data-revoke-device="${esc(device.token_hash)}">Révoquer</button></div>`).join('')}</div>`:''}</section><h3 class="connection-subheading">Métadonnées et portraits · TMDB</h3><section class="metadata-status"><p>${state.tmdbAvailable?'TMDB est configuré. Les fiches récupèrent automatiquement le casting, les portraits et les bandes-annonces disponibles.':'TMDB n’est pas configuré. Renseignez TMDB_API_TOKEN dans .env, puis relancez rebuild.sh.'}</p><p class="hint">Utilisez le jeton de lecture API ou la clé API v3 depuis <a href="https://www.themoviedb.org/settings/api" target="_blank" rel="noopener noreferrer">les paramètres API de votre compte TMDB ↗</a>. Le secret reste sur le serveur. Il n’est pas nécessaire de réimporter la bibliothèque pour activer TMDB.</p><button id="test-tmdb" class="secondary" ${state.tmdbAvailable?'':'disabled'}>Tester la connexion TMDB</button><p id="tmdb-test-result" class="hint play-status" role="status"></p></section><h3 class="connection-subheading">Sources du catalogue</h3>${sources.map(s=>`<div class="connection-card"><div><strong>${esc(s.name)}</strong><p>${s.type==='kodi'?'Kodi / MariaDB':'CLZ Movies'} · ${s.item_count} entrées${s.last_sync?` · ${date(s.last_sync)}`:''}${!s.enabled?' · Désactivée':''}</p>${sourceJobs.get(s.id)?.result?.database?`<p class="hint">Base synchronisée : <strong>${esc(sourceJobs.get(s.id).result.database)}</strong> · ${s.config?.database?'sélection explicite':'détection automatique'}</p>`:''}${sourceJobs.get(s.id)&&['pending','running'].includes(sourceJobs.get(s.id).status)?`<div class="source-progress">${jobProgressMarkup(sourceJobs.get(s.id))}</div>`:''}${s.last_error?`<p class="error-text">${esc(s.last_error)}</p>`:''}</div><div class="connection-actions">${s.type==='kodi'?`<button data-sync="${esc(s.id)}">Synchroniser</button><button data-edit-source="${esc(s.id)}">Modifier</button>`:''}<button data-remove-source="${esc(s.id)}">Retirer</button></div></div>`).join('')||'<p class="hint">Aucune source configurée.</p>'}<details><summary class="text-button">+ Connecter une base Kodi</summary><form id="kodi-form" class="connection-form"><input type="hidden" name="id"><label>Nom<input name="name" required placeholder="Kodi — NAS"></label><label>Hôte MariaDB<input name="host" required placeholder="192.168.1.10"></label><label>Port<input name="port" type="number" min="1" max="65535" value="3306" required></label><label>Base MyVideos<input name="database" placeholder="Automatique, ou MyVideos…"></label><label>Utilisateur<input name="username" required autocomplete="off" placeholder="kodi_library"></label><label>Mot de passe<input name="password" type="password" autocomplete="new-password"></label><label class="check-label wide"><input name="tls" type="checkbox">Connexion MariaDB avec TLS</label><p class="hint wide">Ce formulaire contacte MariaDB, pas l’API HTTP de Kodi. Un compte SELECT suffit ; Kodi peut être arrêté. La connexion est testée avant tout nouvel ajout.</p><button class="secondary wide" type="button" data-test-connection="kodi-form">Tester la connexion MariaDB</button><p id="kodi-form-result" class="connection-result wide" role="status" aria-live="polite" hidden></p><button class="primary wide" type="submit">Enregistrer et synchroniser</button></form></details><h3 class="connection-subheading">Importer CLZ Movies</h3><form id="clz-form" class="upload-row"><select name="source_id" aria-label="Collection CLZ">${clz.map(s=>`<option value="${esc(s.id)}">${esc(s.name)}</option>`).join('')}<option value="">Nouvelle collection…</option></select><input name="file" type="file" accept=".xml,application/xml,text/xml" required aria-label="Export XML CLZ Movies"><button class="secondary" type="submit">Importer l’export XML</button><p class="hint">Un nouvel import remplace les données de cette collection et conserve les données Kodi associées. Une synchronisation Kodi retire les fichiers absents de la base MyVideos, mais conserve les films également présents dans CLZ. Supprimer un fichier du disque ne suffit pas : nettoyez d’abord la médiathèque Kodi.</p></form><div id="job-status" class="job-status" role="status" hidden></div><h3 class="connection-subheading">Instances Kodi pour la lecture</h3>${state.players.map(p=>`<div class="connection-card"><div><strong>${esc(p.name)}</strong><p>${esc(p.host)}:${p.port}</p><p id="player-result-${esc(p.id)}" class="hint play-status" role="status"></p></div><div class="connection-actions"><button data-test-player="${esc(p.id)}">Tester</button><button data-edit-player="${esc(p.id)}">Modifier</button><button data-remove-player="${esc(p.id)}">Retirer</button></div></div>`).join('')||'<p class="hint">Configurez-les lorsque vous souhaitez lancer un média à distance.</p>'}${kodi.length?`<details><summary class="text-button">+ Ajouter une instance Kodi</summary><form id="player-form" class="connection-form"><input type="hidden" name="id"><label>Nom<input name="name" required placeholder="Kodi — Salon"></label><label>Base partagée<select name="source_id">${kodi.map(s=>`<option value="${esc(s.id)}">${esc(s.name)}</option>`).join('')}</select></label><label>Hôte Kodi<input name="host" required placeholder="192.168.1.20"></label><label>Port HTTP<input name="port" type="number" value="8080" min="1" max="65535" required></label><label>Utilisateur HTTP<input name="username" autocomplete="off"></label><label>Mot de passe HTTP<input name="password" type="password" autocomplete="new-password"></label><label class="check-label wide"><input name="tls" type="checkbox">HTTPS sur Kodi</label><p class="hint wide">Ce formulaire contacte l’API HTTP de Kodi, pas MariaDB. Kodi doit être démarré, avec le contrôle distant HTTP activé, et utiliser la base MyVideos choisie.</p><button class="secondary wide" type="button" data-test-connection="player-form">Tester la connexion Kodi</button><p id="player-form-result" class="connection-result wide" role="status" aria-live="polite" hidden></p><button type="submit" class="primary wide">Enregistrer l’instance</button></form></details>`:''}`;
    showDialog('connections-dialog');
    renderActivity();
  } catch(e) {toast(e.message);}
}
function connectionInput(form) {
  const data=new FormData(form);
  const config={host:String(data.get('host')||'').trim(),port:Number(data.get('port')),username:String(data.get('username')||'').trim(),password:data.get('password')||null,tls:data.has('tls')};
  if(form.getAttribute('id')==='kodi-form') return {config:{...config,database:String(data.get('database')||'').trim()},source_id:data.get('id')||null};
  return {...config,player_id:data.get('id')||null};
}
async function testConnection(form) {
  const input=connectionInput(form); const formId=form.getAttribute('id');
  const result=$(formId+'-result'); result.hidden=false; result.className='connection-result wide';
  result.textContent='Vérification de la connexion…';
  const controls=[...form.elements].map(control=>({control,disabled:control.disabled}));
  controls.forEach(({control})=>control.disabled=true);
  try {
    const tested=await api(`connections/${formId==='kodi-form'?'mariadb':'kodi'}/test`,{method:'POST',body:JSON.stringify(input)});
    result.textContent=tested.message+(tested.version?` Version ${tested.version}.`:'');
    result.classList.add('success-text'); return tested;
  } catch(e) {result.textContent=e.message;result.classList.add('error-text');throw e;}
  finally {controls.forEach(({control,disabled})=>control.disabled=disabled);}
}
document.addEventListener('input',event=>{
  const form=event.target.closest('#kodi-form,#player-form');
  if(form) {const result=$(form.getAttribute('id')+'-result');if(result) result.hidden=true;}
});
function fillForm(id,data) {
  const form=$(id); form.closest('details').open=true;
  for(const [key,value] of Object.entries(data)) {
    const el=form.elements.namedItem(key); if(!el) continue;
    if(el.type==='checkbox') el.checked=Boolean(value); else el.value=value??'';
  }
  form.scrollIntoView({block:'nearest',behavior:'smooth'});
}
document.addEventListener('submit',async event=>{
  const form=event.target; const formId=form.getAttribute('id');
  if(!['login-form','kodi-form','clz-form','player-form'].includes(formId)) return;
  event.preventDefault(); const button=form.querySelector('button[type="submit"]'); button.disabled=true;
  try {
    const data=new FormData(form);
    if(formId==='login-form') {
      await api('auth/session',{method:'POST',body:JSON.stringify({token:data.get('token')})}); form.reset(); $('login-error').textContent=''; await initialize();
    } else if(formId==='kodi-form') {
      const body={name:data.get('name'),type:'kodi',config:connectionInput(form).config};
      const id=data.get('id'); if(!id) await testConnection(form); const source=await api(id?`sources/${id}`:'sources',{method:id?'PUT':'POST',body:JSON.stringify(body)});
      await pollJob(await api(`sources/${source.id}/sync`,{method:'POST'}));
    } else if(formId==='clz-form') {
      let id=data.get('source_id');
      if(!id) { const name=prompt('Nom de cette collection','Collection Blu-ray'); if(!name) return; const source=await api('sources',{method:'POST',body:JSON.stringify({name,type:'clz'})}); id=source.id; }
      const body=new FormData(); body.set('source_id',id); body.set('file',data.get('file'));
      await pollJob(await api('imports/clz',{method:'POST',body}));
    } else {
      const id=data.get('id'); const {player_id,...config}=connectionInput(form); const body={name:data.get('name'),source_id:data.get('source_id'),...config};
      if(!id) await testConnection(form);
      await api(id?`players/${id}`:'players',{method:id?'PUT':'POST',body:JSON.stringify(body)}); toast('Instance enregistrée.'); await connections();
    }
  } catch(e) {if(formId==='login-form') $('login-error').textContent=e.message; else toast(e.message);}
  finally {button.disabled=false;}
});
document.addEventListener('click',async event=>{
  const button=event.target.closest('button'); if(!button) return;
  if(button.dataset.close) $(button.dataset.close).close();
  if(button.dataset.media) openDetail(button.dataset.media);
  if(button.dataset.genre) {$('genre').value=button.dataset.genre; $('detail-dialog').close();  loadMedia();}
  if(button.dataset.removeFormat) {
    state.formats=state.formats.filter(v=>v!==button.dataset.removeFormat);
    updateFormatSelections();loadMedia();return;
  }
  if(button.dataset.action==='connections') connections(); if(button.dataset.action==='reset') reset();
  if(button.dataset.action==='lists')openLists();
  if(button.dataset.openList)openLists(button.dataset.openList);
  if(button.id==='pairing-start') {
    button.disabled=true;
    try {
      const response=await api('pairing/start',{method:'POST',body:JSON.stringify({role:$('pairing-role').value})});
      $('pairing-code').textContent=`${response.code.slice(0,4)} ${response.code.slice(4)} · Valable 5 minutes`;
    }catch(e){toast(e.message);}finally{button.disabled=false;}
    return;
  }
  if(button.dataset.revokeDevice) {
    if(confirm('Révoquer cet appareil iOS ? Il devra être appairé de nouveau.')) {
      try {await api('pairing/devices/'+encodeURIComponent(button.dataset.revokeDevice),{method:'DELETE'});await connections();}
      catch(e){toast(e.message);}
    }
    return;
  }
  if(button.dataset.quick) {state.recent=button.dataset.quick==='recent'; $('watch-status').value=['all','recent'].includes(button.dataset.quick)?'':button.dataset.quick;  if(state.recent){$('sort').value='date_added';state.order='desc';$('sort-order').textContent='↓ Décroissant';}loadMedia();}
  if(!button.dataset.testConnection&&!button.dataset.sync&&!button.dataset.removeSource&&!button.dataset.removePlayer&&!button.dataset.editSource&&!button.dataset.editPlayer&&!button.dataset.testPlayer&&!['play-button','test-tmdb'].includes(button.id))return;
  const result=button.id==='play-button'?$('play-status'):button.id==='test-tmdb'?$('tmdb-test-result'):button.dataset.testPlayer?$(`player-result-${button.dataset.testPlayer}`):null;
  if(result){result.className='hint play-status';result.textContent=button.id==='play-button'?'Envoi de la lecture à Kodi…':'Vérification de la connexion…';}
  try {
    if(button.dataset.testConnection) {const form=$(button.dataset.testConnection); const fields=['host','port',...(form.getAttribute('id')==='kodi-form'?['username']:[])];if(fields.every(name=>form.elements.namedItem(name).reportValidity())) await testConnection(form);}
    if(button.dataset.sync) {button.disabled=true; await pollJob(await api(`sources/${button.dataset.sync}/sync`,{method:'POST'}));}
    if(button.dataset.removeSource && confirm('Retirer cette source et ses données importées ?')) {await api(`sources/${button.dataset.removeSource}`,{method:'DELETE'});await refresh();await connections();}
    if(button.dataset.removePlayer && confirm('Retirer cette instance Kodi ?')) {await api(`players/${button.dataset.removePlayer}`,{method:'DELETE'});await connections();}
    if(button.dataset.editSource) {const s=state.facets.sources.find(s=>s.id===button.dataset.editSource); fillForm('kodi-form',{id:s.id,name:s.name,...s.config});}
    if(button.dataset.editPlayer) {const p=state.players.find(p=>p.id===button.dataset.editPlayer); fillForm('player-form',p);}
    if(button.id==='test-tmdb'){button.disabled=true;const tested=await api('connections/tmdb/test',{method:'POST'});result.textContent=tested.message;result.className='success-text play-status';}
    if(button.dataset.testPlayer){
      button.disabled=true;const p=state.players.find(p=>p.id===button.dataset.testPlayer);
      if(!p)throw new Error('Choisissez une instance Kodi.');
      const tested=await api('connections/kodi/test',{method:'POST',body:JSON.stringify({player_id:p.id,host:p.host,port:p.port,username:p.username,tls:p.tls})});
      result.textContent=tested.message;result.className='success-text play-status';
    }
    if(button.id==='play-button') {savePlaybackDraft();button.disabled=true; await api(`players/${$('play-target').value}/play`,{method:'POST',body:JSON.stringify({media_id:state.detail.id,edition_id:$('play-edition').value,resume:$('play-resume').checked})});result.textContent='Lecture envoyée à Kodi. Si elle ne démarre pas, vérifiez sur Kodi ses accès au fichier du NAS.';result.className='success-text play-status';toast('Lecture envoyée à Kodi.');}
  }catch(e){if(result){result.textContent=e.message;result.className='error-text play-status';}toast(e.message);}finally{button.disabled=false;}
});
$('connections-button').addEventListener('click',connections);
$('lists-button').addEventListener('click',()=>openLists());
$('create-list-button').addEventListener('click',()=>openLists(state.listId,{create:true}));
$('logout-button').addEventListener('click',async()=>{try{await api('auth/session',{method:'DELETE'});showLogin();}catch(e){toast(e.message);}});
$('grid-view').addEventListener('click',()=>setView('grid')); $('list-view').addEventListener('click',()=>setView('list'));
$('toggle-filters').addEventListener('click',()=>{const hidden=!$('filters').hidden;$('filters').hidden=hidden;$('toggle-filters').setAttribute('aria-expanded',!hidden);savePreferences();});
$('reset-filters').addEventListener('click',reset);
let searchTimer; $('search').addEventListener('input',()=>{savePreferences();clearTimeout(searchTimer);searchTimer=setTimeout(()=>{loadMedia();},250);});
['kind','source-type','genre','collection','watch-status','sort'].forEach(id=>$(id).addEventListener('change',()=>{loadMedia();}));
for(const id of Object.values(formatSelects())) $(id).addEventListener('change',()=>{
  const value=canonicalFormat($(id).value);
  if(value&&!state.formats.includes(value)&&state.formats.length<20)state.formats.push(value);
  updateFormatSelections();loadMedia();
});
$('sort-order').addEventListener('click',()=>{state.order=state.order==='asc'?'desc':'asc';$('sort-order').textContent=state.order==='asc'?'↑ Croissant':'↓ Décroissant';loadMedia();});
$('load-more').addEventListener('click',()=>loadMedia({append:state.items.length>0}));
document.addEventListener('keydown',event=>{if(event.key==='/'&&!['INPUT','SELECT','TEXTAREA'].includes(event.target.tagName)&&!$('app').hidden&&!document.querySelector('dialog[open]')){event.preventDefault();$('search').focus();}});
for(const dialog of document.querySelectorAll('dialog')) {
  dialog.addEventListener('close',releasePage);
  dialog.addEventListener('click',event=>{if(event.target===dialog){const r=dialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)dialog.close();}});
}
async function enrichDetail(force) {
  const id=state.detail?.id;if(!id||state.enrichingIds.has(id))return;
  state.enrichingIds.add(id);
  const seq=state.detailSeq; const personalSeq=state.personalSeq; const status=$('enrichment-status');
  if(status)status.textContent='Récupération des personnes, portraits et bandes-annonces…';
  const button=$('refresh-metadata');if(button){button.disabled=true;button.classList.add('is-loading');}
  let enriched=false;
  try {
    const item=await api(`media/${encodeURIComponent(id)}/enrich?refresh=${force}`,{method:'POST'});
    enriched=true;
    if(seq===state.detailSeq&&$('detail-dialog').open){
      if((state.personalSeq||0)!==personalSeq)Object.assign(item,{personal:state.detail.personal,user_rating:state.detail.user_rating,watch_status:state.detail.watch_status});
      const episodes=$('episode-list')?.innerHTML;
      const ratingDraft=$('rating-popover')?.dataset.open?{value:$('rating-slider').value,focused:$('rating-popover').contains(document.activeElement)}:null;
      renderDetail(item);
      if(ratingDraft){showRatingPopover(ratingDraft.focused);$('rating-slider').value=ratingDraft.value;previewRating(Number(ratingDraft.value));}
      if(episodes&&$('episode-list'))$('episode-list').innerHTML=episodes;
      if($('play-dialog').open)updatePlayEdition();
    }
  } catch(e) {if(seq===state.detailSeq){if($('enrichment-status'))$('enrichment-status').textContent=e.message;toast(e.message);}}
  finally {state.enrichingIds.delete(id);if(enriched&&seq===state.detailSeq&&$('enrichment-status'))$('enrichment-status').textContent=`Métadonnées TMDB mises en cache le ${date(state.detail.enriched_at)}.`;if(seq===state.detailSeq&&$('refresh-metadata')){$('refresh-metadata').disabled=state.role!=='admin';$('refresh-metadata').classList.remove('is-loading');}}
}
async function chooseList() {
  try {
    const lists=await api('lists');const item=state.detail;
    $('choose-list-content').innerHTML=`<p class="muted">${esc(item.title)}</p><div class="choose-list-buttons">${lists.map(l=>`<button data-choose-list="${esc(l.id)}" ${item.lists.some(current=>current.id===l.id)?'disabled':''}>${esc(l.name)} <span class="muted">· ${l.item_count}${item.lists.some(current=>current.id===l.id)?' · Déjà ajouté':''}</span></button>`).join('')||'<p class="hint">Créez votre première liste ci-dessous.</p>'}</div><form id="choose-new-list" class="inline-form"><label>Nouvelle liste<input name="name" required maxlength="120" placeholder="Films à voir"></label><button type="submit" class="primary">+ Créer et ajouter</button></form>`;
    showDialog('choose-list-dialog');
  } catch(e){toast(e.message);}
}
async function openLists(selected=state.listId,{create=false}={}) {
  try {
    const lists=await api('lists');state.listId=lists.some(l=>l.id===selected)?selected:lists[0]?.id;
    savePreferences();
    const current=lists.find(l=>l.id===state.listId);const admin=state.role==='admin';
    const newListForm=admin?`<form id="new-list-form" class="inline-form" ${current&&!create?'hidden':''}><label>Nom de la liste<input name="name" required maxlength="120" placeholder="Films à voir"></label><button class="primary" type="submit">Créer la liste</button></form>`:'';
    $('lists-content').innerHTML=`<div class="list-toolbar"><label>Liste<select id="selected-list">${lists.map(l=>`<option data-name="${esc(l.name)}" value="${esc(l.id)}" ${l.id===state.listId?'selected':''}>${esc(l.name)} · ${l.item_count}</option>`).join('')||'<option>Aucune liste</option>'}</select></label>${admin?'<button id="new-list-toggle" class="secondary">+ Nouvelle liste</button>':''}</div>${!admin?'<p class="metadata-status hint">Accès en lecture seule : utilisez le jeton administrateur pour créer une liste ou modifier son contenu.</p>':''}<p class="hint">Ajoutez des films et des séries, puis choisissez leur ordre dans chaque liste. Ces sélections sont conservées après les synchronisations.</p>${newListForm}${current?`<div class="list-toolbar"><p class="hint">${esc(current.description||'Votre sélection, dans l’ordre que vous choisissez.')}</p>${admin?'<button id="rename-list" class="text-button">Renommer</button><button id="delete-list" class="text-button">Supprimer</button>':''}</div><div class="list-toolbar"><label>Tri<select id="list-sort"><option value="position">Ordre manuel</option><option value="title">Titre</option><option value="year">Année</option><option value="date_added">Date d’ajout</option><option value="rating">Note</option><option value="user_rating">Votre note</option></select></label><button id="list-sort-order" class="secondary">${state.listOrder==='asc'?'↑ Croissant':'↓ Décroissant'}</button>${admin?'<button id="list-add-button" class="primary">+ Ajouter des médias</button>':''}</div>${admin?'<section id="list-search-area" class="list-search-area" hidden><label>Rechercher dans le catalogue<input id="list-search" type="search" placeholder="Titre, personne, genre…" autocomplete="off"></label><div id="list-search-results" aria-live="polite"></div></section>':''}<p id="list-count" class="hint"></p><div id="list-items" class="saved-list-items" aria-live="polite"></div><button id="list-load-more" class="secondary" hidden>Charger la suite</button>`:'<p class="empty-state">Créez une liste pour organiser vos prochains visionnages.</p>'}`;
    showDialog('lists-dialog');
    if(current){$('list-sort').value=state.listSort;await loadList();}
    if(admin&&(create||!current))$('new-list-form').elements.name.focus();
  } catch(e){toast(e.message);}
}
function listRow(item,index) {
  const manual=state.listSort==='position'&&state.listOrder==='asc'&&state.role==='admin';
  return `<div class="saved-list-row"><span class="list-position">${index+1}</span><button class="saved-media" data-media="${esc(item.id)}">${poster(item)}<span><strong>${esc(item.title)}</strong><small>${[item.year,kindLabels[item.kind],stateLabels[item.watch_status]].filter(Boolean).map(esc).join(' · ')}${item.user_rating!=null?' · Votre note : '+item.user_rating+'/10':''}</small></span></button><div class="saved-row-actions">${manual?`<button data-move-list="${esc(item.id)}" data-direction="-1" aria-label="Monter ${esc(item.title)}" ${index===0?'disabled':''}>↑</button><button data-move-list="${esc(item.id)}" data-direction="1" aria-label="Descendre ${esc(item.title)}" ${index===state.listTotal-1?'disabled':''}>↓</button>`:''}${state.role==='admin'?`<button data-remove-list-media="${esc(item.id)}" aria-label="Retirer ${esc(item.title)} de la liste">×</button>`:''}</div></div>`;
}
async function loadList({append=false}={}) {
  const seq=++state.listSeq;const id=state.listId;
  if(!id||!$('list-items'))return;
  const offset=append?state.listItems.length:0;
  try {
    const page=await api(`lists/${id}/items?sort=${state.listSort}&order=${state.listOrder}&offset=${offset}&limit=60`);
    if(seq!==state.listSeq||id!==state.listId)return;
    if(append&&page.revision!==state.listRevision){toast('La liste a changé, elle a été rechargée.');return loadList();}
    state.listItems=append?[...state.listItems,...page.items]:page.items;state.listRevision=page.revision;state.listTotal=page.total;
    const option=$('selected-list')?.selectedOptions[0];if(option?.dataset.name)option.textContent=`${option.dataset.name} · ${page.total}`;
    $('list-items').innerHTML=state.listItems.map(listRow).join('')||'<p class="hint">Cette liste est vide. Ajoutez des médias avec le bouton +.</p>';
    $('list-count').textContent=`${state.listItems.length} / ${page.total} médias`;
    $('list-load-more').hidden=!page.has_more;attachImageFallbacks($('list-items'));
  } catch(e){toast(e.message);}
}
let listSearchSeq=0,listSearchController;
async function searchList() {
  if(!$('list-search'))return;
  const query=$('list-search').value.trim();const seq=++listSearchSeq;const listId=state.listId;
  listSearchController?.abort();listSearchController=new AbortController();
  if(!query){$('list-search-results').innerHTML='';return;}
  $('list-search-results').textContent='Recherche…';
  try {
    const page=await api(`media?q=${encodeURIComponent(query)}&limit=20&sort=title&order=asc`,{signal:listSearchController.signal});
    if(seq!==listSearchSeq||listId!==state.listId||!$('list-search-results'))return;
    $('list-search-results').innerHTML=page.items.map(item=>`<div class="search-list-result"><span><strong>${esc(item.title)}</strong><small>${item.year||''} · ${esc(kindLabels[item.kind])}</small></span><button data-add-list-media="${esc(item.id)}" aria-label="Ajouter ${esc(item.title)}" ${state.listItems.some(i=>i.id===item.id)?'disabled':''}>+</button></div>`).join('')+(page.has_more?'<p class="hint">20 premiers résultats. Précisez votre recherche pour retrouver les autres.</p>':'')||'<p class="hint">Aucun résultat.</p>';
  } catch(e){if(e.name!=='AbortError'&&seq===listSearchSeq)toast(e.message);}
}
async function moveList(id,direction) {
  const listId=state.listId;const ids=[];let offset=0;let revision=null;
  // Obtain a full consistent revision before sending the complete permutation.
  while(true){
    const page=await api(`lists/${listId}/items?sort=position&order=asc&limit=200&offset=${offset}`);
    if(revision!==null&&revision!==page.revision)throw new Error('La liste a changé. Rechargez-la avant de la réordonner.');
    revision=page.revision;ids.push(...page.items.map(item=>item.id));offset+=page.items.length;
    if(!page.has_more)break;
    if(ids.length>10000)throw new Error('Cette liste dépasse la limite de 10 000 médias pour le réordonnancement.');
  }
  const index=ids.indexOf(id);const other=index+direction;
  if(index<0||other<0||other>=ids.length)return;
  [ids[index],ids[other]]=[ids[other],ids[index]];
  await api(`lists/${listId}/order`,{method:'PUT',body:JSON.stringify({media_ids:ids,revision})});
  await loadList();
}
document.addEventListener('submit',async event=>{
  const form=event.target;const id=form.getAttribute('id');
  if(!['new-list-form','choose-new-list'].includes(id))return;
  event.preventDefault();const button=form.querySelector('button[type="submit"]');button.disabled=true;
  try {
    const data=new FormData(form);
    {
      const name=String(data.get('name')).trim();if(!name)return;
      const list=await api('lists',{method:'POST',body:JSON.stringify({name})});
      if(id==='choose-new-list'){
        const mid=state.detail.id;await api(`lists/${list.id}/items`,{method:'POST',body:JSON.stringify({media_id:mid})});
        $('choose-list-dialog').close();renderDetail(await api(`media/${mid}`));toast('Liste créée et média ajouté.');
      }else await openLists(list.id);
    }
  }catch(e){toast(e.message);}finally{button.disabled=false;}
});
document.addEventListener('click',async event=>{
  const button=event.target.closest('button');if(!button)return;
  if(!['refresh-metadata','add-to-list','new-list-toggle','list-add-button','rename-list','delete-list','list-load-more','list-sort-order'].includes(button.id)&&!button.dataset.chooseList&&!button.dataset.addListMedia&&!button.dataset.removeListMedia&&!button.dataset.moveList&&!button.dataset.copyFile)return;
  try {
    if(button.id==='refresh-metadata'){hideRatingPopover();await enrichDetail(true);}
    if(button.id==='add-to-list')await chooseList();
    if(button.dataset.chooseList){
      button.disabled=true;const mid=state.detail.id;
      await api(`lists/${button.dataset.chooseList}/items`,{method:'POST',body:JSON.stringify({media_id:mid})});
      $('choose-list-dialog').close();renderDetail(await api(`media/${mid}`));if($('lists-dialog').open)await openLists();toast('Média ajouté à la liste.');
    }
    if(button.id==='new-list-toggle'){$('new-list-form').hidden=false;$('new-list-form').elements.name.focus();}
    if(button.id==='list-add-button'){$('list-search-area').hidden=!$('list-search-area').hidden;if(!$('list-search-area').hidden)$('list-search').focus();}
    if(button.id==='rename-list'){
      const name=prompt('Nom de la liste',$('selected-list').selectedOptions[0].textContent.replace(/ · \d+$/,''));
      if(name?.trim()){const existing=(await api('lists')).find(l=>l.id===state.listId);await api(`lists/${state.listId}`,{method:'PUT',body:JSON.stringify({name:name.trim(),description:existing.description})});await openLists();}
    }
    if(button.id==='delete-list'&&confirm('Supprimer cette liste ? Les médias restent dans le catalogue.')){await api(`lists/${state.listId}`,{method:'DELETE'});await openLists();}
    if(button.id==='list-load-more'){button.disabled=true;await loadList({append:true});}
    if(button.id==='list-sort-order'){state.listOrder=state.listOrder==='asc'?'desc':'asc';button.textContent=state.listOrder==='asc'?'↑ Croissant':'↓ Décroissant';savePreferences();await loadList();}
    if(button.dataset.addListMedia){button.disabled=true;await api(`lists/${state.listId}/items`,{method:'POST',body:JSON.stringify({media_id:button.dataset.addListMedia})});await loadList();await searchList();}
    if(button.dataset.removeListMedia){button.disabled=true;await api(`lists/${state.listId}/items/${button.dataset.removeListMedia}`,{method:'DELETE'});await loadList();if($('list-search')?.value)await searchList();}
    if(button.dataset.moveList){button.disabled=true;await moveList(button.dataset.moveList,Number(button.dataset.direction));}
    if(button.dataset.copyFile){
      const file=state.detail.editions.find(e=>e.id===button.dataset.copyFile).metadata.file;
      if(navigator.clipboard&&window.isSecureContext){await navigator.clipboard.writeText(file);toast('Chemin copié.');}
      else prompt('Copiez le chemin du fichier',file);
    }
  }catch(e){toast(e.message);}finally{button.disabled=false;}
});
document.addEventListener('change',event=>{
  if(event.target.id==='play-target'){updatePlayEdition();if($('play-status'))$('play-status').textContent='';}
  if(['play-target','play-edition','play-resume'].includes(event.target.id))savePlaybackDraft();
  if(event.target.id==='selected-list')openLists(event.target.value);
  if(event.target.id==='list-sort'){state.listSort=event.target.value;savePreferences();loadList();}
});
let listSearchTimer;
document.addEventListener('input',event=>{if(event.target.id==='list-search'){clearTimeout(listSearchTimer);listSearchTimer=setTimeout(searchList,250);}});

// The rating editor opens on hover/focus, and remains usable on touch screens.
document.addEventListener('pointerover',event=>{
  if(event.target.closest('#rating-button'))showRatingPopover();
  if(event.target.closest('#rating-popover'))clearTimeout(ratingCloseTimer);
});
document.addEventListener('pointerout',event=>{
  if(!event.target.closest('#rating-button,#rating-popover'))return;
  const next=event.relatedTarget;
  if(next&&($('rating-button')?.contains(next)||$('rating-popover')?.contains(next)))return;
  scheduleRatingClose();
});
document.addEventListener('focusin',event=>{
  if(event.target.id==='rating-button')showRatingPopover();
  if(event.target.closest('#rating-popover'))clearTimeout(ratingCloseTimer);
});
document.addEventListener('focusout',event=>{
  if(!event.target.closest('#rating-button,#rating-popover'))return;
  const next=event.relatedTarget;
  if(!next||!($('rating-button')?.contains(next)||$('rating-popover')?.contains(next)))hideRatingPopover();
});
document.addEventListener('keydown',event=>{
  if(event.key==='Escape'&&$('rating-popover')?.dataset.open){event.preventDefault();event.stopPropagation();$('rating-button').focus({preventScroll:true});hideRatingPopover();}
},true);
document.addEventListener('click',event=>{
  if(!event.target.closest('#rating-button,#rating-popover'))hideRatingPopover();
  if(event.target.closest('[data-trailer]'))$('trailers-dialog').close();
  const button=event.target.closest('button');if(!button||button.disabled)return;
  if(button.id==='rating-button')showRatingPopover(true);
  if(button.dataset.rating)savePersonal({user_rating:Number(button.dataset.rating)});
  if(button.hasAttribute('data-rating-reset'))savePersonal({user_rating:null});
  if(button.id==='watch-button')savePersonal({watched:state.detail.watch_status!=='watched'});
  if(button.id==='open-playback')openPlayback();
  if(button.id==='open-trailers')openTrailers();
});
document.addEventListener('input',event=>{if(event.target.id==='rating-slider')previewRating(Number(event.target.value));});
document.addEventListener('change',event=>{if(event.target.id==='rating-slider')savePersonal({user_rating:Number(event.target.value)});});
$('detail-dialog').addEventListener('scroll',hideRatingPopover);
$('detail-dialog').addEventListener('close',()=>{hideRatingPopover();if($('play-dialog').open)$('play-dialog').close();if($('trailers-dialog').open)$('trailers-dialog').close();});
$('play-dialog').addEventListener('close',savePlaybackDraft);
window.addEventListener('resize',positionRatingPopover);

initialize();

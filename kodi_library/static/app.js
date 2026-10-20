import { startKodiRemote, stopKodiRemote, showKodiRemote } from './remote.js';
import { startI18n, displayLocale, tr, localizedPrompt, localizedConfirm } from './i18n.js';
startI18n();
const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const stateLabels = {watched:tr('Vu'),unwatched:tr('Non vu'),in_progress:tr('En cours'),unknown:tr('État non renseigné')};
const kindLabels = {movie:tr('Film'),tvshow:tr('Série'),episode:tr('Épisode')};
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
const canonicalFormat = value => {
  const label=String(value).trim();
  return /^(4k uhd|4k blu-ray|blu-ray 4k|uhd blu-ray|ultra hd blu-ray)$/i.test(label)?'Blu-ray 4K':/^4k$/i.test(label)?'4K':label;
};
function formatCategory(label) {
  if (/^(dolby atmos|dolby truehd|dolby digital|dts(?:[-:]|$)|aac|flac|pcm|lpcm|ac3|eac3|mp3|opus)/i.test(label)) return 'audio';
  if (/^(blu-ray|dvd|hd dvd|uhd blu-ray|ultra hd blu-ray|4k uhd)/i.test(label)) return 'disc';
  return 'video';
}
// Media formats are always multi-select chips, grouped video → audio → discs.
function updateFormatSelections() {
  const selected=new Set(state.formats);
  const categories={video:'video-formats',audio:'audio-formats',disc:'disc-formats'};
  const values=[...new Set((state.facets?.formats||[]).map(canonicalFormat))]
    .sort((a,b)=>a.localeCompare(b,displayLocale(),{numeric:true}));
  for(const [category,id] of Object.entries(categories)) {
    $(id).innerHTML=values.filter(v=>formatCategory(v)===category).map(label=>{
      const isSelected=selected.has(label);
      return `<button type="button" class="format-option" data-toggle-format="${esc(label)}" aria-label="${esc(label)}" aria-pressed="${isSelected}" title="${esc(label)}">${formatBadges([label])}</button>`;
    }).join('')||'<span class="format-help">Aucun format disponible.</span>';
  }
  $('format-selection-count').textContent=state.formats.length?`${state.formats.length} sélectionné${state.formats.length>1?'s':''} · filtres cumulés (ET)`:'Sélectionnez plusieurs formats pour les cumuler (ET).';
}

const state = {csrf:null,role:'read',view:savedView==='list'?'list':'grid',order:preferences.order==='asc'?'asc':'desc',limit:60,recent:preferences.recent===true,formats:[],deepSearch:preferences.deepSearch===true,facets:null,players:[],tmdbAvailable:false,jobs:[],activityPollBusy:false,activityWasRunning:false,personalUpdateState:null,personalRemoteFailure:false,personalPollBusy:false,personalSaveCount:0,seq:0,controller:null,detail:null,detailSeq:0,personalSeq:0,personalPending:new Set(),enrichingIds:new Set(),personSearch:null,seasons:[],playbackDraft:null,playbackMediaId:null,listId:typeof preferences.listId==='string'?preferences.listId:null,listItems:[],listRevision:0,listSort:['position','title','year','date_added','rating','user_rating'].includes(preferences.listSort)?preferences.listSort:'position',listOrder:preferences.listOrder==='desc'?'desc':'asc',listTotal:0,listSeq:0,items:[],ids:new Set(),nextOffset:0,total:0,hasMore:false,loading:false,loadError:false};
function savePreferences() {
  if(!filtersRestored)return;
  writeStorage(preferenceKey,JSON.stringify({filters:Object.fromEntries(filterIds.map(id=>[id,$(id).value])),formats:state.formats,deepSearch:state.deepSearch,order:state.order,recent:state.recent,view:state.view,listId:state.listId,listSort:state.listSort,listOrder:state.listOrder}));
}
function updateSearchClearButton() {
  $('clear-search').hidden=!$('search').value&&!state.personSearch;
}
function restoreFilters() {
  if(filtersRestored)return;
  for(const id of filterIds){
    const value=id==='kind'&&preferences.filters?.kind==='episode'?'':preferences.filters?.[id];
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
  // Always start collapsed, even if an older version persisted the expanded state.
  $('filters-panel').open=false;
  $('deep-search').checked=state.deepSearch;
  $('toggle-filters').setAttribute('aria-expanded','false');
  filtersRestored=true;
  updateSearchClearButton();
}
const moreObserver = 'IntersectionObserver' in window ? new IntersectionObserver(entries=>{
  if(entries.some(entry=>entry.isIntersecting)&&!state.loading&&!state.loadError&&state.hasMore&&!$('app').hidden&&!document.querySelector('dialog[open]')) loadMedia({append:true});
},{rootMargin:'600px 0px'}) : null;
let toastTimer;
function toast(message) { $('toast').textContent=tr(message); $('toast').hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('toast').hidden=true,5500); }
function error(message) { $('error-banner').textContent=tr(message); $('error-banner').hidden=false; }
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
function showLogin() { stopKodiRemote(); ++state.seq; state.controller?.abort(); state.loading=false; moreObserver?.disconnect(); $('app').hidden=true; $('login').hidden=false; }
function date(value) { if (!value) return '—'; const d=new Date(value); return Number.isNaN(d.getTime()) ? '—' : d.toLocaleDateString(displayLocale()); }
function dateTime(value) {
  if(!value)return '—';
  const parsed=new Date(value);
  return Number.isNaN(parsed.getTime())?'—':parsed.toLocaleString(displayLocale(),{dateStyle:'short',timeStyle:'short'});
}
function writebackStatusLabel(status){
  return ({updated:'Synchronisé',pending:'En attente',failed:'Échec',unavailable:'Indisponible'})[status]||'Statut inconnu';
}
function writebackHistoryRow(entry, historical=false){
  const timestamp=dateTime(historical?entry.created_at:entry.updated_at);
  const source=entry.source_name||'Source';
  const title=entry.media_title||'Média inconnu';
  const status=writebackStatusLabel(entry.status);
  const method=entry.method||'—';
  const fields=historical?entry.fields:entry.pending_fields;
  const detail=Array.isArray(fields)&&fields.length?'<span>Champs : '+esc(fields.join(', '))+'</span>':'';
  const attempts=historical&&Array.isArray(entry.attempts)&&entry.attempts.length
    ?'<span>Tentatives : '+esc(entry.attempts.map(v=>typeof v==='string'?v:JSON.stringify(v)).join(' · '))+'</span>':'';
  return `<li class="writeback-history-row"><div class="writeback-history-main"><strong>${esc(title)}</strong><span class="writeback-result ${esc(entry.status||'unavailable')}">${esc(status)}</span></div><div class="writeback-history-meta"><span>${esc(source)}</span><time datetime="${esc(historical?entry.created_at:entry.updated_at||'')}">${esc(timestamp)}</time><span>Méthode : ${esc(method)}</span>${detail}${attempts}</div>${entry.message?'<p class="hint">'+esc(entry.message)+'</p>':''}</li>`;
}
function playbackTime(seconds) {
  const total=Math.max(0,Math.floor(Number(seconds)||0));
  const hours=Math.floor(total/3600),minutes=Math.floor(total%3600/60),secs=total%60;
  return hours?`${hours}:${String(minutes).padStart(2,'0')}:${String(secs).padStart(2,'0')}`:`${minutes}:${String(secs).padStart(2,'0')}`;
}
const collectionTypes=['clz','mymovies','filmotech'];
function sourceName(type) { return ({kodi:'Kodi',clz:'CLZ',mymovies:'MyMovies',filmotech:'Filmotech'})[type]||type; }
function sourceIcon(type) {
  const shapes={
    clz:'<path d="m3 7 9-5 9 5-9 5Z"/><path d="m3 12 9 5 9-5M3 17l9 5 9-5" fill="none" stroke="currentColor" stroke-width="2"/>',
    mymovies:'<rect x="2" y="4" width="20" height="16" rx="3"/><path d="m10 8 6 4-6 4Z" fill="var(--bg,#121815)"/>',
    filmotech:'<rect x="2" y="3" width="20" height="18" rx="2"/><path d="M7 3v18M17 3v18M2 8h5m-5 8h5M17 8h5m-5 8h5" fill="none" stroke="var(--bg,#121815)" stroke-width="2"/>'
  };
  if(type==='kodi') return `<img class="source-icon" src="${esc(url('static/kodi-logo.svg'))}" alt="" width="18" height="18">`;
  return `<svg class="source-icon source-icon-${esc(type)}" viewBox="0 0 24 24" aria-hidden="true" fill="currentColor">${shapes[type]||shapes.clz}</svg>`;
}
function writebackIndicatorMarkup(item) {
  const states=item.writeback_state||[];
  if(!states.length)return '';
  return '<span class="writeback-indicators" aria-label="Synchronisations vers les référentiels">'+
    states.map(entry=>{
      const mark=entry.status==='updated'?'✓':entry.status==='pending'?'↻':'!';
      const label=`${entry.source_name} : ${entry.status==='updated'?'synchronisé':entry.status==='pending'?'en attente de rejeu':'échec'}${entry.message?' · '+entry.message:''}`;
      return `<span class="writeback-chip ${esc(entry.status)}" title="${esc(label)}" aria-label="${esc(label)}">${sourceIcon(entry.source_type)}<span class="writeback-mark" aria-hidden="true">${mark}</span></span>`;
    }).join('')+'</span>';
}
function renderWritebackIndicators(){
  const target=$('writeback-indicators');
  if(target&&state.detail)target.innerHTML=writebackIndicatorMarkup(state.detail);
}
async function refreshWritebackQueue(){
  const queue=$('writeback-queue'),summary=$('writeback-summary');
  if(!queue||!summary)return;
  try{
    const data=await api('writebacks/status');
    summary.textContent=data.pending_count?
      `${data.pending_count} synchronisation(s) en attente de rejeu.`:
      'Aucune synchronisation en attente. Les imports ne réécrivent jamais les sources.';
    queue.innerHTML=(data.pending||[]).map(item=>
      `<div class="connection-card"><div><strong>${sourceIcon(item.source_type)} ${esc(item.source_name)}</strong><p>${esc(item.media_title)} · ${esc(Object.keys(item.changes).join(', '))} · ${esc(date(item.updated_at))}</p>${item.last_error?`<p class="error-text">${esc(item.last_error)}</p>`:''}</div><div class="connection-actions"><button type="button" data-retry-writeback="${esc(item.id)}" ${state.role==='admin'?'':'disabled'}>Rejouer</button></div></div>`).join('')||'<p class="hint">Aucun échec à rejouer.</p>';
    const all=$('retry-all-writebacks');if(all)all.disabled=!data.pending_count||state.role!=='admin';
    const recent=$('writeback-recent');
    if(recent){
      const entries=data.recent||[];
      recent.innerHTML=entries.length
        ?'<ul class="writeback-history-list">'+entries.map(item=>writebackHistoryRow(item)).join('')+'</ul>'
        :'<p class="hint">Aucune synchronisation sortante récente.</p>';
    }
  }catch(e){summary.textContent='Chargement impossible : '+e.message;}
}
// Global status remains in the header when a media detail is dismissed.
function renderPersonalStatus(){
  const node=$('personal-update-indicator'),status=state.personalUpdateState;
  node.hidden=!status||$('app').hidden;
  if(!status)return;
  const labels={saving:'Enregistrement…',pending:'Synchronisation externe en attente',saved:'Modifications enregistrées',failed:'Échec de l’enregistrement',remoteFailed:'Modifications locales enregistrées · échec externe'};
  $('personal-update-icon').textContent=['saving','pending'].includes(status)?'↻':status==='saved'?'✓':'!';
  $('personal-update-label').textContent=labels[status];
  node.className='personal-update-indicator '+status;
  node.title=labels[status];
  node.setAttribute('aria-label',labels[status]);
}
function beginPersonalSave(){
  state.personalSaveCount++;
  state.personalUpdateState='saving';
  state.personalRemoteFailure=false;
  renderPersonalStatus();
}
function endPersonalSave(failed=false,checkRemote=false){
  state.personalSaveCount=Math.max(0,state.personalSaveCount-1);
  if(state.personalSaveCount)return;
  state.personalUpdateState=failed?'failed':checkRemote?'pending':'saved';
  renderPersonalStatus();
  if(!failed&&checkRemote)pollPersonalWritebacks();
}
async function pollPersonalWritebacks(){
  if(state.personalPollBusy||state.personalSaveCount||state.personalUpdateState!=='pending'||$('app').hidden)return;
  state.personalPollBusy=true;
  try{
    const result=await api('writebacks/status');
    if(state.personalUpdateState!=='pending'||state.personalSaveCount)return;
    if(result.pending_count===0){
      state.personalUpdateState=state.personalRemoteFailure?'remoteFailed':'saved';
      renderPersonalStatus();
    }
  }catch(e){
    // Unreachable server does not imply a completed synchronization.
  }finally{state.personalPollBusy=false;}
}
$('personal-update-indicator').addEventListener('click',()=>pollPersonalWritebacks());
function sourceBadge(type,label) { return `<span class="source-badge" title="${esc(label||sourceName(type))}">${sourceIcon(type)}<span>${esc(label||sourceName(type))}</span></span>`; }
function badges(item) {
  return (item.media_sources?.length?item.media_sources.map(s=>sourceBadge(s.type,s.type_count>1?s.name:sourceName(s.type))):item.source_types.map(s=>sourceBadge(s))).join('');
}
function seriesProgress(item) {
  if(item.kind!=='tvshow')return null;
  const total=Number(item.episode_count),watched=Number(item.watched_episode_count);
  if(!Number.isFinite(total)||total<=0||!Number.isFinite(watched))return null;
  const count=Math.max(0,Math.min(total,watched));
  return {total,watched:count,partial:count>0&&count<total,complete:count===total};
}
function watchDisplayLabel(item) {
  const progress=seriesProgress(item);
  if(progress?.partial)return `Partiellement vu (${progress.watched}/${progress.total} épisodes)`;
  if(progress?.complete)return `Vu (${progress.watched}/${progress.total} épisodes)`;
  return stateLabels[item.watch_status];
}
function seriesProgressMarkup(item) {
  const progress=seriesProgress(item);
  if(!progress||!progress.watched)return '';
  const label=watchDisplayLabel(item);
  return `<span class="series-progress-chip ${progress.partial?'partial':'complete'}" role="status" title="${esc(label)}" aria-label="${esc(label)}"><span aria-hidden="true">${progress.partial?'◐':'✓'}</span> ${progress.watched}/${progress.total} épisodes vus</span>`;
}
function poster(item, detail=false) {
  const progress=item.total_seconds>0 ? Math.min(100,100*item.resume_seconds/item.total_seconds):0;
  const series=seriesProgress(item);
  const stateIcon=series?.partial
    ? `<span class="state partial" title="${esc(watchDisplayLabel(item))}" aria-label="${esc(watchDisplayLabel(item))}">◐</span>`
    :series?.complete||item.watch_status==='watched'
    ? '<span class="state" title="Vu" aria-label="Vu">✓</span>'
    :item.watch_status==='in_progress'
    ? '<span class="state progress" title="En cours" aria-label="En cours">◐</span>':'';
  return `<div class="poster"><span class="poster-placeholder" aria-hidden="true">${esc(item.title.slice(0,2).toUpperCase())}</span>${item.artwork.poster?`<img src="${esc(url(item.artwork.poster))}" alt="" ${detail?'':'loading="lazy"'}>`:''}${!detail&&item.rating?`<span class="rating">★ ${Number(item.rating).toFixed(1)}</span>`:''}${!detail?stateIcon:''}${(series?.partial||progress>0&&item.watch_status==='in_progress')?`<span class="progress-track ${series?.partial?'series-track':''}"><span style="width:${series?.partial?100*series.watched/series.total:progress}%"></span></span>`:''}</div>`;
}
const formatLogos = {'4K':['4k','video'],'Blu-ray 4K':['bluray4k','disc'],'8K':['8k','video'],'1080p':['1080p','video'],'720p':['720p','video'],'3D':['3d','video'],'Blu-ray 3D':['3d','disc'],'Blu-ray':['bluray','disc'],'DVD':['dvd','disc'],'Dolby Vision':['vision','hdr'],'HDR10':['hdr10','hdr'],'HDR10+':['hdr10plus','hdr'],'HLG':['hlg','hdr'],'Dolby Atmos':['atmos','audio'],'Dolby TrueHD':['truehd','audio'],'DTS-HD MA':['dtshdma','audio'],'DTS-HD':['dtshd','audio'],'DTS:X':['dtsx','audio'],'DTS':['dts','audio'],'Dolby Digital':['dd','audio'],'Dolby Digital+':['ddplus','audio'],'AAC':['aac','audio'],'FLAC':['flac','audio'],'Opus':['opus','audio']};

// Reuse the exact PNG blobs in iOS Assets.xcassets for branded formats.
// New native PDF vector badges use the equivalent SVG symbols below because
// browser <img> does not reliably render PDFs.
const formatImages = {
  '4K':'format-4k','3D':'format-3d','Blu-ray 3D':'format-3d',
  'Blu-ray':'format-blu-ray','Dolby Vision':'format-dolby-vision',
  'HDR10':'format-hdr10','HDR10+':'format-hdr10-plus','HLG':'format-hlg',
  'Dolby Atmos':'format-dolby-atmos','Dolby TrueHD':'format-dolby-truehd',
  'Dolby Digital':'format-dolby-digital','Dolby Digital+':'format-dolby-digital-plus',
  'DTS':'format-dts','DTS-HD':'format-dts-hd','DTS-HD MA':'format-dts-hd-ma',
  'DTS:X':'format-dts-x','AAC':'format-aac','FLAC':'format-flac','Opus':'format-opus'
};
function formatBadges(values, compact=false, provenance=null) {
  const seen=new Set();
  const order={video:0,hdr:0,audio:1,disc:2};
  const labels=values.map(canonicalFormat).filter(label=>{const key=formatLogos[label]?.[0]||label;if(seen.has(key))return false;seen.add(key);return true;})
    .sort((a,b)=>(order[formatLogos[a]?.[1]]??0)-(order[formatLogos[b]?.[1]]??0)||a.localeCompare(b,displayLocale(),{numeric:true}));
  const visible=compact?labels.slice(0,4):labels;
  return visible.map(label=>{
    const logo=formatLogos[label];
    const sources=provenance?.get(label);
    const description=sources?.length?`${label} · ${sources.join(', ')}`:label;
    return logo?`<span class="format-badge ${logo[1]}" title="${esc(description)}" aria-label="${esc(description)}">${formatImages[label]?`<img src="${esc(url('static/format-icons/'+formatImages[label]+'.png'))}" alt="" loading="lazy">`:`<svg aria-hidden="true" viewBox="0 0 112 36"><use href="${esc(url('static/format-logos.svg'))}#${logo[0]}"></use></svg>`}</span>`:`<span class="format-chip" title="${esc(description)}">${esc(label)}</span>`;
  }).join('')+(compact&&labels.length>4?`<span class="format-overflow" title="${esc(labels.slice(4).join(', '))}">+${labels.length-4}</span>`:'');
}
function detailFormatBadges(item) {
  // The detail must reflect editions, never an outdated aggregated works.formats.
  const origins=new Map();
  for(const edition of item.editions||[]) {
    const source=sourceName(edition.source_type);
    const name=edition.source_name?`${source} (${edition.source_name})`:source;
    for(const raw of edition.metadata?.formats||[]) {
      const label=canonicalFormat(raw);
      if(!origins.has(label))origins.set(label,[]);
      if(!origins.get(label).includes(name))origins.get(label).push(name);
    }
  }
  return formatBadges([...origins.keys()].sort((a,b)=>a.localeCompare(b,displayLocale())),false,origins);
}
const personCategories = {cast:'Casting',director:'Réalisation',writer:'Scénario',producer:'Production',composer:'Musique · Composition',photography:'Photographie',crew:'Autres fonctions'};
function castMarkup(people=[]) {
  const members=list=>`<ul class="cast-grid">${list.map(member=>{
    const links=member.links||{};
    const name=esc(member.name);
    return `<li class="cast-member"><button type="button" class="person-photo" data-search-person="${name}" data-person-tmdb="${esc(member.tmdb_id||'')}" data-person-imdb="${esc(member.imdb_id||'')}" aria-label="Rechercher ${name} dans la médiathèque" title="Rechercher les films et séries de ${name}"><span class="cast-initials" aria-hidden="true">${esc(member.name.split(/\s+/).slice(0,2).map(word=>word[0]).join(''))}</span>${member.thumbnail?`<img src="${esc(url(member.thumbnail))}" alt="" loading="lazy">`:''}</button><span><button type="button" class="person-name" data-search-person="${name}" data-person-tmdb="${esc(member.tmdb_id||'')}" data-person-imdb="${esc(member.imdb_id||'')}" title="Rechercher ${name} dans la médiathèque"><strong>${name}</strong></button>${member.role?`<small>${esc(member.role)}</small>`:''}<span class="person-links">${Object.entries(links).map(([site,link])=>`<a href="${esc(link)}" target="_blank" rel="noopener noreferrer">${site==='imdb_search'?'Recherche IMDb':site.toUpperCase()} ↗</a>`).join(' · ')}</span></span></li>`;
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
function watchState(item) {
  if(item.kind==='tvshow'){
    const total=Number(item.episode_count)||0,viewed=Number(item.watched_episode_count)||0;
    const watched=total>0&&viewed===total;
    return {watched,available:total>0,status:watched?'watched':viewed>0?'in_progress':'unwatched',label:watched?'Vu':viewed>0?'Partiellement vu':'Non vu'};
  }
  return {watched:item.watch_status==='watched',available:true,status:item.watch_status,label:stateLabels[item.watch_status]};
}
function watchMarkup(item) {
  const view=watchState(item);
  const label=state.role==='admin'?(view.available?`${view.label} · Marquer comme ${view.watched?'non vu':'vu'}${item.kind==='tvshow'?' tous les épisodes':''}`:'Aucun épisode importé'):`${view.label} · Accès en lecture seule`;
  return `<button id="watch-button" class="status-chip watch-button ${esc(view.status)}" aria-label="${esc(label)}" aria-pressed="${view.watched}" title="${esc(label)}" data-tooltip="${esc(label)}" ${state.role==='admin'&&view.available&&!state.personalPending.has(item.id)?'':'disabled'}>${actionIcon(view.watched?'watched':'watch')}<span class="watch-state">${esc(view.label)}</span></button>`;
}
function detailActions(item) {
  const trailers=[...new Set(item.trailers||[])];
  const link=trailers[0]||'https://www.youtube.com/results?search_query='+encodeURIComponent(item.title+' '+(item.year||'')+' bande annonce');
  const multiple=trailers.length>1;
  const label=multiple?`Choisir une bande-annonce parmi ${trailers.length}`:trailers.length?'Voir la bande-annonce sur YouTube':'Rechercher une bande-annonce sur YouTube';
  const attributes=multiple?'id="open-trailers" aria-haspopup="dialog" aria-controls="trailers-dialog"':`id="trailer-link" href="${esc(link)}" target="_blank" rel="noopener noreferrer"`;
  const trailerButton=`<${multiple?'button':'a'} ${attributes} class="action-button trailer-button" aria-label="${label}" title="${label}">${actionIcon('youtube')}<span>Bande-annonce${multiple?` · ${trailers.length}`:''}</span></${multiple?'button':'a'}>`;
  const playable=item.kind!=='tvshow'&&item.editions.some(e=>e.source_type==='kodi');
  return `<div class="detail-actions compact-actions">${trailerButton}${personalActions(item)}${playable?`<button id="open-playback" class="action-button kodi-button" title="Lire sur Kodi" data-tooltip="Lire sur Kodi" aria-label="Lire sur Kodi" aria-haspopup="dialog" aria-controls="play-dialog"><img src="${esc(url('static/kodi-logo.svg'))}" alt="" width="27" height="27"></button>`:''}<span id="writeback-indicators" class="writeback-indicators-container">${writebackIndicatorMarkup(item)}</span><span id="personal-status" class="visually-hidden" role="status" aria-live="polite"></span></div>`;
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
    const view=watchState(item);
    const label=state.role==='admin'?(view.available?`${view.label} · Marquer comme ${view.watched?'non vu':'vu'}${item.kind==='tvshow'?' tous les épisodes':''}`:'Aucun épisode importé'):`${view.label} · Accès en lecture seule`;
    watch.disabled=!writable||!view.available;watch.className=`status-chip watch-button ${view.status}`;watch.setAttribute('aria-pressed',view.watched);watch.setAttribute('aria-label',label);watch.title=label;watch.dataset.tooltip=label;
    watch.innerHTML=`${actionIcon(view.watched?'watched':'watch')}<span class="watch-state">${esc(view.label)}</span>`;
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
  state.personalPending.add(id);++state.personalSeq;hideRatingPopover();beginPersonalSave();
  item.personal={...item.personal,...values};
  if(values.user_rating!=null)item.user_rating=values.user_rating;
  if(typeof values.watched==='boolean')item.watch_status=values.watched?'watched':'unwatched';
  syncPersonalControls();
  let saved=false;
  try {
    const response=await api(`media/${encodeURIComponent(id)}/personal`,{method:'PATCH',body:JSON.stringify(values)});
    saved=true;
    const warnings=(response.writeback||[]).filter(result=>result.status!=='updated'||result.message);
    const message=warnings.map(result=>result.source_name+' : '+(result.message||'Synchronisation non effectuée')).join(' · ');
    if(seq===state.detailSeq&&state.detail?.id===id){
      Object.assign(state.detail,{personal:response.personal,user_rating:response.user_rating,watch_status:response.watch_status,writeback_state:response.writeback_state||[]});renderWritebackIndicators();
      if($('personal-status'))$('personal-status').textContent=warnings.length?'Modification locale enregistrée. '+message:'Modification enregistrée.';
    }
    if(warnings.length)toast(message);
    const outbound=(response.writeback||[]);
    state.personalRemoteFailure=outbound.some(result=>['failed','unavailable'].includes(result.status));
    endPersonalSave(false,outbound.some(result=>result.status!=='updated'));
  }catch(e){
    if(seq===state.detailSeq&&state.detail?.id===id){Object.assign(state.detail,previous);if($('personal-status'))$('personal-status').textContent='Échec de l’enregistrement : '+e.message;}
    toast(e.message);
    endPersonalSave(true);
  }finally{state.personalPending.delete(id);if(seq===state.detailSeq&&state.detail?.id===id)syncPersonalControls();}
  if(saved){await loadMedia();if($('lists-dialog').open)await loadList();}
}
async function saveEpisodeWatches(watched,season=null,upToEpisode=null) {
  const item=state.detail;
  if(!item||item.kind!=='tvshow'||state.role!=='admin'||state.personalPending.has(item.id))return;
  const total=Number(item.episode_count)||0;
  if(season===null&&!localizedConfirm(`Attention : ${watched?'marquer comme vus':'marquer comme non vus'} les ${total} épisodes importés de « ${item.title} » (toutes les saisons et les spéciaux) ? Cette action modifie également les états envoyés aux sources configurées.`))return;
  if(upToEpisode!==null&&!localizedConfirm(`Marquer comme vus les épisodes de la saison ${season}, du début jusqu'à l'épisode ${upToEpisode} inclus ?`))return;
  const id=item.id,seq=state.detailSeq,selected=$('season-list')?.querySelector('.season-button[aria-expanded="true"]')?.dataset.season;
  state.personalPending.add(id);++state.personalSeq;syncPersonalControls();beginPersonalSave();
  document.querySelectorAll('[data-mark-season]').forEach(button=>button.disabled=true);
  try {
    const result=await api(`media/${encodeURIComponent(id)}/episodes/watch`,{method:'PATCH',body:JSON.stringify({watched,...(season===null?{}:{season}),...(upToEpisode===null?{}:{up_to_episode:upToEpisode})})});
    // The mutation succeeded. A subsequent catalogue refresh error must not
    // relabel an already persisted edit as a failed save.
    endPersonalSave(false,true);
    if(seq===state.detailSeq&&state.detail?.id===id){
      const updated=await api(`media/${encodeURIComponent(id)}`);
      if(seq===state.detailSeq&&state.detail?.id===id){
        renderDetail(updated);
        await loadSeasons(id,selected===undefined?null:Number(selected));
        if($('personal-status'))$('personal-status').textContent=`${result.updated} épisode(s) modifié(s) localement. État externe dans la barre supérieure.`;
      }
    }
    toast(`${result.updated} épisode(s) mis à jour sur ${result.total}.`);
    await loadMedia();
    if($('lists-dialog').open)await loadList();
  }catch(e){
    toast('Actualisation impossible : '+e.message);
    if(state.personalSaveCount>0)endPersonalSave(true);
  }finally{
    state.personalPending.delete(id);
    if(seq===state.detailSeq&&state.detail?.id===id){
      syncPersonalControls();
      document.querySelectorAll('[data-mark-season]').forEach(button=>button.disabled=false);
    }
  }
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
  const roles=state.personSearch&&(item.matched_roles||[]).filter(role=>typeof role==='string'&&role.trim());
  const credit=roles?.length?`<span class="matched-person-role" title="${esc(roles.join(' · '))}">${esc(tr('Rôle'))} : ${esc(roles.join(' · '))}</span>`:'';
  return `<button class="media-card" data-media="${esc(item.id)}" aria-label="${esc(item.title)}, ${esc(watchDisplayLabel(item))}${roles?.length?', rôle : '+esc(roles.join(' · ')):''}">${poster(item)}<span><span class="card-title">${esc(item.title)}</span>${credit}<span class="card-meta">${meta.map(v=>`<span>${esc(v)}</span>`).join('<span aria-hidden="true">·</span>')}</span><span class="format-badges compact">${formatBadges(item.formats,true)}</span><span class="card-meta">${badges(item)}</span></span>${state.view==='list'?`<span class="list-status">${esc(watchDisplayLabel(item))}<br>${item.rating?`★ ${Number(item.rating).toFixed(1)} · `:''}${date(item.date_added)}</span>`:''}</button>`;
}
function attachImageFallbacks(root) { root.querySelectorAll('img').forEach(img=>{img.addEventListener('error',()=>img.classList.add('failed'),{once:true});}); }
function currentParams(offset=0) {
  const params=new URLSearchParams({limit:state.limit,offset,sort:$('sort').value,order:state.order});
  for (const [id,name] of [['search','q'],['kind','kind'],['source-type','source_type'],['genre','genre'],['collection','collection'],['watch-status','watch_status']]) if ($(id).value) params.set(name,$(id).value);
  for (const value of state.formats) params.append('format',value);
  if(state.personSearch) {
    params.delete('q');
    if(state.personSearch.tmdbId)params.set('person_tmdb_id',state.personSearch.tmdbId);
    else if(state.personSearch.imdbId)params.set('person_imdb_id',state.personSearch.imdbId);
    params.set('person_name',state.personSearch.name);
    if(!state.personSearch.tmdbId&&!state.personSearch.imdbId){
      // Old servers ignore person_name without an identity; retain the
      // original cast-name FTS fallback until the server is upgraded.
      params.set('q',state.personSearch.name);
      params.set('deep_search','true');
    }
  }
  if(state.deepSearch) params.set('deep_search','true');
  if (state.recent) params.set('recent_days','30');
  return params;
}
function updateFilterState() {
  let count=['search','kind','source-type','genre','collection','watch-status'].filter(id=>$(id).value).length + state.formats.length + Number(state.recent) + Number(state.deepSearch);
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
  $('page-info').textContent=`${state.items.length.toLocaleString(displayLocale())} / ${state.total.toLocaleString(displayLocale())} titres affichés`;
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
    $('result-count').textContent=`${page.total.toLocaleString(displayLocale())} résultat${page.total>1?'s':''}${state.personSearch?.tmdbId?' · TMDB #'+state.personSearch.tmdbId+' (crédits non identifiés inclus)':state.personSearch?.imdbId?' · IMDb '+state.personSearch.imdbId+' (crédits non identifiés inclus)':''}${state.recent?' · ajoutés depuis 30 jours':''}`;
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
  $('scan-all-kodi').hidden=state.role!=='admin'||!players.length;
  state.tmdbAvailable=status.tmdb_available===true;
  $('server-version').textContent='Serveur '+(status.version||'—')+(status.data_model_version?' · Modèle '+status.data_model_version:'');
  fillSelect('genre',facets.genres,'Tous les genres'); fillSelect('collection',facets.collections,'Toutes les collections');
  if(filtersRestored){
    const available=new Set(facets.formats.map(canonicalFormat));
    state.formats=state.formats.filter(v=>available.has(v));
    updateFormatSelections();
  }
  restoreFilters();
  $('catalogue-status').textContent=`${status.works.toLocaleString(displayLocale())} titres · ${status.editions.toLocaleString(displayLocale())} fichiers et éditions · ${status.sources.length} source${status.sources.length>1?'s':''}`;
  const latest=status.sources.map(s=>s.last_sync).filter(Boolean).sort().at(-1);
  $('sync-status').textContent=latest?`Dernière mise à jour : ${date(latest)}`:'Aucune source importée';
  await loadMedia();
}
// Keep the activity indicator in the main header, including automated jobs
// started by the server scheduler and jobs launched by another client.
const jobPhaseLabels={
  connecting:'Connexion à MariaDB',loading:'Lecture des métadonnées Kodi',parsing_clz:'Analyse du fichier CLZ',parsing_mymovies:'Analyse du fichier MyMovies',parsing_filmotech:'Analyse du fichier Filmotech',
  movies:'Films',series:'Séries',episodes:'Épisodes',
  verifying_library:'Contrôle de la vidéothèque Kodi',
  publishing:'Mise à jour du catalogue',completed:'Terminé',failed:'Échec',
  kodi_scanning:'Analyse de la médiathèque distante Kodi (JSON-RPC)',
  kodi_scan_finished:'Kodi analysé · chargement de MariaDB',
  kodi_sql_fallback:'Kodi injoignable · synchronisation SQL uniquement'
};
function indeterminateKodiPhase(job) {
  return ['kodi_scanning','kodi_scan_finished','kodi_sql_fallback'].includes(job.progress_phase);
}
function renderGlobalScanProgress() {
  const root=$('remote-scan-progress');
  const ids=new Set(state.globalScanIds||[]);
  const jobs=state.jobs.filter(job=>ids.has(job.id));
  root.hidden=!jobs.length;
  root.closest('.remote-scan-dashboard').hidden=!jobs.length;
  if(!jobs.length)return;
  const sources=new Map((state.facets?.sources||[]).map(source=>[source.id,source]));
  root.innerHTML=jobs.map(job=>{
    const name=sources.get(job.source_id)?.name||'Kodi';
    const done=job.status==='completed',failed=job.status==='failed';
    const result=job.result||{};
    const summary=failed?'Échec':done
      ?result.kodi_scan==='sql_only'?'SQL actualisé, analyse Kodi non effectuée':'Actualisation terminée'
      :jobProgressLabel(job);
    const warning=(result.kodi_scan_warnings||[]).join(' · ');
    return `<div class="remote-scan-job ${failed?'is-failed':done?'is-completed':''}">
      <strong>${esc(name)}</strong>
      <div><span class="remote-scan-status">${esc(summary)}</span>
        ${!done&&!failed?jobProgressMarkup(job):''}
        ${failed&&job.error?`<span class="remote-scan-warning">${esc(job.error)}</span>`:''}
        ${warning?`<p class="remote-scan-warning">${esc(warning)}</p>`:''}
      </div>
    </div>`;
  }).join('');
}
function jobProgressLabel(job) {
  const phase=jobPhaseLabels[job.progress_phase]||'Préparation';
  const current=Math.max(0,Number(job.progress_current)||0);
  const total=Math.max(0,Number(job.progress_total)||0);
  const overall=Number(job.overall_percent);
  const overallText=Number.isFinite(overall)&&!indeterminateKodiPhase(job)&&!job.progress_phase?.startsWith('parsing_')?' · ≈ '+overall+' % global':'';
  return phase+(total>0?' · '+Math.min(current,total).toLocaleString(displayLocale())+'/'+total.toLocaleString(displayLocale()):'…')+overallText;
}
function jobProgressMarkup(job) {
  const percent=Number(job.overall_percent);
  const showProgress=Number.isFinite(percent)&&!indeterminateKodiPhase(job)&&!job.progress_phase?.startsWith('parsing_');
  return '<div class="sync-phase"><span class="activity-spinner" aria-hidden="true"></span><span>'+esc(jobProgressLabel(job))+'</span></div>'+
    (showProgress?'<progress class="sync-progress" value="'+Math.max(0,Math.min(percent,100))+'" max="100"></progress>':'');
}
function renderActivity() {
  const active=state.jobs.filter(j=>['pending','running'].includes(j.status));
  const indicator=$('activity-indicator');
  indicator.hidden=!active.length || $('app').hidden;
  renderGlobalScanProgress();
  if(!active.length)return;
  const sources=new Map((state.facets?.sources||[]).map(s=>[s.id,s]));
  const kinds=new Set(active.map(j=>sources.get(j.source_id)?.type||'kodi'));
  const label=kinds.size>1?'Synchronisation / import en cours'
    :[...kinds].some(type=>collectionTypes.includes(type))?'Import de collection en cours':'Synchronisation Kodi en cours';
  const first=active[0];
  const overall=Number(first.overall_percent);
  const showProgress=Number.isFinite(overall)&&!indeterminateKodiPhase(first)&&!first.progress_phase?.startsWith('parsing_');
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
$('scan-all-kodi').addEventListener('click',async()=>{
  const button=$('scan-all-kodi');
  button.disabled=true;
  try {
    const jobs=await api('sources/scan-all',{method:'POST'});
    state.globalScanIds=jobs.map(job=>job.id);
    state.jobs=[...jobs,...state.jobs.filter(job=>!state.globalScanIds.includes(job.id))];
    renderActivity();
    toast(`Analyse des médiathèques lancée : ${jobs.length} source(s) Kodi.`);
  } catch(e){toast(e.message);}
  finally {button.disabled=false;}
});

setInterval(()=>{if(!$('app').hidden){pollActivity();pollPersonalWritebacks();}},3000);
async function refreshStaleOnOpen() {
  try {
    const jobs=await api('sources/sync-stale',{method:'POST'});
    if(!jobs.length)return; // Already recent, or another client started it.
    const ids=new Set(jobs.map(job=>job.id));
    state.jobs=[...jobs,...state.jobs.filter(job=>!ids.has(job.id))];
    renderActivity();
  } catch(e) {
    // A failed automatic check must not prevent browsing or sign-out.
    console.warn('Automatic 24-hour catalogue refresh unavailable:',e.message);
  }
}
async function initialize() {
  try {
    const session=await api('auth/session'); state.csrf=session.csrf_token; state.role=session.role;
    $('login').hidden=true; $('app').hidden=false; $('connections-button').hidden=state.role!=='admin'; $('logout-button').hidden=session.access_method!=='session';
    $('create-list-button').hidden=state.role!=='admin';
    setView(state.view,false); renderPersonalStatus(); await refresh(); await refreshHomeListThumbnails(); await pollActivity();
    // Only once on authenticated entry; the server enforces the 24h threshold.
    void refreshStaleOnOpen();
    void startKodiRemote({api,url,role:state.role});
  } catch (e) { if ($('app').hidden) showLogin(); else error(e.message); }
}
// A cast credit searches the local catalogue (including indexed TMDB credits),
// not TMDB's remote person page. Clear unrelated filters before searching.
function searchLocalPerson(name,tmdbId='',imdbId='') {
  const person=String(name||'').trim();
  if(!person)return;
  state.personSearch={
    name:person,
    tmdbId:/^[0-9]+$/.test(tmdbId)?tmdbId:null,
    imdbId:/^nm[0-9]+$/.test(imdbId)?imdbId:null
  };
  if($('detail-dialog').open)$('detail-dialog').close();
  for(const id of ['kind','source-type','genre','collection','watch-status']) $(id).value='';
  state.formats=[];
  state.recent=false;
  state.deepSearch=true;
  $('deep-search').checked=true;
  $('search').value=person;
  updateSearchClearButton();
  $('sort').value='title';
  state.order='asc';
  $('sort-order').textContent='↑ Croissant';
  updateFormatSelections();
  loadMedia();
  $('search').focus({preventScroll:true});
  $('search').scrollIntoView({block:'center',behavior:'smooth'});
}
function reset() { state.personSearch=null; ['search','kind','source-type','genre','collection','watch-status'].forEach(id=>$(id).value=''); state.formats=[];state.deepSearch=false;$('deep-search').checked=false;updateFormatSelections(); state.recent=false; updateSearchClearButton(); loadMedia(); }
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
function showDialog(id) { if (!$(id).open) {lockPage();$(id).showModal();if(id==='connections-dialog')$(id).scrollTop=0;} }
function editionMarkup(e) {
  const m=e.metadata;
  const fields=[['Format',m.formats?.join(', ')],['Vidéo',m.video_streams?.map(v=>[v.width&&v.height?`${v.width} × ${v.height}`:'',v.codec,v.hdr_type,v.hdr_detail,v.stereo_mode].filter(Boolean).join(' · ')).join(' / ')],['Édition',m.edition||m.version],['Code-barres',m.barcode],['Disques',m.disc_count],['Quantité',m.quantity],['Coffret',m.boxset],['Collection',m.collections?.join(', ')],['Régions',m.regions?.join(', ')],['Audio',m.audio_tracks?.join(', ')],['Sous-titres',m.subtitles?.join(', ')],['Dernier visionnage',e.last_played?date(e.last_played):null],['Historique CLZ',m.seen_dates?.map(date).join(', ')],['Lectures',e.play_count],['Reprise',e.resume_seconds?`${Math.floor(e.resume_seconds/60)} min`:null],['Fichier',m.file],['Ajout',e.date_added?date(e.date_added):null],['Identifiants',Object.entries(e.identifiers).map(([k,v])=>`${k}: ${v}`).join(' · ')]];
  const label=collectionTypes.includes(e.source_type)?(m.formats?.join(' / ')||'Édition physique'):(m.version||'Fichier Kodi');
  return `<details class="edition"><summary><b>${esc(label)}</b> <span class="muted">· ${sourceIcon(e.source_type)} ${esc(e.source_name)}</span></summary><div class="format-badges edition-formats">${formatBadges(m.formats||[])}</div><dl class="edition-grid">${fields.filter(([,v])=>v!==null&&v!==undefined&&v!=='').map(([k,v])=>`<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl></details>`;
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
function resumeProgress(item) {
  if(!(item.total_seconds>0&&item.resume_seconds>0)) return '';
  const percent=Math.min(100,Math.max(0,100*item.resume_seconds/item.total_seconds));
  return `<div class="resume-progress" aria-label="Position de lecture : ${esc(playbackTime(item.resume_seconds))}">
    <time class="resume-time">${esc(playbackTime(item.resume_seconds))}</time>
    <progress value="${percent}" max="100" aria-label="Progression de lecture"></progress>
  </div>`;
}
function renderDetail(item) {
  closeEpisodeContextMenu();
  const previousScroll=state.detail?.id===item.id?$('detail-dialog').scrollTop:0;
  hideRatingPopover();
  state.detail=item;
  $('detail-content').innerHTML=`${metadataAction(item)}<div class="detail-layout"><div class="detail-poster">${poster(item,true)}</div><div class="detail-intro"><p class="eyebrow">${esc(kindLabels[item.kind])}</p><div class="detail-heading"><h2>${esc(item.title)}</h2>${item.original_title&&item.original_title!==item.title?`<p class="original-title">${esc(item.original_title)}</p>`:''}</div><div class="detail-info">${item.year?`<span>${item.year}</span>`:''}${item.runtime_minutes?`<span>${item.runtime_minutes} min</span>`:''}${item.rating?`<span class="score" title="Note publique : ${Number(item.rating).toFixed(1)}/10">★ ${Number(item.rating).toFixed(1)}</span>`:''}</div>${detailActions(item)}<div class="format-badges" aria-label="Formats disponibles selon les éditions">${detailFormatBadges(item)}</div> ${resumeProgress(item)}<div class="detail-meta-row">${watchMarkup(item)}${seriesProgressMarkup(item)}<div class="card-meta">${badges(item)}</div></div>${!item.source_types.includes('kodi')?'<p class="hint">Cette fiche provient uniquement de collections importées. Aucun fichier Kodi ne lui est actuellement associé dans le catalogue.</p>':''}<button id="add-to-list" class="secondary" ${state.role==='admin'?'':'disabled title="Un accès administrateur est nécessaire pour modifier les listes."'}>+ Ajouter à une liste</button>${item.lists?.length?`<p class="hint">Listes : ${item.lists.map(l=>`<button class="text-button" data-open-list="${esc(l.id)}">${esc(l.name)} ↗</button>`).join(' · ')}</p>`:''}</div><div class="detail-body">${item.kind==='episode'&&item.parent_id?`<button class="text-button" type="button" data-return-to-series="${esc(item.parent_id)}" data-return-season="${item.season??0}">← Série · Saison ${item.season??0} · Épisode ${item.episode??0}</button>`:''}<div class="genre-chips">${item.genres.map(g=>`<button data-genre="${esc(g)}">${esc(g)}</button>`).join('')}</div><p class="synopsis">${esc(item.plot||'Synopsis non renseigné.')}</p>${detailDatesMarkup(item)}${item.kind==='tvshow'?'<section class="detail-section"><p class="section-label">SAISONS</p><div id="season-list" class="season-links">Chargement…</div><div id="episode-list" class="episode-links"></div></section>':''}${castMarkup(item.people||item.cast)}<section class="detail-section"><p class="section-label">FICHIERS & ÉDITIONS · ${item.editions.length}</p>${item.editions.map(editionMarkup).join('')}${state.role==='admin'&&item.kind==='movie'?'<button id="diagnose-kodi" class="secondary" type="button">Vérifier la présence dans la vidéothèque</button><div id="kodi-diagnostic" class="metadata-status" role="status" aria-live="polite" hidden></div>':''}</section>${tmdbAttribution(item)}</div></div>`;
  attachImageFallbacks($('detail-content'));syncPersonalControls();$('detail-dialog').scrollTop=previousScroll;
}
async function openDetail(id) {
  const seq=++state.detailSeq;
  try {
    const item=await api(`media/${encodeURIComponent(id)}`); if(seq!==state.detailSeq)return;
    renderDetail(item); showDialog('detail-dialog');
    if(item.tmdb_available&&state.role==='admin'&&item.kind!=='episode'&&(!item.enriched_at||Date.now()-Date.parse(item.enriched_at)>30*86400000||(item.kind==='tvshow'&&!item.season_posters_cached))) enrichDetail(false);
    if (item.kind==='tvshow') await loadSeasons(item.id);

  } catch(e) {toast(e.message);}
}
async function loadSeasons(id,selectedSeason=null) {
  const seasons=await api(`media/${encodeURIComponent(id)}/seasons`);
  if(state.detail?.id!==id||!$('season-list'))return;
  state.seasons=seasons;
  const seriesPoster=state.detail.artwork?.poster;
  $('season-list').innerHTML=seasons.length?seasons.map(season=>{
    const label=season.season===0?'Spéciaux':`Saison ${season.season}`;
    const artwork=season.poster||seriesPoster;
    return `<div class="season-entry"><button type="button" class="season-button" data-season="${season.season}" aria-expanded="${selectedSeason===season.season}" aria-label="${esc(label)}, ${season.episode_count} épisodes, ${season.watched_count} vus${season.watched_count>0&&season.watched_count<season.episode_count?', partiellement vu':''}">
      <span class="season-cover">${artwork?`<img loading="lazy" src="${esc(url(artwork))}" alt="">`:'<span aria-hidden="true">▦</span>'}${season.watched_count>0?`<span class="season-watched-flag ${season.watched_count<season.episode_count?'partial':'complete'}" title="${season.watched_count<season.episode_count?'Partiellement vu':'Vu'} : ${season.watched_count}/${season.episode_count} épisodes" aria-label="${season.watched_count<season.episode_count?'Partiellement vu':'Vu'} : ${season.watched_count}/${season.episode_count} épisodes">${season.watched_count<season.episode_count?'◐':'✓'}</span>`:''}</span>
      <span class="season-label">${esc(label)}</span>
      <span class="season-count">${season.episode_count} épisodes · ${season.watched_count} vus</span>
    </button></div>`;
  }).join(''):'Aucune saison importée pour cette série.';
  attachImageFallbacks($('season-list'));
  if(selectedSeason!==null)loadSeasonEpisodes(selectedSeason);
}
async function loadSeasonEpisodes(season,offset=0) {
  const detail=state.detail;
  if(!detail||detail.kind!=='tvshow'||!$('episode-list'))return;
  const parent=detail.id;
  const node=$('episode-list');
  node.dataset.season=String(season);
  if(!offset)node.innerHTML='<p class="hint">Chargement des épisodes…</p>';
  try{
    const page=await api(`media/${encodeURIComponent(parent)}/seasons/${season}/episodes?limit=100&offset=${offset}`);
    if(state.detail?.id!==parent||node.dataset.season!==String(season))return;
    const markup=page.items.map(e=>{
      const watched=e.watch_status==='watched';
      const inProgress=e.watch_status==='in_progress';
      const statusLabel=watched?'Vu':inProgress?'En cours':'Non vu';
      const statusIcon=watched?'✓':inProgress?'◐':'○';
      return `<button type="button" class="season-episode" data-media="${esc(e.id)}" data-episode-season="${season}" data-episode-number="${Number(e.episode??0)}" title="Clic droit ou appui long : marquer les épisodes précédents comme vus" aria-label="Épisode ${esc(e.episode??'?')} : ${esc(e.title)}, ${statusLabel}">
        <span class="season-episode-number">E${String(e.episode??0).padStart(2,'0')}</span>
        <span class="season-episode-description"><span class="season-episode-title">${esc(e.title)}</span>${e.plot?`<small class="episode-synopsis">${esc(e.plot)}</small>`:''}</span>
        ${e.runtime_minutes?`<span class="season-episode-duration">${e.runtime_minutes} min</span>`:''}
        <span class="season-episode-state ${watched?'is-watched':inProgress?'is-progress':'is-unwatched'}" title="${statusLabel}" aria-label="${statusLabel}">${statusIcon}</span>
      </button>`;
    }).join('');

    if(!offset){
      const seasonData=state.seasons?.find(s=>s.season===season);
      const actions=state.role==='admin'?`<div class="selected-season-watch-actions"><button type="button" data-mark-season="${season}" data-watched="true" ${state.personalPending.has(parent)?'disabled':''}>Tout vu</button><button type="button" data-mark-season="${season}" data-watched="false" ${state.personalPending.has(parent)?'disabled':''}>Tout non vu</button></div>`:'';
      node.innerHTML=`<div class="selected-season-header"><p class="section-label">${season===0?'SPÉCIAUX':'SAISON '+season} · ${page.total} ÉPISODES${seasonData?' · '+seasonData.watched_count+' vus':''}</p>${actions}</div>${markup||'<p class="hint">Aucun épisode.</p>'}`;
    }
    else {node.querySelector('[data-load-season]')?.remove();node.insertAdjacentHTML('beforeend',markup);}
    if(page.has_more)node.insertAdjacentHTML('beforeend',`<button class="secondary" data-load-season="${season}" data-offset="${offset+page.items.length}">Afficher plus d’épisodes</button>`);
  }catch(e){if(state.detail?.id===parent&&node.dataset.season===String(season))node.textContent='Chargement des épisodes impossible : '+e.message;}
}
// The native-looking web context menu is also available on touch screens.
let episodeLongPressTimer=null, suppressEpisodeTap=false;
function closeEpisodeContextMenu() {
  document.getElementById('episode-context-menu')?.remove();
}
function openEpisodeContextMenu(button,x,y) {
  if(state.role!=='admin'||!button||state.personalPending.has(state.detail?.id))return;
  const season=Number(button.dataset.episodeSeason);
  const number=Number(button.dataset.episodeNumber);
  if(!Number.isInteger(season)||!Number.isInteger(number)||number<0)return;
  closeEpisodeContextMenu();
  const menu=document.createElement('div');
  menu.id='episode-context-menu';
  menu.className='episode-context-menu';
  menu.setAttribute('role','menu');
  menu.innerHTML=`<button role="menuitem" type="button" data-watch-through-season="${season}" data-watch-through-episode="${number}">✓ Marquer comme vus jusqu'à l'épisode ${number} inclus</button>`;
  document.body.append(menu);
  menu.style.left=Math.max(8,Math.min(x,window.innerWidth-menu.offsetWidth-8))+'px';
  menu.style.top=Math.max(8,Math.min(y,window.innerHeight-menu.offsetHeight-8))+'px';
  menu.querySelector('button')?.focus({preventScroll:true});
}
document.addEventListener('contextmenu',event=>{
  const episode=event.target.closest('.season-episode[data-episode-number]');
  if(!episode||state.role!=='admin')return;
  event.preventDefault();
  openEpisodeContextMenu(episode,event.clientX,event.clientY);
});
document.addEventListener('pointerdown',event=>{
  if(event.target.closest('#episode-context-menu'))return;
  closeEpisodeContextMenu();
  const episode=event.target.closest('.season-episode[data-episode-number]');
  if(!episode||state.role!=='admin'||event.pointerType!=='touch')return;
  clearTimeout(episodeLongPressTimer);
  const x=event.clientX,y=event.clientY;
  episodeLongPressTimer=setTimeout(()=>{
    episodeLongPressTimer=null;
    suppressEpisodeTap=true;
    openEpisodeContextMenu(episode,x,y);
  },550);
});
for(const kind of ['pointerup','pointercancel','pointermove']){
  document.addEventListener(kind,event=>{
    if(kind==='pointermove'&&episodeLongPressTimer&&event.buttons===1)return;
    clearTimeout(episodeLongPressTimer);
    episodeLongPressTimer=null;
  });
}
document.addEventListener('click',event=>{
  if(suppressEpisodeTap&&event.target.closest('.season-episode')){
    event.preventDefault();event.stopImmediatePropagation();suppressEpisodeTap=false;
  }
},true);
document.addEventListener('keydown',event=>{
  if(event.key==='Escape')closeEpisodeContextMenu();
});
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
      paragraphs.push('<p><strong>'+esc(source.source_name)+'</strong> ('+(sourccollectionTypes.includes(e.source_type)?'CLZ':'Kodi')+')</p>');
      if(collectionTypes.includes(source.source_type)){
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

      if(result.kodi_scan==='finished') details.unshift('Analyse Kodi terminée, catalogue local mis à jour');
      if(result.kodi_scan==='sql_only') details.unshift('Kodi non analysé : seule la base SQL existante a été synchronisée.');
      if(result.kodi_scan_warnings?.length) details.push(...result.kodi_scan_warnings.map(msg=>'Attention : '+msg));
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
    const collections=sources.filter(s=>collectionTypes.includes(s.type)); const kodi=sources.filter(s=>s.type==='kodi');
    const sourceJobs=new Map();
    for(const job of state.jobs)if(!sourceJobs.has(job.source_id))sourceJobs.set(job.source_id,job);
    $('connection-content').innerHTML=`<section class="metadata-status"><strong>Version du serveur : ${esc(status.version||'—')}</strong>${status.data_model_version?' · Modèle de données '+esc(status.data_model_version):''}</section><h3 class="connection-subheading">Appairage de l'application iOS</h3><section class="metadata-status"><p>Générez un code à 8 chiffres, valable 5 minutes et utilisable une seule fois. Il remplace la saisie du jeton API dans l'application.</p><label>Autorisations du nouvel appareil <select id="pairing-role"><option value="admin">Administrateur</option><option value="read">Lecture seule</option></select></label><p><button id="pairing-start" class="primary" type="button">Générer un code d'appairage</button></p><p id="pairing-code" class="pairing-code" role="status" aria-live="polite"></p><p class="hint">Dans iOS : sélectionnez le serveur détecté, puis recopiez le code. Un autre code invalide le précédent. Privilégiez HTTPS.</p>${devices.length?`<p class="hint">Appareils appairés</p><div class="paired-devices">${devices.map(device=>`<div class="connection-card"><span><strong>${esc(device.name)}</strong> · ${device.role==='admin'?'Administrateur':'Lecture seule'}</span><button type="button" class="secondary" data-revoke-device="${esc(device.token_hash)}">Révoquer</button></div>`).join('')}</div>`:''}</section><h3 class="connection-subheading">Métadonnées et portraits · TMDB</h3><section class="metadata-status"><p>${state.tmdbAvailable?'TMDB est configuré. Les fiches récupèrent automatiquement le casting, les portraits et les bandes-annonces disponibles.':'TMDB n’est pas configuré. Renseignez TMDB_API_TOKEN dans .env, puis relancez rebuild.sh.'}</p><p class="hint">Utilisez le jeton de lecture API ou la clé API v3 depuis <a href="https://www.themoviedb.org/settings/api" target="_blank" rel="noopener noreferrer">les paramètres API de votre compte TMDB ↗</a>. Le secret reste sur le serveur. Il n’est pas nécessaire de réimporter la bibliothèque pour activer TMDB.</p><button id="test-tmdb" class="secondary" ${state.tmdbAvailable?'':'disabled'}>Tester la connexion TMDB</button><p id="tmdb-test-result" class="hint play-status" role="status"></p></section><h3 class="connection-subheading">Sauvegarde JSON de la bibliothèque</h3><section class="metadata-status"><p>Exportez les médias, notes, visionnages et listes pour restaurer le catalogue sur ce serveur ou un autre. Les mots de passe et jetons d’appairage ne sont pas exportés.</p><div class="connection-actions"><button type="button" class="secondary" id="backup-export">Exporter en JSON</button><label class="secondary backup-file-picker" for="backup-import-file">Importer un JSON</label><input type="file" id="backup-import-file" accept=".json,application/json" hidden></div><p class="hint">L’import remplace tout le catalogue et ses listes. Les appareils appairés sur ce serveur restent autorisés. Les vignettes sont régénérées.</p></section><h3 class="connection-subheading">Sources du catalogue</h3>${sources.map(s=>`<div class="connection-card${s.type==='kodi'?' kodi-source-card':''}"><div><strong>${sourceIcon(s.type)} ${esc(s.name)}</strong><p>${s.type==='kodi'?'Kodi / MariaDB':sourceName(s.type)} · ${s.item_count} entrées${s.last_sync?` · ${date(s.last_sync)}`:''}${!s.enabled?' · Désactivée':''}</p>${sourceJobs.get(s.id)?.result?.database?`<p class="hint">Base synchronisée : <strong>${esc(sourceJobs.get(s.id).result.database)}</strong> · ${s.config?.database?'sélection explicite':'détection automatique'}</p>`:''}${sourceJobs.get(s.id)&&['pending','running'].includes(sourceJobs.get(s.id).status)?`<div class="source-progress">${jobProgressMarkup(sourceJobs.get(s.id))}</div>`:''}${s.last_error?`<p class="error-text">${esc(s.last_error)}</p>`:''}</div><div class="connection-actions">${s.type==='kodi'?`<button data-sync="${esc(s.id)}">Synchroniser</button><button data-scan-kodi="${esc(s.id)}">Actualiser Kodi</button><button data-edit-source="${esc(s.id)}">Modifier</button><button data-writeback-history="${esc(s.id)}">Historique des écritures</button>`:s.type==='mymovies'?`<button data-configure-mymovies="${esc(s.id)}">Configurer l’API</button>`:''}<button data-remove-source="${esc(s.id)}">Retirer</button></div>${s.type==='kodi'?`<section class="source-kodi-instances" data-kodi-instances-for="${esc(s.id)}"><div class="source-kodi-instances-heading"><strong>Instances Kodi · JSON-RPC</strong><button type="button" class="secondary" data-add-player-source="${esc(s.id)}">+ Ajouter une instance</button></div>${players.filter(p=>p.source_id===s.id).map(p=>`<div class="connection-card kodi-instance-row"><div><strong>${esc(p.name)}</strong><p>${esc(p.host)}:${esc(p.port)} · HTTP JSON-RPC</p><p id="player-result-${esc(p.id)}" class="hint play-status" role="status"></p></div><div class="connection-actions"><button type="button" data-test-player="${esc(p.id)}">Tester</button><button type="button" data-edit-player="${esc(p.id)}">Modifier</button><button type="button" data-remove-player="${esc(p.id)}">Retirer</button></div></div>`).join('')||'<p class="hint">Aucune instance associée. Ajoutez un lecteur Kodi pour utiliser la télécommande.</p>'}</section>`:''}</div>`).join('')||'<p class="hint">Aucune source configurée.</p>'}<details><summary class="text-button">+ Connecter une base Kodi</summary><form id="kodi-form" class="connection-form"><input type="hidden" name="id"><label>Nom<input name="name" required placeholder="Kodi — NAS"></label><label>Hôte MariaDB<input name="host" required placeholder="192.168.1.10"></label><label>Port<input name="port" type="number" min="1" max="65535" value="3306" required></label><label>Base MyVideos<input name="database" placeholder="Automatique, ou MyVideos…"></label><label>Utilisateur<input name="username" required autocomplete="off" placeholder="kodi_library"></label><label>Mot de passe<input name="password" type="password" autocomplete="new-password"></label><label class="check-label wide"><input name="tls" type="checkbox">Connexion MariaDB avec TLS</label><label class="check-label wide"><input name="mariadb_write_enabled" type="checkbox">Autoriser le repli en écriture MariaDB si Kodi JSON-RPC est inaccessible</label><p class="hint wide">Repli désactivé par défaut. Le même compte MariaDB sert aux lectures et aux écritures (droits UPDATE requis). Sélectionnez explicitement la base MyVideos. Les opérations sont tracées ; une écriture SQL ne produit pas de notification Kodi.</p><p class="hint wide">Ce formulaire contacte MariaDB, pas l’API HTTP de Kodi. Un compte SELECT suffit ; Kodi peut être arrêté. La connexion est testée avant tout nouvel ajout.</p><button class="secondary wide" type="button" data-test-connection="kodi-form">Tester la connexion MariaDB</button><p id="kodi-form-result" class="connection-result wide" role="status" aria-live="polite" hidden></p><button class="primary wide" type="submit">Enregistrer et synchroniser</button></form></details><h3 class="connection-subheading">Importer une collection</h3><form id="collection-form" class="upload-row"><label>Type de collection<select name="collection_type">${collectionTypes.map(type=>`<option value="${type}">${sourceName(type)}</option>`).join('')}</select></label><label>Collection<select name="source_id" aria-label="Collection"><option value="">Nouvelle collection…</option></select></label><input name="file" type="file" accept=".xml,application/xml,text/xml" required aria-label="Export XML de la collection"><button class="secondary" type="submit">Importer l’export XML</button><p class="hint">Un nouvel import remplace uniquement la collection choisie et conserve les autres sources. Utilisez un export XML Filmotech ou My Movies Collection Management.</p></form><details id="mymovies-api-details"><summary class="text-button">+ My Movies : synchronisation des visionnages (API professionnelle)</summary><form id="mymovies-api-form" class="connection-form"><label>Collection My Movies<select name="source_id" required>${collections.filter(s=>s.type==='mymovies').map(s=>`<option value="${esc(s.id)}">${esc(s.name)}</option>`).join('')}</select></label><label>Hôte du serveur My Movies<input name="remote_host" placeholder="192.168.1.10"></label><label>Port Remote Data API<input name="remote_port" type="number" min="1" max="65535" value="51414"></label><label>Clé API<input name="remote_api_key" type="password" autocomplete="new-password" placeholder="Vide = conserver la clé existante"></label><label class="check-label wide"><input type="checkbox" name="remote_enabled">Activer l’écriture vu/non vu</label><p class="hint wide">API Remote Server professionnelle de My Movies, réservée aux installations autorisées. L’export XML est conservé en lecture seule. Aucun transfert des notes personnelles n’est possible via cette API. La clé transite via HTTP : à utiliser sur un réseau de confiance uniquement.</p><button type="submit" class="primary wide">Enregistrer la configuration</button></form></details><h3 class="connection-subheading">Synchronisations vers Kodi et services tiers</h3><section class="metadata-status"><p id="writeback-summary" role="status">Chargement des synchronisations...</p><p class="hint">Seules les modifications explicites d’une fiche déclenchent une écriture distante. Les imports et actualisations de catalogue restent en lecture seule.</p><div id="writeback-queue"></div><details class="writeback-recent-details"><summary>Synchronisations distantes récentes</summary><div id="writeback-recent" aria-live="polite"></div></details><div id="writeback-source-history" class="writeback-source-history" hidden></div><button id="retry-all-writebacks" class="secondary" type="button" ${state.role==='admin'?'':'disabled'}>Rejouer toutes les synchronisations en échec</button></section><div id="job-status" class="job-status" role="status" hidden></div>${kodi.length?`<details id="player-details" class="source-player-editor" hidden><summary class="text-button">+ Ajouter une instance Kodi</summary><form id="player-form" class="connection-form"><input type="hidden" name="id"><label>Nom<input name="name" required placeholder="Kodi — Salon"></label><label>Source MariaDB associée<select name="source_id">${kodi.map(s=>`<option value="${esc(s.id)}">${esc(s.name)}</option>`).join('')}</select></label><p class="hint wide">Cette connexion JSON-RPC appartient à l’instance Kodi. La source associée définit l’accès MariaDB, sans dupliquer les identifiants HTTP. Plusieurs instances peuvent partager la même source.</p><label>Hôte Kodi<input name="host" required placeholder="192.168.1.20"></label><label>Port HTTP<input name="port" type="number" value="8080" min="1" max="65535" required></label><label>Port WebSocket Kodi (notifications de scan)<input name="websocket_port" type="number" value="9090" min="1" max="65535" required></label><label>Utilisateur HTTP<input name="username" autocomplete="off"></label><label>Mot de passe HTTP<input name="password" type="password" autocomplete="new-password"></label><label class="check-label wide"><input name="tls" type="checkbox">HTTPS sur Kodi</label><p class="hint wide">Ce formulaire contacte l’API HTTP de Kodi, pas MariaDB. Kodi doit être démarré, avec le contrôle distant HTTP activé, et utiliser la base MyVideos choisie.</p><button class="secondary wide" type="button" data-test-connection="player-form">Tester la connexion Kodi</button><p id="player-form-result" class="connection-result wide" role="status" aria-live="polite" hidden></p><button type="submit" class="primary wide">Enregistrer l’instance</button></form></details>`:''}`;
    const importForm=$('collection-form');
    const updateCollections=()=>{
      const type=importForm.elements.collection_type.value;
      importForm.elements.source_id.innerHTML=collections.filter(source=>source.type===type).map(source=>`<option value="${esc(source.id)}">${esc(source.name)}</option>`).join('')+'<option value="">Nouvelle collection…</option>';
    };
    await refreshWritebackQueue();
    importForm.elements.collection_type.addEventListener('change',updateCollections);
    updateCollections();
    showDialog('connections-dialog');
    renderActivity();
  } catch(e) {toast(e.message);}
}
// A single editor moves below the source to which the instance belongs.
function openKodiPlayerEditor(sourceID,player=null) {
  const container=[...document.querySelectorAll('[data-kodi-instances-for]')]
    .find(element=>element.dataset.kodiInstancesFor===sourceID);
  const details=$('player-details'), form=$('player-form');
  if(!container||!details||!form)return;
  const source=state.facets.sources.find(s=>s.id===sourceID&&s.type==='kodi');
  if(!source)return;
  details.hidden=false;
  container.append(details);
  form.reset();
  details.querySelector('summary').textContent=player
    ? 'Modifier une instance Kodi' : '+ Ajouter une instance Kodi';
  fillForm('player-form',player
    ? {...player,password:''}
    : {id:'',name:source.name,source_id:sourceID,host:'',port:8080,
       websocket_port:9090,username:'',password:'',tls:false});
  const result=$('player-form-result');
  if(result){result.hidden=true;result.textContent='';}
}
function connectionInput(form) {
  const data=new FormData(form);
  const config={host:String(data.get('host')||'').trim(),port:Number(data.get('port')),username:String(data.get('username')||'').trim(),password:data.get('password')||null,tls:data.has('tls')};
  if(form.getAttribute('id')==='kodi-form') return {config:{...config,database:String(data.get('database')||'').trim(),mariadb_write_enabled:data.has('mariadb_write_enabled')},source_id:data.get('id')||null};
  return {...config,websocket_port:Number(data.get('websocket_port')||9090),player_id:data.get('id')||null};
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
  if(!['login-form','kodi-form','collection-form','player-form','mymovies-api-form'].includes(formId)) return;
  event.preventDefault(); const button=form.querySelector('button[type="submit"]'); button.disabled=true;
  try {
    const data=new FormData(form);
    if(formId==='login-form') {
      await api('auth/session',{method:'POST',body:JSON.stringify({token:data.get('token')})}); form.reset(); $('login-error').textContent=''; await initialize();
    } else if(formId==='kodi-form') {
      const body={name:data.get('name'),type:'kodi',config:connectionInput(form).config};
      const id=data.get('id'); if(!id) await testConnection(form); const source=await api(id?`sources/${id}`:'sources',{method:id?'PUT':'POST',body:JSON.stringify(body)});
      await pollJob(await api(`sources/${source.id}/sync`,{method:'POST'}));
    } else if(formId==='mymovies-api-form') {
      const s=state.facets.sources.find(x=>x.id===data.get('source_id')&&x.type==='mymovies');
      if(!s) throw new Error('Choisissez une collection My Movies existante.');
      const config={remote_host:String(data.get('remote_host')||'').trim(),remote_port:Number(data.get('remote_port')||51414),remote_api_key:data.get('remote_api_key')||null,remote_enabled:data.has('remote_enabled')};
      if(config.remote_enabled&&(!config.remote_host||(!config.remote_api_key&&!s.config.has_remote_api_key)))throw new Error('Hôte et clé API nécessaires pour activer la synchronisation.');
      await api(`sources/${encodeURIComponent(s.id)}`,{method:'PUT',body:JSON.stringify({name:s.name,type:'mymovies',enabled:s.enabled,config})});
      toast('Configuration de l’API My Movies enregistrée.');await connections();
    } else if(formId==='collection-form') {
      let id=data.get('source_id');
      const type=data.get('collection_type');
      if(!collectionTypes.includes(type)) throw new Error('Unsupported collection type');
      if(!id) { const name=localizedPrompt('Nom de cette collection',sourceName(type)); if(!name) return; const source=await api('sources',{method:'POST',body:JSON.stringify({name,type})}); id=source.id; }
      const body=new FormData(); body.set('source_id',id); body.set('file',data.get('file'));
      await pollJob(await api('imports/'+type,{method:'POST',body}));
    } else {
      const id=data.get('id'); const {player_id,...config}=connectionInput(form); const body={name:data.get('name'),source_id:data.get('source_id'),...config};
      if(!id) await testConnection(form);
      await api(id?`players/${id}`:'players',{method:id?'PUT':'POST',body:JSON.stringify(body)});
      toast('Instance enregistrée.');
      await connections();
      // Rebuild the remote's player/socket subscriptions after a configuration
      // change; the one-shot initial list is otherwise stale until page reload.
      await startKodiRemote({api,url,role:state.role});
    }
  } catch(e) {if(formId==='login-form') $('login-error').textContent=e.message; else toast(e.message);}
  finally {button.disabled=false;}
});
document.addEventListener('click',async event=>{
  const button=event.target.closest('button'); if(!button) return;
  if(button.dataset.searchPerson){searchLocalPerson(button.dataset.searchPerson,button.dataset.personTmdb,button.dataset.personImdb);return;}
  if(button.hasAttribute('data-watch-through-episode')){
    const season=Number(button.dataset.watchThroughSeason),episode=Number(button.dataset.watchThroughEpisode);
    closeEpisodeContextMenu();
    await saveEpisodeWatches(true,season,episode);
    return;
  }
  if(button.hasAttribute('data-mark-season')){await saveEpisodeWatches(button.dataset.watched==='true',Number(button.dataset.markSeason));return;}
  if(button.dataset.close) $(button.dataset.close).close();
  if(button.dataset.media) openDetail(button.dataset.media);
  if(button.hasAttribute('data-season')) {
    document.querySelectorAll('.season-button').forEach(b=>b.setAttribute('aria-expanded',String(b===button)));
    loadSeasonEpisodes(Number(button.dataset.season));
    return;
  }
  if(button.hasAttribute('data-load-season')) {
    loadSeasonEpisodes(Number(button.dataset.loadSeason),Number(button.dataset.offset)||0);
    return;
  }
  if(button.dataset.returnToSeries){
    const seriesId=button.dataset.returnToSeries,season=Number(button.dataset.returnSeason)||0;
    await openDetail(seriesId);
    if(state.detail?.id===seriesId)loadSeasonEpisodes(season);
    return;
  }
  if(button.dataset.genre) {$('genre').value=button.dataset.genre; $('detail-dialog').close();  loadMedia();}
  if(button.hasAttribute('data-toggle-format')) {
    const value=canonicalFormat(button.dataset.toggleFormat);
    if(state.formats.includes(value))state.formats=state.formats.filter(v=>v!==value);
    else if(state.formats.length<20)state.formats.push(value);
    else {toast('20 formats maximum.');return;}
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
    if(localizedConfirm('Révoquer cet appareil iOS ? Il devra être appairé de nouveau.')) {
      try {await api('pairing/devices/'+encodeURIComponent(button.dataset.revokeDevice),{method:'DELETE'});await connections();}
      catch(e){toast(e.message);}
    }
    return;
  }
  if(button.dataset.retryWriteback||button.id==='retry-all-writebacks') {
    button.disabled=true;
    try{
      const path=button.dataset.retryWriteback?'writebacks/pending/'+encodeURIComponent(button.dataset.retryWriteback)+'/retry':'writebacks/retry';
      const result=await api(path,{method:'POST'});
      const failures=result.results.filter(entry=>entry.result.status!=='updated').length;
      toast(failures?`${failures} synchronisation(s) restent en échec.`:'Synchronisation(s) rejouée(s).');
      await refreshWritebackQueue();
      if(state.detail){
        const detail=await api('media/'+encodeURIComponent(state.detail.id));
        state.detail.writeback_state=detail.writeback_state||[];
        renderWritebackIndicators();
      }
    }catch(e){toast(e.message);}finally{button.disabled=false;}
    return;
  }
  if(button.dataset.closeWritebackHistory){
    const target=$('writeback-source-history');target.hidden=true;target.replaceChildren();return;
  }
  if(button.dataset.quick) {state.recent=button.dataset.quick==='recent'; $('watch-status').value=['all','recent'].includes(button.dataset.quick)?'':button.dataset.quick;  if(state.recent){$('sort').value='date_added';state.order='desc';$('sort-order').textContent='↓ Décroissant';}loadMedia();}
  if(!button.dataset.testConnection&&!button.dataset.sync&&!button.dataset.removeSource&&!button.dataset.removePlayer&&!button.dataset.editSource&&!button.dataset.editPlayer&&!button.dataset.addPlayerSource&&!button.dataset.configureMymovies&&!button.dataset.scanKodi&&!button.dataset.writebackHistory&&!button.dataset.testPlayer&&!['play-button','test-tmdb'].includes(button.id))return;
  const result=button.id==='play-button'?$('play-status'):button.id==='test-tmdb'?$('tmdb-test-result'):button.dataset.testPlayer?$(`player-result-${button.dataset.testPlayer}`):null;
  if(result){result.className='hint play-status';result.textContent=button.id==='play-button'?'Envoi de la lecture à Kodi…':'Vérification de la connexion…';}
  try {
    if(button.dataset.testConnection) {const form=$(button.dataset.testConnection); const fields=['host','port',...(form.getAttribute('id')==='kodi-form'?['username']:[])];if(fields.every(name=>form.elements.namedItem(name).reportValidity())) await testConnection(form);}
    if(button.dataset.sync) {button.disabled=true; await pollJob(await api(`sources/${button.dataset.sync}/sync`,{method:'POST'}));}
    if(button.dataset.scanKodi) {button.disabled=true;const job=await api(`sources/${button.dataset.scanKodi}/scan`,{method:'POST'});toast('Analyse Kodi demandée. La base locale sera synchronisée après confirmation de fin.');await pollJob(job);}
    if(button.dataset.writebackHistory) {
      const data=await api(`sources/${encodeURIComponent(button.dataset.writebackHistory)}/writeback`);
      const target=$('writeback-source-history');
      const source=state.facets.sources.find(s=>s.id===button.dataset.writebackHistory);
      target.hidden=false;
      target.innerHTML=`<div class="writeback-history-heading"><h4>Historique des écritures · ${esc(source?.name||'Source')}</h4><button type="button" class="secondary" data-close-writeback-history>Fermer</button></div>${data.length?'<ul class="writeback-history-list">'+data.map(item=>writebackHistoryRow(item,true)).join('')+'</ul>':'<p class="hint">Aucune écriture enregistrée pour cette source.</p>'}`;
      target.scrollIntoView({block:'nearest',behavior:'smooth'});
    }
    if(button.dataset.removeSource && localizedConfirm('Retirer cette source et ses données importées ?')) {
      await api(`sources/${button.dataset.removeSource}`,{method:'DELETE'});
      await refresh();
      await connections();
      // SQLite removes linked Kodi players through its ON DELETE CASCADE FK.
      await startKodiRemote({api,url,role:state.role});
    }
    if(button.dataset.removePlayer && localizedConfirm('Retirer cette instance Kodi ?')) {
      await api(`players/${button.dataset.removePlayer}`,{method:'DELETE'});
      await connections();
      await startKodiRemote({api,url,role:state.role});
    }
    if(button.dataset.editSource) {const s=state.facets.sources.find(s=>s.id===button.dataset.editSource); fillForm('kodi-form',{id:s.id,name:s.name,...s.config});}
    if(button.dataset.configureMymovies) {const s=state.facets.sources.find(s=>s.id===button.dataset.configureMymovies);if(s){fillForm('mymovies-api-form',{source_id:s.id,...s.config});}}
    if(button.dataset.addPlayerSource) {
      openKodiPlayerEditor(button.dataset.addPlayerSource);
    }
    if(button.dataset.editPlayer) {
      const player=state.players.find(p=>p.id===button.dataset.editPlayer);
      if(player)openKodiPlayerEditor(player.source_id,player);
    }
    if(button.id==='test-tmdb'){button.disabled=true;const tested=await api('connections/tmdb/test',{method:'POST'});result.textContent=tested.message;result.className='success-text play-status';}
    if(button.dataset.testPlayer){
      button.disabled=true;const p=state.players.find(p=>p.id===button.dataset.testPlayer);
      if(!p)throw new Error('Choisissez une instance Kodi.');
      const tested=await api('connections/kodi/test',{method:'POST',body:JSON.stringify({player_id:p.id,host:p.host,port:p.port,username:p.username,tls:p.tls})});
      result.textContent=tested.message;result.className='success-text play-status';
    }
    if(button.id==='play-button') {
      savePlaybackDraft();
      button.disabled=true;
      const target=$('play-target').value;
      await api(`players/${target}/play`,{
        method:'POST',
        body:JSON.stringify({media_id:state.detail.id,edition_id:$('play-edition').value,resume:$('play-resume').checked})
      });
      toast('Lecture envoyée à Kodi.');
      // Player.Open was acknowledged: close both media dialogs before opening
      // the same Now Playing remote used by the header Kodi button.
      if($('play-dialog').open)$('play-dialog').close();
      if($('detail-dialog').open)$('detail-dialog').close();
      showKodiRemote(target);
    }
  }catch(e){if(result){result.textContent=e.message;result.className='error-text play-status';}toast(e.message);}finally{button.disabled=false;}
});
$('connections-button').addEventListener('click',connections);
$('lists-button').addEventListener('click',()=>openLists());
$('create-list-button').addEventListener('click',()=>openLists(state.listId,{create:true}));
$('logout-button').addEventListener('click',async()=>{try{await api('auth/session',{method:'DELETE'});showLogin();}catch(e){toast(e.message);}});
$('grid-view').addEventListener('click',()=>setView('grid')); $('list-view').addEventListener('click',()=>setView('list'));
// Native <details> handles opening/closing without JavaScript; the event keeps
// accessible state synchronized while retaining filter values independently.
$('filters-panel').addEventListener('toggle',()=>{
  $('toggle-filters').setAttribute('aria-expanded',String($('filters-panel').open));
});
$('reset-filters').addEventListener('click',reset);
let searchTimer;
$('search').addEventListener('input',()=>{
  state.personSearch=null;
  updateSearchClearButton();
  savePreferences();
  clearTimeout(searchTimer);
  searchTimer=setTimeout(()=>{loadMedia();},250);
});
$('clear-search').addEventListener('click',()=>{
  clearTimeout(searchTimer);
  state.personSearch=null;
  $('search').value='';
  updateSearchClearButton();
  loadMedia();
  $('search').focus({preventScroll:true});
});
['kind','source-type','genre','collection','watch-status','sort'].forEach(id=>$(id).addEventListener('change',()=>{loadMedia();}));
$('deep-search').addEventListener('change',()=>{state.deepSearch=$('deep-search').checked;loadMedia();});
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
      const expandedSeason=$('season-list')?.querySelector('[aria-expanded="true"]')?.dataset.season;
      if((state.personalSeq||0)!==personalSeq)Object.assign(item,{personal:state.detail.personal,user_rating:state.detail.user_rating,watch_status:state.detail.watch_status});
      const episodes=$('episode-list')?.innerHTML;
      const ratingDraft=$('rating-popover')?.dataset.open?{value:$('rating-slider').value,focused:$('rating-popover').contains(document.activeElement)}:null;
      renderDetail(item);
      if(ratingDraft){showRatingPopover(ratingDraft.focused);$('rating-slider').value=ratingDraft.value;previewRating(Number(ratingDraft.value));}
      if(state.detail?.kind==='tvshow') {
        loadSeasons(id,expandedSeason===undefined?null:Number(expandedSeason)).catch(()=>{});
      } else if(episodes&&$('episode-list'))$('episode-list').innerHTML=episodes;
      if($('play-dialog').open)updatePlayEdition();
    }
  } catch(e) {if(seq===state.detailSeq){if($('enrichment-status'))$('enrichment-status').textContent=e.message;toast(e.message);}}
  finally {state.enrichingIds.delete(id);if(enriched&&seq===state.detailSeq&&$('enrichment-status'))$('enrichment-status').textContent=`Métadonnées TMDB mises en cache le ${date(state.detail.enriched_at)}.`;if(seq===state.detailSeq&&$('refresh-metadata')){$('refresh-metadata').disabled=state.role!=='admin';$('refresh-metadata').classList.remove('is-loading');}}
}
// Thumbnail URLs include list revisions, so a changed mosaic bypasses the
// browser cache and always matches the latest ordering.
function listThumbnail(list,extraClass=''){
  const image=list.thumbnail?`<img src="${esc(url(list.thumbnail))}" alt="" loading="lazy">`:'<span aria-hidden="true">▦</span>';
  return `<span class="list-mosaic ${extraClass}" aria-hidden="true">${image}</span>`;
}
function renderHomeListThumbnails(lists){
  const container=$('home-list-thumbnails');
  if(!container)return;
  container.innerHTML=lists.map(list=>`<button type="button" class="home-list-thumb" data-open-home-list="${esc(list.id)}" title="${esc(list.name)}" aria-label="${esc(list.name)}">${listThumbnail(list)}</button>`).join('');
  attachImageFallbacks(container);
}
async function refreshHomeListThumbnails(){
  try{renderHomeListThumbnails(await api('lists'));}catch(e){/* Lists may be temporarily unavailable; keep catalogue visible. */}
}
async function chooseList() {
  try {
    const lists=await api('lists');const item=state.detail;
    $('choose-list-content').innerHTML=`<p class="muted">${esc(item.title)}</p><div class="choose-list-buttons">${lists.map(l=>`<button class="list-choice-row" data-choose-list="${esc(l.id)}" ${item.lists.some(current=>current.id===l.id)?'disabled':''}>${listThumbnail(l)}<span><strong>${esc(l.name)}</strong><small class="muted">${l.item_count} médias${item.lists.some(current=>current.id===l.id)?' · Déjà ajouté':''}</small></span></button>`).join('')||'<p class="hint">Créez votre première liste ci-dessous.</p>'}</div><form id="choose-new-list" class="inline-form"><label>Nouvelle liste<input name="name" required maxlength="120" placeholder="Films à voir"></label><button type="submit" class="primary">+ Créer et ajouter</button></form>`;
    showDialog('choose-list-dialog');
  } catch(e){toast(e.message);}
}
async function openLists(selected=state.listId,{create=false}={}) {
  try {
    const lists=await api('lists');renderHomeListThumbnails(lists);state.listId=lists.some(l=>l.id===selected)?selected:lists[0]?.id;
    savePreferences();
    const current=lists.find(l=>l.id===state.listId);const admin=state.role==='admin';
    const newListForm=admin?`<form id="new-list-form" class="inline-form" ${current&&!create?'hidden':''}><label>Nom de la liste<input name="name" required maxlength="120" placeholder="Films à voir"></label><button class="primary" type="submit">Créer la liste</button></form>`:'';
    $('lists-content').innerHTML=`<div class="list-toolbar"><label>Liste<select id="selected-list">${lists.map(l=>`<option data-name="${esc(l.name)}" value="${esc(l.id)}" ${l.id===state.listId?'selected':''}>${esc(l.name)} · ${l.item_count}</option>`).join('')||'<option>Aucune liste</option>'}</select></label>${admin?'<button id="new-list-toggle" class="secondary">+ Nouvelle liste</button>':''}</div><div class="list-selector-cards" role="group" aria-label="Choix de la liste">${lists.map(l=>`<button type="button" class="list-choice-row ${l.id===state.listId?'is-selected':''}" data-select-list="${esc(l.id)}" aria-pressed="${l.id===state.listId}">${listThumbnail(l)}<span><strong>${esc(l.name)}</strong><small class="muted">${l.item_count} médias</small></span></button>`).join('')}</div>${!admin?'<p class="metadata-status hint">Accès en lecture seule : utilisez le jeton administrateur pour créer une liste ou modifier son contenu.</p>':''}<p class="hint">Ajoutez des films et des séries, puis choisissez leur ordre dans chaque liste. Ces sélections sont conservées après les synchronisations.</p>${newListForm}${current?`<div class="list-toolbar"><p class="hint">${esc(current.description||'Votre sélection, dans l’ordre que vous choisissez.')}</p>${admin?'<button id="rename-list" class="text-button">Renommer</button><button id="delete-list" class="text-button">Supprimer</button>':''}</div><div class="list-toolbar"><label>Tri<select id="list-sort"><option value="position">Ordre manuel</option><option value="title">Titre</option><option value="year">Année</option><option value="date_added">Date d’ajout</option><option value="rating">Note</option><option value="user_rating">Votre note</option></select></label><button id="list-sort-order" class="secondary">${state.listOrder==='asc'?'↑ Croissant':'↓ Décroissant'}</button>${admin?'<button id="list-add-button" class="primary">+ Ajouter des médias</button>':''}</div>${admin?'<section id="list-search-area" class="list-search-area" hidden><label>Rechercher dans le catalogue<input id="list-search" type="search" placeholder="Titre, personne, genre…" autocomplete="off"></label><div id="list-search-results" aria-live="polite"></div></section>':''}<p id="list-count" class="hint"></p><div id="list-items" class="saved-list-items" aria-live="polite"></div><button id="list-load-more" class="secondary" hidden>Charger la suite</button>`:'<p class="empty-state">Créez une liste pour organiser vos prochains visionnages.</p>'}`;
    attachImageFallbacks($('lists-content'));
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
        $('choose-list-dialog').close();renderDetail(await api(`media/${mid}`));await refreshHomeListThumbnails();toast('Liste créée et média ajouté.');
      }else await openLists(list.id);
    }
  }catch(e){toast(e.message);}finally{button.disabled=false;}
});
document.addEventListener('click',async event=>{
  const button=event.target.closest('button');if(!button)return;
  if(!['refresh-metadata','add-to-list','new-list-toggle','list-add-button','rename-list','delete-list','list-load-more','list-sort-order'].includes(button.id)&&!button.dataset.openHomeList&&!button.dataset.selectList&&!button.dataset.chooseList&&!button.dataset.addListMedia&&!button.dataset.removeListMedia&&!button.dataset.moveList&&!button.dataset.copyFile)return;
  try {
    if(button.dataset.openHomeList){await openLists(button.dataset.openHomeList);return;}
    if(button.dataset.selectList){await openLists(button.dataset.selectList);return;}
    if(button.id==='refresh-metadata'){hideRatingPopover();await enrichDetail(true);}
    if(button.id==='add-to-list')await chooseList();
    if(button.dataset.chooseList){
      button.disabled=true;const mid=state.detail.id;
      await api(`lists/${button.dataset.chooseList}/items`,{method:'POST',body:JSON.stringify({media_id:mid})});
      $('choose-list-dialog').close();renderDetail(await api(`media/${mid}`));if($('lists-dialog').open)await openLists();else await refreshHomeListThumbnails();toast('Média ajouté à la liste.');
    }
    if(button.id==='new-list-toggle'){$('new-list-form').hidden=false;$('new-list-form').elements.name.focus();}
    if(button.id==='list-add-button'){$('list-search-area').hidden=!$('list-search-area').hidden;if(!$('list-search-area').hidden)$('list-search').focus();}
    if(button.id==='rename-list'){
      const name=localizedPrompt('Nom de la liste',$('selected-list').selectedOptions[0].textContent.replace(/ · \d+$/,''));
      if(name?.trim()){const existing=(await api('lists')).find(l=>l.id===state.listId);await api(`lists/${state.listId}`,{method:'PUT',body:JSON.stringify({name:name.trim(),description:existing.description})});await openLists();}
    }
    if(button.id==='delete-list'&&localizedConfirm('Supprimer cette liste ? Les médias restent dans le catalogue.')){await api(`lists/${state.listId}`,{method:'DELETE'});await openLists();}
    if(button.id==='list-load-more'){button.disabled=true;await loadList({append:true});}
    if(button.id==='list-sort-order'){state.listOrder=state.listOrder==='asc'?'desc':'asc';button.textContent=state.listOrder==='asc'?'↑ Croissant':'↓ Décroissant';savePreferences();await loadList();}
    if(button.dataset.addListMedia){button.disabled=true;await api(`lists/${state.listId}/items`,{method:'POST',body:JSON.stringify({media_id:button.dataset.addListMedia})});await loadList();await refreshHomeListThumbnails();await searchList();}
    if(button.dataset.removeListMedia){button.disabled=true;await api(`lists/${state.listId}/items/${button.dataset.removeListMedia}`,{method:'DELETE'});await loadList();await refreshHomeListThumbnails();if($('list-search')?.value)await searchList();}
    if(button.dataset.moveList){button.disabled=true;await moveList(button.dataset.moveList,Number(button.dataset.direction));await refreshHomeListThumbnails();}
    if(button.dataset.copyFile){
      const file=state.detail.editions.find(e=>e.id===button.dataset.copyFile).metadata.file;
      if(navigator.clipboard&&window.isSecureContext){await navigator.clipboard.writeText(file);toast('Chemin copié.');}
      else localizedPrompt('Copiez le chemin du fichier',file);
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
  if(button.id==='watch-button'){
    if(state.detail?.kind==='tvshow')saveEpisodeWatches(!watchState(state.detail).watched);
    else savePersonal({watched:state.detail.watch_status!=='watched'});
  }
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


// JSON catalogue backups: the server is the authoritative library store.
document.addEventListener('click',async event=>{
  const button=event.target.closest('#backup-export');
  if(!button)return;
  button.disabled=true;
  try {
    const backup=await api('backup');
    const objectURL=URL.createObjectURL(new Blob([JSON.stringify(backup,null,2)],{type:'application/json'}));
    const link=document.createElement('a');
    link.href=objectURL;
    link.download='media-catalog-'+new Date().toISOString().slice(0,10)+'.json';
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(()=>URL.revokeObjectURL(objectURL),30000);
    toast('Export JSON téléchargé.');
  }catch(e){toast(e.message);}
  finally{button.disabled=false;}
});
document.addEventListener('change',async event=>{
  if(event.target.id!=='backup-import-file')return;
  const file=event.target.files?.[0];
  event.target.value='';
  if(!file)return;
  if(state.role!=='admin'){toast('Accès administrateur nécessaire.');return;}
  if(file.size>100*1024*1024){toast('Le fichier JSON dépasse 100 Mio.');return;}
  if(!localizedConfirm('Importer cette sauvegarde et remplacer tout le catalogue, ses notes, visionnages et listes ?'))return;
  const button=$('backup-export');
  if(button)button.disabled=true;
  try {
    const content=await file.text();
    const backup=JSON.parse(content);
    const result=await api('backup',{method:'POST',body:JSON.stringify(backup)});
    toast('Catalogue restauré : '+result.media+' médias et '+result.lists+' listes. Vignettes en régénération.');
    await refresh();
    await refreshHomeListThumbnails();
    await connections();
    await startKodiRemote({api,url,role:state.role});
  }catch(e){toast(e.message);}
  finally{if(button)button.disabled=false;}
});

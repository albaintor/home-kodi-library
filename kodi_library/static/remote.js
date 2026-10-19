/** Kodi remote overlay and live status (the browser never sees Kodi credentials). */
const safe = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let remote = null;
export function stopKodiRemote() { remote?.stop(); remote = null; }
export async function startKodiRemote({api, url, role}) {
  stopKodiRemote();
  const controller = new KodiRemote(api, url, role);
  remote = controller;
  await controller.start();
}

class KodiRemote {
  constructor(api, url, role) {
    this.api=api; this.url=url; this.role=role; this.players=[]; this.player=null;
    this.state={active:false}; this.socket=null; this.timer=null; this.closed=false;
    this.retry=1500; this.page=0; this.connected=false;
    this.storageKey='media_catalog.remote.player:'+new URL('.',location.href).pathname;
  }
  async start() {
    try { this.players=await this.api('players'); } catch { return; }
    if(this.closed || !this.players.length) return;
    document.getElementById('kodi-remote-indicator')?.remove();
    document.getElementById('kodi-remote-dialog')?.remove();
    document.querySelector('.main-header nav').insertAdjacentHTML('afterbegin',
      '<button id="kodi-remote-indicator" type="button" class="quiet remote-indicator" title="Télécommande Kodi">◉ Kodi</button>');
    document.body.insertAdjacentHTML('beforeend', `
      <dialog id="kodi-remote-dialog" class="remote-dialog" aria-labelledby="remote-heading">
        <div class="remote-heading"><h2 id="remote-heading">Télécommande Kodi</h2><button id="remote-close" aria-label="Fermer">×</button></div>
        <label class="remote-player-label">Lecteur <select id="remote-player"></select></label>
        <p id="remote-connection" class="remote-connection" role="status"></p>
        <div class="remote-tabs" role="tablist">
          <button id="remote-tab-now" type="button" aria-selected="true" role="tab">Lecture en cours</button>
          <button id="remote-tab-controls" type="button" aria-selected="false" role="tab">Contrôles</button>
        </div>
        <section id="remote-now" class="remote-panel">
          <img id="remote-art" alt="" hidden>
          <h3 id="remote-title">Aucune lecture</h3>
          <p id="remote-subtitle"></p>
          <input id="remote-seek" aria-label="Position de lecture" type="range" min="0" max="100" step=".1" value="0">
          <div class="remote-times"><span id="remote-elapsed">0:00</span><span id="remote-total">0:00</span></div>
          <div class="remote-transport">
            <button data-remote-action="previous" aria-label="Précédent">⏮</button>
            <button data-remote-action="playpause" id="remote-playpause" aria-label="Lecture ou pause">⏯</button>
            <button data-remote-action="next" aria-label="Suivant">⏭</button>
            <button data-remote-action="stop" aria-label="Stop">⏹</button>
          </div>
        </section>
        <section id="remote-controls" class="remote-panel" hidden>
          <div class="remote-dpad">
            <span></span><button data-remote-action="up" aria-label="Haut">▲</button><span></span>
            <button data-remote-action="left" aria-label="Gauche">◀</button><button data-remote-action="select" aria-label="OK">OK</button><button data-remote-action="right" aria-label="Droite">▶</button>
            <span></span><button data-remote-action="down" aria-label="Bas">▼</button><span></span>
          </div>
          <div class="remote-secondary">
            <button data-remote-action="back">Retour</button><button data-remote-action="home">Accueil</button>
            <button data-remote-action="context">Contexte</button><button data-remote-action="info">Infos</button>
            <button data-remote-action="osd">OSD</button><button data-remote-action="fullscreen">Plein écran</button>
            <button data-remote-action="subtitle_next">Sous-titres +</button><button data-remote-action="subtitle_off">ST off</button>
            <button data-remote-action="audio_next">Piste audio +</button>
          </div>
          <p id="remote-stream-info" class="remote-stream-info"></p>
        </section>
        <p id="remote-error" role="alert"></p>
      </dialog>`);
    this.indicator=document.getElementById('kodi-remote-indicator');
    this.dialog=document.getElementById('kodi-remote-dialog');
    const select=document.getElementById('remote-player');
    select.innerHTML=this.players.map(p=>`<option value="${safe(p.id)}">${safe(p.name)}</option>`).join('');
    const saved=localStorage.getItem(this.storageKey);
    this.player=this.players.find(p=>p.id===saved)||this.players[0];
    select.value=this.player.id;
    select.addEventListener('change',()=>{this.player=this.players.find(p=>p.id===select.value);localStorage.setItem(this.storageKey,this.player.id);this.switchPlayer();});
    this.indicator.addEventListener('click',()=>this.dialog.showModal());
    document.getElementById('remote-close').addEventListener('click',()=>this.dialog.close());
    document.getElementById('remote-tab-now').addEventListener('click',()=>this.showPage(0));
    document.getElementById('remote-tab-controls').addEventListener('click',()=>this.showPage(1));
    this.dialog.addEventListener('click',e=>{
      const button=e.target.closest('[data-remote-action]');
      if(button) this.command(button.dataset.remoteAction);
    });
    document.getElementById('remote-seek').addEventListener('change',e=>this.command('seek',Number(e.target.value)));
    this.showPage(0);
    // Select an already playing Kodi when no previous player was explicitly chosen.
    if(!saved && this.players.length>1) {
      for(const p of this.players) {
        try { const state=await this.api(`players/${p.id}/remote`); if(state.active) {this.player=p;select.value=p.id;break;} } catch {}
      }
    }
    this.switchPlayer();
    document.addEventListener('visibilitychange',this.onVisibility=()=>{if(!document.hidden && this.socket?.readyState!==WebSocket.OPEN) this.switchPlayer();});
  }
  showPage(page) {
    this.page=page;
    document.getElementById('remote-now').hidden=page!==0;
    document.getElementById('remote-controls').hidden=page!==1;
    document.getElementById('remote-tab-now').setAttribute('aria-selected',String(page===0));
    document.getElementById('remote-tab-controls').setAttribute('aria-selected',String(page===1));
  }
  switchPlayer() {
    clearTimeout(this.timer);
    if(this.socket) {this.socket.onclose=null;this.socket.close();this.socket=null;}
    this.connected=false; this.retry=1500; this.render({active:false});
    this.openSocket();
  }
  openSocket() {
    if(this.closed || !this.player) return;
    const selected=this.player.id;
    const http=this.url(`api/v1/players/${selected}/events`);
    const endpoint=http.replace(/^http/i,'ws');
    try {
      const ws=new WebSocket(endpoint);
      this.socket=ws;
      ws.onopen=()=>{if(this.socket!==ws)return;this.connected=true;this.retry=1500;this.setConnection('Connecté');};
      ws.onmessage=event=>{
        if(this.socket!==ws)return;
        try{
          const message=JSON.parse(event.data);
          if(message.type==='state') {this.render(message.state);this.setConnection('Connecté');}
          else if(message.type==='offline') {this.setConnection(message.message||'Kodi indisponible');this.render({active:false});}
        }catch{}
      };
      ws.onclose=()=>{if(this.socket!==ws || this.closed)return;this.socket=null;this.connected=false;this.setConnection('Connexion interrompue');this.timer=setTimeout(()=>this.openSocket(),this.retry);this.retry=Math.min(this.retry*2,30000);};
      ws.onerror=()=>ws.close();
    } catch {
      this.setConnection('WebSocket indisponible');
      this.timer=setTimeout(()=>this.openSocket(),this.retry);
    }
  }
  setConnection(message){const el=document.getElementById('remote-connection');if(el)el.textContent=message;}
  render(state) {
    this.state=state||{active:false};
    const s=this.state, active=!!s.active;
    this.indicator.classList.toggle('playing',active);
    this.indicator.textContent=active?`▶ ${s.title||'Kodi'}`:'◉ Kodi';
    document.getElementById('remote-title').textContent=active?s.title||'Lecture Kodi':'Aucune lecture en cours';
    document.getElementById('remote-subtitle').textContent=active?[s.show_title,s.season!=null&&s.episode!=null?`S${s.season} E${s.episode}`:''].filter(Boolean).join(' · '):'';
    const art=document.getElementById('remote-art');
    const src=active&&s.artwork?this.url(s.artwork):'';
    if(art.dataset.path!==src){art.dataset.path=src;art.removeAttribute('src');if(src)art.src=src;}
    art.hidden=!src;
    const seek=document.getElementById('remote-seek');
    seek.value=String(Math.max(0,Math.min(100,Number(s.percentage)||0)));
    seek.disabled=!active||this.role!=='admin';
    document.getElementById('remote-elapsed').textContent=this.time(s.position||0);
    document.getElementById('remote-total').textContent=this.time(s.duration||0);
    document.getElementById('remote-playpause').textContent=s.paused?'▶':'⏸';
    for(const b of this.dialog.querySelectorAll('[data-remote-action]')) {
      const needsPlayback=/^(playpause|stop|previous|next|subtitle_|audio_)/.test(b.dataset.remoteAction);
      b.disabled=this.role!=='admin'||(needsPlayback&&!active);
    }
    const audio=s.current_audio?.name||s.current_audio?.language||'';
    const sub=s.subtitles_enabled?(s.current_subtitle?.name||s.current_subtitle?.language||'Activés'):'Désactivés';
    document.getElementById('remote-stream-info').textContent=active?`Audio : ${audio||'—'} · Sous-titres : ${sub}`:'';
  }
  time(seconds){let n=Math.max(0,Math.floor(seconds||0));const h=Math.floor(n/3600);n%=3600;return h?`${h}:${String(Math.floor(n/60)).padStart(2,'0')}:${String(n%60).padStart(2,'0')}`:`${Math.floor(n/60)}:${String(n%60).padStart(2,'0')}`;}
  async command(action, percentage) {
    if(this.role!=='admin'||!this.player)return;
    const out=document.getElementById('remote-error');out.textContent='';
    try {await this.api(`players/${this.player.id}/remote`,{method:'POST',body:JSON.stringify({action,...(action==='seek'?{percentage}:{})})});}
    catch(e){out.textContent=e.message;}
  }
  stop() {
    this.closed=true;clearTimeout(this.timer);
    if(this.socket){this.socket.onclose=null;this.socket.close();this.socket=null;}
    if(this.onVisibility)document.removeEventListener('visibilitychange',this.onVisibility);
    this.dialog?.remove();this.indicator?.remove();
  }
}

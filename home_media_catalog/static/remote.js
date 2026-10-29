/** Kodi remote overlay and live status (the browser never sees Kodi credentials). */
const safe = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let remote = null;
export function stopKodiRemote() { remote?.stop(); remote = null; }
export function showKodiRemote(playerID) {
  if(!remote) return false;
  return remote.present(playerID);
}
export async function startKodiRemote({api, url, role}) {
  stopKodiRemote();
  const controller = new KodiRemote(api, url, role);
  remote = controller;
  await controller.start();
}

class KodiRemote {
  constructor(api, url, role) {
    this.api=api; this.url=url; this.role=role; this.players=[]; this.player=null;
    this.state={active:false}; this.sockets=new Map(); this.timers=new Map(); this.states=new Map(); this.closed=false; this.manualSelection=false;
    this.page=0; this.received=new Map(); this.refreshInterval=null; this.clockInterval=null; this.userSeeking=false;
    this.httpConnected=new Map(); this.wsConnected=new Map(); this.socketRetryDelay=new Map();
    this.volume=100;this.muted=false;this.volumeEditing=false;this.volumeInterval=null;
    this.touchpadStart=null;this.touchpadDirection=null;this.touchpadTimer=null;
    this.storageKey='media_catalog.remote.player:'+new URL('.',location.href).pathname;
  }
  async start() {
    try { this.players=await this.api('players'); } catch { this.players=[]; }
    if(this.closed) return;
    document.getElementById('kodi-remote-indicator')?.remove();
    document.getElementById('kodi-remote-dialog')?.remove();
    // Until Kodi states load, keep the permanent Kodi shortcut visible.
    document.getElementById('kodi-remote-button').hidden=false;
    document.getElementById('kodi-remote-button').insertAdjacentHTML('afterend',
      '<button id="kodi-remote-indicator" type="button" class="quiet remote-indicator" title="Lecture Kodi en cours" aria-label="Lecture Kodi en cours" hidden>▶</button>');
    document.body.insertAdjacentHTML('beforeend', `
      <dialog id="kodi-remote-dialog" class="remote-dialog" aria-labelledby="remote-heading">
        <div class="remote-heading"><h2 id="remote-heading">Télécommande Kodi</h2><button id="remote-close" aria-label="Fermer">×</button></div>
        <label class="remote-player-label">Lecteur <select id="remote-player"></select></label><button id="remote-connection" class="remote-connection-button" type="button" title="Réessayer la connexion Kodi">Connexion en cours… ↻</button>
        <div id="remote-playing-options" class="remote-playing-options" hidden></div>
        <button id="remote-wake" class="secondary" type="button" title="Wake-on-LAN" hidden>⏻ Réveiller Kodi</button>
        <div class="remote-tabs" role="tablist">
          <button id="remote-tab-now" type="button" aria-selected="true" role="tab">Lecture en cours</button>
          <button id="remote-tab-controls" type="button" aria-selected="false" role="tab">Contrôles</button>
        </div>
        <section id="remote-now" class="remote-panel">
          <img id="remote-art" alt="" hidden>
          <p id="remote-art-error" class="remote-art-error" role="status" hidden></p>
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
          <div class="remote-volume" role="group" aria-label="Volume Kodi">
            <button id="remote-mute" type="button" aria-label="Couper le son">🔊</button>
            <input id="remote-volume" type="range" min="0" max="100" value="100" step="1" aria-label="Volume Kodi">
            <output id="remote-volume-value">100 %</output>
          </div>
        </section>
        <section id="remote-controls" class="remote-panel" hidden>
          <div class="remote-touchpad-tabs" role="group" aria-label="Type de contrôle">
            <button id="remote-mode-dpad" type="button" aria-pressed="true">Commandes</button>
            <button id="remote-mode-touchpad" type="button" aria-pressed="false">Pavé tactile</button>
          </div>
          <div class="remote-touchpad" id="remote-touchpad" hidden role="button" tabindex="0" aria-label="Touchpad Kodi : glisser pour naviguer, toucher pour sélectionner">Glisser pour naviguer · toucher pour OK</div>
          <div class="remote-dpad" id="remote-dpad">
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
          <div class="remote-stream-choices">
            <label>Audio <select id="remote-audio-select" aria-label="Choisir la piste audio"></select></label>
            <label>Sous-titres <select id="remote-subtitle-select" aria-label="Choisir la piste de sous-titres"></select></label>
          </div>
          <p id="remote-stream-info" class="remote-stream-info"></p>
        </section>
        <p id="remote-error" role="alert"></p>
      </dialog>`);
    this.indicator=document.getElementById('kodi-remote-indicator');
    this.dialog=document.getElementById('kodi-remote-dialog');
    const cover=document.getElementById('remote-art');
    const coverError=document.getElementById('remote-art-error');
    cover.addEventListener('load',()=>{
      coverError.hidden=true;
      coverError.textContent='';
    });
    cover.addEventListener('error',()=>{
      cover.hidden=true;
      coverError.hidden=false;
      coverError.textContent='Artwork Kodi inaccessible (consulter les logs du serveur).';
    });
    const select=document.getElementById('remote-player');
    select.innerHTML=this.players.map(p=>`<option value="${safe(p.id)}">⚪ ${safe(p.name)}</option>`).join('');
    const saved=localStorage.getItem(this.storageKey);
    this.player=this.players.find(p=>p.id===saved)||this.players[0];
    if(this.player) select.value=this.player.id;
    select.addEventListener('change',()=>{this.manualSelection=true;this.player=this.players.find(p=>p.id===select.value);localStorage.setItem(this.storageKey,this.player.id);this.switchPlayer();});
    this.open = () => this.present();
    this.headerButton = document.getElementById('kodi-remote-button');
    this.headerButton.addEventListener('click',this.open);
    this.indicator.addEventListener('click',this.open);
    document.getElementById('remote-close').addEventListener('click',()=>this.dialog.close());
    document.getElementById('remote-connection').addEventListener('click',()=>this.reconnect());
    document.getElementById('remote-wake').addEventListener('click',()=>void this.wake());
    document.getElementById('remote-mute').addEventListener('click',()=>void this.setAudioProperty('set_mute',{muted:!this.muted}));
    const volumeSlider=document.getElementById('remote-volume');
    volumeSlider.addEventListener('pointerdown',()=>{this.volumeEditing=true;});
    volumeSlider.addEventListener('input',()=>{
      this.volumeEditing=true;
      document.getElementById('remote-volume-value').textContent=volumeSlider.value+' %';
    });
    volumeSlider.addEventListener('change',()=>{
      this.volumeEditing=false;
      void this.setAudioProperty('set_volume',{volume:Number(volumeSlider.value)});
    });
    document.getElementById('remote-mode-dpad').addEventListener('click',()=>this.setTouchpadMode(false));
    document.getElementById('remote-mode-touchpad').addEventListener('click',()=>this.setTouchpadMode(true));
    this.bindTouchpad();
    document.getElementById('remote-tab-now').addEventListener('click',()=>this.showPage(0));
    document.getElementById('remote-tab-controls').addEventListener('click',()=>this.showPage(1));
    this.dialog.addEventListener('click',e=>{
      const button=e.target.closest('[data-remote-action]');
      if(button) this.command(button.dataset.remoteAction);
    });
    const seekInput=document.getElementById('remote-seek');
    seekInput.addEventListener('pointerdown',()=>{this.userSeeking=true;});
    seekInput.addEventListener('input',()=>{this.userSeeking=true;this.updateClock();});
    seekInput.addEventListener('change',e=>{
      this.userSeeking=false;
      const duration=Number(this.state?.duration)||0;
      if(duration>0)this.command('seek',null,null,duration*Number(e.target.value)/100);
    });
    document.getElementById('remote-audio-select').addEventListener('change',e=>{
      if(e.target.value!=='')this.command('audio_select',null,Number(e.target.value));
    });
    document.getElementById('remote-subtitle-select').addEventListener('change',e=>{
      this.command(e.target.value==='off'?'subtitle_off':'subtitle_select',
        null,e.target.value==='off'?null:Number(e.target.value));
    });
    this.showPage(0);
    if(!this.players.length) {
      this.setConnection('Aucun lecteur Kodi configuré. Ajoutez-en un dans Paramètres.');
      this.render({active:false});
      select.disabled=true;
      return;
    }
    this.manualSelection=!!saved;
    await Promise.allSettled(this.players.map(async player=>{
      try {
        const state=await this.api(`players/${player.id}/remote`);
        this.states.set(player.id,state);
        this.received.set(player.id,performance.now());
        this.httpConnected.set(player.id,true);
      } catch { this.httpConnected.set(player.id,false); }
    }));
    this.refreshChoices();
    this.setConnection();
    this.players.forEach(player=>this.openSocket(player));
    // The HTTP endpoint remains authoritative even when the browser's WS
    // upgrade is blocked by a reverse proxy or fails after device sleep.
    this.clockInterval=setInterval(()=>{if(!document.hidden)this.updateClock();},500);
    this.volumeInterval=setInterval(()=>{if(!document.hidden&&!this.volumeEditing)void this.refreshVolume();},5000);
    void this.refreshVolume();
    this.refreshInterval=setInterval(()=>{
      if(!document.hidden) this.players.forEach(p=>this.refreshPlayer(p));
    },5000);
    document.addEventListener('visibilitychange',this.onVisibility=()=>{
      if(!document.hidden) this.players.forEach(p=>{
        this.refreshPlayer(p);
        if(this.sockets.get(p.id)?.readyState!==WebSocket.OPEN)this.openSocket(p);
      });
    });
  }
  present(playerID) {
    if(this.closed || !this.dialog)return false;
    if(playerID && this.players.some(p=>p.id===playerID)) {
      this.manualSelection=true;
      this.player=this.players.find(p=>p.id===playerID);
      document.getElementById('remote-player').value=playerID;
      localStorage.setItem(this.storageKey,playerID);
      this.switchPlayer();
      // Playback may only become available after Player.Open has returned.
      void this.refreshPlayer(this.player);
    }
    this.showPage(0);
    void this.refreshVolume();
    if(!this.dialog.open)this.dialog.showModal();
    return true;
  }
  async reconnect() {
    const player=this.player;
    if(!player)return;
    const button=document.getElementById('remote-connection');
    if(button)button.textContent='Reconnexion…';
    await this.refreshPlayer(player);
    const socket=this.sockets.get(player.id);
    if(socket?.readyState!==WebSocket.OPEN) {
      if(socket) {socket.onclose=null;socket.close();this.sockets.delete(player.id);}
      this.openSocket(player);
    }
    this.setConnection();
    void this.refreshVolume();
  }
  async refreshPlayer(player) {
    if(this.closed || !player)return;
    try {
      const status=await this.api(`players/${player.id}/remote`);
      if(this.closed)return;
      this.states.set(player.id,status);
      this.received.set(player.id,performance.now());
      this.httpConnected.set(player.id,true);
      if(this.player?.id===player.id)this.render(status);
      this.setConnection();
      this.refreshChoices();
    }catch {
      // If only the event socket is unavailable, do not mark Kodi offline.
      // A real HTTP failure is independently observable here.
      this.httpConnected.set(player.id,false);
      this.setConnection();
    }
  }
  updateClock() {
    if(!this.player || !this.state?.active)return;
    const s=this.state, duration=Number(s.duration)||0;
    const age=Math.max(0,(performance.now()-(this.received.get(this.player.id)??performance.now()))/1000);
    const rate=s.paused?0:Math.max(0,Number(s.speed)||0);
    const seconds=Math.max(0,Math.min(duration>0?duration:Infinity,(Number(s.position)||0)+age*rate));
    const seek=document.getElementById('remote-seek');
    if(!this.userSeeking && duration>0)seek.value=String(100*seconds/duration);
    document.getElementById('remote-elapsed').textContent=this.time(this.userSeeking&&duration>0?duration*Number(seek.value)/100:seconds);
  }
  showPage(page) {
    this.page=page;
    document.getElementById('remote-now').hidden=page!==0;
    document.getElementById('remote-controls').hidden=page!==1;
    document.getElementById('remote-tab-now').setAttribute('aria-selected',String(page===0));
    document.getElementById('remote-tab-controls').setAttribute('aria-selected',String(page===1));
  }
  switchPlayer() {
    this.render(this.states.get(this.player?.id)||{active:false});
    void this.refreshVolume();
    this.refreshChoices();
    this.setConnection();
  }
  refreshChoices() {
    const active=this.players.filter(p=>this.states.get(p.id)?.active);
    if(active.length===1 && !this.manualSelection) {
      this.player=active[0];
      document.getElementById('remote-player').value=this.player.id;
      this.render(this.states.get(this.player.id));
    }
    const choices=document.getElementById('remote-playing-options');
    if(!choices)return;
    choices.hidden=active.length<2;
    choices.innerHTML=active.length<2?'':`<strong>Plusieurs lectures en cours : choisissez Kodi</strong>`+
      active.map(p=>`<button type="button" data-select-kodi="${safe(p.id)}" class="${p.id===this.player?.id?'selected':''}">▶ ${safe(p.name)} · ${safe(this.states.get(p.id)?.title||'Lecture en cours')}</button>`).join('');
    choices.querySelectorAll('[data-select-kodi]').forEach(button=>button.addEventListener('click',()=>{
      this.manualSelection=true;
      this.player=this.players.find(p=>p.id===button.dataset.selectKodi);
      localStorage.setItem(this.storageKey,this.player.id);
      document.getElementById('remote-player').value=this.player.id;
      this.switchPlayer();
    }));
    this.indicator.textContent=active.length>1?`▶ ${active.length} Kodi`:
      active.length===1?`▶ ${this.states.get(active[0].id)?.title||active[0].name}`:'◉ Kodi';
    // One header entry at a time: the persistent Kodi button while idle,
    // replaced with the Now Playing indicator while any Kodi is active.
    const hasPlayback=active.length>0;
    this.headerButton.hidden=hasPlayback;
    this.indicator.hidden=!hasPlayback;
    this.indicator.classList.toggle('playing',hasPlayback);
    this.setConnection();
  }
  openSocket(player) {
    if(this.closed || !player || this.sockets.get(player.id)?.readyState===WebSocket.OPEN ||
       this.sockets.get(player.id)?.readyState===WebSocket.CONNECTING) return;
    clearTimeout(this.timers.get(player.id));
    const target=this.url(`api/v1/players/${player.id}/events`).replace(/^http/i,'ws');
    try {
      const ws=new WebSocket(target);
      this.sockets.set(player.id,ws);
      ws.onopen=()=>{if(this.sockets.get(player.id)!==ws)return;this.wsConnected.set(player.id,true);this.socketRetryDelay.set(player.id,5000);if(this.player?.id===player.id)this.setConnection();};
      ws.onmessage=event=>{
        if(this.sockets.get(player.id)!==ws)return;
        try {
          const msg=JSON.parse(event.data);
          if(msg.type==='state'){
            this.states.set(player.id,msg.state);
            this.received.set(player.id,performance.now());
            this.httpConnected.set(player.id,true);
            if(this.player?.id===player.id) {this.render(msg.state);this.setConnection();}
            this.refreshChoices();
          } else if(msg.type==='offline'){
            this.httpConnected.set(player.id,false);
            this.states.set(player.id,{active:false});
            if(this.player?.id===player.id){this.setConnection();this.render({active:false});}
            this.refreshChoices();
          }
        }catch{}
      };
      ws.onclose=()=>{
        if(this.sockets.get(player.id)!==ws||this.closed)return;
        this.sockets.delete(player.id);
        this.wsConnected.set(player.id,false);
        if(this.player?.id===player.id)this.setConnection();
        const delay=this.socketRetryDelay.get(player.id)||5000;
        this.socketRetryDelay.set(player.id,Math.min(60000,Math.round(delay*1.5)));
        this.timers.set(player.id,setTimeout(()=>this.openSocket(player),delay));
      };
      ws.onerror=()=>ws.close();
    }catch {
      this.timers.set(player.id,setTimeout(()=>this.openSocket(player),5000));
    }
  }
  setConnection() {
    const select=document.getElementById('remote-player');
    if(!select)return;
    const marker=(id)=>this.httpConnected.get(id)===true?'🟢':
      this.httpConnected.get(id)===false?'🔴':'⚪';
    // Native <select> options cannot reliably render custom HTML or CSS.
    // Emoji markers work on Safari/iOS and browsers, directly inside the list.
    for(const player of this.players) {
      const option=Array.from(select.options).find(o=>o.value===player.id);
      if(option) option.textContent=`${marker(player.id)} ${player.name}`;
    }
    const id=this.player?.id, ws=id&&this.wsConnected.get(id)===true;
    const ready=id?this.httpConnected.get(id):undefined;
    select.title=!id?'Aucun lecteur Kodi configuré':
      ready===true?(ws?'Kodi connecté · notifications actives':
                       'Kodi connecté · notifications en reconnexion, suivi HTTP actif'):
      ready===false?'Kodi indisponible par HTTP JSON-RPC':
                    'Vérification de la connexion Kodi';
    select.setAttribute('aria-label',`Instance Kodi : ${select.title}`);
    const wake=document.getElementById('remote-wake');
    if(wake){wake.hidden=!this.player?.mac;wake.disabled=this.role!=='admin'||!this.player?.mac;}
    const indicator=document.getElementById('remote-connection');
    if(indicator) {
      indicator.textContent=!id?'Aucun lecteur configuré':
        ready===true?'Connecté ↻':ready===false?'Déconnecté ↻':'Connexion en cours… ↻';
      indicator.disabled=!id;
      indicator.title=select.title+' · Toucher pour reconnecter';
      indicator.setAttribute('aria-label',indicator.textContent+' · Reconnecter Kodi');
    }
  }

  render(state) {
    this.state=state||{active:false};
    const s=this.state, active=!!s.active;
    document.getElementById('remote-title').textContent=active?s.title||'Lecture Kodi':'Aucune lecture en cours';
    document.getElementById('remote-subtitle').textContent=active?[s.show_title,s.season!=null&&s.episode!=null?`S${s.season} E${s.episode}`:''].filter(Boolean).join(' · '):'';
    const art=document.getElementById('remote-art');
    const src=active&&s.artwork?this.url(String(s.artwork).replace(/^\/+/,'')):'';
    if(art.dataset.path!==src){
      art.dataset.path=src;
      const warning=document.getElementById('remote-art-error');
      warning.hidden=true;
      warning.textContent='';
      art.removeAttribute('src');
      if(src)art.src=src;
    }
    art.hidden=!src;
    const seek=document.getElementById('remote-seek');
    if(!this.userSeeking)seek.value=String(Math.max(0,Math.min(100,Number(s.percentage)||0)));
    seek.disabled=!active||this.role!=='admin';
    this.updateClock();
    document.getElementById('remote-total').textContent=this.time(s.duration||0);
    document.getElementById('remote-playpause').textContent=s.paused?'▶':'⏸';
    for(const b of this.dialog.querySelectorAll('[data-remote-action]')) {
      const needsPlayback=/^(playpause|stop|previous|next|subtitle_|audio_)/.test(b.dataset.remoteAction);
      b.disabled=this.role!=='admin'||(needsPlayback&&!active);
    }
    const audio=s.current_audio?.name||s.current_audio?.language||'';
    const sub=s.subtitles_enabled?(s.current_subtitle?.name||s.current_subtitle?.language||'Activés'):'Désactivés';
    document.getElementById('remote-stream-info').textContent=active?`Audio : ${audio||'—'} · Sous-titres : ${sub}`:'';
    const audioSelect=document.getElementById('remote-audio-select');
    const audioItems=Array.isArray(s.audio_streams)?s.audio_streams:[];
    audioSelect.innerHTML=audioItems.map((item,i)=>
      `<option value="${item.index??i}">${safe([item.language,item.name].filter(Boolean).join(' · ')||`Piste ${i+1}`)}</option>`).join('');
    audioSelect.disabled=!active||!audioItems.length||this.role!=='admin';
    if(s.current_audio?.index!=null)audioSelect.value=String(s.current_audio.index);
    const subtitleSelect=document.getElementById('remote-subtitle-select');
    const subItems=Array.isArray(s.subtitles)?s.subtitles:[];
    subtitleSelect.innerHTML='<option value="off">Désactivés</option>'+subItems.map((item,i)=>
      `<option value="${item.index??i}">${safe([item.language,item.name].filter(Boolean).join(' · ')||`Piste ${i+1}`)}</option>`).join('');
    subtitleSelect.disabled=!active||this.role!=='admin';
    if(s.subtitles_enabled&&s.current_subtitle?.index!=null)subtitleSelect.value=String(s.current_subtitle.index);
    else subtitleSelect.value='off';
  }
  async refreshVolume() {
    if(!this.player)return;
    const id=this.player.id;
    try {
      const status=await this.api('players/'+encodeURIComponent(id)+'/volume');
      if(this.closed||this.player?.id!==id)return;
      this.volume=Number(status.volume)||0;
      this.muted=status.muted===true;
      const slider=document.getElementById('remote-volume');
      if(!this.volumeEditing){slider.value=String(this.volume);document.getElementById('remote-volume-value').textContent=this.volume+' %';}
      const mute=document.getElementById('remote-mute');
      mute.textContent=this.muted?'🔇':'🔊';
      mute.setAttribute('aria-label',this.muted?'Réactiver le son':'Couper le son');
      mute.disabled=this.role!=='admin';slider.disabled=this.role!=='admin';
    } catch { /* Volume remains independent of playback and websocket state. */ }
  }
  async setAudioProperty(action,payload) {
    if(this.role!=='admin'||!this.player)return;
    try {
      await this.api('players/'+encodeURIComponent(this.player.id)+'/remote',{
        method:'POST',body:JSON.stringify({action,...payload})
      });
      await this.refreshVolume();
    } catch(e){document.getElementById('remote-error').textContent=e.message;}
  }
  async wake() {
    if(this.role!=='admin'||!this.player?.mac)return;
    const button=document.getElementById('remote-wake');button.disabled=true;
    try {
      await this.api('players/'+encodeURIComponent(this.player.id)+'/wake',{method:'POST'});
      document.getElementById('remote-error').textContent='Signal Wake-on-LAN envoyé.';
    }catch(e){document.getElementById('remote-error').textContent=e.message;}
    finally{button.disabled=false;}
  }
  setTouchpadMode(enabled) {
    document.getElementById('remote-touchpad').hidden=!enabled;
    document.getElementById('remote-dpad').hidden=enabled;
    document.getElementById('remote-mode-dpad').setAttribute('aria-pressed',String(!enabled));
    document.getElementById('remote-mode-touchpad').setAttribute('aria-pressed',String(enabled));
  }
  bindTouchpad() {
    const pad=document.getElementById('remote-touchpad');
    const direction=(dx,dy)=>Math.abs(dx)>Math.abs(dy)?(dx>0?'right':'left'):(dy>0?'down':'up');
    pad.addEventListener('pointerdown',event=>{
      if(event.button!==0)return;
      this.touchpadStart={x:event.clientX,y:event.clientY};
      this.touchpadDirection=null;
      pad.setPointerCapture(event.pointerId);
      clearInterval(this.touchpadTimer);
    });
    pad.addEventListener('pointermove',event=>{
      if(!this.touchpadStart)return;
      const dx=event.clientX-this.touchpadStart.x,dy=event.clientY-this.touchpadStart.y;
      if(Math.hypot(dx,dy)<32)return;
      const next=direction(dx,dy);
      if(next!==this.touchpadDirection){
        this.touchpadDirection=next;
        clearInterval(this.touchpadTimer);
        void this.command(next);
        this.touchpadTimer=setInterval(()=>void this.command(next),230);
      }
    });
    const end=()=>{
      clearInterval(this.touchpadTimer);this.touchpadTimer=null;
      if(this.touchpadStart&&!this.touchpadDirection)void this.command('select');
      this.touchpadStart=null;this.touchpadDirection=null;
    };
    pad.addEventListener('pointerup',end);
    pad.addEventListener('pointercancel',()=>{this.touchpadStart=null;this.touchpadDirection=null;clearInterval(this.touchpadTimer);});
    pad.addEventListener('keydown',event=>{
      const action={Enter:'select',' ':'select',ArrowUp:'up',ArrowDown:'down',ArrowLeft:'left',ArrowRight:'right'}[event.key];
      if(action){event.preventDefault();void this.command(action);}
    });
  }
  time(seconds){let n=Math.max(0,Math.floor(seconds||0));const h=Math.floor(n/3600);n%=3600;return h?`${h}:${String(Math.floor(n/60)).padStart(2,'0')}:${String(n%60).padStart(2,'0')}`:`${Math.floor(n/60)}:${String(n%60).padStart(2,'0')}`;}
  async command(action, percentage, index, seconds) {
    if(this.role!=='admin'||!this.player)return;
    const out=document.getElementById('remote-error');out.textContent='';
    try {
      const player=this.player;
      await this.api(`players/${player.id}/remote`,{method:'POST',body:JSON.stringify({action,...(percentage!=null?{percentage}:{}),...(seconds!=null?{seconds}:{}),...(index!=null?{index}:{})})});
      // A successful command proves HTTP JSON-RPC works. Refresh now and
      // reinstate WS events if the connection was lost.
      void this.refreshPlayer(player);
      if(this.sockets.get(player.id)?.readyState!==WebSocket.OPEN)this.openSocket(player);
    } catch(e) {out.textContent=e.message;}
  }
  stop() {
    this.closed=true;
    clearInterval(this.clockInterval);clearInterval(this.refreshInterval);clearInterval(this.volumeInterval);
    clearInterval(this.touchpadTimer);
    this.timers.forEach(timer=>clearTimeout(timer));this.timers.clear();
    this.sockets.forEach(socket=>{socket.onclose=null;socket.close();});this.sockets.clear();
    if(this.onVisibility)document.removeEventListener('visibilitychange',this.onVisibility);
    if(this.headerButton && this.open)this.headerButton.removeEventListener('click',this.open);
    if(this.headerButton)this.headerButton.hidden=false;
    this.dialog?.remove();this.indicator?.remove();
  }
}

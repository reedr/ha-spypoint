// spypoint-gallery-card.js
// Lovelace card for the SPYPOINT get_photos service (returns response data), with a
// second source — classified detections from pyscript.spypoint_detection_photos — that
// returns the same shape plus a per-photo `detection` block shown in the lightbox.
// Full-width grid, full-aspect photos, with header controls:
//   - camera selector (All / a specific Spypoint device -> service_data.device_id)
//   - photo-count slider (-> service_data.limit; service caps at 100)
//   - detections mode menu (Detections / Rejections / Everything -> service_data.mode).
//     Detections keeps the photo grid (hover cycles the preview frames); Rejections and
//     Everything render as a metadata list with no thumbnails, and clicking a row lightboxes
//     the photo with the classifier metadata (species, confidence, first pass, verify, model, ms)
//   - time-window menu (-> service_data.date_start / date_end): "Since <current block>",
//     the three preceding blocks as bounded ranges, then "Since <current block - 24h>".
//     Day split into 8am-12pm, 12pm-4pm, 4pm-8pm, and a single 8pm-8am overnight block.
// Per-photo lifecycle badges (bottom-right):
//   tag "preview"     -> request button -> spypoint.request_hdvideo
//   tag "hdvideopend" -> pending status icon (suppressed if "hdvideo" tag is also present)
//   field hdVideo     -> centered play button -> lightbox
//   field hdVideoFailed=true -> error icon (replaces the pending icon)
//
// Lovelace usage:
//   type: custom:spypoint-gallery-card
//   title: Photos
//   service: spypoint.get_photos
//   request_service: spypoint.request_hdvideo
//   columns: 4
//   size: large
//   max_photos: 1000         # slider ceiling (must be <= the service's limit max)
//   refresh_interval: 0
//   response_key: photos
//   service_data:
//     config_entry_id: 01KTWFX4AMDKRET7RN2FW3CA6H
//     limit: 20

class SpypointGalleryCard extends HTMLElement {
  setConfig(config) {
    if (!config.service || !config.service.includes('.')) {
      throw new Error('Set "service" to "<domain>.<service>", e.g. spypoint.get_photos');
    }
    this._config = {
      title: 'Photos',
      columns: 3,
      refresh_interval: 0,
      response_key: 'photos',
      size: 'large',
      max_photos: 100,
      hover_interval: 300,    // ms per frame when cycling a preview on hover
      request_service: 'spypoint.request_hdvideo',
      detections_service: 'pyscript.spypoint_detection_photos', // detections source (get_photos shape + .detection)
      default_source: 'detections',                             // 'detections' | 'cloud'
      default_mode: 'detections',                               // 'detections' | 'rejections' | 'all'
      map_pulse_entity: 'input_text.spypoint_map_pulse', // bumped on each load to re-render the map
      map_green_key: 'spypoint_map_green',               // localStorage key of loaded-camera bases
      service_data: {},
      ...config,
    };
    this._photos = [];
    this._error = null;
    this._loaded = false;
    this._camsInit = false;
    this._hoverTimer = null;
    // Control state
    this._selectedCamera = '';   // '' = all cameras; otherwise an HA device_id
    this._source = this._config.default_source === 'cloud' ? 'cloud' : 'detections';
    this._mode = ['detections', 'rejections', 'disagree', 'all'].includes(this._config.default_mode) ? this._config.default_mode : 'detections';
    this._window = 'cur';        // window key: 'cur' | 'b0'..'bN' | 'h24' | 'today' | 'yday' | 'all'
    this._taggedOnly = false;    // only photos with a real (non-housekeeping) tag
    this._videoOnly = false;     // only photos that have an HD video
    const baseLimit = Number((this._config.service_data && this._config.service_data.limit) || 48);
    this._limit = Math.min(baseLimit, this._config.max_photos);
    this._sliderMax = this._config.max_photos;
    this._render();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first && this._config.refresh_interval > 0) {
      this._timer = setInterval(() => this._fetch(), this._config.refresh_interval * 1000);
    }
    // Populate the camera selector once the device registry is available, without
    // fetching any photos (the Load Photos button is still required to show images).
    // The slider keeps its limit-option value from the constructor.
    if (!this._camsInit && this._cameras().length) {
      this._camsInit = true;
      this._render();
    }
  }

  disconnectedCallback() {
    if (this._timer) clearInterval(this._timer);
  }

  getGridOptions() {
    return { columns: 'full', rows: 'auto', min_columns: 4 };
  }

  // Spypoint camera devices for the selector, drawn from the device registry.
  _cameras() {
    const cfgId = this._config.service_data && this._config.service_data.config_entry_id;
    const devices = (this._hass && this._hass.devices) || {};
    return Object.values(devices)
      .filter((d) => {
        if (cfgId && Array.isArray(d.config_entries)) return d.config_entries.includes(cfgId);
        return (d.manufacturer || '').toLowerCase() === 'spypoint';
      })
      .map((d) => ({ id: d.id, name: d.name_by_user || d.name || d.id }))
      .sort((a, b) => a.name.localeCompare(b.name));
  }

  // Publish the set of camera bases (device_tracker object_ids) that have photos in
  // the just-loaded set to localStorage, and bump the pulse helper so the map view
  // recolors those cameras green. photo.camera is the SPYPOINT camera_id, which is the
  // device's ('spypoint', <id>) identifier; the device's device_tracker gives the base.
  _publishLoadedCameras() {
    if (!this._hass) return;
    const entities = this._hass.entities || {};
    const devices = this._hass.devices || {};
    const devToBase = {};
    for (const eid in entities) {
      if (eid.indexOf('device_tracker.') === 0 && entities[eid] && entities[eid].device_id) {
        devToBase[entities[eid].device_id] = eid.slice('device_tracker.'.length);
      }
    }
    const camToBase = {};
    for (const id in devices) {
      const d = devices[id];
      const ids = (d && d.identifiers) || [];
      for (let i = 0; i < ids.length; i++) {
        const tup = ids[i];
        if (Array.isArray(tup) && tup[0] === 'spypoint' && devToBase[d.id]) camToBase[tup[1]] = devToBase[d.id];
      }
    }
    const bases = [];
    for (const p of (this._photos || [])) {
      const b = p && camToBase[p.camera];
      if (b && bases.indexOf(b) < 0) bases.push(b);
    }
    try { localStorage.setItem(this._config.map_green_key || 'spypoint_map_green', JSON.stringify(bases)); } catch (e) { /* ignore */ }
    const pulse = this._config.map_pulse_entity;
    if (pulse && this._hass.states && this._hass.states[pulse]) {
      this._hass.callService('input_text', 'set_value', { entity_id: pulse, value: String(Date.now()) });
    }
  }

  // Naive local datetime string for the service's date_start / date_end.
  _fmtDateStart(d) {
    const p = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:00:00`;
  }

  // Hour only, e.g. 16:00 -> "4pm", 12:00 -> "12pm", 8:00 -> "8am", 0:00 -> "12am".
  _fmtHour(d) {
    const h = d.getHours();
    let h12 = h % 12; if (h12 === 0) h12 = 12;
    return `${h12}${h >= 12 ? 'pm' : 'am'}`;
  }

  // "4pm"; prefixed with weekday ("Sat 4pm") when d isn't today. Used for "Since …".
  _fmtSinceLabel(d) {
    const now = new Date();
    const today = d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate();
    return today ? this._fmtHour(d) : `${['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][d.getDay()]} ${this._fmtHour(d)}`;
  }

  // Polls run at :05 on even hours and deliver captures from the preceding 2h, so the
  // "Since" anchor is (last poll hour - 2h): from 10:05 to 12:05 it reads "Since 8am".
  // Preceding blocks step back 2h through the day; 8pm-8am is a single overnight block
  // (a night-time anchor yields a partial "8pm-Xam" block first). Then the 24h anchor,
  // Today, Yesterday, All. Recomputed each render to track the clock.
  _windowOptions() {
    const now = new Date();
    const t = new Date(now.getTime() - 5 * 60 * 1000);                 // :05 poll lag
    const lastPoll = new Date(t.getFullYear(), t.getMonth(), t.getDate(), Math.floor(t.getHours() / 2) * 2, 0, 0, 0);
    const cur = new Date(lastPoll); cur.setHours(cur.getHours() - 2);

    const prevBoundary = (b) => {
      const h = b.getHours();
      const p = new Date(b);
      if (h === 8 || h < 8) { p.setDate(p.getDate() - 1); p.setHours(20, 0, 0, 0); }   // back across/into the night
      else if (h > 20) { p.setHours(20, 0, 0, 0); }                                     // 10pm/midnight-ish anchor -> 8pm
      else { p.setHours(h - 2, 0, 0, 0); }
      return p;
    };

    const opts = [{ key: 'cur', start: new Date(cur), end: null, label: 'Since ' + this._fmtSinceLabel(cur) }];
    const s24 = new Date(cur); s24.setHours(s24.getHours() - 24);
    let b = new Date(cur);
    for (let i = 0; i < 14; i++) {
      const ps = prevBoundary(b);
      if (ps < s24) break;
      opts.push({ key: 'b' + i, start: new Date(ps), end: new Date(b), label: `${this._fmtHour(ps)}-${this._fmtHour(b)}` });
      b = ps;
    }
    opts.push({ key: 'h24', start: s24, end: null, label: 'Since ' + this._fmtSinceLabel(s24) });
    const y = now.getFullYear(), m = now.getMonth(), d = now.getDate();
    const midToday = new Date(y, m, d, 0, 0, 0, 0);
    const midYest = new Date(y, m, d, 0, 0, 0, 0); midYest.setDate(midYest.getDate() - 1);
    opts.push({ key: 'today', start: midToday, end: null, label: 'Today' });
    opts.push({ key: 'yday', start: midYest, end: new Date(midToday), label: 'Yesterday' });
    opts.push({ key: 'all', start: null, end: null, label: 'All' });
    return opts;
  }

  async _fetch() {
    this._loaded = true;
    const detections = this._source === 'detections';
    const svc = detections ? this._config.detections_service : this._config.service;
    const [domain, service] = svc.split('.');
    const data = detections ? { mode: this._mode } : { ...(this._config.service_data || {}) };
    data.limit = this._limit;
    if (this._selectedCamera) data.device_id = this._selectedCamera; else delete data.device_id;
    const opts = this._windowOptions();
    let sel = opts[0];
    for (const o of opts) { if (o.key === this._window) { sel = o; break; } }
    if (sel.start) data.date_start = this._fmtDateStart(sel.start); else delete data.date_start;
    if (sel.end) data.date_end = this._fmtDateStart(sel.end); else delete data.date_end;
    if (!detections) { if (this._videoOnly) data.media_types = ['hdvideo']; else delete data.media_types; }
    try {
      const result = await this._hass.callService(
        domain, service, data, undefined, false, true, // returnResponse
      );
      const resp = result.response || {};
      this._photos = resp[this._config.response_key] || resp.photos || resp.images || [];
      this._error = null;
      this._publishLoadedCameras();
    } catch (e) {
      this._error = e.message || String(e);
      this._photos = [];
    }
    this._render();
  }

  _join(host, path) {
    if (!host) return path || '';
    let h = /^https?:\/\//.test(host) ? host : `https://${host}`;
    h = h.replace(/\/+$/, '');
    const p = (path || '').replace(/^\/+/, '');
    return `${h}/${p}`;
  }

  _toSrc(photo) {
    if (typeof photo === 'string') {
      if (/^(https?:|\/|data:)/.test(photo)) return photo;
      return `data:image/jpeg;base64,${photo}`;
    }
    const v = photo[this._config.size] || photo.large || photo.original;
    if (v && (v.host || v.path)) return this._join(v.host, v.path);
    if (photo.url) return photo.url;
    if (photo.path) return photo.path;
    if (photo.b64 || photo.base64) {
      const ct = photo.content_type || 'image/jpeg';
      return `data:${ct};base64,${photo.b64 || photo.base64}`;
    }
    return '';
  }

  async _requestVideo(btn, photo) {
    if (!photo || photo.camera == null || photo.id == null) return;
    const [domain, service] = this._config.request_service.split('.');
    const data = { camera_id: photo.camera, photo_id: photo.id };
    const cfgId = this._config.service_data && this._config.service_data.config_entry_id;
    if (cfgId) data.config_entry_id = cfgId; // request_hdvideo requires this
    const icon = btn.querySelector('ha-icon');
    btn.disabled = true;
    btn.classList.add('busy');
    try {
      await this._hass.callService(domain, service, data, undefined, false, true); // returnResponse
      btn.classList.remove('busy', 'req');
      btn.classList.add('pending');
      if (icon) icon.setAttribute('icon', 'mdi:progress-clock');
      btn.title = 'HD video requested — pending';
    } catch (err) {
      btn.disabled = false;
      btn.classList.remove('busy');
      if (icon) icon.setAttribute('icon', 'mdi:alert-circle');
      btn.title = `Request failed: ${err.message || err}`;
      console.error('request_hdvideo failed', err);
    }
  }

  _lightbox(contentHTML, onReady, onClose) {
    const overlay = document.createElement('div');
    overlay.style.cssText =
      'position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,.85);' +
      'display:flex;align-items:center;justify-content:center;padding:24px;';
    overlay.innerHTML = contentHTML +
      '<button class="sgc-close" aria-label="Close" style="position:absolute;top:8px;right:8px;z-index:10;' +
      'width:44px;height:44px;padding:0;margin:0;display:flex;align-items:center;justify-content:center;' +
      'font-size:32px;line-height:1;background:rgba(0,0,0,.35);border:none;border-radius:22px;' +
      'color:#fff;cursor:pointer;pointer-events:auto;-webkit-tap-highlight-color:transparent;">&times;</button>';
    const onKey = (e) => { if (e.key === 'Escape') close(); };
    let closed = false;
    const close = () => {
      if (closed) return;
      closed = true;
      if (onClose) onClose();
      overlay.remove();
      document.removeEventListener('keydown', onKey);
    };
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay || (e.target.closest && e.target.closest('.sgc-close'))) { e.stopPropagation(); close(); }
    });
    document.addEventListener('keydown', onKey);
    document.body.appendChild(overlay);
    if (onReady) onReady(overlay);
  }

  _playVideo(url) {
    this._lightbox(
      `<video src="${url}" controls autoplay playsinline ` +
      'style="max-width:100%;max-height:100%;border-radius:8px;"></video>');
  }

  _showImage(url) {
    this._lightbox(
      `<img src="${url}" style="max-width:100%;max-height:100%;border-radius:8px;" />`);
  }

  _playSlideshow(frames) {
    let timer = null;
    this._lightbox(
      `<img class="sgc-slide" src="${frames[0]}" ` +
      'style="max-width:100%;max-height:100%;border-radius:8px;" />',
      (overlay) => {
        frames.forEach((f) => { const im = new Image(); im.src = f; }); // preload
        const img = overlay.querySelector('.sgc-slide');
        let i = 0;
        timer = setInterval(() => { i = (i + 1) % frames.length; img.src = frames[i]; }, 1000);
      },
      () => { if (timer) clearInterval(timer); });
  }

  _frameSrc(f) {
    if (!f) return '';
    if (typeof f === 'string') {
      return /^(https?:|\/|data:)/.test(f) ? f : `data:image/jpeg;base64,${f}`;
    }
    if (f.host || f.path) return this._join(f.host, f.path);
    if (f.url) return f.url;
    if (f.path) return f.path;
    return '';
  }

  // Resolve a preview's frames to a list of image URLs. `photo.preview` is an
  // array of {host,path} entries (same shape as `large`/`hdVideo`). A
  // configurable `preview_key` overrides the field name if ever needed.
  _previewFrames(photo) {
    if (!photo) return [];
    const key = this._config.preview_key;
    const candidates = key
      ? [photo[key]]
      : [photo.preview, photo.frames, photo.previews, photo.urls, photo.images];
    for (const c of candidates) {
      if (Array.isArray(c) && c.length) {
        return c.map((f) => this._frameSrc(f)).filter(Boolean);
      }
    }
    return [];
  }

  _esc(v) {
    return String(v == null ? '' : v).replace(/[&<>"]/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch]));
  }

  _cap(v) { const t = String(v || ''); return t ? t.charAt(0).toUpperCase() + t.slice(1) : t; }

  _detectionPanel(photo) {
    const d = photo && photo.detection;
    if (!d) return '';
    const e = (v) => this._esc(v);
    const check = d.verified === 'spypoint' ? 'Corroborated by Spypoint detector'
      : d.verified === '1' ? 'Confirmed by second pass'
      : d.verified === '2' ? 'Species set by second pass'
      : d.verified === '0' ? 'Rejected by second pass'
      : d.verified === 'shadow' ? 'Found by the second model (primary saw nothing)' : '';
    const verify = d.verify_species
      ? `${this._cap(e(d.verify_species))}${d.verify_confidence != null ? ` (${e(d.verify_confidence)}%)` : ''}`
      : '';
    const ms = d.infer_ms != null && d.infer_ms !== '' ? `${(Number(d.infer_ms) / 1000).toFixed(1)} s` : '';
    const rows = [
      ['Species', `${this._cap(d.species)}${d.subtype ? ` (${e(d.subtype)})` : ''}${d.count ? ` &times;${e(d.count)}` : ''}`],
      ['Confidence', `${e(d.confidence)}%`],
      ['Camera', e(d.camera_name)],
      ['Captured', e(d.captured)],
      check ? ['Check', e(check)] : null,
      d.body_parts ? ['Seen', e(String(d.body_parts).split(',').join(', '))] : null,
      d.first_species && d.first_species !== d.species ? ['First pass', this._cap(e(d.first_species))] : null,
      verify ? ['Second pass', verify] : null,
      d.location && d.location !== 'none' ? ['Position', this._cap(e(d.location))] : null,
      d.note ? ['Note', e(d.note)] : null,
      d.model ? ['Model', `${e(d.model)}${ms ? ` \u00b7 ${ms}` : ''}`] : (ms ? ['Inference', ms] : null),
      d.shadow_model ? ['Second model', `${e(d.shadow_model)}: ${this._cap(e(d.shadow_species))}${d.shadow_subtype ? ` (${e(d.shadow_subtype)})` : ''}${d.shadow_confidence != null ? ` ${e(d.shadow_confidence)}%` : ''}${d.shadow_infer_ms != null ? ` \u00b7 ${(Number(d.shadow_infer_ms) / 1000).toFixed(1)} s` : ''}${d.agree === false ? ' \u2014 disagrees' : ''}`] : null,
      d.shadow_model && d.shadow_description && d.agree === false ? ['Second model said', e(d.shadow_description)] : null,
      (photo.tag || []).length ? ['Spypoint tags', e((photo.tag || []).join(', '))] : null,
    ].filter(Boolean);
    const tags = (photo.tag || []).map((t) => String(t).toLowerCase());
    let action = '';
    if (photo.hdVideo) {
      action = `<button class="sgc-btn play-hd"><ha-icon icon="mdi:play-circle"></ha-icon> Play HD video</button>`;
    } else if (tags.includes('hdvideopend')) {
      action = `<button class="sgc-btn" disabled><ha-icon icon="mdi:progress-clock"></ha-icon> HD video requested — pending</button>`;
    } else if (photo.camera != null && photo.id != null) {
      action = `<button class="sgc-btn req-hd"><ha-icon icon="mdi:download-circle"></ha-icon> Request HD video</button>`;
    }
    return `<div class="sgc-meta">
      <div class="sgc-desc">${e(d.description)}</div>
      <table>${rows.map(([k, v]) => `<tr><th>${k}</th><td>${v}</td></tr>`).join('')}</table>
      ${action ? `<div class="sgc-actions">${action}</div>` : ''}
    </div>`;
  }

  _openDetection(photo) {
    const frames = this._previewFrames(photo);
    const first = frames.length ? frames[0] : this._toSrc(photo);
    let timer = null;
    const style = `<style>
      .sgc-wrap { display:flex; flex-direction:column; align-items:center; gap:12px; max-width:min(96vw,1400px); max-height:96vh; }
      .sgc-wrap img { max-width:100%; max-height:70vh; border-radius:8px; object-fit:contain; }
      .sgc-meta { color:#eee; font-size:.95rem; background:rgba(20,20,20,.85); border-radius:8px;
                  padding:12px 16px; max-width:100%; width:min(96vw,900px); }
      .sgc-desc { margin-bottom:8px; font-style:italic; color:#ddd; }
      .sgc-meta table { border-collapse:collapse; }
      .sgc-meta th { text-align:left; color:#9ad; font-weight:500; padding:2px 14px 2px 0; white-space:nowrap; vertical-align:top; }
      .sgc-meta td { padding:2px 0; }
      .sgc-actions { margin-top:10px; }
      .sgc-btn { display:inline-flex; align-items:center; gap:6px; padding:6px 12px; border-radius:6px;
                 border:1px solid #9ad; background:rgba(255,255,255,.08); color:#fff; cursor:pointer; font:inherit; }
      .sgc-btn:hover:not([disabled]) { background:rgba(255,255,255,.18); }
      .sgc-btn[disabled] { opacity:.6; cursor:default; }
      .sgc-btn.pending { border-color:#fc6; }
      .sgc-btn ha-icon { --mdc-icon-size:20px; }
      @media (max-width:600px) { .sgc-wrap img { max-height:52vh; } .sgc-meta { font-size:.85rem; } }
    </style>`;
    this._lightbox(
      `${style}<div class="sgc-wrap"><img class="sgc-slide" src="${first}" />${this._detectionPanel(photo)}</div>`,
      (overlay) => {
        if (frames.length > 1) {
          frames.forEach((f) => { const im = new Image(); im.src = f; }); // preload
          const img = overlay.querySelector('.sgc-slide');
          let i = 0;
          timer = setInterval(() => { i = (i + 1) % frames.length; img.src = frames[i]; }, 1000);
        }
        const req = overlay.querySelector('.sgc-btn.req-hd');
        if (req) req.onclick = async (ev) => {
          ev.stopPropagation();
          await this._requestVideo(req, photo);
          if (req.classList.contains('pending')) {
            req.innerHTML = '<ha-icon icon="mdi:progress-clock"></ha-icon> HD video requested \u2014 pending';
            if (!(photo.tag || []).includes('hdvideopend')) (photo.tag = photo.tag || []).push('hdvideopend');
          }
        };
        const play = overlay.querySelector('.sgc-btn.play-hd');
        if (play) play.onclick = (ev) => {
          ev.stopPropagation();
          const slide = overlay.querySelector('.sgc-slide');
          if (timer) { clearInterval(timer); timer = null; }
          const v = document.createElement('video');
          v.src = this._join(photo.hdVideo.host, photo.hdVideo.path);
          v.controls = true; v.autoplay = true; v.playsInline = true;
          v.style.cssText = 'max-width:100%;max-height:70vh;border-radius:8px;';
          slide.replaceWith(v);
          play.remove();
        };
      },
      () => { if (timer) clearInterval(timer); });
  }

  _openLightbox(photo) {
    if (!photo) return;
    if (photo.detection) { this._openDetection(photo); return; }
    if (photo.hdVideo) {
      this._playVideo(this._join(photo.hdVideo.host, photo.hdVideo.path));
      return;
    }
    const frames = this._previewFrames(photo);
    if (frames.length > 1) { this._playSlideshow(frames); return; }
    if (frames.length === 1) { this._showImage(frames[0]); return; }
    this._showImage(this._toSrc(photo)); // otherwise: the large image
  }

  // Rejections / Everything: a metadata list instead of a thumbnail grid (hundreds of 'none'
  // frames aren't worth thumbnails). Each row carries the classifier verdict; the photo itself
  // is only fetched when the row is clicked (lightbox).
  _verdictClass(d) {
    if (!d) return 'v-none';
    if (d.verified === '0') return 'v-rej';
    if (d.species === 'none') return 'v-none';
    if (d.species === 'unknown') return 'v-unk';
    if (['bear', 'cougar', 'wolf'].includes(d.species)) return 'v-alert';
    return 'v-pos';
  }

  _detectionRows(passes) {
    const e = (v) => this._esc(v);
    return this._photos.map((p, i) => {
      if (!passes(p)) return '';
      const d = (p && p.detection) || {};
      const cls = this._verdictClass(d);
      const species = `${this._cap(d.species)}${d.subtype ? ` (${e(d.subtype)})` : ''}${d.count > 1 ? ` \u00d7${e(d.count)}` : ''}`;
      const first = d.first_species && d.first_species !== d.species
        ? `<span class="first">${this._cap(e(d.first_species))} \u2192</span> ` : '';
      const verify = d.verified === 'spypoint' ? 'SP' : d.verified === '1' ? '\u2713' : d.verified === '2' ? '\u21bb' : d.verified === '0' ? '\u2717' : d.verified === 'shadow' ? '2nd' : '';
      const ms = d.infer_ms != null && d.infer_ms !== '' ? `${(Number(d.infer_ms) / 1000).toFixed(1)}s` : '';
      const shadow = d.shadow_model
        ? `<span class="shadow ${d.agree === false ? 'dis' : 'agr'}" title="${e(d.shadow_model)}: ${e(d.shadow_description)}">${d.agree === false ? '\u2260 ' : '= '}${this._cap(e(d.shadow_species))}${d.shadow_confidence != null ? ` ${e(d.shadow_confidence)}%` : ''}</span>`
        : '';
      const note = `<span class="extra">${shadow}${d.note ? `<span class="note" title="${e(d.note)}">${e(d.note)}</span>` : ''}</span>`;
      return `<div class="row ${cls}" data-idx="${i}" title="${e(d.description)}">
        <span class="when">${e(d.captured)}</span>
        <span class="cam">${e(d.camera_name || p.camera)}</span>
        <span class="verdict">${first}<b>${species}</b>${verify ? `<span class="chk" title="${d.verified === '0' ? 'rejected by second pass' : 'verified'}">${verify}</span>` : ''}</span>
        <span class="conf">${d.confidence != null ? `${e(d.confidence)}%` : ''}</span>
        <span class="ms">${ms}</span>
        ${note}
      </div>`;
    }).join('');
  }

  _render() {
    if (!this._root) {
      this._root = document.createElement('ha-card');
      this.appendChild(this._root);
    }
    const c = this._config;

    const camOpts = ['<option value="">All cameras</option>']
      .concat(this._cameras().map((cam) =>
        `<option value="${cam.id}"${cam.id === this._selectedCamera ? ' selected' : ''}>${cam.name}</option>`))
      .join('');

    const srcSel = `<select class="srcsel" title="Source">` +
      `<option value="detections"${this._source === 'detections' ? ' selected' : ''}>Detections</option>` +
      `<option value="cloud"${this._source === 'cloud' ? ' selected' : ''}>Cloud photos</option>` +
      `</select>`;
    const modeSel = this._source === 'detections'
      ? `<select class="modesel" title="Which classified photos to list">` +
        `<option value="detections"${this._mode === 'detections' ? ' selected' : ''}>Detections</option>` +
        `<option value="rejections"${this._mode === 'rejections' ? ' selected' : ''}>Rejections</option>` +
        `<option value="disagree"${this._mode === 'disagree' ? ' selected' : ''}>Models disagree</option>` +
        `<option value="all"${this._mode === 'all' ? ' selected' : ''}>Everything</option>` +
        `</select>`
      : '';
    const winSel = `<select class="winsel" title="Time window">` +
      this._windowOptions().map((o) => `<option value="${o.key}"${this._window === o.key ? ' selected' : ''}>${o.label}</option>`).join('') +
      `</select>`;
    const HOUSEKEEPING = new Set(['day', 'night', 'preview', 'hdvideopend', 'hdvideo']);
    const tagsOf = (p) => ((p && (p.tag || p.tags)) || []).map((x) => String(x).toLowerCase());
    const isTagged = (p) => tagsOf(p).some((t) => !HOUSEKEEPING.has(t)); // has a real content tag
    const passes = (p) => (!this._taggedOnly || isTagged(p));
    const shownCount = this._photos.filter(passes).length;

    const listMode = this._source === 'detections' && this._mode !== 'detections';
    const items = listMode ? '' : this._photos.map((p, i) => {
      if (!passes(p)) return ''; // hide filtered-out photos, keep index i intact
      const src = this._toSrc(p);
      const caption = (p && p.caption) ? `<div class="cap">${p.caption}</div>` : '';
      const tags = (p && (p.tag || p.tags)) || [];
      const has = (t) => Array.isArray(tags) && tags.includes(t);

      let corner = '';
      if (p && p.hdVideoFailed === true) {
        corner = `<div class="dl failed" title="HD video request failed">` +
                 `<ha-icon icon="mdi:alert-circle"></ha-icon></div>`;
      } else if (has('hdvideopend') && !has('hdvideo')) {
        corner = `<div class="dl pending" title="HD video requested — pending">` +
                 `<ha-icon icon="mdi:progress-clock"></ha-icon></div>`;
      } else if (has('preview')) {
        corner = `<button class="dl req" data-idx="${i}" title="Request HD video">` +
                 `<ha-icon icon="mdi:download"></ha-icon></button>`;
      }

      const playBadge = (p && p.hdVideo)
        ? `<button class="play" data-idx="${i}" title="Play HD video"><ha-icon icon="mdi:play-circle"></ha-icon></button>`
        : '';

      return `<figure data-idx="${i}">${corner}${playBadge}<img src="${src}" loading="lazy" />${caption}</figure>`;
    }).join('');

    if (this._hoverTimer) { clearInterval(this._hoverTimer); this._hoverTimer = null; }

    this._root.innerHTML = `
      <style>
        .header { display:flex; align-items:center; justify-content:space-between; padding:12px 16px 8px; }
        .header .name { font-weight:500; }
        .controls { display:flex; flex-wrap:wrap; align-items:center; gap:16px;
                    padding:0 16px 12px; color:var(--primary-text-color); font-size:.9rem; }
        .controls select { background:var(--card-background-color); color:var(--primary-text-color);
                            border:1px solid var(--divider-color); border-radius:6px; padding:4px 8px; max-width:220px; }
        .controls label { display:flex; align-items:center; gap:6px; white-space:nowrap; cursor:pointer; }
        .controls .checks { display:flex; flex-wrap:wrap; align-items:center; gap:16px; }
        .controls input[type=range] { width:150px; accent-color:var(--primary-color); }
        .controls .limval { min-width:2.6em; text-align:right; font-variant-numeric:tabular-nums; }
        .controls .load { background:var(--primary-color); color:var(--text-primary-color,#fff);
                          border:none; border-radius:6px; padding:6px 14px; font-size:.9rem;
                          font-weight:500; cursor:pointer; display:inline-flex; align-items:center; }
        .controls .load:hover { filter:brightness(1.1); }
        .controls .load:disabled { opacity:.8; cursor:progress; filter:none; }
        .controls .load .spin { display:inline-block; width:12px; height:12px; margin-right:6px;
                                border:2px solid rgba(255,255,255,.45); border-top-color:#fff;
                                border-radius:50%; animation:sgc-spin .8s linear infinite; }
        @keyframes sgc-spin { to { transform:rotate(360deg); } }
        .grid { display:grid; gap:8px; padding:0 16px 16px; align-items:start;
                grid-template-columns: repeat(${c.columns}, 1fr); }
        @media (max-width: 600px) {
          .grid { grid-template-columns: repeat(${this._config.mobile_columns || 2}, 1fr); }
          .dl { width:26px; height:26px; --mdc-icon-size:16px; bottom:5px; right:5px; }
          .controls { flex-direction:column; align-items:stretch; gap:14px; }
          .controls .cam { max-width:none; width:100%; }
          .controls .photos { width:100%; }
          .controls .photos input[type=range] { flex:1; width:auto; min-width:0; }
          .controls .load { width:100%; justify-content:center; padding:10px 14px; }
        }
        figure { margin:0; position:relative; cursor:pointer; }
        img { width:100%; height:auto; display:block; border-radius:8px;
              background: var(--secondary-background-color); }
        .cap { font-size:.8rem; color:var(--secondary-text-color); margin-top:4px; }
        .empty, .err { padding:0 16px 16px; color:var(--secondary-text-color); }
        .err { color: var(--error-color); }
        .dl { position:absolute; bottom:6px; right:6px; z-index:1;
              display:flex; align-items:center; justify-content:center;
              width:34px; height:34px; border-radius:50%; padding:0; border:none;
              color:#fff; --mdc-icon-size:20px; box-shadow:0 1px 4px rgba(0,0,0,.6); }
        .dl ha-icon { color:#fff; }
        .dl.req { background:#2196f3; cursor:pointer; }
        .dl.req:hover { filter:brightness(1.12); }
        .dl.req:disabled { cursor:default; }
        .dl.pending { background:#ff9800; }
        .dl.failed { background:#f44336; }
        .dl.busy, .dl.pending { animation:sgc-pulse 1.2s ease-in-out infinite; }
        @keyframes sgc-pulse { 50% { opacity:.5; } }
        .play { position:absolute; top:50%; left:50%; transform:translate(-50%,-50%); z-index:1;
                display:flex; align-items:center; justify-content:center;
                width:56px; height:56px; border-radius:50%; padding:0;
                background:rgba(0,0,0,.5); color:#fff; border:none; cursor:pointer;
                --mdc-icon-size:40px; }
        .play ha-icon { color:#fff; }
        .play:hover { background:rgba(0,0,0,.7); }
        .list { display:flex; flex-direction:column; padding:0 16px 16px; font-size:.9rem; }
        .list .row { display:grid; grid-template-columns: 9.5em 10em minmax(10em,1.4fr) 3.5em 3.5em minmax(6em,1fr);
                     gap:10px; align-items:center; padding:6px 8px; border-left:4px solid transparent;
                     border-bottom:1px solid var(--divider-color); cursor:pointer; }
        .list .row:hover { background:var(--secondary-background-color); }
        .list .row.v-pos { border-left-color:#4caf50; }
        .list .row.v-alert { border-left-color:#f44336; }
        .list .row.v-unk { border-left-color:#ff9800; }
        .list .row.v-rej { border-left-color:#9c27b0; }
        .list .row.v-none { border-left-color:var(--divider-color); color:var(--secondary-text-color); }
        .list .when, .list .cam { white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
        .list .when { font-variant-numeric:tabular-nums; color:var(--secondary-text-color); }
        .list .first { color:var(--secondary-text-color); }
        .list .chk { margin-left:6px; font-size:.8em; color:var(--secondary-text-color); }
        .list .conf, .list .ms { text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }
        .list .note { font-size:.8em; color:var(--secondary-text-color); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
        .list .extra { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
        .list .shadow { font-size:.8em; margin-right:8px; white-space:nowrap; }
        .list .shadow.dis { color:#e65100; font-weight:600; }
        .list .shadow.agr { color:var(--secondary-text-color); }
        .list .legend { display:flex; flex-wrap:wrap; gap:14px; padding:6px 8px 10px; font-size:.78rem; color:var(--secondary-text-color); }
        .list .legend i { display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:5px; vertical-align:middle; }
        @media (max-width: 600px) {
          .list .row { grid-template-columns: 1fr auto; grid-template-areas: "verdict conf" "meta ms"; row-gap:2px; }
          .list .verdict { grid-area:verdict; }
          .list .conf { grid-area:conf; }
          .list .ms { grid-area:ms; }
          .list .when, .list .cam, .list .extra { grid-area:meta; font-size:.8em; }
          .list .cam { padding-left:9.5em; }
          .list .note { display:none; }
        }
      </style>
      <div class="header">
        <span class="name">${this._source === 'detections' ? ({ detections: 'Detections', rejections: 'Rejections', disagree: 'Models disagree', all: 'All classified' }[this._mode]) : c.title}${shownCount ? ` (${shownCount})` : ''}</span>
        <ha-icon-button title="Refresh"><ha-icon icon="mdi:refresh"></ha-icon></ha-icon-button>
      </div>
      <div class="controls">
        ${srcSel}
        ${modeSel}
        <select class="cam" title="Camera">${camOpts}</select>
        <div class="checks">
          ${winSel}
          <label><input type="checkbox" class="tagchk"${this._taggedOnly ? ' checked' : ''}/> Tagged</label>
          ${this._source === 'cloud' ? `<label><input type="checkbox" class="vidchk"${this._videoOnly ? ' checked' : ''}/> Video</label>` : ''}
        </div>
        <label class="photos">Photos
          <input type="range" class="lim" min="1" max="${this._sliderMax}" value="${this._limit}"/>
          <span class="limval">${this._limit}</span>
        </label>
        <button class="load">Load Photos</button>
      </div>
      ${this._error ? `<div class="err">${this._error}</div>` : ''}
      ${!this._error && shownCount === 0 ? `<div class="empty">${!this._loaded ? `Press \u201CLoad Photos\u201D to load ${this._source === 'detections' ? 'detections' : 'images'}.` : ((this._taggedOnly || this._videoOnly) && this._photos.length ? 'No matching photos.' : 'No photos.')}</div>` : ''}
      ${listMode && shownCount
        ? `<div class="list">
            <div class="legend"><span><i style="background:#4caf50"></i>detection</span><span><i style="background:#f44336"></i>alert species</span><span><i style="background:#ff9800"></i>unknown (review)</span><span><i style="background:#9c27b0"></i>rejected by verify</span><span><i style="background:var(--divider-color)"></i>none</span>
              <span>&nbsp;SP corroborated by Spypoint &middot; \u2713 confirmed &middot; \u21bb species set by verify &middot; \u2717 rejected &middot; 2nd found by second model</span></div>
            ${this._detectionRows(passes)}
          </div>`
        : `<div class="grid">${items}</div>`}
    `;

    const refresh = this._root.querySelector('ha-icon-button');
    if (refresh) refresh.onclick = () => this._fetch();

    const load = this._root.querySelector('.load');
    if (load) load.onclick = async () => {
      load.disabled = true;
      load.innerHTML = '<span class="spin"></span>Loading\u2026';
      await this._fetch(); // _fetch re-renders at the end, restoring the button
    };

    const cam = this._root.querySelector('.cam');
    if (cam) cam.onchange = () => { this._selectedCamera = cam.value; };

    const src = this._root.querySelector('.srcsel');
    if (src) src.onchange = () => { this._source = src.value; this._photos = []; this._loaded = false; this._render(); };

    const msel = this._root.querySelector('.modesel');
    if (msel) msel.onchange = () => { this._mode = msel.value; };

    this._root.querySelectorAll('.list .row[data-idx]').forEach((row) => {
      row.onclick = () => this._openLightbox(this._photos[Number(row.dataset.idx)]);
    });

    const wsel = this._root.querySelector('.winsel');
    if (wsel) wsel.onchange = () => { this._window = wsel.value; };

    const tag = this._root.querySelector('.tagchk');
    if (tag) tag.onchange = () => { this._taggedOnly = tag.checked; this._render(); };

    const vid = this._root.querySelector('.vidchk');
    if (vid) vid.onchange = () => { this._videoOnly = vid.checked; };

    const sl = this._root.querySelector('.lim');
    const lv = this._root.querySelector('.limval');
    if (sl) {
      sl.oninput = () => { if (lv) lv.textContent = sl.value; };   // live label
      sl.onchange = () => { this._limit = Number(sl.value); };
    }

    this._root.querySelectorAll('.req').forEach((b) => {
      b.onclick = (e) => {
        e.stopPropagation();
        this._requestVideo(b, this._photos[Number(b.dataset.idx)]);
      };
    });
    this._root.querySelectorAll('.play').forEach((b) => {
      b.onclick = (e) => {
        e.stopPropagation();
        const p = this._photos[Number(b.dataset.idx)];
        if (p && p.hdVideo) this._playVideo(this._join(p.hdVideo.host, p.hdVideo.path));
      };
    });
    this._root.querySelectorAll('figure[data-idx]').forEach((fig) => {
      const photo = this._photos[Number(fig.dataset.idx)];
      fig.onclick = () => this._openLightbox(photo);

      const frames = this._previewFrames(photo);
      if (frames.length > 1) {
        const img = fig.querySelector('img');
        const orig = img.getAttribute('src');
        let idx = 0;
        fig.addEventListener('mouseenter', () => {
          frames.forEach((f) => { const im = new Image(); im.src = f; }); // preload
          if (this._hoverTimer) clearInterval(this._hoverTimer);
          idx = 0;
          this._hoverTimer = setInterval(() => {
            idx = (idx + 1) % frames.length;
            img.src = frames[idx];
          }, this._config.hover_interval);
        });
        fig.addEventListener('mouseleave', () => {
          if (this._hoverTimer) { clearInterval(this._hoverTimer); this._hoverTimer = null; }
          img.src = orig; // restore the static thumbnail
        });
      }
    });
  }

  getCardSize() {
    if (this._source === 'detections' && this._mode !== 'detections') return Math.max(1, Math.ceil(this._photos.length / 2) + 2);
    return Math.max(1, Math.ceil(this._photos.length / (this._config.columns || 3)) + 2);
  }
}

customElements.define('spypoint-gallery-card', SpypointGalleryCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: 'spypoint-gallery-card',
  name: 'Spypoint Gallery Card',
  description: 'SPYPOINT gallery: cloud photos or AI detections, camera filter, 2h time windows, HD request/playback.',
});

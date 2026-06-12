// spypoint-gallery-card.js
// Lovelace card for the SPYPOINT get_photos service (returns response data).
// Full-width grid, full-aspect photos, with header controls:
//   - camera selector (All / a specific Spypoint device -> service_data.device_id)
//   - photo-count slider (-> service_data.limit; service caps at 100)
//   - "New only" toggle (-> service_data.date_start = most recent 4h boundary since local midnight)
// Per-photo lifecycle badges (bottom-right):
//   tag "preview"     -> request button -> spypoint.request_hdvideo
//   tag "hdvideopend" -> pending status icon
//   field hdVideo     -> centered play button -> lightbox
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
      max_photos: 1000,
      hover_interval: 300,    // ms per frame when cycling a preview on hover
      request_service: 'spypoint.request_hdvideo',
      service_data: {},
      ...config,
    };
    this._photos = [];
    this._error = null;
    this._hoverTimer = null;
    // Control state
    this._selectedCamera = '';   // '' = all cameras; otherwise an HA device_id
    this._newOnly = true;
    this._taggedOnly = false;    // only photos with a real (non-housekeeping) tag
    this._videoOnly = false;     // only photos that have an HD video
    const baseLimit = Number((this._config.service_data && this._config.service_data.limit) || 48);
    this._limit = Math.min(baseLimit, this._config.max_photos);
    this._render();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) {
      this._fetch();
      if (this._config.refresh_interval > 0) {
        this._timer = setInterval(() => this._fetch(), this._config.refresh_interval * 1000);
      }
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

  // Previous 4-hour-aligned boundary in local time, as a naive local datetime.
  // e.g. at 11:00 -> 04:00; at 13:30 -> 08:00; at 00:30 -> previous day 20:00.
  _fourHourBoundary() {
    const now = new Date();
    const blockStart = Math.floor(now.getHours() / 4) * 4; // current block start: 0,4,8,12,16,20
    const b = new Date(now.getFullYear(), now.getMonth(), now.getDate(), blockStart, 0, 0, 0);
    b.setHours(b.getHours() - 4); // step back one block (handles day/DST rollover)
    const p = (n) => String(n).padStart(2, '0');
    return `${b.getFullYear()}-${p(b.getMonth() + 1)}-${p(b.getDate())}T${p(b.getHours())}:00:00`;
  }

  async _fetch() {
    const [domain, service] = this._config.service.split('.');
    const data = { ...(this._config.service_data || {}) };
    data.limit = this._limit;
    if (this._selectedCamera) data.device_id = this._selectedCamera; else delete data.device_id;
    if (this._newOnly) data.date_start = this._fourHourBoundary(); else delete data.date_start;
    if (this._videoOnly) data.media_types = ['hdvideo']; else delete data.media_types;
    try {
      const result = await this._hass.callService(
        domain, service, data, undefined, false, true, // returnResponse
      );
      const resp = result.response || {};
      this._photos = resp[this._config.response_key] || resp.photos || resp.images || [];
      this._error = null;
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
      '<button aria-label="Close" style="position:absolute;top:16px;right:20px;' +
      'font-size:32px;line-height:1;background:none;border:none;color:#fff;cursor:pointer;">&times;</button>';
    const onKey = (e) => { if (e.key === 'Escape') close(); };
    const close = () => {
      if (onClose) onClose();
      overlay.remove();
      document.removeEventListener('keydown', onKey);
    };
    overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });
    overlay.querySelector('button').onclick = close;
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

  _openLightbox(photo) {
    if (!photo) return;
    if (photo.hdVideo) {
      this._playVideo(this._join(photo.hdVideo.host, photo.hdVideo.path));
      return;
    }
    const frames = this._previewFrames(photo);
    if (frames.length > 1) { this._playSlideshow(frames); return; }
    if (frames.length === 1) { this._showImage(frames[0]); return; }
    this._showImage(this._toSrc(photo)); // otherwise: the large image
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

    const HOUSEKEEPING = new Set(['day', 'night', 'preview', 'hdvideopend', 'hdvideo']);
    const tagsOf = (p) => ((p && (p.tag || p.tags)) || []).map((x) => String(x).toLowerCase());
    const isTagged = (p) => tagsOf(p).some((t) => !HOUSEKEEPING.has(t)); // has a real content tag
    const passes = (p) => (!this._taggedOnly || isTagged(p));
    const shownCount = this._photos.filter(passes).length;

    const items = this._photos.map((p, i) => {
      if (!passes(p)) return ''; // hide filtered-out photos, keep index i intact
      const src = this._toSrc(p);
      const caption = (p && p.caption) ? `<div class="cap">${p.caption}</div>` : '';
      const tags = (p && (p.tag || p.tags)) || [];
      const has = (t) => Array.isArray(tags) && tags.includes(t);

      let corner = '';
      if (has('hdvideopend')) {
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
        .controls input[type=range] { width:150px; accent-color:var(--primary-color); }
        .controls .limval { min-width:2.6em; text-align:right; font-variant-numeric:tabular-nums; }
        .grid { display:grid; gap:8px; padding:0 16px 16px; align-items:start;
                grid-template-columns: repeat(${c.columns}, 1fr); }
        @media (max-width: 600px) {
          .grid { grid-template-columns: repeat(${this._config.mobile_columns || 2}, 1fr); }
          .dl { width:26px; height:26px; --mdc-icon-size:16px; bottom:5px; right:5px; }
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
        .dl.busy, .dl.pending { animation:sgc-pulse 1.2s ease-in-out infinite; }
        @keyframes sgc-pulse { 50% { opacity:.5; } }
        .play { position:absolute; top:50%; left:50%; transform:translate(-50%,-50%); z-index:1;
                display:flex; align-items:center; justify-content:center;
                width:56px; height:56px; border-radius:50%; padding:0;
                background:rgba(0,0,0,.5); color:#fff; border:none; cursor:pointer;
                --mdc-icon-size:40px; }
        .play ha-icon { color:#fff; }
        .play:hover { background:rgba(0,0,0,.7); }
      </style>
      <div class="header">
        <span class="name">${c.title}${shownCount ? ` (${shownCount})` : ''}</span>
        <ha-icon-button title="Refresh"><ha-icon icon="mdi:refresh"></ha-icon></ha-icon-button>
      </div>
      <div class="controls">
        <select class="cam" title="Camera">${camOpts}</select>
        <label><input type="checkbox" class="newchk"${this._newOnly ? ' checked' : ''}/> New only</label>
        <label><input type="checkbox" class="tagchk"${this._taggedOnly ? ' checked' : ''}/> Tagged</label>
        <label><input type="checkbox" class="vidchk"${this._videoOnly ? ' checked' : ''}/> Video</label>
        <label>Photos
          <input type="range" class="lim" min="1" max="${c.max_photos}" value="${this._limit}"/>
          <span class="limval">${this._limit}</span>
        </label>
      </div>
      ${this._error ? `<div class="err">${this._error}</div>` : ''}
      ${!this._error && shownCount === 0 ? `<div class="empty">${(this._taggedOnly || this._videoOnly) && this._photos.length ? 'No matching photos.' : 'No photos.'}</div>` : ''}
      <div class="grid">${items}</div>
    `;

    const refresh = this._root.querySelector('ha-icon-button');
    if (refresh) refresh.onclick = () => this._fetch();

    const cam = this._root.querySelector('.cam');
    if (cam) cam.onchange = () => { this._selectedCamera = cam.value; this._fetch(); };

    const chk = this._root.querySelector('.newchk');
    if (chk) chk.onchange = () => { this._newOnly = chk.checked; this._fetch(); };

    const tag = this._root.querySelector('.tagchk');
    if (tag) tag.onchange = () => { this._taggedOnly = tag.checked; this._render(); };

    const vid = this._root.querySelector('.vidchk');
    if (vid) vid.onchange = () => { this._videoOnly = vid.checked; this._fetch(); };

    const sl = this._root.querySelector('.lim');
    const lv = this._root.querySelector('.limval');
    if (sl) {
      sl.oninput = () => { if (lv) lv.textContent = sl.value; };   // live label, no fetch mid-drag
      sl.onchange = () => { this._limit = Number(sl.value); this._fetch(); };
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
    return Math.max(1, Math.ceil(this._photos.length / (this._config.columns || 3)) + 2);
  }
}

customElements.define('spypoint-gallery-card', SpypointGalleryCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: 'spypoint-gallery-card',
  name: 'Spypoint Gallery Card',
  description: 'SPYPOINT photo gallery with camera filter, count slider, new-only, and HD request/playback.',
});

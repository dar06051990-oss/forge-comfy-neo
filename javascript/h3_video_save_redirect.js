// MiniMax H3 v17: Full Comfy backend UI bridge, validation-aware state, clean Save As.
(() => {
  const SAVE_IDS = new Set(['save_txt2img', 'save_img2img', 'txt2img_save', 'img2img_save']);
  const GEN_IDS = new Set(['txt2img_generate', 'img2img_generate']);
  const COUNTER_KEY = 'forge_h3_download_counter_v1';
  const RUNS = new Map();
  let suppressSaveClickUntil = 0;

  const visible = el => !!el && el.offsetParent !== null && getComputedStyle(el).display !== 'none';
  // Gradio hides tab ancestors while switching txt2img/img2img/Settings.
  // Don't mistake this for deactivating MiniMax and discard the completed video.
  // Hidden UI marker updated by Forge's checkpoint callbacks. Never derive H3
  // activation from the old accordion's visibility: the accordion stays hidden.
  const h3Active = tab => {
    const panel = document.getElementById(`${tab}_h3_panel`);
    return !!panel && !panel.hidden && getComputedStyle(panel).display !== 'none' && getComputedStyle(panel).visibility !== 'hidden';
  };
  // DEV18: only route ANIMA to Comfy when the ORIGINAL Forge controls are
  // physically inside the Comfy Neo tab. In ordinary txt2img, ANIMA must
  // continue using Forge's native engine with its ordinary Generate callback.
  function comfyAnimaActive(button) {
    if (!button || !button.closest('#tab_comfy_engine')) return false;
    const root = document.getElementById('forge_ui_preset');
    if (!root) return false;
    const input = root.matches?.('input,select') ? root : root.querySelector('input,select');
    const value = String(input?.value || root.getAttribute('value') || root.textContent || '').trim().toLowerCase();
    return value === 'comfy_anima' || value === 'anima' || /\banima\b/.test(value);
  }

  function activeGenerator(button, tab) {
    return comfyAnimaActive(button) ? 'anima' : (h3Active(tab) ? 'h3' : null);
  }

  function autoplayEnabled(tab) {
    const root = document.getElementById(`${tab}_h3_autoplay`);
    const input = root?.matches?.('input[type=checkbox]') ? root : root?.querySelector?.('input[type=checkbox]');
    return !!input?.checked;
  }
  // DEV16: read the actual Gradio Settings controls. These are rendered once in
  // the Settings tab even while that tab is hidden. If not ready, fall back to
  // SAFE muted playback. Changes are persisted by Forge's normal Apply settings.
  function settingCheckbox(key, fallback) {
    const root = document.getElementById(`setting_${key}`);
    if (!root) return fallback;
    const input = root.matches?.('input[type="checkbox"]') ? root : root.querySelector?.('input[type="checkbox"]');
    return input ? !!input.checked : fallback;
  }
  function previewVolume() {
    const root = document.getElementById('setting_comfy_video_volume') || document.getElementById('setting_h3_preview_volume');
    const input = root?.querySelector?.('input[type="number"]') || root?.querySelector?.('input[type="range"]');
    const percent = Number(input?.value ?? 25);
    return Number.isFinite(percent) ? Math.max(0, Math.min(1, percent / 100)) : 0.25;
  }
  function previewAudioSettings() {
    return {
      sound: settingCheckbox('comfy_video_sound', settingCheckbox('h3_preview_sound', false)),
      volume: previewVolume(),
    };
  }
  function applyPreviewAudio(video) {
    if (!video) return;
    const prefs = previewAudioSettings();
    video.defaultMuted = !prefs.sound;
    video.muted = !prefs.sound;
    video.volume = prefs.volume;
  }
  // Keep the current playback in sync while the user adjusts Settings.
  // This does NOT modify the generated file or the audio-generation checkbox.
  function previewSettingChanged(ev) {
    if (!ev.target?.closest?.('#setting_comfy_video_sound, #setting_comfy_video_volume, #setting_h3_preview_sound, #setting_h3_preview_volume')) return;
    for (const tab of ['txt2img', 'img2img']) {
      const video = document.querySelector(`#${tab}_h3_main_preview video`);
      if (video) applyPreviewAudio(video);
    }
  }
  document.addEventListener('input', previewSettingChanged);
  document.addEventListener('change', previewSettingChanged);
  const findAnchor = root => root?.matches?.('a[href]') ? root : root?.querySelector?.('a[href]');

  function gallery(tab) {
    return document.getElementById(`${tab}_gallery`) ||
           document.querySelector(`#${tab}_results`) ||
           document.querySelector(`#${tab}_gallery_container`);
  }

  function statusBox(tab) {
    const host = gallery(tab); if (!host) return null;
    let box = document.getElementById(`${tab}_h3_live_status`);
    if (!box) {
      box = document.createElement('div');
      box.id = `${tab}_h3_live_status`;
      box.style.cssText = 'position:absolute;inset:0;z-index:30;display:flex;align-items:center;justify-content:center;background:rgba(10,16,27,.86);color:#fff;font-size:18px;font-weight:600;text-align:center;padding:24px;';
      host.style.position = 'relative'; host.appendChild(box);
    }
    return box;
  }

  function fmtTime(ms) {
    const sec = Math.max(0, Math.floor(ms / 1000)), m = Math.floor(sec / 60), s = sec % 60;
    return `${m}:${String(s).padStart(2,'0')}`;
  }

  const LAST_COMFY_SEEDS = Object.create(null);
  function rememberComfySeed(tab, value) {
    // Either path is generated by our own backend: H3-<seed>-*.mp4 or ANIMA-<seed>-*.png/jpg/webp.
    const match = String(value || '').match(/(?:H3|ANIMA)-(\d+)-[\da-f]+\.(?:mp4|png|jpe?g|webp)/i);
    if (match && /^\d+$/.test(match[1])) {
      const n = Number(match[1]);
      if (Number.isSafeInteger(n) && n >= 0) LAST_COMFY_SEEDS[tab] = n;
      else console.warn('[Comfy Neo] Seed is not representable exactly in Gradio Number:', match[1]);
    }
  }

  function setNativeSeed(tab, value) {
    const root = document.getElementById(`${tab}_seed`);
    // Stable elem_id comes from Forge's built-in ScriptSeed.elem_id('seed').
    const input = root?.matches?.('input') ? root : root?.querySelector?.('input');
    if (!input || !Number.isSafeInteger(value)) return false;
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
    if (!setter) return false;
    setter.call(input, String(value));
    input.dispatchEvent(new Event('input', {bubbles:true}));
    input.dispatchEvent(new Event('change', {bubbles:true}));
    return true;
  }

  // DEV23: native green Reuse Seed expects Forge generation_info JSON, which
  // Comfy's overlay never populates. Intercept only while inside Comfy Neo.
  window.addEventListener('click', ev => {
    const button = ev.target?.closest?.('button');
    if (!button) return;
    const tab = button.id.includes('img2img') ? 'img2img' : 'txt2img';
    const valid = button.id === `${tab}_reuse_seed`;
    const isComfy = !!button.closest('#tab_comfy_engine');
    if (!valid || !isComfy) return;
    ev.preventDefault(); ev.stopPropagation(); ev.stopImmediatePropagation();
    const last = LAST_COMFY_SEEDS[tab];
    if (!Number.isSafeInteger(last)) {
      console.warn('[Comfy Neo] No successful Comfy result seed for Reuse Seed yet.');
      return;
    }
    if (setNativeSeed(tab, last)) console.log('[Comfy Neo] Reusing last exact seed:', last);
  }, true);

  function stateValue(tab) {
    const root = document.getElementById(`${tab}_h3_comfy_state`);
    const input = root?.matches?.('input,textarea') ? root : root?.querySelector?.('input,textarea');
    return String(input?.value || root?.getAttribute?.('value') || '');
  }

  function readNumber(id) {
    const root = document.getElementById(id); if (!root) return NaN;
    if (root.matches?.('input')) { const n = Number(root.value); if (Number.isFinite(n)) return n; }
    const inputs = [...root.querySelectorAll?.('input') || []];
    for (const input of [...inputs.filter(x => x.type === 'number'), ...inputs]) {
      const n = Number(input.value); if (Number.isFinite(n)) return n;
    }
    const aria = root.querySelector?.('[aria-valuenow]');
    const n = Number(aria?.getAttribute?.('aria-valuenow'));
    return Number.isFinite(n) ? n : NaN;
  }

  function nearest32(v) {
    const lo = Math.floor(v / 32) * 32, hi = lo + 32;
    return [lo, hi];
  }

  // DEV17: Forge's img2img Resize by tab does not change the Width/Height
  // sliders. The *visible* scale control identifies which resize tab is active.
  function img2imgResizeBySelected() {
    const scaleRoot = document.getElementById('img2img_scale');
    if (!scaleRoot) return false;
    const byTab = document.getElementById('img2img_tab_resize_by');
    const toTab = document.getElementById('img2img_tab_resize_to');
    if (byTab?.getAttribute?.('aria-selected') === 'true') return true;
    if (toTab?.getAttribute?.('aria-selected') === 'true') return false;
    return visible(scaleRoot);
  }

  function dimensionError(tab) {
    // In Resize by, dimensions come from the input picture and scale; validate
    // those exact dimensions on the Python side instead of stale Width/Height.
    if (tab === 'img2img' && img2imgResizeBySelected()) return '';
    const w = Math.trunc(readNumber(`${tab}_width`));
    const h = Math.trunc(readNumber(`${tab}_height`));
    if (!Number.isFinite(w) || !Number.isFinite(h)) return '';
    if (w % 32 === 0 && h % 32 === 0) return '';
    const [wl, wh] = nearest32(w), [hl, hh] = nearest32(h);
    return `MiniMax H3: width и height должны быть кратны 32. Получено: ${w}×${h}. Ближайшие: width ${wl} или ${wh}; height ${hl} или ${hh}. Размер не изменён.`;
  }

  function showError(tab, message) {
    const kind = RUNS.get(tab)?.kind;
    clearRun(tab);
    const box = statusBox(tab); if (!box) return;
    box.style.display = 'flex';
    const model = kind === 'anima' ? 'ANIMA' : 'MiniMax H3';
    box.innerHTML = `<div style="max-width:760px"><div style="font-size:18px;color:#ffb3b3">${model} — генерация не запущена</div><div style="font-size:14px;font-weight:400;line-height:1.45;margin-top:10px">${String(message).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}</div></div>`;
  }

  function watchBackendState(tab) {
    const run = RUNS.get(tab); if (!run) return;
    run.stateTimer = setInterval(() => {
      const value = stateValue(tab);
      if (!value || value === run.oldState) return;
      run.oldState = value;
      if (value.startsWith('done|') || value.startsWith('done-image|')) rememberComfySeed(tab, value);
      if (value.startsWith('error|')) {
        const parts = value.split('|'); showError(tab, parts.slice(2).join('|') || 'Ошибка MiniMax H3');
      } else if (value.startsWith('stopped|')) {
        clearRun(tab);
        const box = document.getElementById(`${tab}_h3_live_status`);
        if (box) { box.innerHTML = '<div>Генерация остановлена</div>'; setTimeout(() => box.remove(), 900); }
      }
    }, 100);
  }

  function clearRun(tab) {
    const run = RUNS.get(tab);
    if (run?.timer) clearInterval(run.timer);
    if (run?.resultTimer) clearInterval(run.resultTimer);
    if (run?.observer) run.observer.disconnect();
    if (run?.stateTimer) clearInterval(run.stateTimer);
    RUNS.delete(tab);
  }

  function showWorking(tab, kind='h3') {
    clearRun(tab);
    const previous = document.getElementById(`${tab}_h3_main_preview`);
    if (previous) previous.remove();
    const started = Date.now(), box = statusBox(tab);
    const run = {started, kind, timer:null, resultTimer:null, observer:null, stateTimer:null, oldState:stateValue(tab)};
    const paint = () => {
      if (!box) return;
      box.style.display = 'flex';
      box.innerHTML = `<div><div>${kind === 'anima' ? 'ANIMA • ComfyUI генерирует изображение…' : 'MiniMax H3 • ComfyUI генерирует видео…'}</div><div style="font-size:14px;font-weight:400;opacity:.8;margin-top:8px">Прошло: ${fmtTime(Date.now()-started)}</div><button id="${tab}_h3_stop_visible" type="button" style="margin-top:14px;padding:8px 22px;border-radius:8px;border:1px solid #777;background:#8b2525;color:white;font-weight:700;cursor:pointer">■ Stop</button></div>`;
      const stop = box.querySelector(`#${tab}_h3_stop_visible`);
      if (stop) stop.onclick = ev => {
        ev.preventDefault(); ev.stopPropagation();
        const hidden = document.getElementById(`${tab}_h3_comfy_stop`);
        if (!hidden) return;
        stop.disabled = true; stop.textContent = 'Останавливаю…';
        hidden.click(); clearRun(tab);
        box.innerHTML = '<div>Генерация остановлена</div>';
        setTimeout(() => box.remove(), 900);
      };
    };
    paint(); run.timer = setInterval(paint, 1000); RUNS.set(tab, run); watchBackendState(tab);
  }

  function resultHref(tab) {
    const vr = document.getElementById(`${tab}_h3_comfy_video`);
    const v = vr?.querySelector?.('video');
    const source = vr?.querySelector?.('source[src]');
    const va = findAnchor(vr);
    const dl = document.getElementById(`${tab}_h3_download`);
    const da = findAnchor(dl);
    const raw = v?.currentSrc || v?.src || source?.src || va?.href || da?.href || '';
    return raw ? String(raw) : '';
  }

  function showVideo(tab, href) {
    rememberComfySeed(tab, href);
    const host = gallery(tab); if (!host || !href) return;
    const run = RUNS.get(tab); const elapsed = run ? Date.now() - run.started : 0;
    clearRun(tab);
    const box = document.getElementById(`${tab}_h3_live_status`); if (box) box.remove();
    let wrap = document.getElementById(`${tab}_h3_main_preview`);
    if (!wrap) {
      wrap = document.createElement('div'); wrap.id = `${tab}_h3_main_preview`;
      wrap.style.cssText = 'position:absolute;inset:0;z-index:25;background:#0b111c;display:flex;align-items:center;justify-content:center;padding:8px;';
      host.style.position = 'relative'; host.appendChild(wrap);
    }
    wrap.innerHTML = '';
    if (elapsed) {
      const badge = document.createElement('div'); badge.textContent = `Готово за ${fmtTime(elapsed)}`;
      badge.style.cssText = 'position:absolute;top:12px;right:12px;z-index:2;background:rgba(0,0,0,.68);color:#fff;padding:6px 10px;border-radius:8px;font-size:13px;font-weight:600;';
      wrap.appendChild(badge);
    }
    const video = document.createElement('video');
    // Set muted and volume BEFORE assigning src or appending the element.
    // Narrow vertical videos often hide the native volume slider entirely.
    video.controls = true;
    video.autoplay = settingCheckbox('comfy_video_autoplay', settingCheckbox('h3_autoplay_video', autoplayEnabled(tab)));
    video.loop = true;
    video.playsInline = true;
    video.preload = 'metadata';
    applyPreviewAudio(video);
    video.src = href;
    video.style.cssText = 'max-width:100%;max-height:100%;width:auto;height:auto;';
    wrap.appendChild(video);
    console.log('[MiniMax H3 Full Comfy] Forge preview updated:', href);
  }

  function resultImageHref(tab) {
    const root = document.getElementById(`${tab}_h3_comfy_image`);
    const img = root?.querySelector?.('img[src]');
    return String(img?.currentSrc || img?.src || '');
  }

  function showImage(tab, href) {
    rememberComfySeed(tab, href);
    const host = gallery(tab); if (!host || !href) return;
    const run = RUNS.get(tab), elapsed = run ? Date.now() - run.started : 0;
    clearRun(tab);
    document.getElementById(`${tab}_h3_live_status`)?.remove();
    let wrap = document.getElementById(`${tab}_h3_main_preview`);
    if (!wrap) {
      wrap = document.createElement('div'); wrap.id = `${tab}_h3_main_preview`;
      host.style.position = 'relative'; host.appendChild(wrap);
    }
    wrap.style.cssText = 'position:absolute;inset:0;z-index:25;background:#0b111c;display:flex;align-items:center;justify-content:center;padding:8px;';
    wrap.innerHTML = '';
    const img = document.createElement('img');
    img.src = href;
    img.style.cssText = 'max-width:100%;max-height:100%;width:auto;height:auto;object-fit:contain;cursor:zoom-in';
    // Use Forge's own native image lightbox instead of opening a new tab.
    // The same global showModal() is used by the regular Forge gallery.
    img.addEventListener('click', ev => {
      ev.preventDefault(); ev.stopPropagation();
      const lightboxDocument = typeof window.gradioApp === 'function' ? window.gradioApp() : document;
      if (typeof window.showModal === 'function' && lightboxDocument?.getElementById?.('lightboxModal')) {
        const zoom = lightboxDocument.getElementById('modalImage');
        if (typeof window.modalZoomSet === 'function') {
          window.modalZoomSet(zoom, !!window.opts?.js_modal_lightbox_initially_zoomed);
        }
        window.showModal(ev);
      } else {
        console.warn('[Comfy Neo] Forge native lightbox not found; preview remains inline.');
      }
    });
    wrap.appendChild(img);
    if (elapsed) {
      const badge = document.createElement('div'); badge.textContent = `ANIMA • Готово за ${fmtTime(elapsed)}`;
      badge.style.cssText = 'position:absolute;top:12px;right:12px;z-index:2;background:rgba(0,0,0,.68);color:#fff;padding:6px 10px;border-radius:8px;font-size:13px;';
      wrap.appendChild(badge);
    }
  }

  function waitResult(tab, oldHref) {
    const run = RUNS.get(tab); if (!run) return;
    const kind = run.kind;
    const result = () => kind === 'anima' ? resultImageHref(tab) : resultHref(tab);
    const display = kind === 'anima' ? showImage : showVideo;
    const check = () => {
      const href = result();
      if (href && href !== oldHref) { display(tab, href); return true; }
      return false;
    };
    if (check()) return;
    run.resultTimer = setInterval(check, 250);
    const roots = (kind === 'anima'
      ? [document.getElementById(`${tab}_h3_comfy_image`)]
      : [document.getElementById(`${tab}_h3_comfy_video`), document.getElementById(`${tab}_h3_download`)]).filter(Boolean);
    if (roots.length) {
      run.observer = new MutationObserver(() => {
        const href = result();
        // Do not resurrect old results while a new job is initializing.
        if (href && href !== oldHref) display(tab, href);
      });
      for (const root of roots) run.observer.observe(root, {subtree:true,childList:true,attributes:true,attributeFilter:['href','src','value']});
    }
  }

  function cleanupH3Ui(tab) {
    clearRun(tab);
    document.getElementById(`${tab}_h3_live_status`)?.remove();
    document.getElementById(`${tab}_h3_main_preview`)?.remove();
  }

  function hiddenInput(id) {
    const root = document.getElementById(id);
    return root?.matches?.('textarea,input') ? root : root?.querySelector?.('textarea,input');
  }

  function setHiddenValue(id, value) {
    const el = hiddenInput(id); if (!el) return false;
    el.value = String(value ?? '');
    el.dispatchEvent(new Event('input', {bubbles:true}));
    el.dispatchEvent(new Event('change', {bubbles:true}));
    return true;
  }

  function mediaKey(el) {
    if (!el) return '';
    if (el.tagName === 'IMG') return `img:${el.currentSrc || el.src || el.getAttribute('src') || ''}`;
    if (el.tagName === 'CANVAS') return `canvas:${el.width}x${el.height}:${el.toDataURL('image/png').slice(0,64)}`;
    const bg = getComputedStyle(el).backgroundImage || '';
    return `bg:${bg}`;
  }

  function mediaArea(el) {
    const w = el?.naturalWidth || el?.videoWidth || el?.width || el?.clientWidth || 0;
    const h = el?.naturalHeight || el?.videoHeight || el?.height || el?.clientHeight || 0;
    return w * h;
  }

  function uniqueMedia(elements) {
    const out = [], seen = new Set();
    for (const el of elements || []) {
      const key = mediaKey(el); if (!key || seen.has(key)) continue;
      seen.add(key); out.push(el);
    }
    return out;
  }

  function visibleMedia(root) {
    return uniqueMedia([...(root?.querySelectorAll?.('img, canvas, [style*="background-image"]') || [])].filter(el => {
      if (!visible(el)) return false;
      if (el.tagName === 'IMG') {
        const src = el.currentSrc || el.src || el.getAttribute('src') || '';
        return !!src && ((el.naturalWidth || el.width || 0) >= 24 || (el.naturalHeight || el.height || 0) >= 24);
      }
      if (el.tagName === 'CANVAS') return (el.width || 0) >= 24 || (el.height || 0) >= 24;
      const bg = getComputedStyle(el).backgroundImage || '';
      return bg && bg !== 'none';
    }));
  }

  function candidateRoots(selectors) {
    const seen = new Set(), out = [];
    for (const sel of selectors) {
      for (const el of document.querySelectorAll(sel)) {
        if (!el || seen.has(el) || !visible(el)) continue;
        seen.add(el); out.push(el);
      }
    }
    return out;
  }

  // v23: Never search the whole img2img tab: it contains the ImageStitch gallery.
  // The primary reference must come from the *actual* img2img image input only.
  function findImg2imgInputMedia() {
    const preciseIds = ['img2img_image', 'img2img_img2img_tab'];
    for (const id of preciseIds) {
      const root = document.getElementById(id);
      if (!root) continue;
      // Custom extension elements can be nested beneath a Gradio img2img wrapper.
      // Exclude them even when their thumbnails are larger than the primary image.
      const media = visibleMedia(root).filter(el => {
        const marked = el.closest('[id*="imagestitch"], [id*="image_stitch"], [class*="imagestitch"], [class*="image_stitch"]');
        if (marked) return false;
        const ids = [];
        for (let node = el; node && node !== root.parentElement; node = node.parentElement)
          ids.push(String(node.id || '').toLowerCase());
        return !ids.some(x => /gallery|result|output|comfy|imagestitch|image_stitch/.test(x));
      });
      // A single primary image is expected. Prefer the actual image over gallery thumbnails.
      const images = media.filter(el => el.tagName === 'IMG' || el.tagName === 'CANVAS');
      if (images.length) return images.sort((a,b) => mediaArea(b)-mediaArea(a))[0];
    }
    console.warn('[MiniMax H3] img2img main image not found in dedicated input; refusing to substitute ImageStitch thumbnails');
    return null;
  }

  // DEV30: native ForgeCanvas exposes two LogicalImage textareas per canvas.
  // The foreground is a transparent drawing layer: for Sketch it is blended
  // into the picture, for Inpaint its alpha is the mask.
  function canvasLayers(rootId) {
    const root = document.getElementById(rootId);
    const holder = root?.querySelector('[id^="container_uuid_"]');
    if (!holder) throw new Error(`ANIMA: ${rootId} canvas not found.`);
    const uid = holder.id.slice('container_'.length);
    const read = kind => document.querySelector(`[id="${uid}"].logical_image_${kind} textarea`)?.value || '';
    const background = read('background');
    const foreground = read('foreground');
    if (!background.startsWith('data:image/png;base64,'))
      throw new Error(`ANIMA: load a background picture in ${rootId} first.`);
    return {background, foreground};
  }

  function selectedImg2imgSubmode() {
    const root = document.getElementById('mode_img2img');
    const buttons = root?.querySelectorAll('button') || [];
    for (let i = 0; i < Math.min(buttons.length, 6); i++) {
      const b = buttons[i];
      if (b.getAttribute('aria-selected') === 'true' ||
          b.classList.contains('selected') || b.getAttribute('data-state') === 'active') return i;
    }
    // Gradio may mark its TabItem instead of the tab button.
    const chosen = root?.querySelector('[role="tab"][aria-selected="true"]');
    if (chosen) return Array.from(buttons).indexOf(chosen);
    throw new Error('ANIMA: could not determine the selected img2img subtab.');
  }

  function radioIsInverted() {
    const root = document.getElementById('img2img_mask_mode');
    if (!root) return false;
    const radios = [...root.querySelectorAll('input[type="radio"]')];
    const active = radios.find(r => r.checked);
    if (active) return radios.indexOf(active) === 1 || /not masked/i.test(active.value);
    const labels = [...root.querySelectorAll('[role="radio"]')];
    const selected = labels.find(r => r.getAttribute('aria-checked') === 'true');
    if (selected) return labels.indexOf(selected) === 1;
    return false;
  }

  async function uploadImageFrom(id) {
    const root = document.getElementById(id);
    if (!root) return '';
    const imgs = visibleMedia(root).filter(el => el.tagName === 'IMG' || el.tagName === 'CANVAS');
    const media = imgs.sort((a, b) => mediaArea(b) - mediaArea(a))[0];
    return media ? await elementToDataUrl(media) : '';
  }

  async function collectAnimaEditPayload() {
    const modeIndex = selectedImg2imgSubmode();
    const names = ['img2img', 'sketch', 'inpaint', 'inpaint_sketch', 'inpaint_upload'];
    if (modeIndex === 5)
      throw new Error('ANIMA Batch is not supported yet. Use a single image in the other tabs.');
    const name = names[modeIndex];
    if (!name) throw new Error('ANIMA: unsupported img2img tab.');
    const payload = {engine:'anima', edit_mode:name};
    if (name === 'img2img') {
      const media = findImg2imgInputMedia();
      if (!media) throw new Error('ANIMA img2img: add a source picture in the Image tab.');
      payload.init_image = await elementToDataUrl(media);
    } else if (name === 'inpaint_upload') {
      payload.init_image = await uploadImageFrom('img_inpaint_base');
      payload.mask_image = await uploadImageFrom('img_inpaint_mask');
      if (!payload.mask_image) throw new Error('ANIMA Inpaint upload: load a separate mask image.');
    } else {
      const id = {sketch:'img2img_sketch', inpaint:'img2maskimg',
                  inpaint_sketch:'inpaint_sketch'}[name];
      const layers = canvasLayers(id);
      payload.init_image = layers.background;
      payload.sketch_layer = layers.foreground;
      if (!payload.sketch_layer) throw new Error(`ANIMA ${name}: paint on the canvas before generating.`);
    }
    if (!payload.init_image) throw new Error(`ANIMA ${name}: no source picture found.`);
    if (name.startsWith('inpaint')) {
      payload.invert_mask = radioIsInverted();
      const blur = readNumber('img2img_mask_blur');
      payload.mask_blur = Number.isFinite(blur) ? blur : 4;
    }
    if (img2imgResizeBySelected()) {
      const scale = readNumber('img2img_scale');
      if (!Number.isFinite(scale) || scale < 0.5 || scale > 4)
        throw new Error('ANIMA img2img: Resize by must be between 0.5 and 4.');
      payload.resize_by = scale;
    }
    return payload;
  }

  function visibleTextNodes(pattern) {
    return [...document.querySelectorAll('button, summary, .label-wrap, .label, span, div')]
      .filter(el => visible(el) && pattern.test(String(el.textContent || '')));
  }

  // v26: Never collect refs from the other (possibly hidden) Forge tab.
  // Its ImageStitch gallery may remain in the DOM even after clearing another tab.
  function forgeTabRoot(tab) {
    const button = document.getElementById(`${tab}_generate`);
    const candidates = [`tab_${tab}`, `${tab}_tab`, `tabitem_${tab}`, tab];
    for (const id of candidates) {
      const root = document.getElementById(id);
      if (root && (!button || root.contains(button))) return root;
    }
    // A Gradio tab panel is the narrowest ancestor containing the Generate button.
    if (!button) return null;
    return button.closest('[role="tabpanel"]') || button.closest('.tabitem') || null;
  }

  // Forge ImageStitch Integrated assigns a stable elem_id to its gr.Gallery:
  // Script.elem_id("ref_latent") => script_<tab>_imagestitch_integrated_ref_latent.
  // Read ONLY that input gallery, never Forge's generated-image gallery or the
  // temporary "Image to Upload" picker.  v28-v30 walked up the DOM and could
  // miss the actual gallery entirely (or accidentally read output images).
  function imageStitchGallery(tab) {
    const scope = forgeTabRoot(tab);
    if (!scope) return null;
    const ids = [
      `script_${tab}_imagestitch_integrated_ref_latent`,
      `${tab}_imagestitch_integrated_ref_latent`,
      `script_${tab}_image_stitch_ref_latent`,
    ];
    for (const id of ids) {
      const root = document.getElementById(id);
      if (root && scope.contains(root)) return root;
    }
    // A name suffix fallback is safe: it identifies ImageStitch's INPUT gallery,
    // not a general "gallery", and must still belong to the requested tab.
    return scope.querySelector('[id$="imagestitch_integrated_ref_latent"], [id$="image_stitch_ref_latent"]');
  }

  function checkboxValue(component) {
    if (!component) return null;
    const input = component.matches?.('input[type="checkbox"]')
      ? component : component.querySelector?.('input[type="checkbox"]');
    if (input) return !!input.checked;
    const role = component.matches?.('[role="checkbox"], [role="switch"]') ? component
      : component.querySelector?.('[role="checkbox"], [role="switch"]');
    if (role && role.hasAttribute('aria-checked')) return role.getAttribute('aria-checked') === 'true';
    return null;
  }

  // v32: A Gradio InputAccordion can be OPEN while its independent Enable
  // checkbox is OFF. Do not use aria-expanded, .open, or accordion visibility
  // as evidence that references are enabled. This matters especially in img2img.
  function imageStitchEnabled(tab, galleryRoot) {
    const scope = forgeTabRoot(tab);
    if (!scope || !galleryRoot) return false;

    // Forge's InputAccordion creates a hidden gr.Checkbox named
    // `<accordion-id>-checkbox`. Prefer this authoritative checkbox when the
    // gallery is inside the ImageStitch accordion.
    const accordion = galleryRoot.closest('.input-accordion');
    if (accordion?.id) {
      const state = checkboxValue(document.getElementById(`${accordion.id}-checkbox`));
      if (state !== null) return state;
    }

    // On some Forge/Gradio layouts the accordion header and its gallery are
    // rendered in adjacent wrappers. Resolve the ImageStitch header by name
    // within the *current tab* and read its matching hidden checkbox.
    const headers = [...scope.querySelectorAll('.input-accordion')].filter(el => {
      const title = el.querySelector('.label-wrap, .label, summary, button') || el;
      return /ImageStitch\s*Integrated/i.test(String(title.textContent || ''));
    });
    for (const header of headers.reverse()) {
      if (!(header === galleryRoot || (header.compareDocumentPosition(galleryRoot) & Node.DOCUMENT_POSITION_FOLLOWING))) continue;
      if (header.id) {
        const state = checkboxValue(document.getElementById(`${header.id}-checkbox`));
        if (state !== null) return state;
      }
      // Visible checkbox inside this named header is also authoritative.
      const state = checkboxValue(header);
      if (state !== null) return state;
    }

    // The displayed "Enable" toggle may be a separate gr.Checkbox. Take only
    // the closest preceding labelled toggle in this Forge tab, not an
    // arbitrary checkbox from another extension or the results gallery.
    const labels = [...scope.querySelectorAll('label, .gradio-checkbox, .checkbox, [role="checkbox"], [role="switch"]')]
      .filter(el => {
        if (!(el.compareDocumentPosition(galleryRoot) & Node.DOCUMENT_POSITION_FOLLOWING)) return false;
        if (el.closest('[id*="results"], [id*="output"], [id*="h3_comfy"]')) return false;
        const label = String(el.textContent || '').trim();
        return /^Enable$/i.test(label) ||
          (/^Enable$/i.test(String(el.getAttribute('aria-label') || '').trim()));
      });
    for (const label of labels.reverse()) {
      const input = label.matches?.('label') && label.htmlFor
        ? document.getElementById(label.htmlFor) : null;
      const state = checkboxValue(input || label);
      if (state !== null) return state;
    }

    // Fail CLOSED instead of treating a missing toggle as enabled. We must not
    // silently feed refs when the user has deliberately disabled ImageStitch.
    console.warn(`[MiniMax H3] ${tab}: cannot read ImageStitch Enable state; skipping its references`);
    return false;
  }

  function findImageStitchMedia(tab) {
    const inputGallery = imageStitchGallery(tab);
    if (!inputGallery) {
      console.warn(`[MiniMax H3] ${tab}: ImageStitch input ref_latent gallery not found`);
      return [];
    }
    if (!imageStitchEnabled(tab, inputGallery)) {
      console.log(`[MiniMax H3] ${tab}: ImageStitch disabled, skipping gallery`);
      return [];
    }
    // Gradio Gallery's thumbnails live inside this exact component; preserve
    // DOM/gallery order for <Picture N> and do not scan adjacent elements.
    const media = visibleMedia(inputGallery);
    console.log(`[MiniMax H3] ${tab}: ImageStitch input gallery contains ${media.length} preview(s)`);
    return media;
  }

  function blobToDataUrl(blob) {
    return new Promise((resolve, reject) => {
      const r = new FileReader();
      r.onload = () => resolve(String(r.result || ''));
      r.onerror = reject;
      r.readAsDataURL(blob);
    });
  }

  async function elementToDataUrl(el) {
    if (!el) return '';
    if (el.tagName === 'CANVAS') {
      try { return el.toDataURL('image/png'); } catch (_e) { return ''; }
    }
    if (el.tagName === 'IMG') {
      const src = String(el.currentSrc || el.src || el.getAttribute('src') || '');
      if (!src) return '';
      if (src.startsWith('data:image/')) return src;
      try {
        const r = await fetch(src, {credentials:'same-origin', cache:'force-cache'});
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return await blobToDataUrl(await r.blob());
      } catch (e) {
        console.warn('[MiniMax H3] could not read image source', src, e);
        return '';
      }
    }
    const bg = getComputedStyle(el).backgroundImage || '';
    const m = bg.match(/^url\(["']?(.*?)["']?\)$/);
    if (m?.[1]) {
      try {
        const r = await fetch(m[1], {credentials:'same-origin', cache:'force-cache'});
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return await blobToDataUrl(await r.blob());
      } catch (_e) { return ''; }
    }
    return '';
  }

  async function collectConditioningPayload(tab) {
    const payload = {init_image:null, stitch_images:[]};
    if (tab === 'img2img') {
      payload.init_image = await elementToDataUrl(findImg2imgInputMedia()) || null;
      if (img2imgResizeBySelected()) {
        const scale = readNumber('img2img_scale');
        if (!Number.isFinite(scale) || scale < 0.5 || scale > 4.0) {
          throw new Error(`MiniMax H3: invalid img2img Resize by scale: ${scale}`);
        }
        if (!payload.init_image) throw new Error('MiniMax H3: Resize by requires a valid img2img input image.');
        // Backend reads the intrinsic image dimensions from this image payload.
        // No DOM thumbnail dimensions are used for the final video size.
        payload.resize_by = scale;
      }
    }
    const refs = findImageStitchMedia(tab);
    const seen = new Set();
    for (const el of refs) {
      const data = await elementToDataUrl(el);
      if (!data || seen.has(data)) continue;
      seen.add(data);
      payload.stitch_images.push(data);
      if (payload.stitch_images.length >= 9) break;
    }
    if (payload.init_image && seen.has(payload.init_image)) {
      payload.stitch_images = payload.stitch_images.filter(x => x !== payload.init_image);
    }
    return payload;
  }

  document.addEventListener('click', async ev => {
    const b = ev.target?.closest?.('button'); if (!b || !GEN_IDS.has(b.id)) return;
    const tab = b.id.startsWith('img2img') ? 'img2img' : 'txt2img';
    const kind = activeGenerator(b, tab);
    if (!kind) { cleanupH3Ui(tab); return; }
    const trigger = document.getElementById(`${tab}_h3_comfy_generate`); if (!trigger) return;
    ev.preventDefault(); ev.stopPropagation(); ev.stopImmediatePropagation();
    const dimError = kind === 'h3' ? dimensionError(tab) : '';
    if (dimError) {
      console.warn('[MiniMax H3 Full Comfy] validation:', dimError);
      showError(tab, dimError);
      return;
    }
    const oldHref = kind === 'anima' ? resultImageHref(tab) : resultHref(tab);
    showWorking(tab, kind); waitResult(tab, oldHref);
    // DEV26: ANIMA uses Forge's native input image + Denoise/Resize Mode.
    // Collect ONLY the real input, never ImageStitch/previous output thumbnails.
    if (kind === 'anima') {
      setHiddenValue(`${tab}_h3_comfy_images`, JSON.stringify({engine:'anima'}));
      if (tab === 'img2img') {
        try {
          const payload = await collectAnimaEditPayload();
          setHiddenValue(`${tab}_h3_comfy_images`, JSON.stringify(payload));
          console.log('[Comfy Neo] ANIMA edit input prepared', {
            mode:payload.edit_mode, mask:!!payload.sketch_layer || !!payload.mask_image,
            resize_by:payload.resize_by ?? null
          });
        } catch (e) {
          showError(tab, String(e?.message || e));
          return;
        }
      }
      trigger.click();
      return;
    }
    // Clear Gradio-bound payload synchronously before async image reads.
    setHiddenValue(`${tab}_h3_comfy_images`, JSON.stringify({init_image:null,stitch_images:[]}));
    try {
      const payload = await collectConditioningPayload(tab);
      setHiddenValue(`${tab}_h3_comfy_images`, JSON.stringify(payload));
      console.log('[MiniMax H3] media payload prepared:', {tab, init: !!payload.init_image, stitch: payload.stitch_images.length});
    } catch (e) {
      console.warn('[MiniMax H3] media payload collection failed', e);
      setHiddenValue(`${tab}_h3_comfy_images`, JSON.stringify({init_image:null,stitch_images:[]}));
      // Do not silently submit an unscaled clip when the resize payload fails.
      showError(tab, String(e?.message || e));
      return;
    }
    console.log('[MiniMax H3 Full Comfy] intercepted ordinary Generate');
    trigger.click();
  }, true);

  setInterval(() => {
    for (const tab of ['txt2img','img2img']) {
      // On tab switch the gallery may be recreated; the backend video stays available.
      const host = gallery(tab);
      if (host && !document.getElementById(`${tab}_h3_main_preview`) && !RUNS.has(tab)) {
        if (h3Active(tab)) { const href = resultHref(tab); if (href) showVideo(tab, href); }
        else if (comfyAnimaActive(document.getElementById(`${tab}_generate`))) {
          const href = resultImageHref(tab); if (href) showImage(tab, href);
        }
      }
    }
  }, 750);

  function nextNumber() {
    let n = parseInt(localStorage.getItem(COUNTER_KEY) || '0', 10);
    if (!Number.isFinite(n) || n < 0) n = 0;
    n++; localStorage.setItem(COUNTER_KEY, String(n)); return n;
  }

  async function cleanDownload(href, number) {
    const name = `H3-${String(number).padStart(5,'0')}.mp4`;
    try {
      const r = await fetch(href, {credentials:'same-origin', cache:'no-store'});
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const blob = await r.blob(), url = URL.createObjectURL(blob), a = document.createElement('a');
      a.href = url; a.download = name; a.style.display = 'none'; document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 30000);
      console.log('[MiniMax H3] Save As:', name);
    } catch (e) {
      console.warn('[MiniMax H3] clean download fallback:', e);
      const a = document.createElement('a'); a.href = href; a.download = name; a.style.display='none'; document.body.appendChild(a); a.click(); a.remove();
    }
  }

  async function saveAnima(href, number) {
    try {
      const res = await fetch(href, {credentials:'same-origin'});
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const blob = await res.blob();
      // Read the delivered file header. Do not guess from the Gradio image URL:
      // that URL can contain a path/query suffix unrelated to the actual image.
      const head = new Uint8Array(await blob.slice(0, 12).arrayBuffer());
      const ascii = (from, to) => String.fromCharCode(...head.slice(from, to));
      const ext = (head[0] === 0xFF && head[1] === 0xD8) ? 'jpg'
          : (ascii(0, 4) === 'RIFF' && ascii(8, 12) === 'WEBP') ? 'webp'
          : (head[0] === 0x89 && ascii(1, 4) === 'PNG') ? 'png' : null;
      if (!ext) throw new Error('Unknown ANIMA preview image format; refusing mislabeled download');
      const name = `ANIMA-${String(number).padStart(5, '0')}.${ext}`;
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a'); a.href = url; a.download = name;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 30000);
      console.log('[ANIMA] Manual Save As:', name);
    } catch (e) { console.warn('[ANIMA] Save failed:', e); }
  }

  function saveFromDiskette(ev, button) {
    const tab = button.id.includes('img2img') ? 'img2img' : 'txt2img';
    if (comfyAnimaActive(button)) {
      const href = resultImageHref(tab);
      if (!href) return false;
      ev.preventDefault(); ev.stopPropagation(); ev.stopImmediatePropagation();
      saveAnima(href, nextNumber()); return true;
    }
    if (!h3Active(tab)) return false;
    const href = resultHref(tab); if (!href) return false;
    ev.preventDefault(); ev.stopPropagation(); ev.stopImmediatePropagation();
    cleanDownload(href, nextNumber()); return true;
  }

  // Window capture runs before document-level save extensions such as forge-no-copy-download.
  window.addEventListener('pointerdown', ev => {
    const b = ev.target?.closest?.('button'); if (!b || !SAVE_IDS.has(b.id)) return;
    if (saveFromDiskette(ev, b)) suppressSaveClickUntil = Date.now() + 1200;
  }, true);
  window.addEventListener('click', ev => {
    const b = ev.target?.closest?.('button'); if (!b || !SAVE_IDS.has(b.id)) return;
    if (Date.now() < suppressSaveClickUntil && (h3Active(b.id.includes('img2img') ? 'img2img' : 'txt2img') || comfyAnimaActive(b))) {
      ev.preventDefault(); ev.stopPropagation(); ev.stopImmediatePropagation(); return;
    }
    saveFromDiskette(ev, b);
  }, true);
})();

// Comfy Neo — native Gradio portal with safe resize-handle recovery.
// Temporarily relocates the already-mounted txt2img/img2img native Blocks.
// All original callback wiring, script controls and Gradio component ids remain intact.
// Production native UI integration; model/preset state is still Forge-global.
(() => {
  'use strict';
  if (window.__comfyNativePortalDev11) return;
  window.__comfyNativePortalDev11 = true;

  const config = {
    txt2img: {srcId: 'tab_txt2img', slotId: 'comfy-native-t2i-slot'},
    img2img: {srcId: 'tab_img2img', slotId: 'comfy-native-i2i-slot'},
  };
  const state = {
    enabled: false,
    mode: 'txt2img',
    moved: Object.create(null),
    busy: false,
    layoutMode: null,
  };

  function get(id) { return document.getElementById(id); }
  function isVisible(el) {
    if (!el || !el.isConnected) return false;
    for (let node = el; node && node.nodeType === 1; node = node.parentElement) {
      const style = getComputedStyle(node);
      if (node.hidden || style.display === 'none' || style.visibility === 'hidden') return false;
    }
    return true;
  }
  function writeHint(message, warning = false) {
    const hint = get('comfy-native-hint');
    if (!hint) return;
    if (hint.textContent === message && hint.dataset.warning === String(warning)) return;
    hint.textContent = message;
    hint.dataset.warning = String(warning);
    hint.style.display = message ? 'block' : 'none';
  }
  function slotFor(mode) { return get(config[mode].slotId); }
  function srcFor(mode) { return get(config[mode].srcId); }

  // Forge Neo's javascript/resizeHandle.js keeps a single global R.parentWidth
  // for ALL .resize-handle-row instances. A synthetic window resize after a
  // tab is hidden can permanently shrink its left column to 320px. Do not
  // dispatch synthetic resize events when moving native Forge DOM nodes.
  // Heal only obviously broken rows; preserve normal user-resized columns.
  function healCollapsedResizeRows(mode) {
    const nodes = state.moved[mode] || [];
    for (const node of nodes) {
      if (node.nodeType !== 1) continue;
      const rows = [];
      if (node.matches?.('.resize-handle-row')) rows.push(node);
      if (node.querySelectorAll) rows.push(...node.querySelectorAll('.resize-handle-row'));
      for (const row of rows) {
        if (!row.isConnected || !row.querySelector('.resize-handle')) continue;
        const width = row.getBoundingClientRect().width;
        if (!Number.isFinite(width) || width < 800) continue;
        const live = String(row.style.gridTemplateColumns || '');
        const match = /^([\d.]+)px\s+[\d.]+px\s+1fr$/.exec(live);
        if (!match) continue;  // Original fraction layout or responsive flex.
        const left = Number(match[1]);
        if (!Number.isFinite(left)) continue;
        const original = row.style.originalGridTemplateColumns;
        if (typeof original !== 'string' || !original.includes('fr')) continue;
        // Only a collapsed or wildly oversized side is repaired. A normal
        // manual split, such as 40%/60%, remains untouched.
        if (left <= 352 || left < width * 0.24 || left > width * 0.83) {
          row.style.gridTemplateColumns = original;
          console.info('[Comfy Neo] Restored collapsed Forge column:', mode);
        }
      }
    }
  }

  function scheduleLayoutRecovery(mode) {
    requestAnimationFrame(() => healCollapsedResizeRows(mode));
    // Forge's own resize handler debounces at 100ms. Check AFTER it runs,
    // including cases where the destination tab becomes visible one frame later.
    setTimeout(() => healCollapsedResizeRows(mode), 180);
    setTimeout(() => healCollapsedResizeRows(mode), 420);
  }

  // Do not move an entire Gradio tab root. It belongs to the outer Tab system.
  // Moving its existing children preserves the actual components and listeners.
  function mount(mode) {
    const src = srcFor(mode), slot = slotFor(mode);
    if (!src || !slot || src === slot || src.contains(slot)) return false;
    let nodes = state.moved[mode];
    if (!nodes) {
      nodes = Array.from(src.childNodes);
      if (!nodes.length) return false;
      state.moved[mode] = nodes;
    }
    let moved = false;
    for (const node of nodes) {
      if (node.parentNode !== slot) {
        slot.appendChild(node);
        moved = true;
      }
    }
    if (moved) scheduleLayoutRecovery(mode);
    return true;
  }
  function restore(mode) {
    const src = srcFor(mode);
    const nodes = state.moved[mode];
    if (src && nodes) {
      let moved = false;
      for (const node of nodes) {
        if (node.parentNode !== src) {
          src.appendChild(node);
          moved = true;
        }
      }
      if (moved) scheduleLayoutRecovery(mode);
    }
  }
  function restoreAll() {
    restore('txt2img');
    restore('img2img');
    state.enabled = false;
    state.layoutMode = null;
  }
  function setMode(next) {
    if (!Object.hasOwn(config, next)) return;
    if (state.enabled && state.mode !== next) restore(state.mode);
    state.mode = next;
    for (const name of Object.keys(config)) {
      const slot = slotFor(name);
      if (slot) slot.style.display = name === next ? '' : 'none';
    }
    // The selector is absolutely positioned: it never adds a row or changes
    // the original Forge interface geometry.
    const selector = get('comfy-native-mode-select');
    if (selector && selector.value !== next) selector.value = next;
  }
  function sync() {
    if (state.busy) return;
    state.busy = true;
    try {
      const active = isVisible(get('comfy-native-workspace'));
      if (!active) {
        if (state.enabled) restoreAll();
        return;
      }
      if (state.enabled && state.moved[state.mode] &&
          state.moved[state.mode].some(node => node.parentNode && node.parentNode !== slotFor(state.mode))) {
        // Gradio re-rendered the original tab. Preserve it instead of stealing new nodes.
        restoreAll();
      }
      const previousMode = state.mode;
      setMode(previousMode);
      const success = mount(state.mode);
      state.enabled = success;
      if (success) {
        // Reparenting into a live Gradio column naturally causes layout;
        // window.resize here would make Forge recalculate HIDDEN resize rows.
        // Restore only collapsed columns, not the user's ordinary split.
        state.layoutMode = state.mode;
        writeHint('');
      } else {
        writeHint('Не удалось найти исходную вкладку Forge. Попробуй открыть txt2img и затем вернуться в Comfy Neo.', true);
      }
    } catch (err) {
      restoreAll();
      writeHint('Не удалось перенести элементы Forge: ' + String(err), true);
      console.error('[Comfy Neo] UI relocation error', err);
    } finally {
      state.busy = false;
    }
  }

  document.addEventListener('change', event => {
    const selector = event.target;
    if (!(selector instanceof HTMLSelectElement) || selector.id !== 'comfy-native-mode-select') return;
    const next = selector.value;
    if (!Object.hasOwn(config, next)) return;
    if (next !== state.mode) restore(state.mode);
    setMode(next);
    // Switch only the original Forge UI nodes; no new Gradio listeners.
    queueMicrotask(sync);
  });

  // When navigating back to the Forge tabs, restore the same nodes to their
  // original TabItem. No copy, duplicate id, or detached backend callback.
  document.addEventListener('click', () => setTimeout(sync, 0), true);
  document.addEventListener('keydown', event => {
    if (event.key === 'Enter' || event.key === ' ') setTimeout(sync, 0);
  }, true);

  // Gradio may mount TabItem contents slightly after initial page load. A
  // lightweight periodic check also handles navigation from extension scripts.
  let checks = 0;
  const timer = setInterval(() => {
    sync();
    checks++;
    if (!document.documentElement.isConnected || checks > 36000) clearInterval(timer);
  }, 300);

  // Real browser resizes may also touch a hidden native tab through Forge's
  // resize handler; heal it once it is visible again. No synthetic resize.
  window.addEventListener('resize', () => {
    setTimeout(() => {
      healCollapsedResizeRows('txt2img');
      healCollapsedResizeRows('img2img');
    }, 180);
  });
  window.addEventListener('beforeunload', restoreAll, {once: true});
  window.__comfyNativePortalInspect = () => ({
    enabled: state.enabled,
    mode: state.mode,
    parentTxt2img: state.moved.txt2img?.[0]?.parentElement?.id || null,
    parentImg2img: state.moved.img2img?.[0]?.parentElement?.id || null,
  });
})();

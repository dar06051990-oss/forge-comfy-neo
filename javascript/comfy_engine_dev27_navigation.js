// Comfy Neo: native Forge tab isolation and real Comfy image -> img2img.
// Only acts while the Comfy Neo top-level tab is open. Ordinary Forge
// txt2img/img2img, Send-to, Sketch and Inpaint keep their original handlers.
(() => {
  'use strict';
  if (window.__comfyEngineDev27Navigation) return;
  window.__comfyEngineDev27Navigation = true;

  const $ = id => document.getElementById(id);
  const workspaceActive = () => {
    const workspace = $('comfy-native-workspace');
    const outerTab = $('tab_comfy_engine');
    return !!workspace && !!outerTab && outerTab.contains(workspace) &&
      workspace.getClientRects().length > 0 && outerTab.getClientRects().length > 0;
  };
  const comfyAnimaSelected = () => {
    const root = $('forge_ui_preset');
    const input = root?.matches?.('input,select') ? root : root?.querySelector?.('input,select');
    return /^(comfy_anima|anima)$/i.test(String(input?.value || '').trim());
  };
  const isComfyEvent = el => workspaceActive() && !!el?.closest?.('#tab_comfy_engine');

  function notice(message) {
    const el = $('comfy-native-hint');
    if (!el) { console.warn('[Comfy Neo]', message); return; }
    el.textContent = message;
    el.style.display = 'block';
    el.dataset.warning = 'true';
    setTimeout(() => {
      if (el.textContent === message) el.style.display = 'none';
    }, 4500);
  }

  // Native Forge's switch_to_img2img_tab() calls the outer `#tabs` control.
  // Redirect just that call inside Comfy Neo, preserving the existing
  // Gradio click callbacks which copy canvas images between inner modes.
  function installNativeSwitchGuard() {
    const previous = window.switch_to_img2img_tab;
    if (typeof previous !== 'function' || previous.__comfyDev27Guard) return;
    const guarded = function (index) {
      if (workspaceActive()) {
        const group = $('mode_img2img');
        const tabs = group?.querySelectorAll('button') || [];
        const number = Number(index);
        if (Number.isInteger(number) && number >= 0 && number < 4 && tabs[number]) {
          tabs[number].click();
          console.log('[Comfy Neo] Native img2img submode only:', number);
          return;
        }
        notice('Comfy Neo: could not switch the internal img2img mode.');
        return;
      }
      return previous.apply(this, arguments);
    };
    guarded.__comfyDev27Guard = true;
    guarded.__comfyOriginal = previous;
    window.switch_to_img2img_tab = guarded;
    console.log('[Comfy Neo] Forge internal mode navigation isolated');
  }
  installNativeSwitchGuard();
  document.addEventListener('DOMContentLoaded', installNativeSwitchGuard, {once:true});
  // Some theme/extensions load native JS after our file. Stop probing quickly.
  let attempts = 0;
  const guardTimer = setInterval(() => {
    installNativeSwitchGuard();
    if (++attempts >= 40 || window.switch_to_img2img_tab?.__comfyDev27Guard)
      clearInterval(guardTimer);
  }, 500);

  function nativeField(id) {
    const root = $(id);
    if (!root) return null;
    if (root.matches?.('textarea,input,select')) return root;
    return root.querySelector('input[type="number"], textarea, input:not([type="checkbox"]), select');
  }
  function current(id) {
    const input = nativeField(id);
    return input ? String(input.value || '') : null;
  }
  function snapshot(tab) {
    return {
      prompt: current(`${tab}_prompt`),
      negative_prompt: current(`${tab}_neg_prompt`),
      steps: current(`${tab}_steps`),
      cfg: current(`${tab}_cfg_scale`),
      width: current(`${tab}_width`),
      height: current(`${tab}_height`),
      seed: current(`${tab}_seed`),
      sampler: current(`${tab}_sampling`),
      scheduler: current(`${tab}_scheduler`),
    };
  }
  // Preserve the values used to launch a generation rather than copying any
  // edits typed after that generation finished.
  const launched = Object.create(null);
  window.addEventListener('click', ev => {
    const button = ev.target?.closest?.('button');
    if (!button || !/^(txt2img|img2img)_generate$/.test(button.id)) return;
    if (!isComfyEvent(button) || !comfyAnimaSelected()) return;
    const tab = button.id.startsWith('img2img') ? 'img2img' : 'txt2img';
    launched[tab] = snapshot(tab);
  }, true);

  const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
  async function awaitMountedImg2Img() {
    for (let tries = 0; tries < 50; tries++) {
      if ($('comfy-native-i2i-slot')?.contains($('img2img_image')))
        return true;
      await pause(40);
    }
    return false;
  }
  async function switchComfyToImg2Img(toInpaint=false) {
    const selector = $('comfy-native-mode-select');
    if (!selector) throw new Error('Comfy generation-mode selector is missing.');
    if (selector.value !== 'img2img') {
      selector.value = 'img2img';
      selector.dispatchEvent(new Event('change', {bubbles:true}));
    }
    if (!(await awaitMountedImg2Img()))
      throw new Error('Native img2img controls did not move to Comfy Neo.');
    // Select the appropriate native Forge input without opening outer Forge tabs.
    const group = $('mode_img2img');
    const chosen = group?.querySelectorAll('button')?.[toInpaint ? 2 : 0];
    if (!chosen) throw new Error('Native img2img edit tab was not found.');
    chosen.click();
  }

  function actualComfyImage(tab) {
    const preview = $(`${tab}_h3_main_preview`);
    return preview?.querySelector?.('img[src]') || null;
  }

  async function toPngDataUrl(imageElement) {
    const src = imageElement?.currentSrc || imageElement?.src;
    if (!src) throw new Error('No ANIMA preview image available.');
    // The preview may be WebP/JPG or PNG, while ForgeCanvas LogicalImage
    // *requires* a data:image/png;base64 URL.
    const response = await fetch(src, {credentials:'same-origin'});
    if (!response.ok) throw new Error(`Cannot read preview (${response.status}).`);
    const blob = await response.blob();
    let bitmap;
    try {
      bitmap = await createImageBitmap(blob);
      const width = bitmap.width, height = bitmap.height;
      if (!width || !height || width * height > 40_000_000)
        throw new Error('Unsupported preview dimensions.');
      const canvas = document.createElement('canvas');
      canvas.width = width;
      canvas.height = height;
      canvas.getContext('2d').drawImage(bitmap, 0, 0);
      return canvas.toDataURL('image/png');
    } finally {
      bitmap?.close?.();
    }
  }

  function setTextComponent(id, text) {
    const node = $(id);
    const input = node?.matches?.('input,textarea') ? node : node?.querySelector?.('textarea,input');
    if (!input) return false;
    const proto = input.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
    if (!setter) return false;
    setter.call(input, text);
    input.dispatchEvent(new Event('input', {bubbles:true}));
    input.dispatchEvent(new Event('change', {bubbles:true}));
    return true;
  }

  let transferring = false;
  async function sendCurrentResultToImg2Img(fromTab, toInpaint=false) {
    if (transferring) return;
    transferring = true;
    try {
      const img = actualComfyImage(fromTab);
      if (!img) throw new Error('Generate an ANIMA image first; the native gallery may contain an old result.');
      const url = img.currentSrc || img.src;
      const picture = await toPngDataUrl(img);
      const values = {...(launched[fromTab] || snapshot(fromTab))};
      // Recover the real random seed from the Comfy image filename where
      // possible. Gradio Number cannot represent integers above MAX_SAFE_INTEGER.
      if (String(values.seed) === '-1') {
        const match = url.match(/ANIMA-(\d+)-[\da-f]+\.(?:png|jpe?g|webp)/i);
        if (match) {
          const realSeed = Number(match[1]);
          if (Number.isSafeInteger(realSeed)) values.seed = String(realSeed);
        }
      }
      const data = JSON.stringify({image:picture, ...values});
      await switchComfyToImg2Img(toInpaint);
      if (!setTextComponent('comfy_dev27_send_payload', data))
        throw new Error('Comfy image-transfer field is missing. Restart Forge after installing DEV27.');
      const root = $('comfy_dev27_send_go');
      const trigger = root?.matches?.('button') ? root : root?.querySelector?.('button');
      if (!trigger) throw new Error('Comfy image-transfer action is missing. Restart Forge.');
      // Allow Gradio to register the hidden Textbox input before the event.
      await pause(120);
      trigger.click();
      if (toInpaint) {
        // The existing native callback fills img2img background; replicate
        // that background into Inpaint's ForgeCanvas LogicalImage afterward.
        const source = picture;
        await pause(400);
        const holder = $('img2maskimg')?.querySelector('[id^="container_uuid_"]');
        const uid = holder?.id?.slice('container_'.length);
        if (!uid) throw new Error('ANIMA Inpaint canvas is not available.');
        const target = document.querySelector(`[id="${uid}"].logical_image_background textarea`);
        if (!target) throw new Error('ANIMA Inpaint image field is unavailable.');
        const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set;
        setter.call(target, source);
        target.dispatchEvent(new Event('input',{bubbles:true}));
        target.dispatchEvent(new Event('change',{bubbles:true}));
      }
      console.log('[Comfy Neo] Sent actual ANIMA image to', toInpaint ? 'inpaint' : 'img2img');
    } catch (err) {
      console.error('[Comfy Neo] Send to img2img failed:', err);
      notice('Send to Comfy img2img failed: ' + (err?.message || String(err)));
    } finally {
      transferring = false;
    }
  }

  // Native Send-to callbacks read the *old* underlying gr.Gallery, not the
  // Comfy result painted above it. Intercept ONLY this Comfy action and route
  // the real preview into the original ForgeCanvas via the Gradio bridge.
  window.addEventListener('click', ev => {
    const button = ev.target?.closest?.('button');
    if (!button || !isComfyEvent(button)) return;
    const imageMatch = button.id.match(/^(txt2img|img2img)_send_to_img2img$/);
    const inpaintMatch = button.id.match(/^(txt2img|img2img)_send_to_inpaint$/);
    if (!imageMatch && !inpaintMatch) return;
    if (!comfyAnimaSelected()) return;
    ev.preventDefault();
    ev.stopPropagation();
    ev.stopImmediatePropagation();
    sendCurrentResultToImg2Img((imageMatch || inpaintMatch)[1], !!inpaintMatch);
  }, true);
})();

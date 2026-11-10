// Comfy Neo: hide the obsolete H3 settings accordion without
// destroying the hidden Gradio controls used by the existing Comfy bridge.
// IMPORTANT: h3_video_save_redirect.js checks computed display and visibility
// of #txt2img_h3_panel / #img2img_h3_panel to detect H3 activation. Never
// use display:none or visibility:hidden on these selectors while H3 is active.
(() => {
  'use strict';
  const ID = 'comfy-dev13-hide-legacy-h3';
  function install() {
    if (document.getElementById(ID)) return;
    const style = document.createElement('style');
    style.id = ID;
    style.textContent = `
/* Backend-only transport. The old accordion must stay mounted and active. */
#txt2img_h3_panel, #img2img_h3_panel {
    position: absolute !important;
    width: 1px !important;
    height: 1px !important;
    min-width: 0 !important;
    min-height: 0 !important;
    max-width: 1px !important;
    max-height: 1px !important;
    padding: 0 !important;
    margin: 0 !important;
    border: 0 !important;
    overflow: hidden !important;
    clip-path: inset(50%) !important;
    pointer-events: none !important;
    flex: 0 0 1px !important;
}
`;
    document.head.appendChild(style);
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', install, {once:true});
  } else install();
})();

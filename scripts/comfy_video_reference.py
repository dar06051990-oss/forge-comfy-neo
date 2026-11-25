"""Ref2VA video and optional audio controls in Forge's always-visible Scripts.

Three compact native upload slots, each with independent soundtrack control.
"""

import sys
import subprocess
import base64
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import gradio as gr
from modules import scripts
from modules.ui_components import InputAccordion

from forge_h3 import video_reference
from forge_h3.refvideo_clipboard_native import local_request, clipboard_paths, select_mp4


from forge_h3.media import find_ffmpeg
from forge_h3.hidden_subprocess import hidden_windows_kwargs

_EMPTY = '<div class="neo-ref-thumb" title="No video">&#9655;</div>'


def thumbnail(path):
    if not path:
        return _EMPTY
    try:
        p = Path(path)
        if p.suffix.lower() != '.mp4' or not p.is_file() or p.stat().st_size > 96 * 1024 * 1024:
            return _EMPTY
        result = subprocess.run([find_ffmpeg(), '-hide_banner', '-loglevel', 'error', '-nostdin', '-i', str(p), '-map', '0:v:0', '-frames:v', '1', '-vf', 'scale=540:540:force_original_aspect_ratio=decrease', '-an', '-c:v', 'png', '-threads', '1', '-f', 'image2pipe', 'pipe:1'], capture_output=True, timeout=12, **hidden_windows_kwargs())
        if result.returncode or not result.stdout.startswith(b'\x89PNG\r\n\x1a\n'):
            return _EMPTY
        encoded = base64.b64encode(result.stdout).decode('ascii')
        return '<div class="neo-ref-thumb"><img alt="Video thumbnail" src="data:image/png;base64,' + encoded + '"></div>'
    except Exception as exc:
        print(f'[Comfy Neo] Video thumbnail unavailable: {exc}')
        return _EMPTY


def paste_video(request: gr.Request):
    if not local_request(request):
        gr.Warning("Clipboard paste is available only in a local Forge session.")
        return gr.update(), gr.update()
    try:
        selected = select_mp4(clipboard_paths())
        return selected, thumbnail(selected)
    except (OSError, RuntimeError) as exc:
        gr.Warning(str(exc))
        return gr.update(), gr.update()


# Scoped entirely to the Video Reference uploaders; other Forge Scripts,
# img2img and ImageStitch widgets are unaffected. The File component itself is
# retained so its upload/delete state remains native to Gradio.
_COMPACT_CSS = """
<style>
[id$="_comfy_refvideo_gallery"] {
  background: var(--background-fill-secondary, #202937);
  border-radius: 8px;
  padding: 8px !important;
  gap: 8px !important;
  align-items: flex-start;
}
[id$="_comfy_refvideo_gallery"] > div { gap: 6px !important; }
[id^="txt2img_comfy_refvideo_"][id$="_thumb"],
[id^="img2img_comfy_refvideo_"][id$="_thumb"] {
  padding: 0 !important; margin: 0 !important; background: transparent !important;
  width: 100% !important; min-width: 0 !important;
}
.neo-ref-thumb {
  width: 100%; aspect-ratio: 1 / 1; max-height: 270px;
  box-sizing: border-box; display: flex; align-items: center; justify-content: center;
  background: var(--background-fill-primary, #111827);
  border: 1px solid var(--border-color-primary, #445); border-radius: 8px;
  overflow: hidden; color: var(--body-text-color-subdued, #888);
}
.neo-ref-thumb img { width: 100%; height: 100%; object-fit: contain; display: block; }
[id^="txt2img_comfy_refvideo_"][id$="_file"],
[id^="img2img_comfy_refvideo_"][id$="_file"] {
  min-height: 0 !important; max-height: 76px !important; overflow: hidden;
}
</style>
"""


class Script(scripts.Script):
    sorting_priority = 530  # ImageStitch is 529.
    create_group = True

    def title(self):
        return 'Video Reference'

    def show(self, is_img2img):
        return scripts.AlwaysVisible

    def ui(self, is_img2img):
        name = 'img2img' if is_img2img else 'txt2img'
        with InputAccordion(value=False, label=self.title()) as enable:
            gr.HTML(_COMPACT_CSS)
            controls = []
            with gr.Row(equal_height=False, elem_id=f'{name}_comfy_refvideo_gallery'):
                for slot in range(3):
                    with gr.Column(scale=1, min_width=150, elem_id=f'{name}_comfy_refvideo_{slot}_card'):
                        preview = gr.HTML(value=_EMPTY, elem_id=f'{name}_comfy_refvideo_{slot}_thumb')
                        video = gr.File(
                            label=f'Reference MP4 — Video {slot + 1}',
                            file_types=['.mp4'], file_count='single', type='filepath',
                            height=60, elem_id=f'{name}_comfy_refvideo_{slot}_file',
                        )
                        use_soundtrack = gr.Checkbox(
                            value=False, label='Use reference audio track',
                            elem_id=f'{name}_comfy_refvideo_{slot}_soundtrack',
                        )
                        paste = gr.Button(
                            'Paste from clipboard', size='sm', variant='secondary',
                            elem_id=f'{name}_comfy_refvideo_{slot}_paste',
                        )
                        video.change(fn=thumbnail, inputs=video, outputs=preview,
                                     queue=False, api_name=False)
                        paste.click(fn=paste_video, inputs=None, outputs=[video, preview],
                                    queue=False, api_name=False)
                    controls.extend([video, use_soundtrack])
            gr.HTML(
                '<div style="font-size:11px;line-height:1.35;opacity:.7;margin-top:2px">'
                'Ref2VA · MP4 · 24 FPS · 2–15 s · ≤96 MiB · prompt: '
                '&lt;Video 1&gt;–&lt;Video 3&gt; (top to bottom, empty slots skipped) · Audio is a reference, not copied to output.'
                '</div>'
            )

        video_reference.register(is_img2img, enable, *controls)
        return [enable, *controls]

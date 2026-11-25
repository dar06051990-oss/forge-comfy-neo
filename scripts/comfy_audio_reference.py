"""Standalone Audio Reference script for Forge Comfy Neo."""
import sys
from pathlib import Path
_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
import gradio as gr
from modules import scripts
from modules.ui_components import InputAccordion
from forge_h3 import video_reference
from forge_h3.audio_reference import FORMATS, select_audio
from forge_h3.refvideo_clipboard_native import local_request, clipboard_paths

def paste_audio(request: gr.Request):
    if not local_request(request):
        gr.Warning("Clipboard paste is available only in a local Forge session.")
        return gr.update()
    try:
        return select_audio(clipboard_paths())
    except (OSError, RuntimeError, ValueError) as exc:
        gr.Warning(str(exc))
        return gr.update()


class Script(scripts.Script):
    sorting_priority = 531
    create_group = True

    def title(self):
        return 'Audio Reference'

    def show(self, is_img2img):
        return scripts.AlwaysVisible

    def ui(self, is_img2img):
        name = 'img2img' if is_img2img else 'txt2img'
        with InputAccordion(value=False, label='Audio Reference') as audio_enable:
            audio_controls = []
            with gr.Row(equal_height=False):
                for slot in range(3):
                    with gr.Column(scale=1, min_width=150):
                        audio = gr.File(label=f'Audio {slot + 1}', file_types=list(FORMATS),
                                        file_count='single', type='filepath', height=60,
                                        elem_id=f'{name}_comfy_refaudio_{slot}_file')
                        paste = gr.Button('Paste from clipboard', size='sm', variant='secondary',
                                          elem_id=f'{name}_comfy_refaudio_{slot}_paste')
                        paste.click(fn=paste_audio, inputs=None, outputs=audio,
                                    queue=False, api_name=False)
                        audio_controls.append(audio)
            gr.HTML('<div style="font-size:11px;opacity:.7">Ref2VA · WAV / MP3 / FLAC / OGG / M4A / AAC / OPUS · up to 96 MiB per file · trimmed to output duration (max 15 s) · &lt;Audio 1&gt;–&lt;Audio 3&gt; · empty slots skipped</div>')
        video_reference.register_audio(is_img2img, audio_enable, *audio_controls)
        return [audio_enable, *audio_controls]

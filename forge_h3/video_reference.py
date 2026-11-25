"""Pass native Forge Script controls to the embedded Comfy Neo Generate button.

The reference source stays a real Gradio File component in the Scripts area;
no DOM reparenting, secondary upload state or synthetic UI events are needed.
"""

_CONTROLS = {}
_AUDIO_CONTROLS = {}


def register(is_img2img, enable, *controls):
    _CONTROLS['img2img' if is_img2img else 'txt2img'] = (enable, *controls)


def get_controls(tab):
    return _CONTROLS.get(tab)


def register_audio(is_img2img, enable, *controls):
    _AUDIO_CONTROLS['img2img' if is_img2img else 'txt2img'] = (enable, *controls)

def get_audio_controls(tab):
    return _AUDIO_CONTROLS.get(tab)

def clear():
    _CONTROLS.clear()
    _AUDIO_CONTROLS.clear()

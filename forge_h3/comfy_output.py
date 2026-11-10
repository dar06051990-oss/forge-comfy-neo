"""Shared Comfy Neo output preferences (images and videos).

Never changes the native Forge output settings or model workflows. The first
appearance of the new keys inherits previous ANIMA / MiniMax preferences.
"""
from __future__ import annotations

from typing import Any

IMAGE_FORMATS = ('PNG', 'JPG', 'WEBP')
VIDEO_FORMATS = ('MP4',)  # Only format implemented by embedded MiniMax H3.

LEGACY_DEFAULTS = {
    'comfy_image_format': ('anima_output_format', 'PNG'),
    'comfy_image_auto_save': ('anima_auto_save', False),
    'comfy_video_format': ('comfy_video_format', 'MP4'),
    'comfy_video_auto_save': ('h3_auto_save_video', False),
    'comfy_video_autoplay': ('h3_autoplay_video', False),
    'comfy_video_sound': ('h3_preview_sound', False),
    'comfy_video_volume': ('h3_preview_volume', 25),
    'comfy_video_ffmpeg_path': ('h3_ffmpeg_path', ''),
}


def option(name: str, fallback: Any = None):
    """Read a shared Comfy key, or the legacy key on old installations."""
    from modules import shared
    data = getattr(shared.opts, 'data', {})
    if name in data:
        return data[name]
    if name in LEGACY_DEFAULTS:
        old, default = LEGACY_DEFAULTS[name]
        return data.get(old, default)
    return fallback


def _initial(opts, key):
    if key in opts.data:
        return opts.data[key]
    old, fallback = LEGACY_DEFAULTS[key]
    return opts.data.get(old, fallback)


def register(shared, gr, group='comfy_engine'):
    """Group all output preferences by media type, NOT by model preset."""
    image = ('comfy_image_output', 'Image Output')
    video = ('comfy_video_output', 'Video Output')
    def add(key, label, component, args, section, info):
        kw = {}
        if args is not None:
            kw['component_args'] = args
        entry = shared.OptionInfo(_initial(shared.opts, key), label, component,
                                  section=section, category_id=group, **kw)
        shared.opts.add_option(key, entry.info(info))

    add('comfy_image_format', 'Image format', gr.Dropdown,
        lambda: {'choices': list(IMAGE_FORMATS)}, image,
        'PNG lossless; JPG maximum quality; WEBP lossless. Applies to all image models.')
    add('comfy_image_auto_save', 'Automatically save generated images', gr.Checkbox, None, image,
        'Off: preview and manual save with the Forge diskette. On: Forge images output directory.')
    add('comfy_video_format', 'Video format (currently MP4)', gr.Dropdown,
        lambda: {'choices': list(VIDEO_FORMATS)}, video,
        'Only MP4 is supported by the current H3 workflow; more formats can be added later.')
    add('comfy_video_auto_save', 'Automatically save generated videos (MP4)', gr.Checkbox, None, video,
        'Off: temp preview; manually save with Forge diskette. On: Forge output directory.')
    add('comfy_video_autoplay', 'Autoplay generated video', gr.Checkbox, None, video,
        'Browser policy may require clicking Play when sound is enabled.')
    add('comfy_video_sound', 'Sound in video preview', gr.Checkbox, None, video,
        'Off by default. Does not remove audio from exported MP4.')
    add('comfy_video_volume', 'Video preview volume (%)', gr.Slider,
        {'minimum': 0, 'maximum': 100, 'step': 5}, video,
        'Only controls browser playback, not MP4 sound track.')
    add('comfy_video_ffmpeg_path', 'FFmpeg executable (empty = automatic)', gr.Textbox, None, video,
        'Used by the H3 video exporter. Existing files remain MP4.')

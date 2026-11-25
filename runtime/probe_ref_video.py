"""Metadata-only MP4 validation for Comfy Neo's first Ref2VA video trial.

Executed in Comfy Neo's PRIVATE Python with already-installed PyAV. Does not
read video frames into memory, start a server, or modify the uploaded file.
"""
import json
import sys

import av


def inspect(path):
    with av.open(path, mode="r") as container:
        stream = next((s for s in container.streams if s.type == "video"), None)
        if stream is None:
            raise ValueError("No video stream")
        rate = stream.average_rate or stream.base_rate
        if not rate:
            raise ValueError("Cannot determine frame rate")
        fps = float(rate)
        duration = (float(stream.duration * stream.time_base)
                    if stream.duration is not None and stream.time_base else None)
        if (duration is None or duration <= 0) and container.duration:
            duration = float(container.duration / av.time_base)
        if (duration is None or duration <= 0) and stream.frames:
            duration = float(stream.frames) / fps
        if duration is None or duration <= 0:
            raise ValueError("Cannot determine clip duration")
        has_audio = any(s.type == 'audio' for s in container.streams)
        return {"fps": fps, "duration": duration, "has_audio": has_audio}


if __name__ == "__main__":
    try:
        print(json.dumps(inspect(sys.argv[1])))
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)

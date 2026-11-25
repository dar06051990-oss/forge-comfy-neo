"""Read Explorer's CF_HDROP clipboard format for local Forge sessions."""
import ctypes
from ctypes import wintypes
import ipaddress
import os
from pathlib import Path
from urllib.parse import urlsplit


def _loopback(host):
    if host == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except (ValueError, TypeError):
        return False


def local_request(request):
    if request is None or not getattr(request, 'client', None):
        return False
    # The actual peer must be a loopback address, not a forwarded client.
    if not _loopback(request.client.host):
        return False
    headers = request.headers
    if any(headers.get(k) for k in ('forwarded', 'x-forwarded-for', 'x-forwarded-host', 'x-real-ip')):
        return False
    try:
        host = urlsplit('http://' + headers.get('host', '')).hostname
        if not _loopback(host):
            return False
        for key in ('origin', 'referer'):
            value = headers.get(key)
            if value and not _loopback(urlsplit(value).hostname):
                return False
        return True
    except ValueError:
        return False


def clipboard_paths():
    if os.name != 'nt':
        raise RuntimeError('Windows clipboard is unavailable on this system.')
    user = ctypes.WinDLL('user32', use_last_error=True)
    shell = ctypes.WinDLL('shell32', use_last_error=True)
    user.OpenClipboard.argtypes = [wintypes.HWND]
    user.OpenClipboard.restype = wintypes.BOOL
    user.CloseClipboard.argtypes = []
    user.CloseClipboard.restype = wintypes.BOOL
    user.GetClipboardData.argtypes = [wintypes.UINT]
    user.GetClipboardData.restype = wintypes.HANDLE
    shell.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
    shell.DragQueryFileW.restype = wintypes.UINT
    if not user.OpenClipboard(None):
        raise RuntimeError('Clipboard is busy. Try again.')
    try:
        handle = user.GetClipboardData(15)  # CF_HDROP, Explorer file copy
        if not handle:
            raise RuntimeError('Copy an MP4 file in Windows Explorer first (Ctrl+C).')
        count = shell.DragQueryFileW(handle, 0xFFFFFFFF, None, 0)
        paths = []
        for index in range(count):
            length = shell.DragQueryFileW(handle, index, None, 0)
            if length:
                buf = ctypes.create_unicode_buffer(length + 1)
                if shell.DragQueryFileW(handle, index, buf, length + 1):
                    paths.append(buf.value)
        return paths
    finally:
        user.CloseClipboard()


def select_mp4(paths):
    for raw in paths:
        p = Path(raw)
        if p.suffix.lower() != '.mp4':
            continue
        # No network paths or device paths.
        if str(raw).startswith(('\\\\', '//')) or not p.is_absolute():
            continue
        if p.is_file():
            if p.stat().st_size > 96 * 1024 * 1024:
                raise RuntimeError('MP4 exceeds 96 MiB.')
            return str(p)
    raise RuntimeError('No local MP4 file in clipboard.')

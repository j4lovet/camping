"""Bring the reservation browser forward without a popup or sound."""
import logging
import os


def _raise_window(marker):
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.FlashWindow.argtypes = [wintypes.HWND, wintypes.BOOL]
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    found = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def visit(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            title = ctypes.create_unicode_buffer(user32.GetWindowTextLengthW(hwnd) + 1)
            user32.GetWindowTextW(hwnd, title, len(title))
            if marker in title.value:
                found.append(hwnd)
                return False
        return True

    user32.EnumWindows(visit, 0)
    if found:
        hwnd = found[0]
        user32.ShowWindow(hwnd, 9)  # Restore a minimized browser.
        try:
            user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0003)
            if not user32.SetForegroundWindow(hwnd):
                user32.FlashWindow(hwnd, True)
        finally:
            user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0003)


def focus_browser(page):
    try:
        page.bring_to_front()
        if os.name == 'nt':
            marker = f'[Camping-{id(page):x}]'
            page.evaluate("marker => { document.title = marker + ' ' + document.title; }", marker)
            try:
                _raise_window(marker)
            finally:
                page.evaluate("marker => { if (document.title.startsWith(marker + ' ')) document.title = document.title.slice(marker.length + 1); }", marker)
    except Exception:
        logging.exception('예약 브라우저를 앞으로 띄우지 못했습니다 · 기존 알림을 확인하세요')

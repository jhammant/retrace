"""ctypes bindings for the handful of Win32 calls the Windows backend needs.

Import this module only on Windows. Every prototype sets ``argtypes``/``restype``
explicitly: handles are pointer-sized on 64-bit Windows and ctypes' default
``int`` return type would silently truncate them.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
wtsapi32 = ctypes.WinDLL("wtsapi32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi")
version = ctypes.WinDLL("version")

HWND = wintypes.HWND
DWORD = wintypes.DWORD
BOOL = wintypes.BOOL
HANDLE = wintypes.HANDLE
LPARAM = wintypes.LPARAM


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", DWORD)]


class SYSTEM_POWER_STATUS(ctypes.Structure):
    _fields_ = [
        ("ACLineStatus", ctypes.c_ubyte),
        ("BatteryFlag", ctypes.c_ubyte),
        ("BatteryLifePercent", ctypes.c_ubyte),
        ("SystemStatusFlag", ctypes.c_ubyte),  # 1 = battery saver on
        ("BatteryLifeTime", DWORD),
        ("BatteryFullLifeTime", DWORD),
    ]


WNDENUMPROC = ctypes.WINFUNCTYPE(BOOL, HWND, LPARAM)
WINEVENTPROC = ctypes.WINFUNCTYPE(
    None, HANDLE, DWORD, HWND, ctypes.c_long, ctypes.c_long, DWORD, DWORD
)


def _proto(fn, restype, *argtypes):
    fn.restype = restype
    fn.argtypes = list(argtypes)
    return fn


# --- windows -------------------------------------------------------------------
GetForegroundWindow = _proto(user32.GetForegroundWindow, HWND)
GetWindowTextLengthW = _proto(user32.GetWindowTextLengthW, ctypes.c_int, HWND)
GetWindowTextW = _proto(user32.GetWindowTextW, ctypes.c_int, HWND, wintypes.LPWSTR, ctypes.c_int)
GetClassNameW = _proto(user32.GetClassNameW, ctypes.c_int, HWND, wintypes.LPWSTR, ctypes.c_int)
GetWindowThreadProcessId = _proto(user32.GetWindowThreadProcessId, DWORD, HWND, ctypes.POINTER(DWORD))
IsWindowVisible = _proto(user32.IsWindowVisible, BOOL, HWND)
IsIconic = _proto(user32.IsIconic, BOOL, HWND)
EnumWindows = _proto(user32.EnumWindows, BOOL, WNDENUMPROC, LPARAM)
EnumChildWindows = _proto(user32.EnumChildWindows, BOOL, HWND, WNDENUMPROC, LPARAM)
GetAncestor = _proto(user32.GetAncestor, HWND, HWND, wintypes.UINT)
GA_ROOTOWNER = 3
GetWindowRect = _proto(user32.GetWindowRect, BOOL, HWND, ctypes.POINTER(RECT))
DwmGetWindowAttribute = _proto(dwmapi.DwmGetWindowAttribute, ctypes.c_long,
                               HWND, DWORD, ctypes.c_void_p, DWORD)
DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14

# DPI awareness (so window rectangles are in the same physical pixels as the grab).
try:
    SetThreadDpiAwarenessContext = _proto(
        user32.SetThreadDpiAwarenessContext, ctypes.c_void_p, ctypes.c_void_p
    )
except AttributeError:  # pragma: no cover - Windows < 10 1607
    SetThreadDpiAwarenessContext = None
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE = ctypes.c_void_p(-3)
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)

# --- input / desktop / session ------------------------------------------------
GetLastInputInfo = _proto(user32.GetLastInputInfo, BOOL, ctypes.POINTER(LASTINPUTINFO))
GetTickCount = _proto(kernel32.GetTickCount, DWORD)
OpenInputDesktop = _proto(user32.OpenInputDesktop, HANDLE, DWORD, BOOL, DWORD)
CloseDesktop = _proto(user32.CloseDesktop, BOOL, HANDLE)
GetUserObjectInformationW = _proto(user32.GetUserObjectInformationW, BOOL,
                                   HANDLE, ctypes.c_int, ctypes.c_void_p, DWORD, ctypes.POINTER(DWORD))
UOI_NAME = 2
DESKTOP_READOBJECTS = 0x0001

WTSQuerySessionInformationW = _proto(
    wtsapi32.WTSQuerySessionInformationW, BOOL,
    HANDLE, DWORD, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(DWORD),
)
WTSFreeMemory = _proto(wtsapi32.WTSFreeMemory, None, ctypes.c_void_p)
WTS_CURRENT_SERVER_HANDLE = HANDLE(0)
WTS_CURRENT_SESSION = DWORD(0xFFFFFFFF)
WTSSessionInfoEx = 25
WTS_SESSIONSTATE_LOCK = 0
WTS_SESSIONSTATE_UNLOCK = 1

ProcessIdToSessionId = _proto(kernel32.ProcessIdToSessionId, BOOL, DWORD, ctypes.POINTER(DWORD))
GetCurrentProcessId = _proto(kernel32.GetCurrentProcessId, DWORD)

GetSystemPowerStatus = _proto(kernel32.GetSystemPowerStatus, BOOL, ctypes.POINTER(SYSTEM_POWER_STATUS))

# --- processes / version info ---------------------------------------------------
OpenProcess = _proto(kernel32.OpenProcess, HANDLE, DWORD, BOOL, DWORD)
CloseHandle = _proto(kernel32.CloseHandle, BOOL, HANDLE)
QueryFullProcessImageNameW = _proto(kernel32.QueryFullProcessImageNameW, BOOL,
                                    HANDLE, DWORD, wintypes.LPWSTR, ctypes.POINTER(DWORD))
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

GetFileVersionInfoSizeW = _proto(version.GetFileVersionInfoSizeW, DWORD,
                                 wintypes.LPCWSTR, ctypes.POINTER(DWORD))
GetFileVersionInfoW = _proto(version.GetFileVersionInfoW, BOOL,
                             wintypes.LPCWSTR, DWORD, DWORD, ctypes.c_void_p)
VerQueryValueW = _proto(version.VerQueryValueW, BOOL,
                        ctypes.c_void_p, wintypes.LPCWSTR,
                        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT))

# --- event hook + message loop (retrace.native.win.watch) -----------------------
SetWinEventHook = _proto(user32.SetWinEventHook, HANDLE,
                         DWORD, DWORD, HANDLE, WINEVENTPROC, DWORD, DWORD, DWORD)
UnhookWinEvent = _proto(user32.UnhookWinEvent, BOOL, HANDLE)
GetMessageW = _proto(user32.GetMessageW, BOOL, ctypes.POINTER(wintypes.MSG), HWND, wintypes.UINT, wintypes.UINT)
TranslateMessage = _proto(user32.TranslateMessage, BOOL, ctypes.POINTER(wintypes.MSG))
DispatchMessageW = _proto(user32.DispatchMessageW, LPARAM, ctypes.POINTER(wintypes.MSG))
EVENT_SYSTEM_FOREGROUND = 0x0003
WINEVENT_OUTOFCONTEXT = 0x0000
WINEVENT_SKIPOWNPROCESS = 0x0002

# --- clipboard -----------------------------------------------------------------
OpenClipboard = _proto(user32.OpenClipboard, BOOL, HWND)
CloseClipboard = _proto(user32.CloseClipboard, BOOL)
GetClipboardData = _proto(user32.GetClipboardData, HANDLE, wintypes.UINT)
IsClipboardFormatAvailable = _proto(user32.IsClipboardFormatAvailable, BOOL, wintypes.UINT)
RegisterClipboardFormatW = _proto(user32.RegisterClipboardFormatW, wintypes.UINT, wintypes.LPCWSTR)
GetClipboardSequenceNumber = _proto(user32.GetClipboardSequenceNumber, DWORD)
GlobalLock = _proto(kernel32.GlobalLock, ctypes.c_void_p, HANDLE)
GlobalUnlock = _proto(kernel32.GlobalUnlock, BOOL, HANDLE)
CF_UNICODETEXT = 13


# --- small helpers ---------------------------------------------------------------

def window_text(hwnd) -> str:
    n = GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def class_name(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    GetClassNameW(hwnd, buf, 256)
    return buf.value


def window_pid(hwnd) -> int:
    pid = DWORD(0)
    GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def window_rect(hwnd) -> tuple[int, int, int, int] | None:
    """Visible bounds (left, top, right, bottom), excluding the invisible resize border."""
    r = RECT()
    if DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r), ctypes.sizeof(r)) != 0:
        if not GetWindowRect(hwnd, ctypes.byref(r)):
            return None
    return r.left, r.top, r.right, r.bottom


def is_cloaked(hwnd) -> bool:
    """True for windows DWM hides (suspended UWP apps, other virtual desktops)."""
    cloaked = DWORD(0)
    if DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked)) != 0:
        return False
    return cloaked.value != 0


def process_image_path(pid: int) -> str | None:
    """Full executable path for ``pid`` (works for elevated processes too)."""
    h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        size = DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
        return None
    finally:
        CloseHandle(h)


def file_description(path: str) -> str | None:
    """The executable's FileDescription (e.g. "Google Chrome"), else ProductName."""
    handle = DWORD(0)
    size = GetFileVersionInfoSizeW(path, ctypes.byref(handle))
    if not size:
        return None
    data = ctypes.create_string_buffer(size)
    if not GetFileVersionInfoW(path, 0, size, data):
        return None
    ptr = ctypes.c_void_p()
    length = wintypes.UINT(0)
    if not VerQueryValueW(data, "\\VarFileInfo\\Translation", ctypes.byref(ptr), ctypes.byref(length)):
        return None
    if not length.value:
        return None
    lang, codepage = ctypes.cast(ptr, ctypes.POINTER(wintypes.WORD * 2)).contents
    for key in ("FileDescription", "ProductName"):
        sub = f"\\StringFileInfo\\{lang:04x}{codepage:04x}\\{key}"
        if VerQueryValueW(data, sub, ctypes.byref(ptr), ctypes.byref(length)) and length.value:
            value = ctypes.wstring_at(ptr.value, length.value).rstrip("\x00").strip()
            if value:
                return value
    return None


def enum_windows() -> list:
    """All top-level window handles, in z-order (topmost first)."""
    found: list = []

    @WNDENUMPROC
    def _cb(hwnd, _lparam):
        found.append(hwnd)
        return True

    EnumWindows(_cb, 0)
    return found


def enum_child_windows(parent) -> list:
    found: list = []

    @WNDENUMPROC
    def _cb(hwnd, _lparam):
        found.append(hwnd)
        return True

    EnumChildWindows(parent, _cb, 0)
    return found

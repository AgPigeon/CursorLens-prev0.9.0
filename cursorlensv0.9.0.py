# -*- coding: utf-8 -*-
"""
================================================================================
 光标镜  CursorLens  v1.0.0
--------------------------------------------------------------------------------
 一个跟随鼠标指针的悬浮指示窗：实时显示"当前输入法语言(中文/英文)"与"大小写状态"，
 让用户不用低头看任务栏右下角，从而提升输入效率。

 A cursor-following floating indicator that shows the current input language (IME)
 and the CapsLock case state right beside the mouse pointer, so the user never has
 to look down at the taskbar again.

 运行环境 / Requirements
   - Python 3.8+（仅标准库 / standard library only：tkinter、ctypes、json、
     math、os、sys、time、traceback）
   - Windows（读取输入法状态依赖 imm32.dll / user32.dll；其他平台可导入运行，
     但状态读取返回空值 / Windows only for real state, importable elsewhere）

 设计要点 / Design notes
   ① 触发：轮询 GetAsyncKeyState 做"上升沿"判定，默认 CapsLock 与 Ctrl+Space，
      用户可在设置界面勾选候选组合键（最多 2 个）。
   ② 语言：GetKeyboardLayout(前台线程) 取语言 ID；ImmGetContext +
      ImmGetConversionStatus 取"中文/英文"输入模式；ImmGetDescription /
      ImmGetIMEFileName 识别拼音/五笔等输入法，映射为单字缩写（拼/五/EN…）。
   ③ 大小写：GetKeyState(VK_CAPITAL)。中文状态显示"大/小"，英文状态显示"U/L"。
   ④ 位置：GetCursorPos + 可调偏移（±100 像素），并做虚拟屏幕边界钳制。
   ⑤ 设置：默认三连组合键 Ctrl+Alt+C 打开设置窗口（支持"录制"自定义）。
   ⑥ 外观：overrideredirect + 透明色键 + 鼠标穿透（WS_EX_TRANSPARENT），
      不抢焦点、不挡鼠标点击。

 悬浮窗示意 / Panel layout
   x +50 y +0     ▲     [拼]  [大]
   └偏移标注┘   └指向鼠标┘ └语言┘ └大小写┘
================================================================================
"""

import ctypes
import json
import math
import os
import sys
import time
import traceback
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox, ttk


# ============================================================================
# 0. 常量与默认配置 / Constants & default configuration
# ============================================================================

APP_NAME = '光标镜 CursorLens'
APP_VERSION = 'v1.0.0'
CONFIG_FILE_NAME = 'cursorlens_config.json'

# 透明色键：该颜色的像素会被 Windows 抠成完全透明。
# 注意：不要与任何绘制用到的颜色相同 / must differ from every painted colour.
TRANSPARENT_KEY = '#FF00FE'

COLOR_PANEL_BG = '#12161C'      # 底板
COLOR_PANEL_EDGE = '#3A4553'    # 底板描边
COLOR_LANG_CJK = '#2F6FED'      # 中文输入法徽标底色
COLOR_LANG_EN = '#404B5A'       # 英文输入法徽标底色
COLOR_CASE_ON = '#E2503C'       # 大写(大/U)徽标底色
COLOR_CASE_OFF = '#404B5A'      # 小写(小/L)徽标底色
COLOR_BADGE_TEXT = '#F2F5F8'    # 徽标文字
COLOR_MARKER = '#E8EEF5'        # 指向鼠标的三角标
COLOR_CAPTION = '#8B97A6'       # 偏移标注文字

FONT_UI = 'Microsoft YaHei UI'  # 徽标字体（中文用雅黑最稳）
FONT_MONO = 'Consolas'          # 偏移标注字体

POLL_MS = 25                    # 按键轮询周期（约 40Hz）
STATE_REFRESH_MS = 180          # 常显模式下的状态刷新周期
CAPTURE_TIMEOUT_MS = 20000      # 快捷键录制超时自动取消

# 徽标字体候选（按优先级），保证中文徽标在英文系统上也有字形可用
UI_FONT_CANDIDATES = ('Microsoft YaHei UI', 'Microsoft YaHei', '微软雅黑',
                      'SimHei', 'SimSun', 'Noto Sans CJK SC', 'Segoe UI')
_UI_FONT_CACHE = []


def ui_font_family(widget):
    """
    挑选一个可用的中文字体族 / pick an available CJK-capable UI font.
    结果缓存，避免每帧遍历字体列表。
    """
    if _UI_FONT_CACHE:
        return _UI_FONT_CACHE[0]
    family = FONT_UI
    try:
        available = set(tkfont.families(widget))
        for cand in UI_FONT_CANDIDATES:
            if cand in available:
                family = cand
                break
    except Exception:
        pass
    _UI_FONT_CACHE.append(family)
    return family

# 可供勾选的触发组合键候选（默认勾选前两个）。
# 想扩展触发键，直接往这个列表里加组合即可 / extend here.
TRIGGER_CANDIDATES = [
    'capslock',
    'ctrl+space',
    'ctrl+shift',
    'alt+shift',
    'win+space',
    'shift+space',
    'ctrl+`',
    'ctrl+alt',
]

DEFAULT_CONFIG = {
    'triggers': ['capslock', 'ctrl+space'],   # ② 触发按键（最多 2 个）
    'settings_hotkey': 'ctrl+alt+c',          # ⑤ 打开设置窗口的三连组合键
    'offset_x': 20,                           # ④ 以鼠标为基准的 X 偏移（±100）
    'offset_y': 20,                           # ④ 以鼠标为基准的 Y 偏移（±100）
    'display_mode': 'trigger',                # 显示方式：trigger/sticky/always
    'dwell_ms': 1000,                         # 触发后显示时长（毫秒），默认 1 秒
    'clamp_to_screen': True,                  # 边界钳制，避免跑出屏幕
    'show_caption': False,                    # 显示 "x +n y +n" 偏移标注
    'show_marker': True,                      # 显示指向鼠标的三角标
    'show_debug': False,                      # 显示诊断信息（排查输入法识别用）
    'click_through': True,                    # 鼠标穿透（不遮挡点击）
    'lang_override': 'auto',                  # 语言识别：auto/pin/wubi/zh/en
    'scale': 1.0,                             # 缩放
    'alpha': 0.95,                            # 不透明度
    'ui_language': 'zh',                      # 设置界面语言：zh/en
}

# 显示方式 / display modes
#   trigger：按下触发键后显示 dwell_ms 毫秒，然后自动隐藏（默认）
#   sticky ：按一次显示，再按一次隐藏（保持显示）
#   always ：常显，一直跟随鼠标
DISPLAY_MODES = ('trigger', 'sticky', 'always')

# 触发后显示时长的预设档位（毫秒），默认 1 秒。
# 只允许这几个档位，避免用户滑出一个不上不下的值。
DWELL_CHOICES = (300, 500, 1000, 2000, 4000)


def dwell_text(ms):
    """300 -> '0.3s' / 1000 -> '1s'（给人看的档位文字）。"""
    return '%gs' % (float(ms) / 1000.0)


def now_s():
    """
    统一的单调时钟（单位：秒）。所有计时都走这里，避免单位混用。
    Single monotonic clock in seconds; every timing computation uses it.
    """
    return time.monotonic()


# 数值范围（同时用于设置界面的滑块与配置校验）
CONFIG_LIMITS = {
    'offset_x': (-100, 100),
    'offset_y': (-100, 100),
    'scale': (0.6, 2.0),
    'alpha': (0.30, 1.0),
}


# ============================================================================
# 1. 按键映射与组合键工具 / Key maps & combo helpers
# ============================================================================

VK = {
    'backspace': 0x08, 'tab': 0x09, 'enter': 0x0D, 'shift': 0x10, 'ctrl': 0x11,
    'alt': 0x12, 'pause': 0x13, 'capslock': 0x14, 'esc': 0x1B, 'space': 0x20,
    'pageup': 0x21, 'pagedown': 0x22, 'end': 0x23, 'home': 0x24,
    'left': 0x25, 'up': 0x26, 'right': 0x27, 'down': 0x28,
    'insert': 0x2D, 'delete': 0x2E, 'win': 0x5B, '`': 0xC0, '-': 0xBD,
    '=': 0xBB, '[': 0xDB, ']': 0xDD, '\\': 0xDC, ';': 0xBA, "'": 0xDE,
    ',': 0xBC, '.': 0xBE, '/': 0xBF,
}
for _i in range(10):
    VK['num%d' % _i] = 0x30 + _i
for _i in range(26):
    VK[chr(ord('a') + _i)] = 0x41 + _i
for _i in range(1, 25):
    VK['f%d' % _i] = 0x6F + _i

KEY_LABEL = {
    'ctrl': 'Ctrl', 'shift': 'Shift', 'alt': 'Alt', 'win': 'Win',
    'space': 'Space', 'capslock': 'CapsLock', 'esc': 'Esc', 'tab': 'Tab',
    'backspace': 'BackSpace', 'enter': 'Enter', 'delete': 'Del',
    'insert': 'Ins', 'home': 'Home', 'end': 'End', 'pageup': 'PgUp',
    'pagedown': 'PgDn', 'left': '←', 'right': '→', 'up': '↑', 'down': '↓',
    'pause': 'Pause',
}

# 排序权重：修饰键在前，主键在后 / modifiers first, main key last
_COMBO_ORDER = ('ctrl', 'shift', 'alt', 'win')


def key_label(key):
    """按键的显示名 / humanity readable key name."""
    if key in KEY_LABEL:
        return KEY_LABEL[key]
    return key.upper()


def _combo_sort_key(key):
    return (_COMBO_ORDER.index(key) if key in _COMBO_ORDER else 99, key)


def combo_parse(text):
    """'Ctrl+Space' -> frozenset({'ctrl','space'})，非法片段自动丢弃。"""
    parts = [p.strip().lower() for p in str(text).replace(' ', '').split('+')]
    return frozenset(p for p in parts if p in VK)


def combo_to_text(keys):
    """frozenset({'ctrl','space'}) -> 'Ctrl + Space'。"""
    return ' + '.join(key_label(k) for k in sorted(keys, key=_combo_sort_key))


def combo_to_spec(keys):
    """frozenset -> 配置里保存的规范字符串 'ctrl+space'。"""
    return '+'.join(sorted(keys, key=_combo_sort_key))


# ============================================================================
# 2. Win32 接口封装 / thin Win32 wrappers
# ============================================================================

WINDOWS = (os.name == 'nt')


def enable_dpi_awareness():
    """声明进程 DPI 感知，保证坐标与字号在缩放屏上不糊、不偏。"""
    if not WINDOWS:
        return
    try:  # Windows 10 1703+：Per-Monitor-V2
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass
    try:  # Windows 8.1+：Per-Monitor
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


class GUITHREADINFO(ctypes.Structure):
    """GetGUIThreadInfo 结构体：取前台线程的活动/焦点窗口句柄。"""
    _fields_ = [('cbSize', ctypes.c_uint32), ('flags', ctypes.c_uint32),
                ('hwndActive', ctypes.c_void_p), ('hwndFocus', ctypes.c_void_p),
                ('hwndCapture', ctypes.c_void_p),
                ('hwndMenuOwner', ctypes.c_void_p),
                ('hwndMoveSize', ctypes.c_void_p),
                ('hwndCaret', ctypes.c_void_p),
                ('rcCaret', ctypes.c_long * 4)]


class WinAPI(object):
    """
    user32 / imm32 的最小封装。所有方法都"尽力而为"，任何异常都退化为默认值，
    以保证轮询循环不会因为某个 API 失败而中断。

    Minimal, never-raising wrappers around user32 / imm32.
    """

    SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
    SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
    VK_CAPITAL = 0x14
    IME_CMODE_NATIVE = 0x0001
    WM_IME_CONTROL = 0x0283
    IMC_GETCONVERSIONMODE = 0x0001
    IMC_GETOPENSTATUS = 0x0005
    SMTO_ABORTIFHUNG = 0x0002
    GWL_EXSTYLE = -20
    WS_EX_LAYERED = 0x00080000
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_NOACTIVATE = 0x08000000
    WS_EX_TOOLWINDOW = 0x00000080

    def __init__(self):
        self.ok = WINDOWS
        self.own_hwnd = 0        # 本进程悬浮窗句柄（最后兜底用）
        self.own_pid = os.getpid() if WINDOWS else 0
        self._u = None
        self._i = None
        if self.ok:
            try:
                self._u = ctypes.WinDLL('user32', use_last_error=True)
                self._i = ctypes.WinDLL('imm32', use_last_error=True)
                self._bind()
            except Exception:
                self.ok = False
                self._u = self._i = None

    def _bind(self):
        """绑定函数签名（64 位下 HANDLE/LONG_PTR 必须显式声明）。"""
        from ctypes import wintypes
        u, i = self._u, self._i

        u.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        u.GetCursorPos.restype = wintypes.BOOL

        u.GetAsyncKeyState.argtypes = [ctypes.c_int]
        u.GetAsyncKeyState.restype = ctypes.c_short

        u.GetKeyState.argtypes = [ctypes.c_int]
        u.GetKeyState.restype = ctypes.c_short

        u.GetForegroundWindow.restype = wintypes.HWND

        u.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                               ctypes.POINTER(wintypes.DWORD)]
        u.GetWindowThreadProcessId.restype = wintypes.DWORD

        u.GetKeyboardLayout.argtypes = [wintypes.DWORD]
        u.GetKeyboardLayout.restype = ctypes.c_void_p

        u.GetSystemMetrics.argtypes = [ctypes.c_int]
        u.GetSystemMetrics.restype = ctypes.c_int

        u.GetParent.argtypes = [wintypes.HWND]
        u.GetParent.restype = wintypes.HWND

        u.GetClassNameW.argtypes = [wintypes.HWND, ctypes.c_wchar_p,
                                    ctypes.c_int]
        u.GetClassNameW.restype = ctypes.c_int

        u.IsWindow.argtypes = [wintypes.HWND]
        u.IsWindow.restype = wintypes.BOOL

        get_long = getattr(u, 'GetWindowLongPtrW', u.GetWindowLongW)
        set_long = getattr(u, 'SetWindowLongPtrW', u.SetWindowLongW)
        get_long.argtypes = [wintypes.HWND, ctypes.c_int]
        get_long.restype = ctypes.c_ssize_t
        set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        set_long.restype = ctypes.c_ssize_t
        self._get_long, self._set_long = get_long, set_long

        i.ImmGetContext.argtypes = [wintypes.HWND]
        i.ImmGetContext.restype = ctypes.c_void_p
        i.ImmReleaseContext.argtypes = [wintypes.HWND, ctypes.c_void_p]
        i.ImmReleaseContext.restype = wintypes.BOOL
        i.ImmGetConversionStatus.argtypes = [ctypes.c_void_p,
                                             ctypes.POINTER(wintypes.DWORD),
                                             ctypes.POINTER(wintypes.DWORD)]
        i.ImmGetConversionStatus.restype = wintypes.BOOL
        i.ImmGetDescriptionW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p,
                                         wintypes.UINT]
        i.ImmGetDescriptionW.restype = wintypes.UINT
        i.ImmGetIMEFileNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p,
                                         wintypes.UINT]
        i.ImmGetIMEFileNameW.restype = wintypes.UINT
        i.ImmGetDefaultIMEWnd.argtypes = [wintypes.HWND]
        i.ImmGetDefaultIMEWnd.restype = wintypes.HWND

        u.GetGUIThreadInfo.argtypes = [wintypes.DWORD,
                                       ctypes.POINTER(GUITHREADINFO)]
        u.GetGUIThreadInfo.restype = wintypes.BOOL

        u.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT,
                                          ctypes.c_size_t, ctypes.c_ssize_t,
                                          wintypes.UINT, wintypes.UINT,
                                          ctypes.POINTER(ctypes.c_size_t)]
        u.SendMessageTimeoutW.restype = ctypes.c_ssize_t

    def cursor_pos(self):
        """当前鼠标坐标 / current cursor position."""
        if not self.ok:
            return (0, 0)
        from ctypes import wintypes
        pt = wintypes.POINT()
        if not self._u.GetCursorPos(ctypes.byref(pt)):
            return (0, 0)
        return (pt.x, pt.y)

    def key_down(self, vk):
        """按键当前是否按下 / is the key currently down."""
        if not self.ok:
            return False
        return bool(self._u.GetAsyncKeyState(int(vk)) & 0x8000)

    def caps_lock_on(self):
        """CapsLock 是否处于"开"状态 / CapsLock toggle state."""
        if not self.ok:
            return False
        return bool(self._u.GetKeyState(self.VK_CAPITAL) & 0x0001)

    def virtual_screen(self):
        """虚拟屏幕矩形（多屏合并）/ virtual desktop rect (x, y, w, h)."""
        if not self.ok:
            return (0, 0, 1920, 1080)
        m = self._u.GetSystemMetrics
        return (m(self.SM_XVIRTUALSCREEN), m(self.SM_YVIRTUALSCREEN),
                m(self.SM_CXVIRTUALSCREEN), m(self.SM_CYVIRTUALSCREEN))

    def foreground_layout(self):
        """兼容旧调用：当前前台窗口线程的键盘布局 / legacy wrapper."""
        return self.layout_of_window(None)

    def layout_of_window(self, hwnd=None):
        """
        指定窗口（默认前台窗口）线程的键盘布局句柄 HKL。
        Keyboard layout (HKL) of the thread owning hwnd (foreground by default).
        """
        if not self.ok:
            return None
        if not hwnd:
            hwnd = self.foreground_window()
        tid = 0
        if hwnd:
            tid = self.foreground_thread_id(hwnd)
        return self._u.GetKeyboardLayout(tid or 0)

    def window_pid(self, hwnd):
        """窗口所属进程 ID / process id owning a window."""
        if not self.ok or not hwnd:
            return 0
        from ctypes import wintypes
        pid = wintypes.DWORD(0)
        try:
            self._u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        except Exception:
            return 0
        return int(pid.value)

    def is_own_window(self, hwnd):
        """该窗口是否属于本程序（避免读到自己的输入法上下文）。"""
        return bool(hwnd) and self.window_pid(hwnd) == self.own_pid

    def is_window(self, hwnd):
        """句柄是否仍是有效窗口 / whether the handle is still a window."""
        if not self.ok or not hwnd:
            return False
        try:
            return bool(self._u.IsWindow(hwnd))
        except Exception:
            return False

    def ime_description(self, hkl):
        """输入法描述（如"微软拼音"）/ IME description text."""
        if not self.ok or not hkl:
            return ''
        buf = ctypes.create_unicode_buffer(256)
        n = self._i.ImmGetDescriptionW(hkl, buf, 256)
        return (buf.value[:n] if n else '').strip()

    def ime_file_name(self, hkl):
        """输入法文件（如 MSIMEP.IME）/ IME file name (secondary hint)."""
        if not self.ok or not hkl:
            return ''
        buf = ctypes.create_unicode_buffer(256)
        n = self._i.ImmGetIMEFileNameW(hkl, buf, 256)
        return (buf.value[:n] if n else '').strip()

    def ime_native_mode(self):
        """
        前台窗口的输入法是否处于"本机(中文)模式"。
        True=中文模式，False=英文(字母)模式，None=读不到。

        True = native (e.g. Chinese) mode, False = alphanumeric mode, None = unknown.
        """
        if not self.ok:
            return None
        hwnd = self._u.GetForegroundWindow()
        if not hwnd:
            return None
        himc = self._i.ImmGetContext(hwnd)
        if not himc:
            return None
        try:
            conv = ctypes.c_uint32(0)
            sent = ctypes.c_uint32(0)
            if not self._i.ImmGetConversionStatus(himc, ctypes.byref(conv),
                                                  ctypes.byref(sent)):
                return None
            return bool(conv.value & self.IME_CMODE_NATIVE)
        finally:
            self._i.ImmReleaseContext(hwnd, himc)

    def set_click_through(self, hwnd):
        """给窗口加上"鼠标穿透 + 不激活 + 工具窗"扩展样式。"""
        if not self.ok or not hwnd:
            return
        style = self._get_long(hwnd, self.GWL_EXSTYLE)
        self._set_long(hwnd, self.GWL_EXSTYLE,
                       style | self.WS_EX_LAYERED | self.WS_EX_TRANSPARENT |
                       self.WS_EX_NOACTIVATE | self.WS_EX_TOOLWINDOW)

    # -- 输入法状态（跨进程）/ IME state across processes --------------------
    def foreground_thread_id(self, hwnd=None):
        """前台窗口所属线程 / thread that owns the foreground window."""
        if not self.ok:
            return 0
        if not hwnd:
            hwnd = self.foreground_window()
        if not hwnd:
            return 0
        from ctypes import wintypes
        pid = wintypes.DWORD(0)
        return self._u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)) or 0

    def foreground_window(self):
        """当前前台窗口句柄 / current foreground window handle."""
        if not self.ok:
            return 0
        try:
            return int(self._u.GetForegroundWindow() or 0)
        except Exception:
            return 0

    def gui_thread_info(self, tid):
        """前台线程的焦点/活动窗口 / focus & active windows of a thread."""
        info = GUITHREADINFO()
        info.cbSize = ctypes.sizeof(GUITHREADINFO)
        if not self.ok or not tid:
            return 0, 0
        if not self._u.GetGUIThreadInfo(int(tid), ctypes.byref(info)):
            return 0, 0
        return int(info.hwndFocus or 0), int(info.hwndActive or 0)

    def default_ime_wnd(self, hwnd):
        """
        该窗口线程的默认输入法窗口 / default IME window of a window's thread.
        这个窗口通常属于 CTF，可跨进程发 WM_IME_CONTROL 消息。
        """
        if not self.ok or not hwnd:
            return 0
        try:
            return int(self._i.ImmGetDefaultIMEWnd(hwnd) or 0)
        except Exception:
            return 0

    def send_ime_control(self, ime_wnd, imc):
        """
        向输入法窗口发 WM_IME_CONTROL（带超时，避免卡住前台程序）。
        返回结果整数，失败返回 None。
        """
        if not self.ok or not ime_wnd:
            return None
        try:
            out = ctypes.c_size_t(0)
            ok = self._u.SendMessageTimeoutW(
                ime_wnd, self.WM_IME_CONTROL, imc, 0, self.SMTO_ABORTIFHUNG,
                200, ctypes.byref(out))
            return int(out.value) if ok else None
        except Exception:
            return None

    def conversion_status(self, hwnd):
        """
        用 ImmGetContext + ImmGetConversionStatus 读一次（同进程窗口最准）。
        返回 (native, is_open) 或 (None, None)。
        """
        if not self.ok or not hwnd:
            return None, None
        himc = self._i.ImmGetContext(hwnd)
        if not himc:
            return None, None
        try:
            conv = ctypes.c_uint32(0)
            sent = ctypes.c_uint32(0)
            if not self._i.ImmGetConversionStatus(himc, ctypes.byref(conv),
                                                  ctypes.byref(sent)):
                return None, None
            opened = self._i.ImmGetOpenStatus(himc)
            return bool(conv.value & self.IME_CMODE_NATIVE), bool(opened)
        finally:
            self._i.ImmReleaseContext(hwnd, himc)

    def ime_state(self, hwnd=None):
        """
        读取前台输入法的"本机模式/打开状态"，返回 (native, is_open, source)。

        关键点：现代程序（浏览器、Electron、UWP 等）走 TSF，
        ImmGetContext 对它们的窗口返回 NULL，所以必须再加一条兜底路径：
        ImmGetDefaultIMEWnd + WM_IME_CONTROL/IMC_GETCONVERSIONMODE，
        这条路径是跨进程可用的（已实测 Chromium/Electron 窗口可用）。

        native/is_open 为 None 表示读不到。
        """
        if not self.ok:
            return None, None, 'no-winapi'
        if not hwnd:
            hwnd = int(self._u.GetForegroundWindow() or 0)
        tid = self.foreground_thread_id(hwnd)
        focus, active = self.gui_thread_info(tid)
        targets = [('focus', focus), ('fg', hwnd), ('active', active)]
        # ① 优先用 HIMC（同进程或支持 IMM32 的程序最准）
        for tag, h in targets:
            native, opened = self.conversion_status(h)
            if native is not None:
                return native, opened, 'himc:' + tag
        # ② 退化为给该线程的默认输入法窗口发 WM_IME_CONTROL（跨进程可用）
        for tag, h in targets:
            ime = self.default_ime_wnd(h)
            if not ime:
                continue
            conv = self.send_ime_control(ime, self.IMC_GETCONVERSIONMODE)
            if conv is None:
                continue
            opened = self.send_ime_control(ime, self.IMC_GETOPENSTATUS)
            return (bool(conv & self.IME_CMODE_NATIVE),
                    None if opened is None else bool(opened),
                    'imewnd:' + tag)
        # ③ 最后退到本进程自己的输入法窗口（同进程一定可用）
        ime = self.default_ime_wnd(self.own_hwnd)
        if ime:
            conv = self.send_ime_control(ime, self.IMC_GETCONVERSIONMODE)
            if conv is not None:
                opened = self.send_ime_control(ime, self.IMC_GETOPENSTATUS)
                return (bool(conv & self.IME_CMODE_NATIVE),
                        None if opened is None else bool(opened),
                        'imewnd:self')
        return None, None, 'none'

    def parent_of(self, hwnd):
        """取窗口的父窗口句柄 / parent HWND (0 when unavailable)."""
        if not self.ok or not hwnd:
            return 0
        try:
            return int(self._u.GetParent(hwnd) or 0)
        except Exception:
            return 0

    def class_name(self, hwnd):
        """窗口类名（诊断用）/ window class name for diagnostics."""
        if not self.ok or not hwnd:
            return ''
        try:
            buf = ctypes.create_unicode_buffer(256)
            self._u.GetClassNameW(hwnd, buf, 256)
            return buf.value
        except Exception:
            return ''

    def window_ex_style(self, hwnd):
        """读取扩展样式，便于自检 / read extended window style."""
        if not self.ok or not hwnd:
            return 0
        try:
            return int(self._get_long(hwnd, self.GWL_EXSTYLE))
        except Exception:
            return 0


# ============================================================================
# 3. 输入法状态解析 / input-language state resolution
# ============================================================================

# 中文系语言 ID / Chinese-family language ids
CJK_LANG_IDS = {0x0804, 0x0404, 0x0C04, 0x1004, 0x1404}

# 其他语言徽标（可自行扩展）/ badges for other languages
OTHER_LANG_BADGE = {
    0x0411: '日',   # ja-JP
    0x0412: '한',   # ko-KR
}

# 输入法描述关键字 -> 单字缩写
# 顺序敏感：先匹配"五笔/注音/仓颉"，最后匹配"拼音"。
SUBTYPE_RULES = (
    (('五笔', 'wubi', '笔画'), '五'),
    (('注音', 'bopomofo', 'zhuyin'), '注'),
    (('仓颉', '倉頡', 'cangjie'), '仓'),
    (('郑码', 'zhengma'), '郑'),
    (('双拼', 'shuangpin'), '拼'),
    (('拼音', 'pinyin', 'piny'), '拼'),
)


def subtype_badge(text, lang_id=0, default='中'):
    """
    把输入法描述/文件名映射成单字缩写 / description -> single-char badge.
    default=None 时不猜默认值，交给调用方判断（用于"是否识别成功"）。
    """
    low = (text or '').lower()
    for keys, badge in SUBTYPE_RULES:
        for k in keys:
            if k in low:
                return badge
    if default is not None:
        # 繁体中文默认多为注音/仓颉，简体默认多为拼音
        if lang_id in (0x0404, 0x0C04, 0x1404):
            return '注'
    return default


# TSF 输入法（TIP）注册位置 / where text services (TSF IMEs) are registered
CTF_TIP_KEY = r'SOFTWARE\Microsoft\CTF\TIP'
_IME_NAME_CACHE = {'time': -1e9, 'data': {}}


def indirect_string(text):
    """
    解析 "@%SystemRoot%\\system32\\input.dll,-5300" 这种间接字符串。
    输入法名称常常以这种形式存储，必须借 SHLoadIndirectString 取回可读名称。
    解析失败时原样返回。
    """
    if not isinstance(text, str) or not text.strip().startswith('@'):
        return text
    try:
        shlwapi = ctypes.WinDLL('shlwapi', use_last_error=True)
        shlwapi.SHLoadIndirectString.argtypes = [ctypes.c_wchar_p,
                                                 ctypes.c_wchar_p,
                                                 ctypes.c_uint, ctypes.c_void_p]
        shlwapi.SHLoadIndirectString.restype = ctypes.c_long
        buf = ctypes.create_unicode_buffer(512)
        if shlwapi.SHLoadIndirectString(text.strip(), buf, 512, None) == 0:
            return buf.value.strip() or text
    except Exception:
        pass
    return text


def enabled_ime_names(lang_id, ttl=10.0):
    """
    该语言下"用户已启用"的 TSF 输入法名称列表。

    为什么需要它：Windows 11 自带的微软拼音/微软五笔是 TSF 输入法，
    它们**不注册键盘布局(KLID)**，因此 GetKeyboardLayout + 注册表 Keyboard Layouts
    查不到名字；但它们会注册在 CTF\\TIP 下，并且带 "Display Description"（如"微软拼音"）。
    当该语言只有一个启用的输入法时，就能唯一确定当前输入法是拼音还是五笔。

    Names of the user-enabled TSF IMEs for a language (registry, cached).
    """
    if not WINDOWS or not lang_id:
        return []
    now = time.monotonic()
    if (now - _IME_NAME_CACHE['time'] < ttl
            and lang_id in _IME_NAME_CACHE['data']):
        return _IME_NAME_CACHE['data'][lang_id]
    names = []
    try:
        import winreg
        profile = '0x%08X' % lang_id
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, CTF_TIP_KEY) as root:
            clsids = [winreg.EnumKey(root, i)
                      for i in range(winreg.QueryInfoKey(root)[0])]
        for clsid in clsids:
            base = '%s\\%s\\LanguageProfile\\%s' % (CTF_TIP_KEY, clsid, profile)
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as key:
                    guids = [winreg.EnumKey(key, i)
                             for i in range(winreg.QueryInfoKey(key)[0])]
            except OSError:
                continue
            for guid in guids:
                entry = base + '\\' + guid
                name = ''
                enabled = False
                try:
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, entry) as key:
                        candidates = []
                        for value_name in ('Display Description', 'Description'):
                            try:
                                val = winreg.QueryValueEx(key, value_name)[0]
                            except OSError:
                                continue
                            if isinstance(val, str) and val.strip():
                                resolved = indirect_string(val.strip())
                                if resolved and not resolved.startswith('@'):
                                    candidates.append(resolved)
                        # 优先选能识别出拼音/五笔等关键字的那个名字
                        for cand in candidates:
                            if subtype_badge(cand, lang_id, default=None):
                                name = cand
                                break
                        if not name and candidates:
                            name = candidates[0]
                        try:
                            enabled = bool(int(winreg.QueryValueEx(key, 'Enable')[0]))
                        except OSError:
                            enabled = False
                except OSError:
                    continue
                try:  # 每个用户的启用状态优先
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, entry) as key:
                        enabled = bool(int(winreg.QueryValueEx(key, 'Enable')[0]))
                except OSError:
                    pass
                if enabled and name:
                    names.append(name)
    except Exception:
        names = []
    _IME_NAME_CACHE['time'] = now
    _IME_NAME_CACHE['data'][lang_id] = names
    return names


def resolve_subtype(hint_text, lang_id=0):
    """
    解析中文输入法的单字缩写，返回 (徽标, 来源说明)。
    顺序：① 描述/文件名/键盘布局名关键字  ② 该语言唯一启用的 TSF 输入法
          ③ 无法确定时退化为 中 / 注。
    """
    badge = subtype_badge(hint_text, lang_id, default=None)
    if badge:
        return badge, 'hint'
    names = enabled_ime_names(lang_id)
    if len(names) == 1:
        badge = subtype_badge(names[0], lang_id, default=None)
        if badge:
            return badge, 'ime:' + names[0]
    if not names:
        return subtype_badge('', lang_id), 'no-ime'
    return subtype_badge('', lang_id), 'ime-ambiguous:' + '/'.join(names)


def registry_layout_hints(hkl):
    """
    注册表兜底：TSF 版输入法（微软拼音、微软五笔等）常常让
    ImmGetDescription 返回空串，此时从
    HKLM\\SYSTEM\\CurrentControlSet\\Control\\Keyboard Layouts\\<KLID>
    读取 "Layout Text" / "Layout File" / "IME File"，照样能识别拼音/五笔。

    Registry fallback for TSF-based IMEs whose ImmGetDescription is empty.
    返回字符串列表，Windows 之外或读取失败时返回空列表。
    """
    if not WINDOWS or not hkl:
        return []
    try:
        import winreg
    except Exception:
        return []
    try:
        klid = '%08X' % (int(hkl) & 0xFFFFFFFF)
    except (TypeError, ValueError):
        return []
    # 依次尝试：完整 KLID、去掉高位设备段的低 16 位写法
    candidates = [klid]
    low = klid[-4:]
    if not klid.startswith('0000'):
        candidates.append('0000' + low)
    hints = []
    for cand in candidates:
        try:
            path = ('SYSTEM\\CurrentControlSet\\Control\\Keyboard Layouts\\'
                    + cand)
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
                for value_name in ('Layout Text', 'Layout File', 'IME File'):
                    try:
                        data, _type = winreg.QueryValueEx(key, value_name)
                    except OSError:
                        continue
                    if isinstance(data, str) and data.strip():
                        hints.append(data.strip())
        except OSError:
            continue
        if hints:                       # 命中就不再试下一个候选
            break
    return hints


class InputState(object):
    """一次采样得到的显示状态 / one sampled display state."""

    __slots__ = ('lang_badge', 'case_badge', 'lang_color', 'case_color',
                 'is_english', 'lang_id', 'desc', 'native', 'is_open',
                 'source', 'hkl', 'subtype_source')

    def __init__(self, lang_badge, case_badge, lang_color, case_color,
                 is_english, lang_id=0, desc='', native=None, is_open=None,
                 source='', hkl=0, subtype_source=''):
        self.lang_badge = lang_badge
        self.case_badge = case_badge
        self.lang_color = lang_color
        self.case_color = case_color
        self.is_english = is_english
        self.lang_id = lang_id
        self.desc = desc
        self.native = native
        self.is_open = is_open
        self.source = source
        self.hkl = hkl
        self.subtype_source = subtype_source

    def key(self):
        """用于判断"内容是否变化"的轻量键 / change-detection key."""
        return (self.lang_badge, self.case_badge)


class InputStateReader(object):
    """
    读取并翻译当前输入法状态。
    流程：
      ① HKL（前台线程）-> 语言 ID；
      ② 描述/IME 文件名/键盘布局注册表/TIP 注册表 -> 输入法名（拼音/五笔…）；
      ③ HIMC 或 默认输入法窗口(WM_IME_CONTROL) -> 中文/英文输入模式（跨进程）；
      ④ GetKeyState(VK_CAPITAL) -> 大小写。
    """

    def __init__(self, api):
        self.api = api

    def read(self, override='auto', hwnd=None):
        hkl = self.api.layout_of_window(hwnd)
        try:
            lang_id = (int(hkl) & 0xFFFF) if hkl else 0
        except (TypeError, ValueError):
            lang_id = 0
        desc = self.api.ime_description(hkl) if hkl else ''
        fname = self.api.ime_file_name(hkl) if hkl else ''
        reg = registry_layout_hints(hkl) if hkl else []
        hint = ' '.join([desc, fname] + reg).strip()
        # 跨进程读"本机模式/打开状态"：HIMC 优先，失败则发 WM_IME_CONTROL
        native, is_open, source = self.api.ime_state(hwnd)
        caps = self.api.caps_lock_on()

        # 是否处于英文输入：优先看"本机模式"标志位（最可靠）；
        # 读不到时再看 IME 的打开状态（老式输入法：关闭即英文）。
        english = None
        if native is not None:
            english = (not native)
        elif is_open is not None:
            english = (not is_open)

        subtype_source = ''
        if lang_id in CJK_LANG_IDS:
            if english is True:
                lang_badge, is_english = 'EN', True
            else:
                # 读不到状态时按中文处理（中文布局下这是更常见的状态）
                lang_badge, subtype_source = resolve_subtype(hint, lang_id)
                is_english = False
        else:
            lang_badge = OTHER_LANG_BADGE.get(lang_id, 'EN')
            is_english = True

        # 手动兜底：识别不出来时用户可以固定语言 / manual fallback
        if override == 'pin':
            lang_badge, is_english = '拼', False
        elif override == 'wubi':
            lang_badge, is_english = '五', False
        elif override == 'zh':
            lang_badge, is_english = '中', False
        elif override == 'en':
            lang_badge, is_english = 'EN', True
        if override != 'auto':
            subtype_source = 'override:' + override

        if is_english:
            case_badge = 'U' if caps else 'L'        # 英文：U / L
        else:
            case_badge = '大' if caps else '小'      # 中文：大 / 小

        return InputState(
            lang_badge=lang_badge,
            case_badge=case_badge,
            lang_color=COLOR_LANG_CJK if not is_english else COLOR_LANG_EN,
            case_color=COLOR_CASE_ON if caps else COLOR_CASE_OFF,
            is_english=is_english,
            lang_id=lang_id,
            desc=hint,
            native=native,
            is_open=is_open,
            source=source,
            hkl=int(hkl) & 0xFFFFFFFF if hkl else 0,
            subtype_source=subtype_source,
        )


# ============================================================================
# 4. 配置持久化 / configuration persistence
# ============================================================================

def default_config_path():
    """配置文件与脚本同目录 / config file lives next to the script."""
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, CONFIG_FILE_NAME)


class Config(object):
    """极简配置对象：dict + JSON 读写 + 范围校验。"""

    def __init__(self, path=None):
        self.path = path or default_config_path()
        self.data = dict(DEFAULT_CONFIG)
        self.load()

    def load(self):
        try:
            with open(self.path, 'r', encoding='utf-8') as fp:
                raw = json.load(fp)
            if isinstance(raw, dict):
                for k, v in raw.items():
                    if k in DEFAULT_CONFIG:
                        self.data[k] = v
                # 旧版兼容：曾经用 always_show 表示"常显"
                if 'display_mode' not in raw and raw.get('always_show'):
                    self.data['display_mode'] = 'always'
        except Exception:
            pass  # 读取失败即使用默认值 / fall back to defaults silently
        self._sanitize()

    def save(self):
        self._sanitize()
        try:
            with open(self.path, 'w', encoding='utf-8') as fp:
                json.dump(self.data, fp, ensure_ascii=False, indent=2)
        except Exception:
            traceback.print_exc()

    def _sanitize(self):
        """范围/类型校验，保证任何来源的数据都不会把界面搞崩。"""
        for key, (lo, hi) in CONFIG_LIMITS.items():
            try:
                val = float(self.data.get(key, DEFAULT_CONFIG[key]))
            except (TypeError, ValueError):
                val = float(DEFAULT_CONFIG[key])
            if key in ('offset_x', 'offset_y'):
                val = int(round(val))
            else:
                val = round(val, 2)
            self.data[key] = max(lo, min(hi, val))
        # 显示时长：只能落在预设档位（0.3/0.5/1/2/4 秒），就近吸附
        try:
            dwell = float(self.data.get('dwell_ms', DEFAULT_CONFIG['dwell_ms']))
        except (TypeError, ValueError):
            dwell = float(DEFAULT_CONFIG['dwell_ms'])
        # 就近吸附；正好居中时向上取档（1500 -> 2000）
        self.data['dwell_ms'] = int(min(DWELL_CHOICES,
                                        key=lambda c: (abs(c - dwell), -c)))
        for key in ('clamp_to_screen', 'show_caption',
                    'show_marker', 'show_debug', 'click_through'):
            self.data[key] = bool(self.data.get(key, DEFAULT_CONFIG[key]))
        # 显示方式
        if self.data.get('display_mode') not in DISPLAY_MODES:
            self.data['display_mode'] = DEFAULT_CONFIG['display_mode']
        self.data.pop('always_show', None)      # 旧字段不再保留
        # 触发键：只保留合法组合，最多 2 个
        trig = self.data.get('triggers')
        if isinstance(trig, str):
            trig = [trig]
        picked = []
        if isinstance(trig, (list, tuple)):
            for spec in trig:
                keys = combo_parse(spec)
                if keys and len(picked) < 2:
                    picked.append(combo_to_spec(keys))
        if not picked:
            picked = list(DEFAULT_CONFIG['triggers'])
        self.data['triggers'] = picked
        # 设置快捷键：至少 2 键，默认是三连组合键
        hk = combo_parse(self.data.get('settings_hotkey', ''))
        self.data['settings_hotkey'] = (combo_to_spec(hk) if len(hk) >= 2
                                        else DEFAULT_CONFIG['settings_hotkey'])
        # 枚举
        if self.data.get('lang_override') not in ('auto', 'pin', 'wubi', 'zh',
                                                  'en'):
            self.data['lang_override'] = 'auto'
        if self.data.get('ui_language') not in ('zh', 'en'):
            self.data['ui_language'] = 'zh'

    def get(self, key):
        return self.data.get(key, DEFAULT_CONFIG.get(key))

    def set(self, key, value):
        self.data[key] = value

    def triggers(self):
        """触发组合键 -> [frozenset, ...]"""
        return [combo_parse(s) for s in self.data.get('triggers', [])]

    def settings_hotkey(self):
        return combo_parse(self.data.get('settings_hotkey', ''))


# ============================================================================
# 5. 悬浮指示窗 / the floating indicator window
# ============================================================================

class IndicatorWindow(object):
    """
    跟随鼠标的无边框悬浮窗，内容为：
      [偏移标注]  [三角标]  [语言徽标]  [大小写徽标]
    只负责"画"与"摆位"，弹出/隐藏的节奏由 CursorLensApp 控制。
    """

    def __init__(self, root, config, api):
        self.cfg = config
        self.api = api
        self.state = None
        self.cursor = (0, 0)
        self.x = 0
        self.y = 0
        self.width = 100
        self.height = 40
        self.visible = False
        self.preview = False          # 设置窗口打开时=True，用于实时预览
        self._click_through_done = False
        self._own_hwnd = 0
        self._badge_font = None
        self._caption_font = None
        self._debug_font = None
        self._font_scale = None

        self.top = tk.Toplevel(root)
        self.top.withdraw()
        self.top.overrideredirect(True)                 # 无标题栏、无边框
        self.top.attributes('-topmost', True)           # 置顶
        self.top.configure(bg=TRANSPARENT_KEY)
        try:
            self.top.attributes('-transparentcolor', TRANSPARENT_KEY)
        except tk.TclError:
            pass
        self.canvas = tk.Canvas(self.top, bg=TRANSPARENT_KEY,
                                highlightthickness=0, bd=0)
        self.canvas.pack(fill='both', expand=True)
        self.apply_style()

    # -- 字体与样式 -------------------------------------------------------
    def _ensure_fonts(self):
        scale = float(self.cfg.get('scale'))
        if self._badge_font is None or self._font_scale != scale:
            self._badge_font = tkfont.Font(
                family=ui_font_family(self.top),
                size=max(8, int(round(11 * scale))),
                weight='bold')
            self._caption_font = tkfont.Font(
                family=FONT_MONO, size=max(7, int(round(9 * scale))))
            self._font_scale = scale
        return self._badge_font, self._caption_font

    def apply_style(self):
        """应用不透明度与窗口样式 / apply alpha and window styles."""
        try:
            self.top.attributes('-alpha', float(self.cfg.get('alpha')))
        except tk.TclError:
            pass
        if self.cfg.get('click_through'):
            self._enable_click_through()

    def _ensure_debug_font(self):
        """诊断行字体（等宽、更小）/ small monospace font for diagnostics."""
        scale = float(self.cfg.get('scale'))
        if getattr(self, '_debug_font', None) is None or \
                self._font_scale != scale:
            self._debug_font = tkfont.Font(
                family=FONT_MONO, size=max(6, int(round(8 * scale))))
        return self._debug_font

    def _debug_text(self):
        """
        诊断串：hkl / native / open / 状态来源 / 输入法名。
        用于排查"某个程序里识别不出中英文"这类问题。
        """
        st = self.state
        if st is None:
            return 'no-state'
        return '%08X n=%s o=%s %s ime=%s' % (
            st.hkl or 0,
            '-' if st.native is None else int(bool(st.native)),
            '-' if st.is_open is None else int(bool(st.is_open)),
            st.source or '-',
            (st.subtype_source or st.desc or '-')[:28])

    def _enable_click_through(self):
        """鼠标穿透：悬浮窗不抢焦点、不遮挡任何点击。"""
        if not WINDOWS:
            return
        try:
            hwnd = self.window_handle()
            if hwnd:
                self.api.set_click_through(int(hwnd))
                self._click_through_done = True
        except Exception:
            pass

    def window_handle(self):
        """悬浮窗的顶层窗口句柄（同时作为输入法状态兜底窗口）。"""
        if self._own_hwnd:
            return self._own_hwnd
        try:
            self.top.update_idletasks()
            hwnd = 0
            try:  # Tk 的窗口外壳（真正的顶层窗口）/ Tk wrapper HWND
                frame = self.top.wm_frame()
                hwnd = int(frame, 16) if frame else 0
            except Exception:
                hwnd = 0
            if not hwnd:
                hwnd = self.api.parent_of(self.top.winfo_id()) \
                    or int(self.top.winfo_id())
            self._own_hwnd = int(hwnd or 0)
            self.api.own_hwnd = self._own_hwnd
        except Exception:
            pass
        return self._own_hwnd

    # -- 绘制 -------------------------------------------------------------
    def _caption_on(self):
        return bool(self.cfg.get('show_caption')) or self.preview

    def _caption_text(self):
        return 'x %+d y %+d' % (int(self.cfg.get('offset_x')),
                                int(self.cfg.get('offset_y')))

    @staticmethod
    def _round_rect(canvas, x1, y1, x2, y2, r, **kw):
        """圆角矩形（Tk 经典写法：平滑多边形）/ rounded rect via smooth polygon."""
        pts = (x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
               x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
               x1, y2, x1, y2 - r, x1, y1 + r, x1, y1)
        return canvas.create_polygon(pts, smooth=True, **kw)

    def _draw_badge(self, x, y, w, h, text, font, fill):
        r = max(3, int(round(6 * float(self.cfg.get('scale')))))
        self._round_rect(self.canvas, x, y, x + w, y + h, r,
                         fill=fill, outline='')
        self.canvas.create_text(x + w / 2.0, y + h / 2.0, text=text,
                                font=font, fill=COLOR_BADGE_TEXT)

    def _draw_marker(self, cx, cy, size):
        """指向鼠标当前位置的三角标 / triangle pointing at the cursor."""
        ang = math.atan2(self.cursor[1] - (self.y + self.height / 2.0),
                         self.cursor[0] - (self.x + self.width / 2.0))
        r = size * 0.46
        pts = []
        for a in (0.0, 2.35, -2.35):        # 主尖角 + 两个底角
            pts.extend([cx + r * math.cos(ang + a),
                        cy + r * math.sin(ang + a)])
        self.canvas.create_polygon(pts, fill=COLOR_MARKER, outline='')

    def render(self):
        """按当前状态与配置重绘，并同步窗口尺寸 / redraw and resize."""
        c = self.canvas
        c.delete('all')
        scale = float(self.cfg.get('scale'))
        badge_font, caption_font = self._ensure_fonts()
        st = self.state

        pad = int(round(7 * scale))
        badge_h = int(round(22 * scale))
        badge_pad = int(round(9 * scale))
        gap = int(round(5 * scale))
        marker_w = int(round(14 * scale)) if self.cfg.get('show_marker') else 0

        cap_text = self._caption_text() if self._caption_on() else ''
        cap_w = caption_font.measure(cap_text) if cap_text else 0
        lang_text = st.lang_badge if st else '--'
        case_text = st.case_badge if st else '--'
        w1 = badge_font.measure(lang_text) + 2 * badge_pad
        w2 = badge_font.measure(case_text) + 2 * badge_pad

        content_w = w1 + gap + w2
        if marker_w:
            content_w += marker_w + gap
        if cap_w:
            content_w += cap_w + gap
        self.width = content_w + 2 * pad

        # 诊断行（可选）/ optional diagnostics line
        debug_text = self._debug_text() if (self.cfg.get('show_debug') and st) \
            else ''
        debug_font = self._ensure_debug_font() if debug_text else None
        debug_h = 0
        if debug_text:
            debug_h = caption_font.measure('Ag') + int(round(6 * scale))
            self.width = max(self.width,
                             debug_font.measure(debug_text) + 2 * pad)
        row_h = badge_h + 2 * pad
        self.height = row_h + debug_h

        # 底板 / panel
        r = int(round(9 * scale))
        self._round_rect(c, 1, 1, self.width - 1, self.height - 1, r,
                         fill=COLOR_PANEL_BG, outline=COLOR_PANEL_EDGE)

        x = pad
        mid = row_h / 2.0
        if cap_text:
            c.create_text(x + cap_w / 2.0, mid, text=cap_text,
                          font=caption_font, fill=COLOR_CAPTION)
            x += cap_w + gap
        if marker_w:
            self._draw_marker(x + marker_w / 2.0, mid, marker_w)
            x += marker_w + gap
        self._draw_badge(x, pad, w1, badge_h, lang_text, badge_font,
                         st.lang_color if st else COLOR_LANG_EN)
        x += w1 + gap
        self._draw_badge(x, pad, w2, badge_h, case_text, badge_font,
                         st.case_color if st else COLOR_CASE_OFF)
        if debug_text and debug_font:
            c.create_text(pad, row_h + debug_h / 2.0, text=debug_text,
                          font=debug_font, fill=COLOR_CAPTION, anchor='w')

    # -- 显示 / 摆位 ------------------------------------------------------
    def place(self, x, y):
        self.x, self.y = int(x), int(y)
        self.top.geometry('%dx%d+%d+%d'
                          % (self.width, self.height, self.x, self.y))

    def show(self, x, y):
        self.place(x, y)
        self.top.deiconify()
        self.top.attributes('-topmost', True)
        self.top.lift()
        self.visible = True
        if self.cfg.get('click_through') and not self._click_through_done:
            self._enable_click_through()

    def hide(self):
        self.top.withdraw()
        self.visible = False


# ============================================================================
# 6. 多语言界面文本 / UI strings (zh / en)
# ============================================================================

STRINGS = {
    'zh': {
        'title': '光标镜 - 设置',
        'tab_trigger': ' 触发与显示 ',
        'tab_position': ' 显示位置 ',
        'tab_style': ' 样式 / 其他 ',
        'trig_hint': '勾选触发按键（最多 2 个）：按下对应组合键就在鼠标旁弹出指示窗。',
        'mode_title': '显示方式',
        'mode_trigger': '触发显示：按下触发键后显示一段时间再自动隐藏（默认 1 秒）',
        'mode_sticky': '保持显示：按一次显示、再按一次隐藏',
        'mode_always': '常显：一直显示并跟随鼠标',
        'dwell': '触发后显示时长（秒，仅"触发显示"生效）',
        'hotkey_title': '打开本设置窗口的快捷键（三连组合键）',
        'hotkey_hint': '点击"录制"后，同时按下 3 个按键即可（按 Esc 取消）',
        'record': '录制',
        'recording': '录制中…请同时按下 3 个按键',
        'pos_hint': '以鼠标位置为基准，指示窗左上角偏移 x / y 像素（范围 -100 ~ +100）。',
        'offset_x': 'X 轴偏移',
        'offset_y': 'Y 轴偏移',
        'reset': '归零',
        'clamp': '限制在屏幕范围内',
        'preview_hint': '打开本窗口期间，指示窗会实时跟随鼠标，便于边看边调。',
        'style_hint': '外观样式',
        'scale': '缩放',
        'alpha': '不透明度',
        'show_caption': '显示偏移标注（x +n y +n）',
        'show_marker': '显示指向鼠标的三角标',
        'show_debug': '显示诊断信息（排查输入法识别，平时请关闭）',
        'click_through': '鼠标穿透（指示窗不遮挡点击）',
        'lang_detect': '语言识别',
        'lang_auto': '自动（从系统读取）',
        'lang_pin': '固定为 中文拼音（拼）',
        'lang_wubi': '固定为 中文五笔（五）',
        'lang_zh': '固定为 中文（中）',
        'lang_en': '固定为 英文（EN）',
        'diagnose': '诊断信息',
        'diagnose_hint': '复制当前输入法识别数据（可在任意程序里按下触发键后再点这里）',
        'copied': '诊断信息已复制到剪贴板。',
        'ui_lang': '界面语言',
        'about': '关于',
        'about_text': '{name} {ver}\n跟随鼠标显示"当前输入法语言 + 大小写状态"。\n默认触发键：CapsLock、Ctrl+Space；默认显示方式：触发后显示 1 秒；默认设置快捷键：Ctrl+Alt+C。',
        'defaults': '恢复默认',
        'exit': '退出程序',
        'close': '关闭',
        'max_trigger': '最多只能选择 2 个触发按键。',
        'captured': '已录制快捷键：{combo}',
        'hotkey_warn': '该快捷键与已选触发按键存在包含关系，可能互相干扰。',
    },
    'en': {
        'title': 'CursorLens - Settings',
        'tab_trigger': ' Triggers & Display ',
        'tab_position': ' Position ',
        'tab_style': ' Style / Misc ',
        'trig_hint': 'Tick the trigger combos (max 2). Pressing one pops the indicator next to the cursor.',
        'mode_title': 'Display mode',
        'mode_trigger': 'On trigger: show for a while after a trigger key, then auto-hide (default 1 s)',
        'mode_sticky': 'Toggle: press once to show, press again to hide',
        'mode_always': 'Always visible: keep showing and following the mouse',
        'dwell': 'Visible duration after a trigger (s) - trigger mode only',
        'hotkey_title': 'Hotkey that opens this settings window (3-key combo)',
        'hotkey_hint': 'Click "Record", then press 3 keys together (Esc cancels)',
        'record': 'Record',
        'recording': 'Recording... press 3 keys together',
        'pos_hint': 'The panel top-left corner is offset from the mouse by x / y pixels (-100 ~ +100).',
        'offset_x': 'X offset',
        'offset_y': 'Y offset',
        'reset': 'Reset',
        'clamp': 'Keep inside the screen',
        'preview_hint': 'While this window is open the panel previews live, so you can tune it visually.',
        'style_hint': 'Appearance',
        'scale': 'Scale',
        'alpha': 'Opacity',
        'show_caption': 'Show offset caption (x +n y +n)',
        'show_marker': 'Show the triangle marker pointing at the cursor',
        'show_debug': 'Show diagnostics (IME detection debug; keep off normally)',
        'click_through': 'Click-through (the panel never blocks clicks)',
        'lang_detect': 'Language detection',
        'lang_auto': 'Auto (read from system)',
        'lang_pin': 'Force Chinese Pinyin (pin)',
        'lang_wubi': 'Force Chinese Wubi (wu)',
        'lang_zh': 'Force Chinese (zh)',
        'lang_en': 'Force English (EN)',
        'diagnose': 'Diagnostics',
        'diagnose_hint': 'Copy the current IME detection data (trigger the panel in another app first)',
        'copied': 'Diagnostics copied to the clipboard.',
        'ui_lang': 'UI language',
        'about': 'About',
        'about_text': '{name} {ver}\nShows the current input language + case state next to the mouse.\nDefault triggers: CapsLock, Ctrl+Space; default display: 1 s after a trigger; default settings hotkey: Ctrl+Alt+C.',
        'defaults': 'Restore defaults',
        'exit': 'Quit',
        'close': 'Close',
        'max_trigger': 'You can select at most 2 trigger combos.',
        'captured': 'Recorded hotkey: {combo}',
        'hotkey_warn': 'This hotkey overlaps with a selected trigger and may interfere.',
    },
}


# ============================================================================
# 7. 设置窗口 / settings window
# ============================================================================

class SettingsWindow(tk.Toplevel):
    """
    选项卡式设置界面：① 触发按键 ② 显示位置 ③ 样式/其他。
    所有改动即时生效并写盘，不需要"应用"按钮。
    """

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.cfg = app.cfg
        self.S = STRINGS[self.cfg.get('ui_language')]
        self.title(self.S['title'])
        self.resizable(False, False)
        self.attributes('-topmost', True)
        self.protocol('WM_DELETE_WINDOW', self.on_close)

        self.trigger_vars = {}
        self._build()
        self._place_near_cursor()

    # -- 布局 -------------------------------------------------------------
    def _build(self):
        S = self.S
        nb = ttk.Notebook(self, padding=6)
        nb.grid(row=0, column=0, sticky='nsew', padx=8, pady=(8, 2))
        self.nb = nb

        self._build_tab_trigger(nb, S)
        self._build_tab_position(nb, S)
        self._build_tab_style(nb, S)

        bar = ttk.Frame(self, padding=(8, 4, 8, 8))
        bar.grid(row=1, column=0, sticky='ew')
        ttk.Button(bar, text=S['defaults'],
                   command=self.on_restore_defaults).pack(side='left')
        ttk.Button(bar, text=S['close'],
                   command=self.on_close).pack(side='right')
        ttk.Button(bar, text=S['exit'],
                   command=self.app.quit_app).pack(side='right', padx=(0, 6))

    def _build_tab_trigger(self, nb, S):
        f = ttk.Frame(nb, padding=12)
        nb.add(f, text=S['tab_trigger'])
        ttk.Label(f, text=S['trig_hint'], justify='left',
                  wraplength=360).grid(row=0, column=0, columnspan=2,
                                       sticky='w', pady=(0, 8))
        row = 1
        for spec in TRIGGER_CANDIDATES:
            var = tk.BooleanVar(value=spec in self.cfg.get('triggers'))
            ttk.Checkbutton(f, text=combo_to_text(combo_parse(spec)),
                            variable=var,
                            command=lambda s=spec: self.on_trigger_toggle(s)
                            ).grid(row=row, column=0, sticky='w', pady=1)
            self.trigger_vars[spec] = var
            row += 1

        # 显示方式：触发显示 / 保持显示 / 常显
        mode_box = ttk.LabelFrame(f, text=S['mode_title'], padding=8)
        mode_box.grid(row=row, column=0, columnspan=2, sticky='ew',
                      pady=(10, 6))
        self.mode_var = tk.StringVar(value=self.cfg.get('display_mode'))
        for i, (value, key) in enumerate((('trigger', 'mode_trigger'),
                                          ('sticky', 'mode_sticky'),
                                          ('always', 'mode_always'))):
            ttk.Radiobutton(mode_box, text=S[key], value=value,
                            variable=self.mode_var,
                            command=self.on_display_mode
                            ).grid(row=i, column=0, sticky='w', pady=1)
        row += 1

        # 触发后显示时长：固定档位 0.3 / 0.5 / 1 / 2 / 4 秒
        box = ttk.LabelFrame(f, text=S['dwell'], padding=8)
        box.grid(row=row, column=0, columnspan=2, sticky='ew', pady=(0, 8))
        self.dwell_var = tk.IntVar(value=int(self.cfg.get('dwell_ms')))
        for i, ms in enumerate(DWELL_CHOICES):
            ttk.Radiobutton(box, text=dwell_text(ms), value=ms,
                            variable=self.dwell_var,
                            command=self.on_dwell
                            ).grid(row=0, column=i, sticky='w', padx=(0, 12))
        row += 1

        hk = ttk.LabelFrame(f, text=S['hotkey_title'], padding=8)
        hk.grid(row=row, column=0, columnspan=2, sticky='ew')
        self.hotkey_lbl = ttk.Label(hk, width=20, anchor='w',
                                    font=(FONT_UI, 10, 'bold'))
        self.hotkey_lbl.pack(side='left')
        ttk.Button(hk, text=S['record'],
                   command=self.on_record_hotkey).pack(side='left', padx=(6, 0))
        ttk.Label(f, text=S['hotkey_hint'], justify='left', wraplength=360,
                  foreground='#666666'
                  ).grid(row=row + 1, column=0, columnspan=2, sticky='w',
                         pady=(3, 0))
        self._sync_hotkey_label()

    def _build_tab_position(self, nb, S):
        f = ttk.Frame(nb, padding=12)
        nb.add(f, text=S['tab_position'])
        ttk.Label(f, text=S['pos_hint'], justify='left', wraplength=360).grid(
            row=0, column=0, columnspan=3, sticky='w', pady=(0, 8))

        self.offset_vars = {}
        for i, (key, text) in enumerate((('offset_x', S['offset_x']),
                                         ('offset_y', S['offset_y']))):
            ttk.Label(f, text=text, width=9).grid(row=1 + i, column=0,
                                                  sticky='w')
            var = tk.DoubleVar(value=float(self.cfg.get(key)))
            ttk.Scale(f, from_=-100, to=100, variable=var,
                      command=lambda v, k=key: self.on_offset(k, v)
                      ).grid(row=1 + i, column=1, sticky='ew', padx=6, pady=3)
            lbl = ttk.Label(f, width=5, anchor='e')
            lbl.grid(row=1 + i, column=2, sticky='e')
            self.offset_vars[key] = (var, lbl)
        f.columnconfigure(1, weight=1)

        self.offset_lbl = ttk.Label(f, font=(FONT_MONO, 11, 'bold'))
        self.offset_lbl.grid(row=3, column=0, columnspan=2, sticky='w',
                             pady=(8, 2))
        ttk.Button(f, text=S['reset'], command=self.on_reset_offset).grid(
            row=3, column=2, sticky='e', pady=(8, 2))

        self.clamp_var = tk.BooleanVar(value=bool(self.cfg.get('clamp_to_screen')))
        ttk.Checkbutton(f, text=S['clamp'], variable=self.clamp_var,
                        command=self.on_clamp
                        ).grid(row=4, column=0, columnspan=3, sticky='w',
                               pady=(4, 0))
        ttk.Label(f, text=S['preview_hint'], justify='left', wraplength=360,
                  foreground='#666666'
                  ).grid(row=5, column=0, columnspan=3, sticky='w',
                         pady=(8, 0))
        self._sync_offset_labels()

    def _build_tab_style(self, nb, S):
        f = ttk.Frame(nb, padding=12)
        nb.add(f, text=S['tab_style'])
        ttk.Label(f, text=S['style_hint']).grid(row=0, column=0, columnspan=3,
                                                sticky='w', pady=(0, 6))

        self.scale_var = tk.DoubleVar(value=float(self.cfg.get('scale')))
        ttk.Label(f, text=S['scale'], width=11).grid(row=1, column=0, sticky='w')
        ttk.Scale(f, from_=0.6, to=2.0, variable=self.scale_var,
                  command=self.on_scale).grid(row=1, column=1, sticky='ew',
                                              padx=6, pady=3)
        self.scale_lbl = ttk.Label(f, width=5, anchor='e')
        self.scale_lbl.grid(row=1, column=2, sticky='e')

        self.alpha_var = tk.DoubleVar(value=float(self.cfg.get('alpha')))
        ttk.Label(f, text=S['alpha'], width=11).grid(row=2, column=0, sticky='w')
        ttk.Scale(f, from_=0.30, to=1.0, variable=self.alpha_var,
                  command=self.on_alpha).grid(row=2, column=1, sticky='ew',
                                              padx=6, pady=3)
        self.alpha_lbl = ttk.Label(f, width=5, anchor='e')
        self.alpha_lbl.grid(row=2, column=2, sticky='e')
        f.columnconfigure(1, weight=1)

        self.caption_var = tk.BooleanVar(value=bool(self.cfg.get('show_caption')))
        ttk.Checkbutton(f, text=S['show_caption'], variable=self.caption_var,
                        command=self.on_caption
                        ).grid(row=3, column=0, columnspan=3, sticky='w',
                               pady=(6, 0))
        self.marker_var = tk.BooleanVar(value=bool(self.cfg.get('show_marker')))
        ttk.Checkbutton(f, text=S['show_marker'], variable=self.marker_var,
                        command=self.on_marker
                        ).grid(row=4, column=0, columnspan=3, sticky='w')
        self.through_var = tk.BooleanVar(value=bool(self.cfg.get('click_through')))
        ttk.Checkbutton(f, text=S['click_through'], variable=self.through_var,
                        command=self.on_click_through
                        ).grid(row=5, column=0, columnspan=3, sticky='w')
        self.debug_var = tk.BooleanVar(value=bool(self.cfg.get('show_debug')))
        ttk.Checkbutton(f, text=S['show_debug'], variable=self.debug_var,
                        command=self.on_show_debug
                        ).grid(row=6, column=0, columnspan=3, sticky='w')

        ttk.Label(f, text=S['lang_detect'], width=11).grid(row=7, column=0,
                                                           sticky='w',
                                                           pady=(8, 0))
        self.lang_var = tk.StringVar(value=self.cfg.get('lang_override'))
        combo = ttk.Combobox(f, state='readonly', width=22, values=(
            S['lang_auto'], S['lang_pin'], S['lang_wubi'], S['lang_zh'],
            S['lang_en']))
        combo.current(('auto', 'pin', 'wubi', 'zh', 'en').index(
            self.cfg.get('lang_override')))
        combo.grid(row=7, column=1, columnspan=2, sticky='w', pady=(8, 0))
        combo.bind('<<ComboboxSelected>>',
                   lambda e: self.on_lang_override(combo.current()))

        ttk.Label(f, text=S['ui_lang'], width=11).grid(row=8, column=0,
                                                       sticky='w', pady=(6, 0))
        ui = ttk.Frame(f)
        ui.grid(row=8, column=1, columnspan=2, sticky='w', pady=(6, 0))
        self.ui_lang_var = tk.StringVar(value=self.cfg.get('ui_language'))
        ttk.Radiobutton(ui, text='中文', value='zh', variable=self.ui_lang_var,
                        command=self.on_ui_language).pack(side='left')
        ttk.Radiobutton(ui, text='English', value='en',
                        variable=self.ui_lang_var,
                        command=self.on_ui_language).pack(side='left',
                                                          padx=(10, 0))

        diag = ttk.Frame(f)
        diag.grid(row=9, column=0, columnspan=3, sticky='ew', pady=(8, 0))
        ttk.Button(diag, text=S['diagnose'],
                   command=self.on_diagnose).pack(side='left')
        ttk.Label(diag, text=S['diagnose_hint'], wraplength=250,
                  foreground='#666666').pack(side='left', padx=(8, 0))

        about = ttk.LabelFrame(f, text=S['about'], padding=8)
        about.grid(row=10, column=0, columnspan=3, sticky='ew', pady=(10, 0))
        ttk.Label(about, justify='left', wraplength=340,
                  text=S['about_text'].format(name=APP_NAME, ver=APP_VERSION)
                  ).pack(anchor='w')

        self._sync_scale_alpha_labels()

    def _place_near_cursor(self):
        """设置窗口出现在鼠标附近（并做屏幕钳制）。"""
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        cx, cy = self.app.api.cursor_pos()
        vx, vy, vw, vh = self.app.api.virtual_screen()
        x = max(vx, min(cx + 30, vx + vw - w - 10))
        y = max(vy, min(cy + 30, vy + vh - h - 10))
        self.geometry('+%d+%d' % (x, y))

    # -- 回调：触发按键 ---------------------------------------------------
    def on_trigger_toggle(self, spec):
        picked = [s for s in TRIGGER_CANDIDATES if self.trigger_vars[s].get()]
        if len(picked) > 2:
            self.trigger_vars[spec].set(False)
            messagebox.showwarning(APP_NAME, self.S['max_trigger'], parent=self)
            return
        self.cfg.set('triggers', picked)
        self._commit()

    def on_display_mode(self):
        """切换显示方式：触发显示 / 保持显示 / 常显。"""
        self.cfg.set('display_mode', self.mode_var.get())
        self._commit()

    def on_dwell(self):
        """触发后显示时长：只允许 0.3 / 0.5 / 1 / 2 / 4 秒。"""
        self.cfg.set('dwell_ms', int(self.dwell_var.get()))
        self._commit()

    def on_record_hotkey(self):
        self.hotkey_lbl.configure(text=self.S['recording'])

        def done(keys):
            spec = combo_to_spec(frozenset(keys))
            self.cfg.set('settings_hotkey', spec)
            self._sync_hotkey_label()
            self._commit()
            if self._overlaps_trigger(spec):
                messagebox.showwarning(APP_NAME, self.S['hotkey_warn'],
                                       parent=self)
            else:
                messagebox.showinfo(
                    APP_NAME,
                    self.S['captured'].format(
                        combo=combo_to_text(combo_parse(spec))),
                    parent=self)

        def cancel():
            self._sync_hotkey_label()

        self.app.start_capture(done, cancel)

    def _overlaps_trigger(self, spec):
        keys = combo_parse(spec)
        for trig in self.cfg.triggers():
            if trig <= keys or keys <= trig:
                return True
        return False

    def _sync_hotkey_label(self):
        self.hotkey_lbl.configure(
            text=combo_to_text(self.cfg.settings_hotkey()) or '--')

    # -- 回调：位置 -------------------------------------------------------
    def on_offset(self, key, value):
        self.cfg.set(key, int(round(float(value))))
        self._sync_offset_labels()
        self._commit()

    def on_reset_offset(self):
        self.cfg.set('offset_x', 0)
        self.cfg.set('offset_y', 0)
        for key in ('offset_x', 'offset_y'):
            var, _lbl = self.offset_vars[key]
            var.set(0)
        self._sync_offset_labels()
        self._commit()

    def on_clamp(self):
        self.cfg.set('clamp_to_screen', bool(self.clamp_var.get()))
        self._commit()

    def _sync_offset_labels(self):
        ox = int(self.cfg.get('offset_x'))
        oy = int(self.cfg.get('offset_y'))
        for key, val in (('offset_x', ox), ('offset_y', oy)):
            _var, lbl = self.offset_vars[key]
            lbl.configure(text='%+d' % val)
        self.offset_lbl.configure(text='x %+d y %+d' % (ox, oy))

    # -- 回调：样式 -------------------------------------------------------
    def on_scale(self, value):
        self.scale_var.set(round(float(value), 3))
        self.cfg.set('scale', round(float(value), 2))
        self._sync_scale_alpha_labels()
        self._commit()

    def on_alpha(self, value):
        self.alpha_var.set(round(float(value), 3))
        self.cfg.set('alpha', round(float(value), 2))
        self._sync_scale_alpha_labels()
        self._commit()

    def _sync_scale_alpha_labels(self):
        self.scale_lbl.configure(text='%.2f' % float(self.cfg.get('scale')))
        self.alpha_lbl.configure(text='%.2f' % float(self.cfg.get('alpha')))

    def on_caption(self):
        self.cfg.set('show_caption', bool(self.caption_var.get()))
        self._commit()

    def on_marker(self):
        self.cfg.set('show_marker', bool(self.marker_var.get()))
        self._commit()

    def on_click_through(self):
        self.cfg.set('click_through', bool(self.through_var.get()))
        self._commit()

    def on_show_debug(self):
        self.cfg.set('show_debug', bool(self.debug_var.get()))
        self._commit()

    def on_diagnose(self):
        """把当前识别数据复制到剪贴板并显示出来（排查用）。"""
        text = self.app.diagnose_text()
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.update_idletasks()
        except tk.TclError:
            pass
        messagebox.showinfo(APP_NAME, '%s\n\n%s' % (self.S['copied'], text),
                            parent=self)

    def on_lang_override(self, index):
        self.cfg.set('lang_override',
                     ('auto', 'pin', 'wubi', 'zh', 'en')[index])
        self._commit()

    def on_ui_language(self):
        lang = self.ui_lang_var.get()
        if lang == self.cfg.get('ui_language'):
            return
        self.cfg.set('ui_language', lang)
        self.cfg.save()
        self.app.rebuild_settings()

    def on_restore_defaults(self):
        self.cfg.data = dict(DEFAULT_CONFIG)
        self.cfg.save()
        self.app.on_config_changed()
        self.app.rebuild_settings()

    def _commit(self):
        """保存 + 通知主体刷新（所有控件改动都走这里）。"""
        self.cfg.save()
        self.app.on_config_changed()

    def on_close(self):
        self.app.close_settings()


# ============================================================================
# 8. 应用主体 / the application core
# ============================================================================

class CursorLensApp(object):
    """
    负责把各部件串起来：
      按键轮询 -> 上升沿判定 -> 读取输入法状态 -> 渲染 -> 跟随鼠标 -> 超时隐藏
    """

    def __init__(self, root, config_path=None):
        self.root = root
        self.cfg = Config(config_path)
        self.api = WinAPI()
        self.reader = InputStateReader(self.api)
        self.indicator = IndicatorWindow(root, self.cfg, self.api)
        self.settings = None
        self.state = None
        self.visible = False
        self.hide_at = 0.0
        self._last_state_ms = 0.0
        self._prev_keys = frozenset()
        self._suppress_triggers = False
        self._capture = None
        self._sticky_visible = False        # "保持显示"模式的开关状态
        self._fg_target = 0                 # 最近一个"非本程序"的前台窗口
        self._tracked = self._tracked_keys()

    # -- 生命周期 ---------------------------------------------------------
    def start(self):
        """
        启动轮询，并做一次启动演示（让用户确认程序已经跑起来）。
        演示固定 2 秒，不改变"保持显示"模式的开关状态。
        """
        self.print_hints()
        self.show_panel()
        self.hide_at = now_s() + 2.0                     # 启动时展示 2 秒
        self.tick()

    def tick(self):
        """主轮询循环：按键边沿判定 + 跟随 + 自动隐藏。"""
        try:
            self._tick_body()
        except Exception:
            traceback.print_exc()
        finally:
            self.root.after(POLL_MS, self.tick)

    def _tick_body(self):
        now = now_s()
        if self._capture is not None:      # 录制快捷键时只做按键采集
            self._tick_capture()
            return
        self._track_foreground()
        down = frozenset(k for k, vk in self._tracked if self.api.key_down(vk))
        hotkey = self.cfg.settings_hotkey()

        # ⑤ 设置快捷键（默认三连组合键）优先判定
        if hotkey and hotkey <= down and not (hotkey <= self._prev_keys):
            self._suppress_triggers = True
            self._prev_keys = down
            self.open_settings()
            return
        if self._suppress_triggers and not (hotkey <= down):
            self._suppress_triggers = False

        # ② 触发键：组合整体由"未满足"变为"满足"的上升沿
        if not self._suppress_triggers:
            for trig in self.cfg.triggers():
                if trig and trig <= down and not (trig <= self._prev_keys):
                    self.on_trigger()
                    break
        self._prev_keys = down
        self._tick_follow(now)

    def _tick_follow(self, now):
        """跟随鼠标 + 状态刷新 + 超时隐藏。"""
        keep = self.keeps_visible() or self.settings_open()
        if not self.visible:
            if keep:
                self.show_panel()
            return
        if now - self._last_state_ms >= STATE_REFRESH_MS / 1000.0:
            if self.refresh_state():
                self.indicator.render()
        self.place_indicator()
        # 不需要"一直显示"时按时间自动隐藏（触发显示 / 保持显示未按下时）
        if (not keep) and now >= self.hide_at:
            self.hide_indicator()

    # -- 触发 -------------------------------------------------------------
    def _track_foreground(self):
        """
        记住最近一个"不属于本程序"的前台窗口。

        本程序自己弹窗时会短暂成为前台窗口，若不排除，就会出现
        "在设置界面里识别正常、在其他程序里读不到"的现象——因为读到的
        其实是本进程的输入法上下文。
        """
        fg = self.api.foreground_window()
        if self._fg_target and not self.api.is_window(self._fg_target):
            self._fg_target = 0          # 目标窗口已关闭，丢弃
        if not fg:
            return
        if not self.api.is_own_window(fg):
            self._fg_target = fg

    def keeps_visible(self):
        """
        当前是否应当"一直显示"：
          always -> 恒为真；sticky -> 由按下的次数切换；trigger -> 恒为假。
        """
        mode = self.cfg.get('display_mode')
        if mode == 'always':
            return True
        if mode == 'sticky':
            return bool(self._sticky_visible)
        return False

    def on_trigger(self):
        """
        触发一次：
          trigger -> 显示 dwell_ms 后自动隐藏
          sticky  -> 按一次显示、再按一次隐藏
          always  -> 始终显示（触发只刷新内容）
        """
        mode = self.cfg.get('display_mode')
        if (mode == 'sticky' and self.visible
                and not self.settings_open()):
            self._sticky_visible = False
            self.hide_indicator()
            return
        if mode == 'sticky':
            self._sticky_visible = True
        self.show_panel()

    def show_panel(self):
        """读状态 -> 重绘 -> 在鼠标旁弹出，并刷新自动隐藏计时。"""
        self.refresh_state(force=True)
        self.indicator.render()
        self.place_indicator()
        self.indicator.show(self.indicator.x, self.indicator.y)
        self.api.own_hwnd = self.indicator.window_handle()
        self.visible = True
        self.hide_at = now_s() + self.cfg.get('dwell_ms') / 1000.0

    def diagnose_text(self):
        """
        汇总一次"输入法识别"的原始数据，便于排查
        "某个程序里识别不出中文/英文"这类问题。
        """
        self.refresh_state(force=True)
        st = self.state
        api = self.api
        fg = self._fg_target or api.foreground_window()
        tid = api.foreground_thread_id(fg)
        focus, active = api.gui_thread_info(tid)
        lang_id = st.lang_id if st else 0
        names = enabled_ime_names(lang_id) if lang_id else []
        lines = [
            '%s %s 诊断信息 / diagnostics' % (APP_NAME, APP_VERSION),
            'time        : %s' % time.strftime('%Y-%m-%d %H:%M:%S'),
            'foreground  : hwnd=0x%X class=%s tid=%d'
            % (fg, api.class_name(fg), tid),
            'focus/active: 0x%X / 0x%X' % (focus, active),
            'layout      : hkl=0x%08X langid=0x%04X'
            % ((st.hkl if st else 0), lang_id),
            'ime state   : native=%s open=%s source=%s'
            % ((st.native if st else None), (st.is_open if st else None),
               (st.source if st else '')),
            'name hints  : %r' % (st.desc if st else ''),
            'enabled IMEs: %r' % (names,),
            'badges      : lang=%s case=%s english=%s subtype_src=%s'
            % ((st.lang_badge if st else '?'), (st.case_badge if st else '?'),
               (st.is_english if st else '?'), (st.subtype_source if st else '')),
            'config      : lang_override=%s triggers=%s mode=%s dwell=%sms'
            % (self.cfg.get('lang_override'), self.cfg.get('triggers'),
               self.cfg.get('display_mode'), self.cfg.get('dwell_ms')),
        ]
        return '\n'.join(lines)

    def hide_indicator(self):
        self.indicator.hide()
        self.visible = False

    def refresh_state(self, force=False):
        """读取系统状态；返回"徽标内容是否变化"。"""
        now = now_s()
        if (not force and self.state is not None
                and now - self._last_state_ms < STATE_REFRESH_MS / 1000.0):
            return False
        self._last_state_ms = now
        new_state = self.reader.read(self.cfg.get('lang_override'),
                                     self._fg_target)
        changed = (self.state is None) or (new_state.key() != self.state.key())
        self.state = new_state
        self.indicator.state = new_state
        return changed

    def place_indicator(self):
        """按"鼠标位置 + 偏移"摆位，必要时做屏幕边界钳制。"""
        cx, cy = self.api.cursor_pos()
        self.indicator.cursor = (cx, cy)
        x = cx + int(self.cfg.get('offset_x'))
        y = cy + int(self.cfg.get('offset_y'))
        if self.cfg.get('clamp_to_screen'):
            vx, vy, vw, vh = self.api.virtual_screen()
            x = max(vx, min(x, vx + max(0, vw - self.indicator.width)))
            y = max(vy, min(y, vy + max(0, vh - self.indicator.height)))
        self.indicator.place(x, y)

    # -- 设置窗口 ---------------------------------------------------------
    def settings_open(self):
        return self.settings is not None and self.settings.winfo_exists()

    def open_settings(self):
        if self.settings_open():
            self.settings.deiconify()
            self.settings.lift()
            self.settings.focus_force()
        else:
            self.settings = SettingsWindow(self)
        if self.cfg.get('display_mode') == 'sticky':
            self._sticky_visible = True     # 保持显示模式下，关掉设置后仍留着
        self.indicator.preview = True
        self.on_config_changed()

    def close_settings(self):
        if self.settings is not None and self.settings.winfo_exists():
            self.settings.destroy()
        self.settings = None
        self.indicator.preview = False
        self.on_config_changed()
        if self.cfg.get('display_mode') == 'sticky':
            # 用户刚在设置里选了"保持显示"，关掉设置后就让指示窗留着
            self._sticky_visible = True
        if not self.keeps_visible():
            self.hide_indicator()

    def rebuild_settings(self):
        """界面语言/恢复默认后重建窗口，保证文案与控件状态同步。"""
        if self.settings_open():
            self.settings.destroy()
            self.settings = SettingsWindow(self)
            self.indicator.preview = True
            self.on_config_changed()

    def on_config_changed(self):
        """配置变更后的统一收敛：重新绑定跟踪键、样式，并刷新画面。"""
        self._tracked = self._tracked_keys()
        self.indicator.apply_style()
        if self.indicator.visible:
            self.refresh_state(force=True)
            self.indicator.render()
            self.place_indicator()

    # -- 快捷键录制 -------------------------------------------------------
    def start_capture(self, on_done, on_cancel=None):
        self._capture = {'done': on_done, 'cancel': on_cancel,
                         'start': now_s()}

    def _tick_capture(self):
        """录制模式：同时按住 >=3 个按键即完成；单独按 Esc 取消。"""
        cap = self._capture
        if cap is None:
            return
        now = now_s()
        if (now - cap['start']) * 1000.0 > CAPTURE_TIMEOUT_MS:
            self._capture = None              # 长时间没有按键：自动放弃录制
            if cap.get('cancel'):
                cap['cancel']()
            return
        pressed = sorted(k for k, vk in VK.items() if self.api.key_down(vk))
        if not pressed:
            return
        if pressed == ['esc']:
            self._capture = None
            self._prev_keys = frozenset(pressed)
            if cap.get('cancel'):
                cap['cancel']()
            return
        if len(pressed) >= 3:
            self._capture = None
            # 录制结束时吞掉这些按键，避免它们立刻被当成触发键
            self._prev_keys = frozenset(pressed)
            if cap.get('done'):
                cap['done'](pressed)

    # -- 工具 -------------------------------------------------------------
    def _tracked_keys(self):
        """每帧需要检查的按键（触发键 + 设置快捷键 + 常用修饰键）。"""
        keys = {'ctrl', 'alt', 'shift', 'win', 'space', 'capslock', 'esc'}
        for spec in self.cfg.get('triggers'):
            keys |= set(combo_parse(spec))
        keys |= set(self.cfg.settings_hotkey())
        return [(k, VK[k]) for k in sorted(keys) if k in VK]

    def print_hints(self):
        trig = ' / '.join(combo_to_text(t) for t in self.cfg.triggers())
        mode = self.cfg.get('display_mode')
        mode_text = {'trigger': '触发显示 %s / on-trigger %s'
                                % (dwell_text(self.cfg.get('dwell_ms')),
                                   dwell_text(self.cfg.get('dwell_ms'))),
                     'sticky': '保持显示（按一次显示/再按一次隐藏）/ toggle',
                     'always': '常显 / always visible'}.get(mode, mode)
        print('=' * 66)
        print('%s %s' % (APP_NAME, APP_VERSION))
        print('  触发按键 Triggers      : %s' % trig)
        print('  显示方式 Display mode  : %s' % mode_text)
        print('  设置快捷键 Settings key: %s'
              % combo_to_text(self.cfg.settings_hotkey()))
        print('  配置文件 Config        : %s' % self.cfg.path)
        print('=' * 66)

    def quit_app(self):
        try:
            self.cfg.save()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass


# ============================================================================
# 9. 入口 / entry point
# ============================================================================

def main():
    enable_dpi_awareness()
    root = tk.Tk()
    root.title(APP_NAME)
    root.withdraw()                     # 主窗口不显示，只作为事件源
    app = CursorLensApp(root)
    app.start()
    try:
        root.mainloop()
    except KeyboardInterrupt:
        app.quit_app()


if __name__ == '__main__':
    main()

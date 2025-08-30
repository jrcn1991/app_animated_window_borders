import os
import time
import json
import math
import ctypes
import psutil
import ctypes.wintypes as wt

import win32gui
import win32con

from typing import Tuple, Dict, Optional, List

# ---------- DWM constants ----------
DWMWA_BORDER_COLOR = 34  # COLORREF (DWORD)
DWMWA_COLOR_DEFAULT = 0xFFFFFFFF
DWMWA_COLOR_NONE = 0xFFFFFFFE
COLOR_INVALID = 0x000000FF

CONFIG_PATH = "config.json"

# Bind native DLLs a single time for performance.
u32 = ctypes.windll.user32
k32 = ctypes.windll.kernel32
dwm = ctypes.windll.dwmapi
try:
    psapi = ctypes.windll.psapi
except Exception:
    psapi = None  # Optional

# Explicit prototypes for extra stability.
u32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
u32.GetWindowThreadProcessId.restype = wt.DWORD
u32.IsWindowVisible.argtypes = [wt.HWND]
u32.IsWindowVisible.restype = wt.BOOL
u32.GetForegroundWindow.restype = wt.HWND

_DwmSetWindowAttribute = dwm.DwmSetWindowAttribute


# ---------- Color helpers ----------
def hex_to_colorref(value: str) -> int:
    """I convert "#RRGGBB", "default", or "none" into a Windows COLORREF (BGR)."""
    if not isinstance(value, str):
        return COLOR_INVALID
    v = value.strip().lower()
    if v == "default":
        return DWMWA_COLOR_DEFAULT
    if v == "none":
        return DWMWA_COLOR_NONE
    if v.startswith("#"):
        v = v[1:]
    if len(v) != 6 or any(c not in "0123456789abcdef" for c in v):
        return COLOR_INVALID
    bgr = int(v[4:6] + v[2:4] + v[0:2], 16)
    return bgr

def colorref_to_hex(colorref: int) -> str:
    """I convert a COLORREF (BGR) back into a "#RRGGBB" string (best-effort)."""
    if colorref in (DWMWA_COLOR_DEFAULT, DWMWA_COLOR_NONE, COLOR_INVALID):
        return "#000000"
    b = (colorref >> 16) & 0xFF
    g = (colorref >> 8) & 0xFF
    r = colorref & 0xFF
    return f"#{r:02X}{g:02X}{b:02X}"

def normalize_hex(s: str) -> str:
    """I normalize arbitrary color inputs to "#RRGGBB", or pass through 'default'/'none'."""
    s = (s or "").strip()
    if not s:
        return "#000000"
    if s.lower() in ("default", "none"):
        return s.lower()
    if not s.startswith("#"):
        s = "#" + s
    if len(s) != 7:
        return "#000000"
    return s.upper()

# ---------- Config ----------
class Config:
    """I load, validate, expose, and save the app configuration atomically."""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._data = None
            cls._instance._load()
        return cls._instance

    def _default(self) -> dict:
        return {
            "hide_tray_icon": False,
            "service_enabled": False,
            "animation": {"type": "none", "speed": 1.0},
            "window_rules": [
                {
                    "match": "Global",
                    "active_border_color": "#c6a0f6",
                    "inactive_border_color": "#ffffff",
                    "animation": {"type": "none", "speed": 1.0}
                }
            ]
        }

    def _load(self):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        except FileNotFoundError:
            self._data = self._default()
        except Exception:
            self._data = self._default()

        self._data.setdefault("animation", {"type": "none", "speed": 1.0})
        self._data.setdefault("service_enabled", False)
        self._data.setdefault("window_rules", [])

        # Keep only Global and Process
        self._data["window_rules"] = [
            r for r in (self._data.get("window_rules") or [])
            if (r.get("match", "").lower() in ("global", "process"))
        ]

        # Ensure a single Global rule at top
        g_idx = None
        for i, r in enumerate(self._data["window_rules"]):
            if (r.get("match") or "").lower() == "global":
                g_idx = i
                r.setdefault("animation", {"type": "none", "speed": 1.0})
                r["contains"] = ""
                break
        if g_idx is None:
            self._data["window_rules"].insert(0, {
                "match": "Global",
                "active_border_color": "#c6a0f6",
                "inactive_border_color": "#ffffff",
                "animation": {"type": "none", "speed": 1.0}
            })
        elif g_idx != 0:
            g = self._data["window_rules"].pop(g_idx)
            self._data["window_rules"].insert(0, g)

    def get(self) -> dict:
        """I give back the current in-memory config dict."""
        return self._data

    def set(self, data: dict):
        """I replace the in-memory config with a new dict (already validated by caller)."""
        self._data = data

    def save(self):
        """I save the config to disk atomically, avoiding partial writes."""
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)

    def reload(self):
        """I reload config from disk and re-apply invariants."""
        self._load()

# ---------- Animations ----------
class Animation:
    """I maintain per-key animation state and produce animated color values (RGB int)."""
    _state: Dict[str, dict] = {}  # key -> {"time": float, "current": int, "last_pass": int}

    @staticmethod
    def _get_state(key: str) -> dict:
        s = Animation._state.get(key)
        if not s:
            s = {"time": 0.0, "current": 0xFF0000, "last_pass": -1}
            Animation._state[key] = s
        return s

    @staticmethod
    def _advance(st: dict, speed: float, scale: float, pass_id: int):
        """Avança o tempo no máximo 1x por 'passo' (chamada de apply_colors_once)."""
        if st.get("last_pass") != pass_id:
            st["time"] += max(0.1, float(speed)) * float(scale)
            st["last_pass"] = pass_id

    @staticmethod
    def _lerp_rgb(a: Optional[int], b: Optional[int], t: float) -> int:
        if a is None:
            a = 0xFF0000
        if b is None:
            b = 0x0000FF
        ar, ag, ab = (a >> 16) & 0xFF, (a >> 8) & 0xFF, a & 0xFF
        br, bg, bb = (b >> 16) & 0xFF, (b >> 8) & 0xFF, b & 0xFF
        r = int(ar + (br - ar) * t)
        g = int(ag + (bg - ag) * t)
        b = int(ab + (bb - ab) * t)
        return (r << 16) | (g << 8) | b

    @staticmethod
    def color_for(
        key: str,
        anim_type: str,
        speed: float,
        start_rgb: Optional[int] = None,
        end_rgb: Optional[int] = None,
        pass_id: int = 0,
    ) -> Optional[int]:
        anim_type = (anim_type or "none").lower()
        if anim_type == "none":
            return None
        if anim_type == "rainbow":
            return Animation._rainbow(key, speed, pass_id)
        if anim_type == "pulse":
            return Animation._pulse(key, speed, start_rgb, end_rgb, pass_id)
        if anim_type == "fade":
            return Animation._fade(key, speed, start_rgb, end_rgb, pass_id)
        if anim_type == "breath":
            return Animation._breath(key, speed, start_rgb, pass_id)
        if anim_type == "tri":
            return Animation._tri(key, speed, start_rgb, end_rgb, pass_id)
        if anim_type == "sparkle":
            return Animation._sparkle(key, speed, start_rgb, pass_id)
        if anim_type == "steps":
            return Animation._steps(key, speed, start_rgb, end_rgb, steps=3, pass_id=pass_id)
        return None

    @staticmethod
    def _rainbow(key: str, speed: float, pass_id: int = 0) -> int:
        st = Animation._get_state(key)
        Animation._advance(st, speed, 1.0, pass_id)
        r = (st["current"] >> 16) & 0xFF
        g = (st["current"] >> 8) & 0xFF
        b = st["current"] & 0xFF
        step = max(1, int(speed * 5))
        if r == 0xFF and g < 0xFF and b == 0x00:
            g = min(0xFF, g + step)
        elif g == 0xFF and r > 0x00 and b == 0x00:
            r = max(0x00, r - step)
        elif g == 0xFF and b < 0xFF and r == 0x00:
            b = min(0xFF, b + step)
        elif b == 0xFF and g > 0x00 and r == 0x00:
            g = max(0x00, g - step)
        elif b == 0xFF and r < 0xFF and g == 0x00:
            r = min(0xFF, r + step)
        elif r == 0xFF and b > 0x00 and g == 0x00:
            b = max(0x00, b - step)
        else:
            r, g, b = 0xFF, 0x00, 0x00
        st["current"] = (r << 16) | (g << 8) | b
        return st["current"]

    @staticmethod
    def _pulse(
        key: str,
        speed: float,
        a: Optional[int] = None,
        b: Optional[int] = None,
        pass_id: int = 0,
    ) -> int:
        st = Animation._get_state(key)
        Animation._advance(st, speed, 0.05, pass_id)
        t = (math.sin(st["time"]) + 1) / 2.0
        st["current"] = Animation._lerp_rgb(a, b, t)
        return st["current"]

    @staticmethod
    def _fade(
        key: str,
        speed: float,
        a: Optional[int] = None,
        b: Optional[int] = None,
        pass_id: int = 0,
    ) -> int:
        st = Animation._get_state(key)
        Animation._advance(st, speed, 0.02, pass_id)
        t = (math.sin(st["time"]) + 1) / 2.0
        st["current"] = Animation._lerp_rgb(a, b, t)
        return st["current"]

    @staticmethod
    def _triangle_wave(x: float) -> float:
        x = x % 2.0
        return 1.0 - abs(x - 1.0)

    @staticmethod
    def _lighten(rgb: int, amt: float) -> int:
        amt = max(0.0, min(1.0, amt))
        return Animation._lerp_rgb(rgb, 0xFFFFFF, amt)

    @staticmethod
    def _darken(rgb: int, amt: float) -> int:
        amt = max(0.0, min(1.0, amt))
        return Animation._lerp_rgb(rgb, 0x000000, amt)

    @staticmethod
    def _breath(key: str, speed: float, a: Optional[int], pass_id: int = 0) -> int:
        st = Animation._get_state(key)
        Animation._advance(st, speed, 0.03, pass_id)
        amp = 0.35 * (0.5 + 0.5 * math.sin(st["time"]))
        base = a if a is not None else 0xFF0000
        st["current"] = Animation._lighten(base, amp)
        return st["current"]

    @staticmethod
    def _tri(
        key: str,
        speed: float,
        a: Optional[int],
        b: Optional[int],
        pass_id: int = 0
    ) -> int:
        st = Animation._get_state(key)
        Animation._advance(st, speed, 0.06, pass_id)
        t = Animation._triangle_wave(st["time"])
        st["current"] = Animation._lerp_rgb(a, b, t)
        return st["current"]

    @staticmethod
    def _sparkle(key: str, speed: float, a: Optional[int], pass_id: int = 0) -> int:
        st = Animation._get_state(key)
        Animation._advance(st, speed, 0.05, pass_id)
        base = a if a is not None else 0xFF0000
        w1 = 0.5 + 0.5 * math.sin(st["time"] * 1.7)
        w2 = 0.5 + 0.5 * math.sin(st["time"] * 2.3 + 1.234)
        jitter = (w1 * 0.6 + w2 * 0.4)
        up = Animation._lighten(base, 0.15 * jitter)
        down = Animation._darken(base, 0.15 * (1.0 - jitter))
        st["current"] = Animation._lerp_rgb(down, up, 0.5)
        return st["current"]

    @staticmethod
    def _steps(
        key: str,
        speed: float,
        a: Optional[int],
        b: Optional[int],
        steps: int = 3,
        pass_id: int = 0
    ) -> int:
        steps = max(2, int(steps))
        st = Animation._get_state(key)
        Animation._advance(st, speed, 0.04, pass_id)
        t = (math.sin(st["time"]) + 1) * 0.5
        q = round(t * (steps - 1)) / (steps - 1)
        st["current"] = Animation._lerp_rgb(a, b, q)
        return st["current"]



# ---------- PID/name caches for performance ----------
_pid_name_cache: Dict[int, str] = {}
_pid_last_seen: Dict[int, float] = {}
_last_colors: Dict[int, int] = {}  # hwnd -> COLORREF

def _gc_pid_cache(max_age: int = 60):
    """I evict stale PID->name cache entries to keep memory bounded."""
    now = time.time()
    stale = [pid for pid, t in _pid_last_seen.items() if now - t > max_age]
    for pid in stale:
        _pid_last_seen.pop(pid, None)
        _pid_name_cache.pop(pid, None)

def _query_process_name_native(pid: int) -> str:
    """I try to read the executable name via QueryFullProcessImageNameW, fallback to psutil."""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if h:
        try:
            buf = ctypes.create_unicode_buffer(260)
            size = wt.DWORD(len(buf))
            if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return (buf.value.rsplit("\\", 1)[-1]).strip()
        finally:
            k32.CloseHandle(h)
    try:
        return psutil.Process(pid).name()
    except Exception:
        return ""

def get_process_name_fast(pid: int) -> str:
    """I return a cached process name if present; otherwise I resolve and cache it."""
    name = _pid_name_cache.get(pid)
    if name is not None:
        _pid_last_seen[pid] = time.time()
        return name
    name = _query_process_name_native(pid) or ""
    _pid_name_cache[pid] = name
    _pid_last_seen[pid] = time.time()
    return name

def get_process_name(hwnd) -> str:
    """I resolve the process name for a given window handle."""
    pid = wt.DWORD()
    u32.GetWindowThreadProcessId(wt.HWND(hwnd), ctypes.byref(pid))
    return get_process_name_fast(pid.value)

# ---------- Rule matching ----------
def _score_rule(rule: dict) -> int:
    """I rank rules. Process beats Global. Higher score wins."""
    m = (rule.get("match") or "").lower()
    if m == "process":
        return 3
    if m == "global":
        return 0
    return 0

def _matches(rule: dict, title: str, class_name: str, process_name: str) -> bool:
    """I check whether a rule matches the current window context. Only Global or Process."""
    m = (rule.get("match") or "").lower()
    contains = (rule.get("contains") or "").lower()
    if m == "global":
        return True
    if m == "process":
        return contains in (process_name or "").lower()
    return False

def pick_rule(config: dict, title: str, class_name: str, process_name: str) -> Optional[dict]:
    """I scan configured rules and return the best match for this window."""
    rules = config.get("window_rules", []) or []
    best = None
    best_score = -1
    for r in rules:
        if _matches(r, title, class_name, process_name):
            sc = _score_rule(r)
            contains_len = len((r.get("contains") or ""))
            key = (sc, contains_len)
            if key > (best_score, len((best or {}).get("contains") or "")):
                best = r
                best_score = sc
    return best

# ---------- Color resolution ----------
def resolve_colors(rule: Optional[dict], defaults: dict) -> Tuple[int, int, dict]:
    """I decide final active/inactive colors or an animation to apply."""
    rule_anim = (rule.get("animation") if rule else None) or defaults.get("animation") or {"type": "none", "speed": 1.0}
    atype = (rule_anim.get("type") or "none").lower()
    aspeed = float(rule_anim.get("speed") or 1.0)

    if atype != "none":
        return -1, -1, {"type": atype, "speed": aspeed}

    active = hex_to_colorref((rule or {}).get("active_border_color") or "default")
    inactive = hex_to_colorref((rule or {}).get("inactive_border_color") or "default")

    if active == COLOR_INVALID:
        active = hex_to_colorref("default")
    if inactive == COLOR_INVALID:
        inactive = hex_to_colorref("default")

    return active, inactive, {"type": "none", "speed": 1.0}

# ---------- DWM application with differential updates ----------
def set_dwm_border_color(hwnd: int, color: int):
    """I set the window border color via DWM, avoiding redundant syscalls."""
    if color in (DWMWA_COLOR_DEFAULT, DWMWA_COLOR_NONE, COLOR_INVALID):
        return
    prev = _last_colors.get(hwnd)
    if prev == color:
        return
    try:
        val = wt.DWORD(color & 0xFFFFFFFF)
        _DwmSetWindowAttribute(
            wt.HWND(hwnd), DWMWA_BORDER_COLOR,
            ctypes.byref(val), ctypes.sizeof(val)
        )
        _last_colors[hwnd] = color
    except Exception:
        pass

def apply_colors_once(config: dict, animated_only: bool = False):
    """
    I enumerate visible windows and apply either animation frames or static colors.
    When animated_only is True, I only touch windows that use animations.
    """
    # Passo global: incrementa 1x por chamada desta função.
    if not hasattr(apply_colors_once, "_pass_id"):
        apply_colors_once._pass_id = 0  # type: ignore[attr-defined]
    apply_colors_once._pass_id += 1     # type: ignore[attr-defined]
    pass_id = apply_colors_once._pass_id  # type: ignore[attr-defined]

    global_anim_defaults = config.get("animation") or {"type": "none", "speed": 1.0}

    def cb(hwnd, _):
        if not u32.IsWindowVisible(wt.HWND(hwnd)):
            return True
        exstyle = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        if exstyle & win32con.WS_EX_TOOLWINDOW:
            return True
        title = win32gui.GetWindowText(hwnd) or ""
        if not title.strip():
            return True
        class_name = win32gui.GetClassName(hwnd) or ""
        process_name = get_process_name(hwnd) or ""

        # Regra preferencial; fallback no Global
        rule = pick_rule(config, title, class_name, process_name)
        if not rule:
            rule = next(
                (r for r in config.get("window_rules", [])
                 if (r.get("match") or "").lower() == "global"),
                None
            )

        active, inactive, anim = resolve_colors(rule, {"animation": global_anim_defaults})

        if animated_only and anim["type"] == "none":
            return True

        if anim["type"] != "none":
            # CHAVE COMPARTILHADA para Global; por processo para Process
            is_global_rule = (rule or {}).get("match", "").lower() == "global"
            key = f'GLOBAL|{anim["type"]}' if is_global_rule else f'{process_name}|{anim["type"]}'

            # Converte "#RRGGBB" do rule para inteiro RGB 0xRRGGBB
            def _hex_to_rgb_int(s: Optional[str]) -> Optional[int]:
                s = normalize_hex(s or "")
                if s in ("default", "none"):
                    return None
                if not s or not s.startswith("#") or len(s) != 7:
                    return None
                try:
                    r = int(s[1:3], 16)
                    g = int(s[3:5], 16)
                    b = int(s[5:7], 16)
                    return (r << 16) | (g << 8) | b
                except Exception:
                    return None

            a_rgb = _hex_to_rgb_int((rule or {}).get("active_border_color"))
            i_rgb = _hex_to_rgb_int((rule or {}).get("inactive_border_color"))

            c = Animation.color_for(key, anim["type"], anim["speed"], a_rgb, i_rgb, pass_id=pass_id)
            if c is not None:
                r = (c >> 16) & 0xFF
                g = (c >> 8) & 0xFF
                b = c & 0xFF
                colorref = (b << 16) | (g << 8) | r  # RGB -> COLORREF (BGR)
                set_dwm_border_color(hwnd, colorref)
            return True

        # Estático
        if active == COLOR_INVALID or inactive == COLOR_INVALID:
            return True

        fg = u32.GetForegroundWindow()
        set_dwm_border_color(hwnd, active if hwnd == fg else inactive)
        return True

    win32gui.EnumWindows(cb, None)


# ---------- Windows utilities (lists) ----------
def list_visible_windows() -> List[tuple]:
    """I return a list of (hwnd, title, class_name, process_name) for visible, non-tool windows."""
    items = []
    def cb(hwnd, _):
        if not u32.IsWindowVisible(wt.HWND(hwnd)):
            return True
        ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        if ex & win32con.WS_EX_TOOLWINDOW:
            return True
        title = win32gui.GetWindowText(hwnd) or ""
        if not title.strip():
            return True
        cls = win32gui.GetClassName(hwnd) or ""
        proc = get_process_name(hwnd) or ""
        items.append((hwnd, title, cls, proc))
        return True
    win32gui.EnumWindows(cb, None)
    return items

def list_process_names(limit: int = 500) -> List[str]:
    """I return a sorted list of unique process executable names, capped by 'limit'."""
    names = set()
    for p in psutil.process_iter(attrs=["name", "pid"]):
        n = p.info.get("name") or ""
        if n:
            names.add(n)
        if len(names) >= limit:
            break
    return sorted(names)

# ---------- Helpers for Process rules ----------
def ensure_single_global_on_top(config_data: dict):
    """I ensure there's exactly one Global rule at index 0."""
    rules = config_data.setdefault("window_rules", [])
    globals_idx = [i for i, r in enumerate(rules) if (r.get("match") or "").lower() == "global"]
    if not globals_idx:
        rules.insert(0, {
            "match": "Global",
            "active_border_color": "#c6a0f6",
            "inactive_border_color": "#ffffff",
            "animation": {"type": "none", "speed": 1.0}
        })
    else:
        first = globals_idx[0]
        for idx in reversed(globals_idx[1:]):
            del rules[idx]
        if first != 0:
            rules.insert(0, rules.pop(first))


# controller.py
# Application controller: I mediate between View and Model. I keep timers and state here.
from typing import List, Tuple
import copy
from PySide6 import QtCore

from model import (
    Config,
    normalize_hex,
    list_process_names,
    list_visible_windows,
    apply_colors_once,
    ensure_single_global_on_top,
)

class AppController(QtCore.QObject):
    status_changed = QtCore.Signal(str)
    rules_changed = QtCore.Signal(list)
    service_toggled = QtCore.Signal(bool)

    def __init__(self):
        super().__init__()
        self.cfg = Config()
        self.config_data = copy.deepcopy(self.cfg.get())

        # Timers that drive updates
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(120)
        self.timer.setTimerType(QtCore.Qt.VeryCoarseTimer)
        self.timer.timeout.connect(self._tick)

        self.animTimer = QtCore.QTimer(self)
        self.animTimer.setInterval(33)  # ~30 FPS
        self.animTimer.setTimerType(QtCore.Qt.CoarseTimer)
        self.animTimer.timeout.connect(self._tick_anim)

        # Start if enabled in config
        if self.config_data.get("service_enabled"):
            self.toggle_service(True)
        else:
            self._emit_status("Status: stopped")

        # Ensure rules list is consistent
        self._emit_rules()

    # ---------- Service control ----------
    def toggle_service(self, enabled: bool):
        self.config_data["service_enabled"] = bool(enabled)
        if enabled:
            if not self.timer.isActive():
                self.timer.start()
            self._rearm_anim_timer()
            self._emit_status("Status: running")
        else:
            if self.timer.isActive():
                self.timer.stop()
            if self.animTimer.isActive():
                self.animTimer.stop()
            self._emit_status("Status: stopped")
        self.service_toggled.emit(bool(enabled))

    def _tick(self):
        try:
            apply_colors_once(self.config_data, animated_only=False)
        except Exception:
            pass

    def _tick_anim(self):
        try:
            apply_colors_once(self.config_data, animated_only=True)
        except Exception:
            pass

    def _emit_status(self, msg: str):
        self.status_changed.emit(msg)

    def _emit_rules(self):
        self.rules_changed.emit(self.rules_text())

    def _has_anim(self) -> bool:
        return any((r.get("animation", {}).get("type", "none") != "none")
                   for r in self.config_data.get("window_rules", []))

    def _rearm_anim_timer(self):
        if self._has_anim() and self.config_data.get("service_enabled"):
            if not self.animTimer.isActive():
                self.animTimer.start()
        else:
            if self.animTimer.isActive():
                self.animTimer.stop()

    # ---------- Rules ----------
    def rules_text(self) -> List[str]:
        ensure_single_global_on_top(self.config_data)
        rows = []
        for i, r in enumerate(self.config_data.get("window_rules", [])):
            num = i + 1
            rows.append(f"{num}. " + self._rule_to_text(r))
        return rows

    def _rule_to_text(self, r: dict) -> str:
        m = r.get("match", "")
        c = r.get("contains", "")
        a = (r.get("animation") or {}).get("type", "none")
        sp = (r.get("animation") or {}).get("speed", 1.0)
        ac = r.get("active_border_color", "")
        ic = r.get("inactive_border_color", "")
        contains_part = f" contains='{c}'" if m.lower() != "global" and c else ""
        return f"[{m}]{contains_part} | active={ac} | inactive={ic} | anim={a}({sp})"

    def add_rule(self, rule: dict):
        self.config_data["window_rules"].append(rule)
        self._emit_rules()
        self._rearm_anim_timer()

    def edit_rule(self, index: int, new_rule: dict):
        if index < 0 or index >= len(self.config_data.get("window_rules", [])):
            return

        old = self.config_data["window_rules"][index]

        # Preserva 'contains' se for regra de processo e o campo vier vazio por engano
        if (new_rule.get("match", "").lower() == "process") and not new_rule.get("contains"):
            new_rule["contains"] = old.get("contains", "")

        self.config_data["window_rules"][index] = new_rule
        self._emit_rules()
        self._rearm_anim_timer()

    def remove_rule(self, index: int) -> str:
        if index < 0 or index >= len(self.config_data.get("window_rules", [])):
            return ""
        r = self.config_data["window_rules"][index]
        if (r.get("match") or "").lower() == "global":
            return "A regra Global é fixa e não pode ser removida."
        del self.config_data["window_rules"][index]
        self._emit_rules()
        self._rearm_anim_timer()
        return ""

    def duplicate_rule(self, index: int) -> str:
        if index < 0 or index >= len(self.config_data.get("window_rules", [])):
            return ""
        r = self.config_data["window_rules"][index]
        if (r.get("match") or "").lower() == "global":
            return "A regra Global não pode ser duplicada."
        self.config_data["window_rules"].append(copy.deepcopy(r))
        self._emit_rules()
        self._rearm_anim_timer()
        return ""

    def get_rule(self, index: int) -> dict | None:
        rules = self.config_data.get("window_rules", [])
        if 0 <= index < len(rules):
            return copy.deepcopy(rules[index])
        return None

    # ---------- Process helpers ----------
    def find_process_rule(self, exe: str) -> int:
        exe_l = (exe or "").strip().lower()
        for i, r in enumerate(self.config_data.get("window_rules", [])):
            if (r.get("match") or "").lower() == "process" and (r.get("contains") or "").strip().lower() == exe_l:
                return i
        return -1

    def add_or_warn_process_rule(self, exe: str, active="#FF0000", inactive="#0000FF") -> str:
        exe = (exe or "").strip()
        if not exe:
            return ""
        if self.find_process_rule(exe) != -1:
            return f"Já existe uma regra para o processo '{exe}'. Edite-a na aba Service."
        r = {
            "match": "Process",
            "contains": exe,
            "active_border_color": active,
            "inactive_border_color": inactive,
            "animation": {"type": "none", "speed": 1.0}
        }
        self.config_data["window_rules"].append(r)
        self._emit_rules()
        self._rearm_anim_timer()
        return ""

    # ---------- Lists ----------
    def refresh_lists(self) -> Tuple[list, list]:
        procs = list_process_names()
        wins_disp = []
        for hwnd, title, cls, proc in list_visible_windows():
            wins_disp.append(f"{title}  |  {cls}  |  {proc}  | hwnd={hwnd}")
        return procs, wins_disp

    # ---------- Persistence ----------
    def reload_config(self):
        self.cfg.reload()
        self.config_data = copy.deepcopy(self.cfg.get())
        self._emit_rules()
        # Respect service flag on reload
        self.toggle_service(bool(self.config_data.get("service_enabled")))

    def save_config(self):
        # Normalize colors and enforce invariants
        for r in self.config_data.get("window_rules", []):
            for k in ("active_border_color", "inactive_border_color"):
                v = r.get(k, "")
                if isinstance(v, str) and v.lower() not in ("default", "none"):
                    r[k] = normalize_hex(v)
        ensure_single_global_on_top(self.config_data)
        self.cfg.set(self.config_data)
        self.cfg.save()
        self._rearm_anim_timer()

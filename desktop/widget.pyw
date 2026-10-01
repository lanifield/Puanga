"""Always-on-top desktop Puanga widget.

Replaces the old timekeeper_widget.pyw, which wrote straight to a JSON log
file. This version is a thin client of the local FastAPI service instead —
the service owns validation and storage, same as the phone PWA. Because this
runs on the same PC as the service, it talks to it over plain
http://127.0.0.1 directly: none of the PWA's HTTPS/mixed-content or
offline-queue concerns apply to a native app calling localhost.

Position/state behaviour (centered, bottom-anchored, full/compact views,
auto-hide on focus loss, minimized until its taskbar icon is clicked) is
carried over from the old widget largely unchanged — only the fields and the
backing store changed.
"""

import json
import os
import subprocess
import time
import tkinter as tk
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from tkinter import ttk

# --- Configuration ---
SERVICE_URL = os.environ.get("PUANGA_SERVICE_URL", "http://127.0.0.1:8420")
REQUEST_TIMEOUT_S = 3.0
SERVICE_PORT = int(SERVICE_URL.rsplit(":", 1)[-1])
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ICON_PNG_PATH = Path(__file__).resolve().parent / "icon.png"
ICON_ICO_PATH = Path(__file__).resolve().parent / "icon.ico"

# Set PUANGA_WIDGET_DEBUG=1 to keep the window visible on startup instead
# of iconifying it — useful for checking position/sizing without having to
# click through the taskbar every time.
STAY_VISIBLE = os.environ.get("PUANGA_WIDGET_DEBUG") == "1"

# --- Sizing / layout ---
FULL_WIDTH = 450
COMPACT_WIDTH = 160
BOTTOM_MARGIN = 80          # keeps the widget clear of the taskbar
HIDE_DELAY_MS = 3000        # 3 seconds after focus is genuinely lost

# --- Colour tokens (placeholder — restyle freely, this is just a base) ---
WHALE_DARK = "#1e2028"
WHALEBONE_DARK = "#efefe0"
WHALEBONE_LIGHT = "#fdfdf6"


def _api_request(method, path, payload=None):
    """Returns (status_code, body_dict). status_code is None if the service
    couldn't be reached at all (as opposed to an HTTP error response)."""
    url = f"{SERVICE_URL}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
            body = resp.read()
            return resp.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            parsed = json.loads(body) if body else None
        except json.JSONDecodeError:
            parsed = {"detail": body.decode("utf-8", "replace")}
        return e.code, parsed
    except (urllib.error.URLError, OSError):
        return None, None


def _parse_iso(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def ensure_service_running():
    """Starts the Puanga service in the background if it isn't already up.

    Runs once, before the window is even created, so that clicking a pinned
    taskbar icon works as "start (or check on) the app" in one step rather
    than requiring the service to already be running in another terminal.
    Only makes sense when SERVICE_URL points at this machine, which is the
    only configuration this actually gets used with in practice.
    """
    if _api_request("GET", "/health")[0] == 200:
        return

    python_exe = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
    if not python_exe.exists():
        return  # nothing sensible to do without the project's own venv

    subprocess.Popen(
        [
            str(python_exe), "-m", "uvicorn", "main:app",
            "--app-dir", str(PROJECT_ROOT / "service"),
            "--host", "0.0.0.0", "--port", str(SERVICE_PORT),
        ],
        cwd=str(PROJECT_ROOT),
        creationflags=subprocess.CREATE_NO_WINDOW,
    )

    for _ in range(20):  # up to ~10s for uvicorn to come up
        time.sleep(0.5)
        if _api_request("GET", "/health")[0] == 200:
            return


class PuangaWidget:
    def __init__(self, root):
        self.root = root
        self.root.title("Puanga")
        self.root.attributes("-topmost", True)
        self.root.configure(bg=WHALE_DARK)

        if ICON_ICO_PATH.exists():
            # iconbitmap (not iconphoto) is what actually gets Windows to use
            # this for the taskbar's big icon, not just the title bar — a
            # long-standing Tk-on-Windows quirk. Needs a real multi-resolution
            # .ico, which is why one was generated alongside icon.png.
            self.root.iconbitmap(default=str(ICON_ICO_PATH))
        elif ICON_PNG_PATH.exists():
            self._icon_image = tk.PhotoImage(file=str(ICON_PNG_PATH))
            self.root.iconphoto(True, self._icon_image)

        # --- state ---
        self.current_entry_id = None
        self.start_dt = None            # aware datetime (UTC), for live elapsed calc
        self.options = []               # [{value, label, client}] from the vault via GET /options
        self._hide_after_id = None
        self._timer_after_id = None

        self._build_full_view()
        self._build_compact_view()

        # Reflect whatever the service thinks is actually running (it may
        # have been started from the phone, or survive a widget restart)
        # rather than always assuming idle.
        status, running_entry = self._fetch_running_entry()
        if status is None:
            self.show_full()
            self.status_label.config(text="Can't reach Puanga service")
        elif running_entry is not None:
            self._enter_running_state(running_entry)
        else:
            self.show_full()

        if not STAY_VISIBLE:
            self.root.after(100, self.root.iconify)

        # auto-hide wiring: bind_all so we see focus moves everywhere in the
        # app (including between our own Entry fields), then filter those out
        self.root.bind_all("<FocusOut>", self._on_focus_out)
        self.root.bind_all("<FocusIn>", self._on_focus_in)

    # ---------- geometry ----------
    def _centered_geometry(self, width, height):
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = (screen_w - width) // 2
        y = screen_h - height - BOTTOM_MARGIN
        return f"{width}x{height}+{x}+{y}"

    # ---------- building the two views ----------
    def _build_full_view(self):
        self.full_frame = tk.Frame(self.root, bg=WHALE_DARK, padx=10, pady=10)

        tk.Label(self.full_frame, text="Project/Course:", bg=WHALE_DARK, fg=WHALEBONE_DARK,
                 font=("Arial", 10, "bold")).grid(row=0, column=0, sticky="w", pady=(5, 0))
        self.project_var = tk.StringVar()
        self.project_combo = ttk.Combobox(self.full_frame, textvariable=self.project_var,
                                           state="readonly", width=38, font=("Arial", 10))
        self.project_combo.grid(row=0, column=1, padx=10, pady=5)
        self.project_combo.bind("<<ComboboxSelected>>", lambda e: self._sync_client_label())

        # Not user-editable: the client is whatever the selected project's
        # frontmatter (or the course's provider) declares, not a free choice —
        # see the design note in the spec doc on why this shouldn't drift
        # independently of the project.
        tk.Label(self.full_frame, text="Client/Provider:", bg=WHALE_DARK, fg=WHALEBONE_DARK,
                 font=("Arial", 10, "bold")).grid(row=1, column=0, sticky="w", pady=(5, 0))
        self.client_label = tk.Label(self.full_frame, text="—", bg=WHALE_DARK, fg=WHALEBONE_DARK,
                                      font=("Arial", 10), anchor="w")
        self.client_label.grid(row=1, column=1, sticky="w", padx=10, pady=5)

        self.fields = {}
        tk.Label(self.full_frame, text="Task:", bg=WHALE_DARK, fg=WHALEBONE_DARK,
                 font=("Arial", 10, "bold")).grid(row=2, column=0, sticky="w", pady=(5, 0))
        task_entry = tk.Entry(self.full_frame, width=40, font=("Arial", 10))
        task_entry.grid(row=2, column=1, padx=10, pady=5)
        self.fields["task"] = task_entry

        self.status_label = tk.Label(self.full_frame, text="Ready", bg=WHALE_DARK, fg=WHALEBONE_DARK)
        self.status_label.grid(row=3, column=0, columnspan=2, pady=10)

        btn_frame = tk.Frame(self.full_frame, bg=WHALE_DARK)
        btn_frame.grid(row=4, column=0, columnspan=2, pady=5)

        self.start_btn = tk.Button(btn_frame, text="START", command=self.start_timer,
                                    bg="#15803d", fg=WHALEBONE_LIGHT, font=("Arial", 10, "bold"), width=10)
        self.start_btn.pack(side="left", padx=5)

    def _build_compact_view(self):
        self.compact_frame = tk.Frame(self.root, bg=WHALE_DARK, padx=8, pady=8)

        self.compact_task_label = tk.Label(self.compact_frame, text="", bg=WHALE_DARK,
                                            fg=WHALEBONE_LIGHT, font=("Arial", 9), wraplength=140)
        self.compact_task_label.pack(pady=(0, 2))

        self.elapsed_var = tk.StringVar(value="00:00:00")
        self.compact_timer_label = tk.Label(self.compact_frame, textvariable=self.elapsed_var,
                                             bg=WHALE_DARK, fg=WHALEBONE_DARK, font=("Consolas", 14, "bold"))
        self.compact_timer_label.pack(pady=(0, 4))

        self.compact_stop_btn = tk.Button(self.compact_frame, text="STOP", command=self.stop_timer,
                                           bg="#d13438", fg=WHALEBONE_LIGHT, font=("Arial", 9, "bold"))
        self.compact_stop_btn.pack()

    # ---------- vault-backed project/client options ----------
    def _refresh_options(self):
        status, body = _api_request("GET", "/options")
        if status == 200 and body is not None:
            self.options = body.get("options", [])

        labels = [o["label"] for o in self.options]
        current = self.project_var.get()
        self.project_combo["values"] = labels
        if current not in labels:
            self.project_var.set(labels[0] if labels else "")
        self._sync_client_label()

    def _sync_client_label(self):
        match = next((o for o in self.options if o["label"] == self.project_var.get()), None)
        self.client_label.config(text=match["client"] if match else "—")

    # ---------- switching between the two views ----------
    def show_full(self):
        self._refresh_options()
        self.compact_frame.pack_forget()
        self.full_frame.pack(fill="both", expand=True)
        self.root.update_idletasks()
        height = self.full_frame.winfo_reqheight()
        self.root.geometry(self._centered_geometry(FULL_WIDTH, height))

    def show_compact(self):
        self.full_frame.pack_forget()
        self.compact_frame.pack(fill="both", expand=True)
        self.root.update_idletasks()
        height = self.compact_frame.winfo_reqheight()
        self.root.geometry(self._centered_geometry(COMPACT_WIDTH, height))

    # ---------- service calls ----------
    def _fetch_running_entry(self):
        """Returns (status, entry_or_None). status is None if unreachable."""
        status, body = _api_request("GET", "/entries")
        if status is None or status != 200 or body is None:
            return status, None
        return status, next((e for e in body if e.get("end_time") is None), None)

    def _enter_running_state(self, entry):
        self.current_entry_id = entry["id"]
        self.start_dt = _parse_iso(entry["start_time"])
        label = f"{entry['client_id']} / {entry['project_id']} — {entry['task']}"
        self.compact_task_label.config(text=label)
        self.show_compact()
        self._tick()

    # ---------- timing ----------
    def start_timer(self):
        match = next((o for o in self.options if o["label"] == self.project_var.get()), None)
        task = self.fields["task"].get().strip()

        if not match or not task:
            self.status_label.config(text="Pick a Project/Course and enter a Task")
            return

        status, body = _api_request("POST", "/entries/start", {
            "project_id": match["value"],
            "task": task,
        })

        if status is None:
            self.status_label.config(text="Can't reach Puanga service")
            return
        if status == 409:
            # Something's already running — likely started from the phone.
            # Reflect the real state instead of just erroring out.
            self.status_label.config(text="Already running elsewhere — showing it")
            _, running_entry = self._fetch_running_entry()
            if running_entry is not None:
                self._enter_running_state(running_entry)
            return
        if status != 200:
            detail = (body or {}).get("detail", f"HTTP {status}")
            self.status_label.config(text=f"Start failed: {detail}")
            return

        self._enter_running_state(body)

    def _tick(self):
        elapsed = datetime.now(timezone.utc) - self.start_dt
        total_seconds = int(elapsed.total_seconds())
        h, rem = divmod(total_seconds, 3600)
        m, s = divmod(rem, 60)
        self.elapsed_var.set(f"{h:02d}:{m:02d}:{s:02d}")
        self._timer_after_id = self.root.after(1000, self._tick)

    def stop_timer(self):
        if self.current_entry_id is None:
            self.show_full()
            return

        status, body = _api_request("POST", f"/entries/{self.current_entry_id}/stop", {})

        if status is None:
            self.status_label.config(text="Can't reach Puanga service — try Stop again")
            return  # stay in compact view so nothing is lost; retry on next click
        if status not in (200, 404, 409):
            detail = (body or {}).get("detail", f"HTTP {status}")
            self.status_label.config(text=f"Stop failed: {detail}")
            return  # also retryable

        if self._timer_after_id is not None:
            self.root.after_cancel(self._timer_after_id)
            self._timer_after_id = None

        end_time = body.get("end_time") if status == 200 else None
        self.current_entry_id = None
        self.start_dt = None

        for key in self.fields:
            self.fields[key].delete(0, tk.END)
        self.status_label.config(text=f"Saved: {end_time}" if end_time else "Stopped")
        self.show_full()

    # ---------- auto-hide on genuine focus loss ----------
    def _on_focus_out(self, event):
        # give Tk a moment to settle where focus actually landed before
        # deciding whether we've left the app entirely
        self.root.after(50, self._maybe_start_hide_timer)

    def _on_focus_in(self, event):
        self._cancel_hide_timer()

    def _maybe_start_hide_timer(self):
        if self.root.state() == "iconic":
            return
        try:
            focused = self.root.focus_get()
        except KeyError:
            # rare Tk quirk where the focus widget can't be resolved; treat as "gone"
            focused = None
        if focused is None:
            self._cancel_hide_timer()
            self._hide_after_id = self.root.after(HIDE_DELAY_MS, self._hide_widget)

    def _cancel_hide_timer(self):
        if self._hide_after_id is not None:
            self.root.after_cancel(self._hide_after_id)
            self._hide_after_id = None

    def _hide_widget(self):
        self._hide_after_id = None
        if self.root.state() != "iconic":
            self.root.iconify()


if __name__ == "__main__":
    try:
        # Without this, Windows groups every pythonw.exe-hosted app together
        # and shows a generic icon on the taskbar button regardless of what
        # the window itself sets via iconbitmap/iconphoto — this gives the
        # process its own identity so the taskbar uses its actual icon.
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Puanga.TimeTracker")
    except Exception:
        pass

    ensure_service_running()
    root = tk.Tk()
    app = PuangaWidget(root)
    root.mainloop()

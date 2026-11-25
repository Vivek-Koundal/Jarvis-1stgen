import ctypes
from ctypes import wintypes
import logging
import os
import queue
import re
import threading
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from tkinter import ttk

from main import run_model_request
from tools import ToolHandler

WDA_EXCLUDEFROMCAPTURE = 0x00000011
GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
VK_BACK = 0x08
VK_RETURN = 0x0D
VK_ESCAPE = 0x1B
VK_SPACE = 0x20
VK_CONTROL = 0x11
VK_SHIFT = 0x10
VK_V = 0x56
VK_A = 0x41
VK_C = 0x43


def configure_stealth_overlay_window(tk_window):
    """Hide window from screen capture AND prevent it from stealing focus from full-screen apps."""
    if os.name != "nt":
        return
    try:
        tk_window.update_idletasks()
        user32 = ctypes.windll.user32
        hwnd = user32.GetParent(tk_window.winfo_id()) or tk_window.winfo_id()
        user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
        ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(
            hwnd,
            GWL_EXSTYLE,
            ex_style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW,
        )
    except Exception as error:
        logging.warning("Could not configure stealth/no-activate window: %s", error)


class NonActivatingKeyboardCapture:
    """Captures keystrokes into Jarvis without activating the window or leaving full-screen."""

    def __init__(self, on_char, on_backspace, on_enter, on_escape, on_paste, on_clear, on_copy_hud):
        self.on_char = on_char
        self.on_backspace = on_backspace
        self.on_enter = on_enter
        self.on_escape = on_escape
        self.on_paste = on_paste
        self.on_clear = on_clear
        self.on_copy_hud = on_copy_hud
        self.active = False
        self.hud_open = False
        self._hook = None
        self._thread = None
        self._thread_id = None

    def start(self):
        if os.name != "nt" or self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._hook_loop,
            daemon=True,
            name="JarvisNoActivateKeys",
        )
        self._thread.start()

    def stop(self):
        self.active = False
        self.hud_open = False
        if os.name == "nt" and self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, 0x0012, 0, 0)

    def _hook_loop(self):
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._thread_id = kernel32.GetCurrentThreadId()

        HOOKPROC = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t,
            ctypes.c_int,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )

        user32.SetWindowsHookExW.argtypes = [
            ctypes.c_int,
            HOOKPROC,
            wintypes.HINSTANCE,
            wintypes.DWORD,
        ]
        user32.SetWindowsHookExW.restype = wintypes.HHOOK

        user32.CallNextHookEx.argtypes = [
            wintypes.HHOOK,
            ctypes.c_int,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.CallNextHookEx.restype = ctypes.c_ssize_t

        user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
        user32.UnhookWindowsHookEx.restype = wintypes.BOOL

        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ("vkCode", wintypes.DWORD),
                ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)),
            ]

        def low_level_handler(nCode, wParam, lParam):
            if nCode == 0 and wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                vk = kb.vkCode
                ctrl_down = bool(user32.GetAsyncKeyState(VK_CONTROL) & 0x8000)

                # Ctrl+Shift+C copies Jarvis's HUD output anytime
                shift_down = bool(user32.GetAsyncKeyState(VK_SHIFT) & 0x8000)
                if ctrl_down and shift_down and vk == VK_C and self.hud_open:
                    self.on_copy_hud()
                    return 1

                if self.active:
                    # Allow Ctrl+Space hotkey to pass through
                    if ctrl_down and vk == VK_SPACE:
                        return user32.CallNextHookEx(self._hook, nCode, wParam, lParam)

                    # Ctrl+V -> paste clipboard text into Jarvis
                    if ctrl_down and vk == VK_V:
                        self.on_paste()
                        return 1

                    # Ctrl+A or Ctrl+Backspace -> clear Jarvis input text
                    if ctrl_down and vk in (VK_A, VK_BACK):
                        self.on_clear()
                        return 1

                    if vk == VK_ESCAPE:
                        self.on_escape()
                        return 1
                    if vk == VK_RETURN:
                        self.on_enter()
                        return 1
                    if vk == VK_BACK:
                        self.on_backspace()
                        return 1

                    # Translate virtual key + shift state to Unicode character
                    key_state = (ctypes.c_ubyte * 256)()
                    if shift_down:
                        key_state[VK_SHIFT] = 0x80
                    if user32.GetKeyState(0x14) & 0x0001:  # Caps Lock
                        key_state[0x14] = 0x01
                    buf = ctypes.create_unicode_buffer(8)
                    rc = user32.ToUnicode(vk, kb.scanCode, key_state, buf, 8, 0)
                    if rc > 0 and buf.value and not ctrl_down:
                        self.on_char(buf.value)
                        return 1

            return user32.CallNextHookEx(self._hook, nCode, wParam, lParam)

        cb = HOOKPROC(low_level_handler)
        self._hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, cb, None, 0)
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        if self._hook:
            user32.UnhookWindowsHookEx(self._hook)
            self._hook = None


class TransparentHUD:
    """Floating transparent typewriter HUD that writes Jarvis's full reply over the screen."""

    TRANSPARENT_KEY = "#010203"

    def __init__(self, parent, on_state_change=None):
        self.parent = parent
        self.on_state_change = on_state_change
        self.win = None
        self.text_widget = None
        self.copy_lbl = None
        self._type_job = None
        self._fade_job = None
        self._full_text = ""
        self._char_index = 0

    def copy_to_clipboard(self, _event=None):
        if not self._full_text:
            return
        try:
            self.parent.clipboard_clear()
            self.parent.clipboard_append(self._full_text)
            self.parent.update()
            if self.copy_lbl is not None and self.copy_lbl.winfo_exists():
                self.copy_lbl.configure(text="Copied!", fg="#7CE38B")
                self.parent.after(1500, self._reset_copy_label)
        except tk.TclError:
            pass

    def _reset_copy_label(self):
        try:
            if self.copy_lbl is not None and self.copy_lbl.winfo_exists():
                self.copy_lbl.configure(text="Copy", fg="#91C8FF")
        except tk.TclError:
            pass

    def show(self, message, auto_hide_ms=16000):
        self.dismiss()
        cleaned = re.sub(r"\*\*(.*?)\*\*", r"\1", message).strip()
        if not cleaned:
            return

        self._full_text = cleaned
        self._char_index = 0

        win = tk.Toplevel(self.parent)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        if os.name == "nt":
            win.configure(bg=self.TRANSPARENT_KEY)
            win.attributes("-transparentcolor", self.TRANSPARENT_KEY)
        else:
            win.configure(bg="#0B121E")
            win.attributes("-alpha", 0.88)

        screen_w = self.parent.winfo_screenwidth()
        screen_h = self.parent.winfo_screenheight()
        hud_w = min(680, screen_w - 80)

        lines = 0
        for raw_line in cleaned.splitlines() or [cleaned]:
            lines += max(1, (len(raw_line) // 72) + 1)
        lines = min(max(lines, 2), 18)
        hud_h = lines * 24 + 44

        x = screen_w - hud_w - 24
        y = max(36, screen_h - 182 - 64 - hud_h - 14)
        win.geometry(f"{hud_w}x{hud_h}+{x}+{y}")
        configure_stealth_overlay_window(win)

        border = tk.Frame(win, bg="#2A5885", padx=1, pady=1)
        border.pack(fill="both", expand=True)

        panel = tk.Frame(border, bg="#0B1320", padx=16, pady=12)
        panel.pack(fill="both", expand=True)

        top_bar = tk.Frame(panel, bg="#0B1320")
        top_bar.pack(fill="x", pady=(0, 6))

        tag = tk.Label(
            top_bar,
            text="JARVIS HUD",
            bg="#0B1320",
            fg="#58B4FF",
            font=("Segoe UI", 8, "bold"),
        )
        tag.pack(side="left")

        close_lbl = tk.Label(
            top_bar,
            text="✕",
            bg="#0B1320",
            fg="#8FA6C0",
            font=("Segoe UI", 9, "bold"),
            cursor="hand2",
            padx=4,
        )
        close_lbl.pack(side="right")
        close_lbl.bind("<Button-1>", lambda _e: self.dismiss())

        copy_lbl = tk.Label(
            top_bar,
            text="Copy",
            bg="#18273B",
            fg="#91C8FF",
            font=("Segoe UI", 8, "bold"),
            cursor="hand2",
            padx=8,
            pady=1,
        )
        copy_lbl.pack(side="right", padx=(0, 8))
        copy_lbl.bind("<Button-1>", self.copy_to_clipboard)

        text = tk.Text(
            panel,
            wrap="word",
            bg="#0B1320",
            fg="#EAF4FF",
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            font=("Segoe UI", 11),
            cursor="hand2",
            takefocus=False,
        )
        text.bind("<Button-3>", self.copy_to_clipboard)
        scrollbar = ttk.Scrollbar(panel, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)
        text.pack(side="left", fill="both", expand=True)
        if lines >= 14:
            scrollbar.pack(side="right", fill="y")

        text.configure(state="disabled")
        win.bind("<Enter>", lambda _e: self._cancel_fade())
        win.bind("<Leave>", lambda _e: self._schedule_fade(auto_hide_ms))

        self.win = win
        self.text_widget = text
        self.copy_lbl = copy_lbl
        if self.on_state_change:
            self.on_state_change(True)
        self._type_step(auto_hide_ms)

    def _type_step(self, auto_hide_ms):
        if self.win is None or self.text_widget is None:
            return
        try:
            if not self.win.winfo_exists():
                return
        except tk.TclError:
            return

        step = 3 if len(self._full_text) < 450 else 8
        next_idx = min(len(self._full_text), self._char_index + step)
        chunk = self._full_text[self._char_index : next_idx]
        self._char_index = next_idx

        self.text_widget.configure(state="normal")
        self.text_widget.insert("end", chunk)
        self.text_widget.see("end")
        self.text_widget.configure(state="disabled")

        if self._char_index < len(self._full_text):
            self._type_job = self.parent.after(14, self._type_step, auto_hide_ms)
        else:
            self._type_job = None
            self._schedule_fade(auto_hide_ms)

    def _schedule_fade(self, delay_ms):
        self._cancel_fade()
        if delay_ms and self.win is not None:
            self._fade_job = self.parent.after(delay_ms, self.dismiss)

    def _cancel_fade(self):
        if self._fade_job is not None:
            try:
                self.parent.after_cancel(self._fade_job)
            except tk.TclError:
                pass
            self._fade_job = None

    def dismiss(self):
        self._cancel_fade()
        if self._type_job is not None:
            try:
                self.parent.after_cancel(self._type_job)
            except tk.TclError:
                pass
            self._type_job = None
        if self.win is not None:
            try:
                if self.win.winfo_exists():
                    self.win.destroy()
            except tk.TclError:
                pass
            self.win = None
            self.text_widget = None
            self.copy_lbl = None
        if self.on_state_change:
            self.on_state_change(False)


class JarvisOverlay:
    WIDTH = 460
    HEIGHT = 182
    PLACEHOLDER_TEXT = "Click here to type or Ctrl+V to paste..."

    def __init__(
        self,
        model_client,
        vision_model=None,
        wake_word_access_key=None,
    ):
        self.model_client = model_client
        self.vision_model = vision_model
        self.wake_word_access_key = wake_word_access_key
        self.chat_history = []
        self.events = queue.Queue()
        self.request_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="JarvisRequest",
        )
        self.request_running = False
        self.request_feedback_shown = False
        self.voice_running = False
        self.voice_mode = False
        self.voice_stop = threading.Event()
        self.voice_resume = threading.Event()
        self.voice_resume.set()
        self.voice_thread = None
        self.wake_word_enabled = False
        self.wake_stop = threading.Event()
        self.wake_resume = threading.Event()
        self.wake_resume.set()
        self.request_idle = threading.Event()
        self.request_idle.set()
        self.wake_thread = None
        self.confirmation_finish = None
        self.tts = None
        self._hotkey_down = False
        self._visible = True
        self.root = tk.Tk()
        self.root.title("Jarvis")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.90)
        self.root.configure(bg="#101722")
        self.root.geometry(
            f"{self.WIDTH}x{self.HEIGHT}+"
            f"{self.root.winfo_screenwidth() - self.WIDTH - 18}+"
            f"{self.root.winfo_screenheight() - self.HEIGHT - 64}"
        )
        configure_stealth_overlay_window(self.root)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.key_capture = NonActivatingKeyboardCapture(
            on_char=lambda ch: self.events.put(("hook_char", ch)),
            on_backspace=lambda: self.events.put(("hook_backspace", None)),
            on_enter=lambda: self.events.put(("hook_enter", None)),
            on_escape=lambda: self.events.put(("hook_escape", None)),
            on_paste=lambda: self.events.put(("hook_paste", None)),
            on_clear=lambda: self.events.put(("hook_clear", None)),
            on_copy_hud=lambda: self.events.put(("hook_copy_hud", None)),
        )
        self.key_capture.start()
        self.hud = TransparentHUD(
            self.root,
            on_state_change=lambda is_open: setattr(self.key_capture, "hud_open", is_open),
        )

        self.tool_handler = ToolHandler(
            model_client=model_client,
            vision_model=vision_model,
            confirmation_callback=self._confirm_from_worker,
            status_callback=self._status_from_worker,
        )
        self._build_ui()
        self._poll_hotkey()
        self._poll_worker_events()
        self._start_wake_listener()

    def _build_ui(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(
            "Jarvis.TEntry",
            fieldbackground="#0F1724",
            foreground="#E8EEF7",
            insertcolor="#E8EEF7",
            bordercolor="#35445A",
            padding=10,
        )
        style.configure(
            "Jarvis.TButton",
            background="#273448",
            foreground="#E8EEF7",
            bordercolor="#3B4D65",
            padding=(13, 8),
            font=("Segoe UI", 10),
        )
        style.map(
            "Jarvis.TButton",
            background=[("active", "#344760"), ("disabled", "#202A38")],
            foreground=[("disabled", "#7F8B9C")],
        )

        frame = tk.Frame(self.root, bg="#101722", padx=18, pady=14)
        frame.pack(fill="both", expand=True)

        header = tk.Frame(frame, bg="#101722")
        header.pack(fill="x", pady=(0, 12))
        title = tk.Label(
            header,
            text="JARVIS",
            bg="#101722",
            fg="#91C8FF",
            font=("Segoe UI", 10, "bold"),
        )
        title.pack(side="left")
        close_button = tk.Button(
            header,
            text="×",
            command=self.close,
            bg="#273448",
            fg="#E8EEF7",
            activebackground="#344760",
            activeforeground="#FFFFFF",
            relief="flat",
            borderwidth=0,
            font=("Segoe UI", 16),
            width=2,
            takefocus=False,
        )
        close_button.pack(side="right")
        self.status_label = tk.Label(
            header,
            text="Ready",
            bg="#101722",
            fg="#A9C6B4",
            font=("Segoe UI", 9),
            anchor="e",
        )
        self.status_label.pack(side="right", padx=(0, 10))

        self.request_var = tk.StringVar()
        self.input = ttk.Entry(
            frame,
            textvariable=self.request_var,
            style="Jarvis.TEntry",
            font=("Segoe UI", 11),
            takefocus=False,
        )
        self.input.pack(fill="x", pady=(0, 11))
        self.input.insert(0, self.PLACEHOLDER_TEXT)
        self.input.configure(foreground="#9AA8BA")
        self.input.bind("<Button-1>", self._activate_stealth_typing)
        self.input.bind("<Button-3>", self._paste_from_clipboard)
        self.input.bind("<Return>", self._submit_text)

        controls = tk.Frame(frame, bg="#101722")
        controls.pack(fill="x")
        self.voice_var = tk.BooleanVar(value=False)
        self.voice_button = tk.Checkbutton(
            controls,
            text="Voice: Off",
            variable=self.voice_var,
            command=self._toggle_voice,
            bg="#273448",
            fg="#E8EEF7",
            activebackground="#344760",
            activeforeground="#FFFFFF",
            selectcolor="#224C75",
            relief="flat",
            padx=10,
            pady=6,
            font=("Segoe UI", 10),
            takefocus=False,
        )
        self.voice_button.pack(side="left")
        self.wake_var = tk.BooleanVar(value=False)
        self.wake_button = tk.Checkbutton(
            controls,
            text="Wake: Off",
            variable=self.wake_var,
            command=self._toggle_wake,
            bg="#273448",
            fg="#E8EEF7",
            activebackground="#344760",
            activeforeground="#FFFFFF",
            selectcolor="#224C75",
            relief="flat",
            padx=10,
            pady=6,
            font=("Segoe UI", 10),
            takefocus=False,
        )
        self.wake_button.pack(side="left", padx=(8, 0))
        self.send_button = ttk.Button(
            controls,
            text="Send",
            style="Jarvis.TButton",
            command=self._submit_text,
            state="disabled",
            takefocus=False,
        )
        self.send_button.pack(side="right")
        self.request_var.trace_add("write", self._update_submit_enabled)

        hint = tk.Label(
            frame,
            text="Click box to type • Ctrl+V pastes • Click Copy on HUD (or Ctrl+Shift+C)",
            bg="#101722",
            fg="#9AA8BA",
            font=("Segoe UI", 8),
            anchor="w",
        )
        hint.pack(fill="x", pady=(9, 0))

    def _activate_stealth_typing(self, _event=None):
        if self.request_running or self.voice_running or self.voice_mode:
            return "break"
        if self.request_var.get() == self.PLACEHOLDER_TEXT:
            self.request_var.set("")
        self.input.configure(foreground="#E8EEF7")
        self.key_capture.active = True
        self._set_status("Typing active (Esc to stop)")
        return "break"

    def _paste_from_clipboard(self, _event=None):
        if self.request_running or self.voice_running or self.voice_mode:
            return "break"
        try:
            clip = self.root.clipboard_get()
        except tk.TclError:
            self._set_status("Clipboard is empty")
            return "break"
        if not clip:
            return "break"
        cleaned = " ".join(clip.splitlines()).strip()
        cur = "" if self.request_var.get() == self.PLACEHOLDER_TEXT else self.request_var.get()
        self.request_var.set(cur + cleaned)
        self.input.configure(foreground="#E8EEF7")
        self.input.icursor("end")
        self.key_capture.active = True
        self._set_status("Pasted (Enter to send, Esc to stop)")
        return "break"

    def _deactivate_stealth_typing(self):
        self.key_capture.active = False
        if not self.request_var.get().strip():
            self.request_var.set(self.PLACEHOLDER_TEXT)
            self.input.configure(foreground="#9AA8BA")
        self._set_status("Ready")

    def _poll_hotkey(self):
        if os.name == "nt":
            user32 = ctypes.windll.user32
            control_down = bool(user32.GetAsyncKeyState(0x11) & 0x8000)
            space_down = bool(user32.GetAsyncKeyState(0x20) & 0x8000)
            pressed = control_down and space_down
            if pressed and not self._hotkey_down:
                self._toggle_visibility()
            self._hotkey_down = pressed
        self.root.after(80, self._poll_hotkey)

    def _toggle_visibility(self):
        if self._visible:
            self.key_capture.active = False
            self.hud.dismiss()
            self.root.withdraw()
            self._visible = False
        else:
            self._show_overlay()

    def _show_overlay(self):
        self.root.deiconify()
        self.root.attributes("-topmost", True)
        configure_stealth_overlay_window(self.root)
        self._visible = True

    def _toggle_voice(self):
        enabled = self.voice_var.get()
        if enabled and self.wake_word_enabled:
            self.voice_var.set(False)
            self._set_status("Turn off wake word before using Voice")
            return
        if not enabled:
            self.voice_mode = False
            self.voice_stop.set()
            self.voice_resume.set()
            self.voice_button.configure(text="Voice: Off")
            if self.voice_running:
                self.voice_button.configure(state="disabled")
            self._set_status("Voice listening off")
            return
        if (
            self.request_running
            or self.voice_mode
            or (self.voice_thread is not None and self.voice_thread.is_alive())
        ):
            self.voice_var.set(False)
            self._set_status("Voice listener is still stopping; try again shortly")
            return

        self.key_capture.active = False
        self.voice_mode = True
        self.voice_stop.clear()
        self.voice_resume.set()
        self.voice_button.configure(text="Voice: On")
        self.wake_button.configure(state="disabled")
        self._update_submit_enabled()
        self._set_status("Voice on; say a command")
        self.voice_thread = threading.Thread(
            target=self._voice_loop,
            daemon=True,
            name="JarvisVoice",
        )
        self.voice_thread.start()

    def _toggle_wake(self):
        if self.request_running or self.voice_running or self.voice_mode:
            self.wake_var.set(self.wake_word_enabled)
            return
        if self.wake_var.get():
            self._start_wake_listener()
        else:
            self._stop_wake_listener()

    def _start_wake_listener(self):
        if self.wake_thread is not None and self.wake_thread.is_alive():
            self.wake_word_enabled = True
            self.wake_var.set(True)
            return
        self.wake_word_enabled = True
        self.wake_stop.clear()
        self.wake_resume.set()
        self.wake_var.set(True)
        self.wake_button.configure(text="Wake: On")
        self.voice_button.configure(state="disabled")
        self._set_status("Starting wake listener...")
        self.wake_thread = threading.Thread(
            target=self._wake_word_loop,
            daemon=True,
            name="JarvisWakeWord",
        )
        self.wake_thread.start()

    def _stop_wake_listener(self):
        self.wake_word_enabled = False
        self.wake_stop.set()
        self.wake_resume.set()
        self.wake_var.set(False)
        self.wake_button.configure(text="Wake: Off", state="disabled")
        self.voice_button.configure(
            state=(
                "disabled"
                if self.request_running or self.voice_running
                else "normal"
            )
        )
        self._set_status("Wake word off")

    def _wake_word_loop(self):
        detector = None
        try:
            from stt import SpeechToText
            from wake_word import WakeWordDetector

            while not self.wake_stop.is_set():
                detector = WakeWordDetector(self.wake_word_access_key)
                self.events.put(("wake_status", "Listening for Jarvis"))
                detected = detector.wait(self.wake_stop)
                detector.close()
                detector = None
                if not detected or self.wake_stop.is_set():
                    return

                self.wake_resume.clear()
                self.events.put(("wake_detected", None))
                self.request_idle.wait()
                if self.wake_stop.is_set():
                    return
                try:
                    transcript = SpeechToText().listen_and_transcribe()
                except Exception as error:
                    logging.exception("Overlay wake-word speech capture failed")
                    self.events.put(("wake_error", str(error)))
                    self.wake_resume.set()
                    continue
                self.events.put(("wake_result", transcript))
                self.wake_resume.wait()
        except Exception as error:
            logging.exception("Overlay wake-word listener failed")
            self.events.put(("wake_error", str(error)))
        finally:
            if detector is not None:
                detector.close()
            self.wake_stop.set()
            self.events.put(("wake_stopped", None))

    def _voice_loop(self):
        try:
            from stt import SpeechToText

            speech_to_text = SpeechToText()
            while not self.voice_stop.is_set():
                self.events.put(("voice_listening", None))
                transcript = speech_to_text.listen_and_transcribe()
                self.voice_resume.clear()
                self.events.put(("voice_result", transcript))
                self.voice_resume.wait()
        except Exception as error:
            logging.exception("Overlay continuous voice capture failed")
            self.events.put(("voice_error", str(error)))
        finally:
            self.events.put(("voice_stopped", None))

    def _current_input_text(self):
        text = self.request_var.get().strip()
        return "" if text == self.PLACEHOLDER_TEXT else text

    def _submit_text(self, _event=None):
        transcript = self._current_input_text()
        if (
            not transcript
            or self.request_running
            or self.voice_running
            or self.voice_mode
        ):
            return "break"
        self.key_capture.active = False
        self.request_var.set("")
        self._start_request(transcript)
        return "break"

    def _start_request(
        self,
        transcript,
        speak_response=False,
        display_transcript=False,
    ):
        if display_transcript:
            self._display_transcript(transcript)
        self.hud.dismiss()
        self.request_feedback_shown = False
        self.request_running = True
        self.request_idle.clear()
        self.input.configure(state="disabled")
        self.send_button.configure(state="disabled")
        self.voice_button.configure(state="disabled")
        self.wake_button.configure(state="disabled")
        self._set_status("Thinking...")
        self.request_executor.submit(
            self._run_request,
            transcript,
            speak_response,
        )

    def _run_request(self, transcript, speak_response=False):
        try:
            try:
                result = run_model_request(
                    transcript,
                    self.model_client,
                    self.tool_handler,
                    confirmation_callback=self._confirm_from_worker,
                    status_callback=self._status_from_worker,
                    history=self.chat_history,
                )
            except TypeError:
                result = run_model_request(
                    transcript,
                    self.model_client,
                    self.tool_handler,
                    confirmation_callback=self._confirm_from_worker,
                    status_callback=self._status_from_worker,
                )
        except Exception as error:
            logging.exception("Overlay request failed")
            self.events.put(("request_error", str(error)))
        else:
            self.events.put(("request_result", result))
            if speak_response:
                try:
                    if self.tts is None:
                        from tts import TextToSpeech

                        self.tts = TextToSpeech()
                    self.tts.speak(result)
                except Exception as error:
                    logging.exception("Overlay speech output failed")
                    self.events.put(("speech_error", str(error)))
        finally:
            self.events.put(("request_finished", None))

    def _confirm_from_worker(self, message):
        event = threading.Event()
        result = []
        self.events.put(("confirmation", (message, event, result)))
        event.wait()
        return bool(result and result[0])

    def _status_from_worker(self, message):
        self.events.put(("status", message))

    def _poll_worker_events(self):
        while True:
            try:
                event_type, payload = self.events.get_nowait()
            except queue.Empty:
                break

            if event_type == "hook_char":
                if not self.request_running:
                    cur = "" if self.request_var.get() == self.PLACEHOLDER_TEXT else self.request_var.get()
                    self.request_var.set(cur + payload)
                    self.input.configure(foreground="#E8EEF7")
                    self.input.icursor("end")
            elif event_type == "hook_backspace":
                if not self.request_running:
                    cur = "" if self.request_var.get() == self.PLACEHOLDER_TEXT else self.request_var.get()
                    self.request_var.set(cur[:-1])
            elif event_type == "hook_paste":
                self._paste_from_clipboard()
            elif event_type == "hook_clear":
                if not self.request_running:
                    self.request_var.set("")
            elif event_type == "hook_copy_hud":
                self.hud.copy_to_clipboard()
            elif event_type == "hook_enter":
                self._submit_text()
            elif event_type == "hook_escape":
                self._deactivate_stealth_typing()
            elif event_type == "status":
                self._set_status(payload)
            elif event_type == "confirmation":
                try:
                    self._show_confirmation(*payload)
                except tk.TclError:
                    logging.exception("Could not display overlay confirmation")
                    _message, event, result = payload
                    result.append(False)
                    event.set()
                    self._set_status("Permission dialog failed; action cancelled")
            elif event_type == "voice_listening":
                if self.voice_mode:
                    self.voice_running = True
                    if not self.request_feedback_shown:
                        self._set_status("Listening for your command...")
            elif event_type == "request_result":
                self.request_feedback_shown = True
                if payload.startswith("WhatsApp message could not be sent:"):
                    self._set_status("WhatsApp failed")
                    self.hud.show(payload, auto_hide_ms=18000)
                else:
                    self._set_status("Done")
                    self.hud.show(payload, auto_hide_ms=16000)
            elif event_type == "request_error":
                self.request_feedback_shown = True
                self._set_status("Error")
                self.hud.show(f"Error: {payload}", auto_hide_ms=16000)
            elif event_type == "speech_error":
                self.request_feedback_shown = True
                self._set_status(f"Speech output failed: {payload}")
            elif event_type == "request_finished":
                self.request_running = False
                self.request_idle.set()
                self.wake_resume.set()
                self.voice_resume.set()
                self.input.configure(state="normal")
                self.voice_button.configure(
                    state="disabled" if self.wake_word_enabled else "normal"
                )
                self.wake_button.configure(
                    state="disabled" if self.voice_mode else "normal"
                )
                self.request_var.set(self.PLACEHOLDER_TEXT)
                self.input.configure(foreground="#9AA8BA")
                self._update_submit_enabled()
            elif event_type == "voice_result":
                self._handle_voice_result(payload)
            elif event_type == "voice_error":
                self.voice_mode = False
                self.voice_stop.set()
                self.voice_resume.set()
                self.voice_var.set(False)
                self.voice_button.configure(text="Voice: Off")
                self._set_status(f"Voice input failed: {payload}")
            elif event_type == "voice_stopped":
                self.voice_running = False
                self.voice_button.configure(
                    state=(
                        "disabled"
                        if self.wake_word_enabled or self.request_running
                        else "normal"
                    )
                )
                if not self.voice_mode:
                    self.voice_var.set(False)
                    self.voice_button.configure(text="Voice: Off")
                    self.wake_button.configure(
                        state="disabled" if self.request_running else "normal"
                    )
                self._update_submit_enabled()
            elif event_type == "voice_confirmation_unclear":
                self._set_status(
                    f"Heard: {payload}. Say approve or deny, or use the buttons."
                )
            elif event_type == "voice_confirmation_error":
                logging.error("Voice approval capture failed: %s", payload)
                self._set_status(
                    f"Voice approval failed: {payload}. Use the buttons."
                )
            elif event_type == "voice_confirmation_result":
                transcript, approved = payload
                self._set_status(
                    f"Heard: {transcript} — "
                    f"{'approved' if approved else 'denied'}"
                )
                if self.confirmation_finish is not None:
                    self.confirmation_finish(approved)
            elif event_type == "wake_status":
                if self.wake_word_enabled:
                    self._set_status(payload)
            elif event_type == "wake_detected":
                if self.wake_word_enabled:
                    self._show_overlay()
                    self.voice_running = True
                    self.voice_button.configure(state="disabled")
                    self.wake_button.configure(state="disabled")
                    self._set_status("Jarvis heard you; listening...")
            elif event_type == "wake_result":
                self.voice_running = False
                if payload and self.wake_word_enabled:
                    self._start_request(
                        payload,
                        speak_response=True,
                        display_transcript=True,
                    )
                else:
                    self._set_status(
                        "No speech detected" if not payload else "Wake word off"
                    )
                    self.wake_resume.set()
                    self.voice_button.configure(
                        state="disabled" if self.wake_word_enabled else "normal"
                    )
                    self.wake_button.configure(state="normal")
            elif event_type == "wake_error":
                self.voice_running = False
                self.wake_word_enabled = False
                self.wake_stop.set()
                self.wake_var.set(False)
                self.wake_button.configure(text="Wake: Off", state="normal")
                self.voice_button.configure(
                    state="disabled" if self.request_running else "normal"
                )
                self._set_status(f"Wake-word error: {payload}")
                self.wake_resume.set()
            elif event_type == "wake_stopped":
                self.wake_button.configure(state="normal")
        self.root.after(30, self._poll_worker_events)

    def _handle_voice_result(self, transcript):
        self.voice_running = False
        if not transcript:
            if not self.request_feedback_shown:
                self._set_status("No speech detected; still listening")
            self.voice_resume.set()
            return
        if not self.voice_mode:
            self._set_status("Voice input cancelled")
            self.voice_resume.set()
            return

        self._start_request(transcript, display_transcript=True)

    def _display_transcript(self, transcript):
        self.request_var.set(transcript)
        self.input.configure(foreground="#E8EEF7")

    def _show_confirmation(self, message, event, result):
        if not self._visible:
            self._show_overlay()
        dialog = tk.Toplevel(self.root)
        dialog.title("Jarvis confirmation")
        dialog.overrideredirect(True)
        dialog.attributes("-topmost", True)
        dialog.geometry("540x360+420+240")
        dialog.configure(bg="#101722")
        configure_stealth_overlay_window(dialog)
        dialog.protocol("WM_DELETE_WINDOW", lambda: finish(False))

        title = tk.Label(
            dialog,
            text="Review and approve this request.",
            bg="#101722",
            fg="#E8EEF7",
            font=("Segoe UI", 11, "bold"),
            anchor="w",
        )
        title.pack(fill="x", padx=14, pady=(14, 8))
        details_frame = tk.Frame(dialog, bg="#101722")
        details_frame.pack(fill="both", expand=True, padx=14)
        details = tk.Text(
            details_frame,
            wrap="word",
            bg="#0F1724",
            fg="#E8EEF7",
            insertbackground="#E8EEF7",
            relief="flat",
            padx=10,
            pady=10,
            font=("Consolas", 9),
        )
        scrollbar = ttk.Scrollbar(
            details_frame,
            orient="vertical",
            command=details.yview,
        )
        details.configure(yscrollcommand=scrollbar.set)
        details.insert("1.0", message)
        details.configure(state="disabled")
        details.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        details_frame.pack_propagate(False)

        buttons = tk.Frame(dialog, bg="#101722")
        buttons.pack(fill="x", padx=14, pady=14)

        def finish(approved):
            if result:
                return
            result.append(approved)
            event.set()
            if self.confirmation_finish is finish:
                self.confirmation_finish = None
            try:
                if dialog.winfo_exists():
                    dialog.destroy()
            except tk.TclError:
                logging.exception("Could not close overlay confirmation")

        self.confirmation_finish = finish
        cancel = tk.Button(
            buttons,
            text="No",
            command=lambda: finish(False),
            bg="#273448",
            fg="#E8EEF7",
            activebackground="#344760",
            relief="flat",
            padx=16,
            pady=7,
            takefocus=False,
        )
        cancel.pack(side="right", padx=(8, 0))
        approve = tk.Button(
            buttons,
            text="Yes, approve",
            command=lambda: finish(True),
            bg="#1769AA",
            fg="#FFFFFF",
            activebackground="#237FC2",
            relief="flat",
            padx=16,
            pady=7,
            takefocus=False,
        )
        approve.pack(side="right")
        if self.voice_mode or self.wake_word_enabled:
            self._set_status("Listening for spoken approval: say approve or deny")
            threading.Thread(
                target=self._listen_for_confirmation,
                args=(event,),
                daemon=True,
                name="JarvisVoiceApproval",
            ).start()

    def _listen_for_confirmation(self, event):
        try:
            from stt import SpeechToText

            speech_to_text = SpeechToText()
            while not event.is_set():
                transcript = speech_to_text.listen_and_transcribe()
                if not transcript:
                    continue
                decision = self._parse_voice_approval(transcript)
                if decision is None:
                    self.events.put(("voice_confirmation_unclear", transcript))
                    continue
                self.events.put(
                    ("voice_confirmation_result", (transcript, decision))
                )
                return
        except Exception as error:
            logging.exception("Overlay voice approval capture failed")
            self.events.put(("voice_confirmation_error", str(error)))

    @staticmethod
    def _parse_voice_approval(transcript):
        words = set(re.findall(r"[a-z]+(?:'[a-z]+)?", transcript.casefold()))
        if words.intersection(
            {"no", "not", "never", "cancel", "deny", "reject", "don't"}
        ):
            return False
        if words.intersection({"yes", "approve", "approved"}):
            return True
        return None

    def _update_submit_enabled(self, *_args):
        if self.request_running or self.voice_running or self.voice_mode:
            self.send_button.configure(state="disabled")
        else:
            text = self._current_input_text()
            if text:
                self.input.configure(foreground="#E8EEF7")
                self.send_button.configure(state="normal")
            else:
                self.send_button.configure(state="disabled")

    def _set_status(self, message):
        self.status_label.configure(
            text=message if len(message) <= 38 else f"{message[:35]}..."
        )

    def close(self):
        if self.request_running:
            self._set_status("Wait for the current task to finish")
            return
        self.key_capture.stop()
        self.hud.dismiss()
        self.voice_mode = False
        self.voice_stop.set()
        self.voice_resume.set()
        self.wake_stop.set()
        self.wake_resume.set()
        if self.voice_thread is not None:
            self.voice_thread.join(timeout=1)
        if self.wake_thread is not None:
            self.wake_thread.join(timeout=1)
        try:
            self.request_executor.submit(self.tool_handler.close).result()
        finally:
            self.request_executor.shutdown(wait=True)
            self.root.destroy()


def run_overlay(
    model_client,
    vision_model=None,
    wake_word_access_key=None,
):
    overlay = JarvisOverlay(
        model_client,
        vision_model=vision_model,
        wake_word_access_key=wake_word_access_key,
    )
    overlay.root.mainloop()
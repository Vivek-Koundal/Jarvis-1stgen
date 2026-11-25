import os
import shutil
import subprocess
import webbrowser
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus


TOOL_DEFINITIONS = [
    {
        "name": "tell_time",
        "description": "Tell the user the current local time.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "help",
        "description": "Explain the supported Jarvis actions.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "search_web",
        "description": "Open a web search for the user's query.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "open_app",
        "description": "Open an app from Jarvis's approved application list.",
        "input_schema": {
            "type": "object",
            "properties": {"app": {"type": "string"}},
            "required": ["app"],
        },
    },
    {
        "name": "set_volume",
        "description": "Set Windows master volume to an integer percentage from 0 to 100.",
        "input_schema": {
            "type": "object",
            "properties": {"level": {"type": "integer", "minimum": 0, "maximum": 100}},
            "required": ["level"],
        },
    },
    {
        "name": "set_brightness",
        "description": "Set display brightness to an integer percentage from 0 to 100.",
        "input_schema": {
            "type": "object",
            "properties": {"level": {"type": "integer", "minimum": 0, "maximum": 100}},
            "required": ["level"],
        },
    },
    {
        "name": "click",
        "description": "Click once at the given on-screen pixel coordinates.",
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "minimum": 0},
                "y": {"type": "integer", "minimum": 0},
            },
            "required": ["x", "y"],
        },
    },
    {
        "name": "type_text",
        "description": "Type the exact text into the currently focused application.",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string", "maxLength": 1000}},
            "required": ["text"],
        },
    },
    {
        "name": "press_key",
        "description": "Press one supported key in the currently focused application.",
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "enum": [
                        "enter",
                        "esc",
                        "tab",
                        "space",
                        "backspace",
                        "delete",
                        "up",
                        "down",
                        "left",
                        "right",
                        "home",
                        "end",
                    ],
                }
            },
            "required": ["key"],
        },
    },
    {
        "name": "list_files",
        "description": "List files and folders inside the configured Jarvis folder.",
        "input_schema": {
            "type": "object",
            "properties": {"directory": {"type": "string"}},
            "required": [],
        },
    },
    {
        "name": "create_file",
        "description": "Create a new text file inside the configured Jarvis folder.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "move_file",
        "description": "Move or rename a file inside the configured Jarvis folder.",
        "input_schema": {
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "destination": {"type": "string"},
            },
            "required": ["source", "destination"],
        },
    },
    {
        "name": "delete_file",
        "description": "Delete one file inside the configured Jarvis folder.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "unknown",
        "description": "Use when the user's request is not supported by another tool.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
]


class ActionHandler:
    APP_COMMANDS = {
        "notepad": "notepad.exe",
        "calculator": "calc.exe",
        "calc": "calc.exe",
        "file explorer": "explorer.exe",
        "explorer": "explorer.exe",
        "paint": "mspaint.exe",
        "chrome": "chrome.exe",
        "google chrome": "chrome.exe",
        "edge": "msedge.exe",
        "microsoft edge": "msedge.exe",
        "spotify": "Spotify.exe",
        "claude": "Claude.exe",
        "antigravity": "Antigravity.exe",
    }

    CONFIRMATION_ACTIONS = {
        "click",
        "type_text",
        "press_key",
        "create_file",
        "move_file",
        "delete_file",
    }

    UI_KEYS = {
        "enter",
        "esc",
        "tab",
        "space",
        "backspace",
        "delete",
        "up",
        "down",
        "left",
        "right",
        "home",
        "end",
    }

    def __init__(self):
        default_directory = Path.home() / "Documents" / "JarvisFiles"
        self.file_root = default_directory.expanduser().resolve()

    def requires_confirmation(self, intent):
        return intent in self.CONFIRMATION_ACTIONS

    def execute(self, intent, params):
        if intent == "tell_time":
            return f"The time is {datetime.now().strftime('%I:%M %p')}."
        if intent == "help":
            return (
                "I can tell the time, search the web, open approved apps, adjust "
                "volume or brightness, use confirmed mouse/keyboard actions, and "
                "manage files in the configured Jarvis folder. I cannot run shell commands."
            )
        if intent == "open_app":
            return self._open_app(params.get("app", ""))
        if intent == "search_web":
            return self._search_web(params.get("query", ""))
        if intent == "set_volume":
            return self._set_volume(params.get("level"))
        if intent == "set_brightness":
            return self._set_brightness(params.get("level"))
        if intent == "click":
            return self._click(params.get("x"), params.get("y"))
        if intent == "type_text":
            return self._type_text(params.get("text", ""))
        if intent == "press_key":
            return self._press_key(params.get("key", ""))
        if intent == "list_files":
            return self._list_files(params.get("directory", ""))
        if intent == "create_file":
            return self._create_file(params.get("path", ""), params.get("content", ""))
        if intent == "move_file":
            return self._move_file(
                params.get("source", ""), params.get("destination", "")
            )
        if intent == "delete_file":
            return self._delete_file(params.get("path", ""))
        if intent == "unknown":
            return "I didn't understand that. Say help to hear what I can do."
        return "That action is not supported."

    def _open_app(self, app):
        if not isinstance(app, str):
            raise ValueError("The app name must be text.")
        normalized = " ".join(app.lower().split())
        executable = self.APP_COMMANDS.get(normalized)
        if executable is None:
            return f"I can only open approved apps: {', '.join(sorted(self.APP_COMMANDS))}."

        resolved_executable = shutil.which(executable)
        if resolved_executable is None:
            resolved_executable = self._find_app_executable(normalized)
        if resolved_executable is not None:
            subprocess.Popen([resolved_executable], shell=False)
            return f"Opening {normalized}."

        shortcut = self._find_start_menu_shortcut(normalized)
        if shortcut is not None:
            start_file = getattr(os, "startfile", None)
            if start_file is None:
                raise OSError("Windows app shortcuts can only be opened on Windows.")
            start_file(str(shortcut))
            return f"Opening {normalized}."
        raise OSError(f"{normalized} was not found in the approved app locations.")

    @staticmethod
    def _find_app_executable(app):
        local_app_data = Path(
            os.environ.get(
                "LOCALAPPDATA", Path.home() / "AppData" / "Local"
            )
        )
        roaming_app_data = Path(
            os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")
        )
        program_files = [
            Path(value)
            for value in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"))
            if value
        ]
        chrome_executable = Path("Google") / "Chrome" / "Application" / "chrome.exe"
        edge_executable = Path("Microsoft") / "Edge" / "Application" / "msedge.exe"
        candidates = {
            "chrome": [
                local_app_data / chrome_executable,
                *(base / chrome_executable for base in program_files),
            ],
            "google chrome": [
                local_app_data / chrome_executable,
                *(base / chrome_executable for base in program_files),
            ],
            "edge": [
                *(base / edge_executable for base in program_files),
            ],
            "microsoft edge": [
                *(base / edge_executable for base in program_files),
            ],
            "spotify": [
                roaming_app_data / "Spotify" / "Spotify.exe",
                local_app_data / "Programs" / "Spotify" / "Spotify.exe",
            ],
            "claude": [
                local_app_data / "Programs" / "Claude" / "Claude.exe",
                local_app_data / "AnthropicClaude" / "Claude.exe",
            ],
            "antigravity": [
                local_app_data / "Programs" / "Antigravity" / "Antigravity.exe",
            ],
        }
        return next(
            (str(path) for path in candidates.get(app, []) if path.is_file()), None
        )

    @staticmethod
    def _find_start_menu_shortcut(app):
        start_menu_roots = [
            Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
            / "Microsoft"
            / "Windows"
            / "Start Menu"
            / "Programs",
            Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
            / "Microsoft"
            / "Windows"
            / "Start Menu"
            / "Programs",
        ]
        for root in start_menu_roots:
            if root.is_dir():
                for shortcut in root.rglob("*.lnk"):
                    if shortcut.stem.casefold() == app.casefold():
                        return shortcut
        return None

    @staticmethod
    def _search_web(query):
        if not isinstance(query, str):
            raise ValueError("The search query must be text.")
        query = query.strip()
        if not query:
            return "What would you like me to search for?"
        url = f"https://duckduckgo.com/?q={quote_plus(query)}"
        if not webbrowser.open(url):
            raise OSError("The browser could not be opened.")
        return f"Searching the web for {query}."

    @staticmethod
    def _validated_level(level, name):
        if (
            isinstance(level, bool)
            or not isinstance(level, int)
            or not 0 <= level <= 100
        ):
            raise ValueError(f"{name} must be an integer from 0 to 100.")
        return level

    def _set_volume(self, level):
        level = self._validated_level(level, "Volume")
        try:
            from pycaw.pycaw import AudioUtilities
        except ImportError as error:
            raise RuntimeError(
                "Volume control is not installed. Run: pip install -r requirements.txt"
            ) from error

        speakers = AudioUtilities.GetSpeakers()
        volume = getattr(speakers, "EndpointVolume", None)
        if volume is None:
            from ctypes import POINTER, cast
            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import IAudioEndpointVolume

            interface = speakers.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            volume = cast(interface, POINTER(IAudioEndpointVolume))

        volume.SetMasterVolumeLevelScalar(level / 100, None)
        return f"Volume set to {level}%."

    def _set_brightness(self, level):
        level = self._validated_level(level, "Brightness")
        try:
            import screen_brightness_control
        except ImportError as error:
            raise RuntimeError(
                "Brightness control is not installed. Run: pip install -r requirements.txt"
            ) from error
        screen_brightness_control.set_brightness(level)
        return f"Brightness set to {level}%."

    @staticmethod
    def _pyautogui():
        try:
            import pyautogui
        except ImportError as error:
            raise RuntimeError(
                "UI automation is not installed. Run: pip install -r requirements.txt"
            ) from error
        return pyautogui

    def _click(self, x, y):
        if (
            isinstance(x, bool)
            or isinstance(y, bool)
            or not isinstance(x, int)
            or not isinstance(y, int)
            or x < 0
            or y < 0
        ):
            raise ValueError("Click coordinates must be non-negative integers.")
        pyautogui = self._pyautogui()
        width, height = pyautogui.size()
        if x >= width or y >= height:
            raise ValueError(f"Coordinates must be within the {width}x{height} screen.")
        pyautogui.click(x, y)
        return f"Clicked at ({x}, {y})."

    def _type_text(self, text):
        if not isinstance(text, str) or not text:
            raise ValueError("Text to type must not be empty.")
        if len(text) > 1000:
            raise ValueError("Text to type cannot exceed 1000 characters.")
        self._pyautogui().write(text, interval=0.02)
        return "Typed the requested text."

    def _press_key(self, key):
        if not isinstance(key, str) or key.lower() not in self.UI_KEYS:
            raise ValueError(f"Supported keys are: {', '.join(sorted(self.UI_KEYS))}.")
        self._pyautogui().press(key.lower())
        return f"Pressed {key.lower()}."

    def _ensure_file_root(self):
        self.file_root.mkdir(parents=True, exist_ok=True)
        if not self.file_root.is_dir():
            raise OSError(
                f"The configured file folder is not a directory: {self.file_root}"
            )

    def _safe_file_path(self, relative_path, allow_root=False):
        if not isinstance(relative_path, str) or not relative_path.strip():
            raise ValueError("A relative file path is required.")
        path = Path(relative_path)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("File paths must stay inside the configured Jarvis folder.")
        self._ensure_file_root()
        candidate = self.file_root / path
        parent = candidate
        while parent != self.file_root:
            if parent.is_symlink():
                raise ValueError("Symbolic links are not allowed in the configured folder.")
            parent = parent.parent
        resolved = candidate.resolve()
        try:
            resolved.relative_to(self.file_root)
        except ValueError as error:
            raise ValueError(
                "File paths must stay inside the configured Jarvis folder."
            ) from error
        if resolved == self.file_root and not allow_root:
            raise ValueError("Choose a file path inside the configured Jarvis folder.")
        return resolved

    def _list_files(self, directory):
        folder = (
            self.file_root
            if directory == ""
            else self._safe_file_path(directory, allow_root=True)
        )
        if not folder.is_dir():
            raise ValueError(
                "That path is not a folder inside the configured Jarvis folder."
            )
        entries = sorted(folder.iterdir(), key=lambda entry: entry.name.casefold())
        if not entries:
            return "The configured folder is empty."
        shown = entries[:50]
        result = "\n".join(
            f"{'Folder' if entry.is_dir() else 'File'}: {entry.name}" for entry in shown
        )
        if len(entries) > len(shown):
            result += f"\nShowing {len(shown)} of {len(entries)} entries."
        return result

    def _create_file(self, relative_path, content):
        if not isinstance(content, str):
            raise ValueError("File content must be text.")
        target = self._safe_file_path(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            raise ValueError("A file already exists at that path.")
        target.write_text(content, encoding="utf-8")
        return f"Created {target.relative_to(self.file_root)}."

    def _move_file(self, source, destination):
        source_path = self._safe_file_path(source)
        destination_path = self._safe_file_path(destination)
        if source_path.is_symlink() or not source_path.is_file():
            raise ValueError(
                "The source must be a regular file inside the configured folder."
            )
        if destination_path.exists() or destination_path.is_symlink():
            raise ValueError("The destination already exists.")
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source_path), str(destination_path))
        return (
            f"Moved {source_path.relative_to(self.file_root)} to "
            f"{destination_path.relative_to(self.file_root)}."
        )

    def _delete_file(self, relative_path):
        target = self._safe_file_path(relative_path)
        if target.is_symlink() or not target.is_file():
            raise ValueError(
                "Only regular files inside the configured folder can be deleted."
            )
        target.unlink()
        return f"Deleted {target.relative_to(self.file_root)}."
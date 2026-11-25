import os
import re
from pathlib import Path


def _load_model_api_key(env_file=None):
    api_key = os.environ.get("MODEL_API_KEY")
    if api_key:
        return api_key

    env_files = (
        [Path(env_file)]
        if env_file is not None
        else [
            Path(__file__).with_name(".env"),
            Path(__file__).with_name(".env_local"),
        ]
    )
    for path in env_files:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = (part.strip() for part in line.split("=", 1))
            if name == "MODLE_API_KEY":
                raise RuntimeError(
                    f"The key in {path.name} is named MODLE_API_KEY. "
                    "Rename it to MODEL_API_KEY."
                )
            if name != "MODEL_API_KEY":
                continue
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            if value:
                return value
            raise RuntimeError(f"MODEL_API_KEY is empty in {path.name}.")
    return None


def load_env_setting(key_name, env_file=None):
    value = os.environ.get(key_name)
    if value:
        return value

    env_files = (
        [Path(env_file)]
        if env_file is not None
        else [
            Path(__file__).with_name(".env"),
            Path(__file__).with_name(".env_local"),
        ]
    )
    for path in env_files:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = (part.strip() for part in line.split("=", 1))
            if name != key_name:
                continue
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            if value:
                return value
    return None


def create_model_client(api_key=None):
    api_key = api_key or _load_model_api_key()
    if not api_key:
        raise RuntimeError(
            "Set MODEL_API_KEY in the environment, .env, or .env_local before using --llm."
        )
    try:
        from openai import OpenAI
    except ImportError as error:
        raise RuntimeError(
            "Meta API support is not installed. Run: pip install -r requirements.txt"
        ) from error
    return OpenAI(
        api_key=api_key,
        base_url=os.environ.get("MODEL_API_BASE_URL", "https://api.meta.ai/v1"),
    )


class IntentParser:
    def parse(self, transcript):
        return self._parse_locally(transcript)

    @staticmethod
    def _parse_locally(transcript):
        text = transcript.strip().lower()
        if text in {"help", "what can you do", "what can you do?"}:
            return "help", {}
        if re.search(r"\b(time|clock)\b", text):
            return "tell_time", {}

        for action, keyword in (
            ("set_volume", "volume"),
            ("set_brightness", "brightness"),
        ):
            if keyword in text:
                level_match = re.search(r"\b(\d{1,3})\s*%?\b", text)
                if level_match:
                    return action, {"level": int(level_match.group(1))}

        search_match = re.match(
            r"^(?:search(?: the web)? for|look up|google)\s+(.+)$", text
        )
        if search_match:
            return "search_web", {"query": search_match.group(1).strip(" .?!")}

        click_match = re.match(r"^click\s+(?:at\s+)?(\d+)\s*[, ]\s*(\d+)$", text)
        if click_match:
            return "click", {
                "x": int(click_match.group(1)),
                "y": int(click_match.group(2)),
            }

        type_match = re.match(r"^type\s+(.+)$", transcript.strip(), re.IGNORECASE)
        if type_match:
            return "type_text", {"text": type_match.group(1)}

        key_match = re.match(r"^press\s+(\w+)$", text)
        if key_match:
            return "press_key", {"key": key_match.group(1)}

        if text in {"list files", "show files"}:
            return "list_files", {"directory": ""}

        create_match = re.match(
            r"^create file\s+(.+?)\s+with content\s+([\s\S]*)$",
            transcript.strip(),
            re.IGNORECASE,
        )
        if create_match:
            return "create_file", {
                "path": create_match.group(1),
                "content": create_match.group(2),
            }

        move_match = re.match(
            r"^move file\s+(.+?)\s+to\s+(.+)$", transcript.strip(), re.IGNORECASE
        )
        if move_match:
            return "move_file", {
                "source": move_match.group(1),
                "destination": move_match.group(2),
            }

        delete_match = re.match(
            r"^delete file\s+(.+)$", transcript.strip(), re.IGNORECASE
        )
        if delete_match:
            return "delete_file", {"path": delete_match.group(1)}

        app_match = re.match(r"^(?:open|launch|start)\s+(.+)$", text)
        if app_match:
            return "open_app", {"app": app_match.group(1).strip(" .?!")}
        return "unknown", {}

import json
import logging
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import quote_plus, urlparse
from urllib.request import Request, urlopen

from actions import ActionHandler

CHROME_EXE = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
CHROME_USER_DATA = r"C:\Users\Vivek Koundal\AppData\Local\Google\Chrome\User Data"
CHROME_PROFILE = "Default"


def open_chrome_with_profile(url=None):
    chrome_bin = CHROME_EXE if os.path.exists(CHROME_EXE) else (shutil.which("chrome") or "chrome")
    cmd = [
        chrome_bin,
        f"--user-data-dir={CHROME_USER_DATA}",
        f"--profile-directory={CHROME_PROFILE}",
    ]
    if url:
        cmd.append(url)
    subprocess.Popen(cmd)


def _find_first_youtube_video_url(query):
    search_url = f"https://www.youtube.com/results?search_query={quote_plus(query.strip())}"
    req = Request(
        search_url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    try:
        with urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        match = re.search(r"var ytInitialData = (\{.*?\});</script>", html)
        if match:
            data = json.loads(match.group(1))
            contents = (
                data.get("contents", {})
                .get("twoColumnSearchResultsRenderer", {})
                .get("primaryContents", {})
                .get("sectionListRenderer", {})
                .get("contents", [])
            )
            for section in contents:
                items = section.get("itemSectionRenderer", {}).get("contents", [])
                for item in items:
                    video = item.get("videoRenderer")
                    if video and video.get("videoId"):
                        title_runs = video.get("title", {}).get("runs", [])
                        title = title_runs[0].get("text", query.strip()) if title_runs else query.strip()
                        return f"https://www.youtube.com/watch?v={video['videoId']}", title
        vid_match = re.search(r'"videoRenderer":\{"videoId":"([a-zA-Z0-9_-]{11})"', html)
        if vid_match:
            return f"https://www.youtube.com/watch?v={vid_match.group(1)}", query.strip()
    except Exception as error:
        logging.warning("Direct YouTube lookup fell back to search URL: %s", error)
    return search_url, query.strip()


TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "open_application",
            "description": (
                "Open an approved app: Notepad, Calculator, File Explorer, Paint, "
                "Chrome, Edge, Spotify, Claude, or Antigravity."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {
                        "type": "string",
                        "description": "The name of an approved application.",
                    }
                },
                "required": ["app_name"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_files",
            "description": (
                "Create, read, or move a file strictly inside Documents or Desktop. "
                "Paths may be relative to Documents or start with Documents\\ or Desktop\\."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["create", "read", "move"],
                    },
                    "path": {
                        "type": "string",
                        "description": (
                            "A relative path under Documents, Desktop, or an absolute "
                            "path inside either folder."
                        ),
                    },
                    "destination": {
                        "type": "string",
                        "description": "Required for move; must be inside Documents or Desktop.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Required for create; text to write.",
                    },
                },
                "required": ["action", "path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "system_control",
            "description": (
                "Set volume or brightness, or check battery or CPU status."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "set_volume",
                            "set_brightness",
                            "battery",
                            "cpu",
                        ],
                    },
                    "value": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 100,
                        "description": "Required for volume and brightness percentages.",
                    },
                },
                "required": ["action"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "play_youtube_video",
            "description": (
                "Use this whenever the user asks to play a video or song on YouTube. "
                "Opens the first non-ad result directly in the user's signed-in Chrome profile "
                "(or in Microsoft Edge if browser='msedge' is requested), unpauses extension pauses, "
                "and monitors for Skip Ad buttons."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "browser": {
                        "type": "string",
                        "enum": ["chrome", "msedge"],
                        "default": "chrome",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_whatsapp_message",
            "description": (
                "Find one exact WhatsApp chat, fill the requested message, show the "
                "recipient and message for review, and send only after explicit user "
                "approval. In the overlay, use its approval dialog; in terminal mode, "
                "require the user to type y."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "contact_name": {"type": "string", "maxLength": 256},
                    "message": {"type": "string", "maxLength": 4096},
                },
                "required": ["contact_name", "message"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "control_screen",
            "description": (
                "For a task that requires seeing or interacting with the current "
                "desktop, request explicit consent to send a screenshot to the "
                "configured vision model, propose bounded mouse/keyboard actions, "
                "and execute them only after the user approves the action plan."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_description": {
                        "type": "string",
                        "description": "The visual task to perform on the screen.",
                    }
                },
                "required": ["task_description"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tell_time",
            "description": "Tell the user the current local time.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Open a web search for the user's query.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "help",
            "description": "Explain the supported Jarvis actions.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        },
    },
]


class BrowserAutomation:
    def __init__(self, confirmation_callback=None):
        self.confirmation_callback = confirmation_callback
        local_app_data = Path(
            os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
        )
        self.user_data_dir = Path(
            os.environ.get(
                "JARVIS_CHROME_USER_DATA",
                local_app_data / "Google" / "Chrome" / "User Data",
            )
        ).expanduser().resolve()
        self._playwright = None
        self._context = None
        self._playwright_error = None

    @staticmethod
    def _chrome_is_running():
        try:
            import psutil
        except ImportError as error:
            raise RuntimeError(
                "Chrome process detection requires psutil. Run: pip install -r requirements.txt"
            ) from error

        for process in psutil.process_iter(["name"]):
            try:
                name = process.info["name"] or ""
            except psutil.Error:
                continue
            if name.casefold() == "chrome.exe":
                return True
        return False

    def _ensure_context(self):
        if self._context is not None:
            return self._context
        if self._chrome_is_running():
            raise RuntimeError(
                "Chrome is already running. Close all Chrome windows before Jarvis "
                "uses your default Chrome profile; Jarvis will not close Chrome for you."
            )
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            raise RuntimeError(
                "Browser automation is not installed. Run: "
                "python -m pip install -r requirements.txt"
            ) from error

        self._playwright_error = PlaywrightError
        try:
            self._playwright = sync_playwright().start()
            self._context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.user_data_dir),
                channel="chrome",
                headless=False,
                no_viewport=True,
                args=[f"--profile-directory={CHROME_PROFILE}"],
            )
        except PlaywrightError as error:
            if self._playwright is not None:
                try:
                    self._playwright.stop()
                except PlaywrightError as cleanup_error:
                    logging.warning(
                        "Could not stop Playwright after launch failure: %s",
                        cleanup_error,
                    )
                self._playwright = None
            raise RuntimeError(
                "Could not launch Chrome with the persistent profile. Make sure "
                "Google Chrome is installed and all Chrome windows are closed."
            ) from error
        return self._context

    def _page_for(self, url):
        context = self._ensure_context()
        host = urlparse(url).hostname
        for page in context.pages:
            if urlparse(page.url).hostname == host:
                page.goto(url, wait_until="domcontentloaded", timeout=20_000)
                return page
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=20_000)
        return page

    def open_chrome(self):
        open_chrome_with_profile()
        return f"Chrome is open with your signed-in profile ({CHROME_PROFILE})."

    def play_youtube_in_chrome(self, query):
        if not isinstance(query, str) or not query.strip():
            raise ValueError("Provide a YouTube video or song to play.")
        video_url, title = _find_first_youtube_video_url(query)
        open_chrome_with_profile(video_url)

        threading.Thread(
            target=self._watch_and_stabilize_youtube,
            args=(25,),
            daemon=True,
            name="JarvisYouTubeWatcher",
        ).start()

        return f"Playing in Chrome ({CHROME_PROFILE}): {title} (monitoring playback & ads for 25s)"

    @staticmethod
    def _watch_and_stabilize_youtube(watch_seconds=25):
        """Monitors YouTube for ~25s after opening: unpauses extension pauses and clicks Skip Ad."""
        try:
            import pyautogui
        except ImportError:
            logging.warning("pyautogui not installed; skipping YouTube playback watcher.")
            return

        skip_img = Path(__file__).resolve().parent / "skip_ad.png"

        # Wait for Chrome tab and YouTube player to load
        time.sleep(4.5)

        # Click center-left of screen once to focus the YouTube player, then press 'k' to unpause
        screen_w, screen_h = pyautogui.size()
        pyautogui.click(int(screen_w * 0.45), int(screen_h * 0.45))
        time.sleep(0.4)
        pyautogui.press("k")

        # Watch for up to `watch_seconds` to click "Skip" button if an ad plays
        deadline = time.time() + watch_seconds
        unpaused_second_time = False
        while time.time() < deadline:
            if skip_img.exists():
                try:
                    btn = pyautogui.locateCenterOnScreen(
                        str(skip_img),
                        confidence=0.8,
                        grayscale=True,
                    )
                    if btn is not None:
                        pyautogui.click(btn)
                        time.sleep(1.0)
                        if not unpaused_second_time:
                            pyautogui.press("k")
                            unpaused_second_time = True
                except Exception as error:
                    logging.debug("Skip ad check error: %s", error)
            time.sleep(1.2)

    def send_whatsapp_message(self, contact_name, message):
        if not isinstance(contact_name, str) or not contact_name.strip():
            raise ValueError("Provide the exact WhatsApp contact name.")
        if not isinstance(message, str) or not message.strip():
            raise ValueError("The WhatsApp message cannot be empty.")
        if len(message) > 4096:
            raise ValueError("WhatsApp messages are limited to 4096 characters here.")

        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        except ImportError as error:
            raise RuntimeError(
                "Browser automation is not installed. Run: "
                "python -m pip install -r requirements.txt"
            ) from error

        try:
            contact_name = contact_name.strip()
            page = self._page_for("https://web.whatsapp.com/")
            page.locator("#pane-side").wait_for(state="visible", timeout=30_000)
            search = page.locator(
                '#side [contenteditable="true"][data-tab="3"]'
            ).first
            search.wait_for(state="visible", timeout=10_000)
            search.fill(contact_name)

            page.wait_for_function(
                "(name) => [...document.querySelectorAll("
                "'#pane-side span[title]')].some((el) => "
                "(el.getAttribute('title') || '').toLocaleLowerCase() === "
                "name.toLocaleLowerCase())",
                contact_name,
                timeout=10_000,
            )
            titled_chats = page.locator("#pane-side span[title]")
            matches = []
            for index in range(titled_chats.count()):
                candidate = titled_chats.nth(index)
                title = candidate.get_attribute("title") or ""
                if title.casefold() == contact_name.casefold():
                    matches.append((candidate, title))
            if len(matches) != 1:
                raise RuntimeError(
                    f"Found {len(matches)} exact WhatsApp chats named {contact_name!r}; "
                    "no message was sent. Use a more specific contact name."
                )
            chat, resolved_contact = matches[0]
            chat.click()

            composer = page.locator(
                '#main footer [contenteditable="true"]'
            ).first
            composer.wait_for(state="visible", timeout=10_000)
            composer.fill(message)
            preview = (
                f"Contact: {resolved_contact}\n"
                f"Message: {message}"
            )
            if self.confirmation_callback is not None:
                approved = bool(
                    self.confirmation_callback(
                        f"Send this exact WhatsApp message?\n{preview}"
                    )
                )
            else:
                print(f"\nWhatsApp message preview\n{preview}")
                try:
                    approved = (
                        input("Send this exact WhatsApp message? [y/N]: ")
                        .strip()
                        .casefold()
                        == "y"
                    )
                except (EOFError, KeyboardInterrupt):
                    approved = False
            if not approved:
                composer.fill("")
                return f"Message to {resolved_contact} cancelled; nothing was sent."

            composer.press("Enter")
            try:
                page.wait_for_function(
                    "() => { const c = document.querySelector("
                    "'#main footer [contenteditable=true]'); "
                    "return c && c.innerText.trim() === ''; }",
                    timeout=8_000,
                )
            except PlaywrightTimeoutError:
                return (
                    f"WhatsApp did not confirm the message was sent to {resolved_contact}. "
                    "Check the open chat before trying again."
                )
            return f"WhatsApp message sent to {resolved_contact}. Chrome remains open."
        except PlaywrightTimeoutError as error:
            raise RuntimeError(
                "WhatsApp Web did not finish loading or the requested chat was not "
                "found. Log in to WhatsApp Web in the open Chrome profile and retry."
            ) from error
        except PlaywrightError as error:
            raise RuntimeError(f"WhatsApp browser automation failed: {error}") from error

    def close(self):
        if self._context is not None:
            try:
                self._context.close()
            except self._playwright_error as error:
                logging.warning("Could not close the Jarvis Chrome context: %s", error)
            self._context = None
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except self._playwright_error as error:
                logging.warning("Could not stop Playwright: %s", error)
            self._playwright = None


class EdgeBrowserAutomation:
    DEBUGGING_PORT = 9222

    def __init__(self):
        self.user_data_dir = Path(__file__).resolve().parent / "jarvis_edge_data"
        self._playwright = None
        self._browser = None
        self._context = None
        self._edge_process = None
        self._playwright_error = None
        logging.info(
            "Edge automation loaded from %s; profile=%s",
            Path(__file__).resolve(),
            self.user_data_dir,
        )

    def _ensure_context(self, browser):
        if browser != "msedge":
            raise ValueError("The YouTube browser must be 'msedge'.")
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            raise RuntimeError(
                "Browser automation is not installed. Run: "
                "python -m pip install -r requirements.txt"
            ) from error

        self._playwright_error = PlaywrightError
        if self._context is not None:
            if self._live_context_pages(self._context) is not None:
                return self._context
            self._discard_context()

        try:
            self._playwright = sync_playwright().start()
            self._browser = self._launch_or_connect_edge(self._playwright)
            contexts = []
            for _ in range(25):
                contexts = self._browser.contexts
                if contexts:
                    break
                time.sleep(0.2)
            if not contexts:
                raise RuntimeError(
                    "Microsoft Edge started, but its default browser context "
                    "did not become available."
                )
            self._context = contexts[0]
        except (OSError, PlaywrightError, RuntimeError) as error:
            if self._playwright is not None:
                try:
                    self._playwright.stop()
                except PlaywrightError as cleanup_error:
                    logging.warning(
                        "Could not stop Playwright after Edge startup failure: %s",
                        cleanup_error,
                    )
                self._playwright = None
            self._browser = None
            if isinstance(error, RuntimeError):
                raise
            raise RuntimeError(
                "Could not launch or connect to Microsoft Edge using Jarvis's "
                "local profile."
            ) from error
        return self._context

    def open_edge(self):
        try:
            self._ensure_context("msedge")
            return "Microsoft Edge is open with Jarvis's local profile."
        finally:
            self._discard_context()

    def _port_is_open(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
            connection.settimeout(0.25)
            return (
                connection.connect_ex(("127.0.0.1", self.DEBUGGING_PORT)) == 0
            )

    def _launch_edge(self):
        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        edge_paths = [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ]
        edge_exe = next(
            (path for path in edge_paths if os.path.exists(path)),
            shutil.which("msedge") or "msedge",
        )
        self._edge_process = subprocess.Popen(
            [
                edge_exe,
                f"--remote-debugging-port={self.DEBUGGING_PORT}",
                "--remote-debugging-address=127.0.0.1",
                f"--user-data-dir={self.user_data_dir}",
                "--enable-automation",
                "--no-first-run",
                "--no-default-browser-check",
                "--new-window",
                "about:blank",
            ]
        )

        for _ in range(30):
            if self._port_is_open():
                return
            if self._edge_process.poll() is not None:
                raise RuntimeError(
                    "Microsoft Edge exited before its local debugging endpoint "
                    "became ready."
                )
            time.sleep(0.2)
        raise RuntimeError(
            "Microsoft Edge did not open Jarvis's local debugging endpoint."
        )

    def _verify_edge_endpoint(self, browser):
        session = browser.new_browser_cdp_session()
        try:
            response = session.send("Browser.getBrowserCommandLine")
        except self._playwright_error as error:
            raise RuntimeError(
                "Port 9222 is open, but Jarvis could not verify that it belongs "
                "to Jarvis's Edge profile; refusing to attach."
            ) from error
        finally:
            try:
                session.detach()
            except self._playwright_error as error:
                logging.debug("Could not detach the Edge verification session: %s", error)

        arguments = response.get("arguments", [])
        if not isinstance(arguments, list):
            arguments = []
        expected_profile = os.path.normcase(
            os.path.abspath(str(self.user_data_dir))
        )
        actual_profile = None
        for index, argument in enumerate(arguments):
            if argument.startswith("--user-data-dir="):
                actual_profile = argument.split("=", 1)[1]
                break
            if (
                argument == "--user-data-dir"
                and index + 1 < len(arguments)
            ):
                actual_profile = arguments[index + 1]
                break
        if actual_profile is None or os.path.normcase(
            os.path.abspath(actual_profile)
        ) != expected_profile:
            raise RuntimeError(
                "Port 9222 belongs to a different Edge profile; refusing to attach."
            )
        if (
            f"--remote-debugging-port={self.DEBUGGING_PORT}" not in arguments
            or "--remote-debugging-address=127.0.0.1" not in arguments
        ):
            raise RuntimeError(
                "Port 9222 is not bound to Jarvis's expected local Edge endpoint."
            )

    def _launch_or_connect_edge(self, playwright):
        if not self._port_is_open():
            self._launch_edge()
        endpoint = f"http://127.0.0.1:{self.DEBUGGING_PORT}"
        browser = playwright.chromium.connect_over_cdp(endpoint, timeout=10_000)
        self._verify_edge_endpoint(browser)
        return browser

    def _live_context_pages(self, context):
        try:
            pages = context.pages
            page_count = len(pages)
            browser = context.browser
            if browser is not None and not browser.is_connected():
                return None
            if context.is_closed():
                return None
            logging.debug("Managed Edge context is open with %d page(s).", page_count)
            return pages
        except self._playwright_error as error:
            logging.debug("Managed Edge context is no longer available: %s", error)
            return None

    def _discard_context(self):
        self._context = None
        self._browser = None
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except self._playwright_error as error:
                logging.warning("Could not stop the stale Edge Playwright instance: %s", error)
            self._playwright = None

    def _page_for(self, url, browser):
        host = urlparse(url).hostname
        from playwright.sync_api import Error as PlaywrightError

        for attempt in range(2):
            context = self._ensure_context(browser)
            creating_page = False
            try:
                pages = self._live_context_pages(context)
                if pages == []:
                    for _ in range(15):
                        time.sleep(0.2)
                        pages = self._live_context_pages(context)
                        if pages is None or pages:
                            break
                if pages is None:
                    self._discard_context()
                    if attempt == 0:
                        continue
                    raise RuntimeError(
                        "Microsoft Edge closed again after its managed context "
                        "was restarted. Check assistant.log for the loaded source "
                        "path and browser recovery details."
                    )
                reusable_page = None
                for page in pages:
                    if page.is_closed():
                        continue
                    if urlparse(page.url).hostname == host:
                        page.goto(url, wait_until="domcontentloaded", timeout=20_000)
                        return page
                    if reusable_page is None:
                        reusable_page = page
                if reusable_page is not None:
                    logging.debug(
                        "Reusing an existing Edge tab for YouTube instead of "
                        "creating another tab."
                    )
                    reusable_page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=20_000,
                    )
                    return reusable_page
                creating_page = True
                if self._browser is None:
                    raise RuntimeError(
                        "The Microsoft Edge CDP connection is unavailable."
                    )
                cdp = self._browser.new_browser_cdp_session()
                try:
                    cdp.send("Target.createTarget", {"url": url})
                finally:
                    try:
                        cdp.detach()
                    except PlaywrightError as error:
                        logging.debug(
                            "Could not detach the Edge target-creation session: %s",
                            error,
                        )

                for _ in range(25):
                    time.sleep(0.2)
                    pages = self._live_context_pages(context)
                    if pages is None:
                        break
                    for page in reversed(pages):
                        if page.is_closed():
                            continue
                        page_host = urlparse(page.url).hostname or ""
                        if not (
                            page_host == "youtube.com"
                            or page_host.endswith(".youtube.com")
                        ):
                            continue
                        page.wait_for_load_state(
                            "domcontentloaded",
                            timeout=20_000,
                        )
                        return page
                raise RuntimeError(
                    "Edge opened the YouTube tab, but Playwright could not attach to it."
                )
            except PlaywrightError as error:
                context_closed = self._live_context_pages(context) is None
                logging.warning(
                    "Edge page operation failed on attempt %d/2 "
                    "(creating_page=%s, context_closed=%s): %s",
                    attempt + 1,
                    creating_page,
                    context_closed,
                    error,
                )
                self._discard_context()
                if attempt == 0:
                    continue
                raise RuntimeError(
                    "Microsoft Edge could not create or navigate a tab after "
                    f"restarting its managed context: {error}"
                ) from error
        raise RuntimeError("Could not open a YouTube tab in Microsoft Edge.")

    def play_youtube_video(self, query, browser="msedge"):
        if not isinstance(query, str) or not query.strip():
            raise ValueError("Provide a YouTube video or song to play.")
        if browser != "msedge":
            raise ValueError("The YouTube browser must be 'msedge'.")
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        except ImportError as error:
            raise RuntimeError(
                "Browser automation is not installed. Run: "
                "python -m pip install -r requirements.txt"
            ) from error

        try:
            url = (
                "https://www.youtube.com/results?search_query="
                f"{quote_plus(query.strip())}"
            )
            page = self._page_for(url, browser)
            results = page.locator("ytd-video-renderer a#video-title")
            results.first.wait_for(state="visible", timeout=15_000)

            first_result = None
            for index in range(results.count()):
                candidate = results.nth(index)
                is_non_ad = candidate.evaluate(
                    """element => {
                        const renderer = element.closest('ytd-video-renderer');
                        if (!renderer || renderer.hasAttribute('is-ad') ||
                            renderer.hasAttribute('data-ad-id')) {
                            return false;
                        }
                        const badges = renderer.querySelectorAll(
                            'ytd-badge-supported-renderer, #ad-badge, .badge-style-type-ad'
                        );
                        return !Array.from(badges).some((badge) => {
                            const text = (
                                badge.innerText ||
                                badge.getAttribute('aria-label') ||
                                ''
                            ).trim().toLowerCase();
                            return ['ad', 'sponsored'].some(
                                (label) =>
                                    text === label ||
                                    text.startsWith(`${label}:`) ||
                                    text.startsWith(`${label} `)
                            );
                        });
                    }"""
                )
                if is_non_ad:
                    first_result = candidate
                    break
            if first_result is None:
                raise RuntimeError("No non-ad YouTube video result was found.")

            title = first_result.get_attribute("title") or query.strip()
            first_result.click(timeout=10_000)
            video = page.locator("video").first
            video.wait_for(state="visible", timeout=20_000)
            try:
                page.wait_for_function(
                    "() => { const v = document.querySelector('video'); "
                    "return v && !v.paused && v.currentTime > 0; }",
                    timeout=5_000,
                )
            except PlaywrightTimeoutError:
                video.click(timeout=5_000)
                page.wait_for_function(
                    "() => { const v = document.querySelector('video'); "
                    "return v && !v.paused && v.currentTime > 0; }",
                    timeout=10_000,
                )
            return f"Playing the first non-ad YouTube result: {title}. Edge remains open."
        except PlaywrightTimeoutError as error:
            raise RuntimeError(
                "YouTube did not load a playable non-ad result. The Edge window is "
                "still open so you can select or play a result manually."
            ) from error
        except PlaywrightError as error:
            logging.exception("YouTube Edge automation failed")
            if self._context is not None:
                try:
                    context_closed = self._context.is_closed()
                except PlaywrightError:
                    context_closed = True
                if context_closed:
                    self._discard_context()
                    raise RuntimeError(
                        "Microsoft Edge closed while Jarvis was opening YouTube. "
                        "Keep the Edge window open and retry."
                    ) from error
            raise RuntimeError(f"YouTube Edge automation failed: {error}") from error
        finally:
            self._discard_context()

    def close(self):
        self._discard_context()


class ToolHandler:
    CONFIRMATION_TOOLS = {"manage_files"}
    MAX_FILE_BYTES = 1_000_000
    MAX_READ_BYTES = 100_000

    def __init__(
        self,
        actions=None,
        model_client=None,
        confirmation_callback=None,
        status_callback=None,
        vision_model=None,
    ):
        self.actions = actions or ActionHandler()
        self.model_client = model_client
        self.confirmation_callback = confirmation_callback
        self.status_callback = status_callback
        self.vision_model = vision_model
        self.browser = None
        self.edge_browser = None
        self.roots = {
            "documents": (Path.home() / "Documents").resolve(),
            "desktop": (Path.home() / "Desktop").resolve(),
        }

    def requires_confirmation(self, tool_name, arguments):
        return isinstance(tool_name, str) and tool_name in self.CONFIRMATION_TOOLS

    def execute(self, tool_name, arguments):
        if not isinstance(tool_name, str) or not isinstance(arguments, dict):
            raise ValueError("A valid tool name and argument object are required.")
        if tool_name == "open_application":
            app_name = arguments.get("app_name", "")
            if isinstance(app_name, str) and app_name.casefold() in {
                "chrome",
                "google chrome",
            }:
                return self._browser().open_chrome()
            if isinstance(app_name, str) and app_name.strip().casefold() in {
                "edge",
                "microsoft edge",
            }:
                return self._edge_browser().open_edge()
            return self.actions.execute(
                "open_app", {"app": app_name}
            )
        if tool_name == "manage_files":
            return self._manage_files(
                arguments.get("action", ""),
                arguments.get("path", ""),
                arguments.get("content"),
                arguments.get("destination"),
            )
        if tool_name == "system_control":
            return self._system_control(
                arguments.get("action", ""), arguments.get("value")
            )
        if tool_name == "play_youtube_video":
            browser = arguments.get("browser", "chrome")
            if browser == "msedge":
                return self._edge_browser().play_youtube_video(
                    arguments.get("query", ""),
                    browser="msedge",
                )
            return self._browser().play_youtube_in_chrome(
                arguments.get("query", ""),
            )
        if tool_name == "send_whatsapp_message":
            return self._browser().send_whatsapp_message(
                arguments.get("contact_name", ""),
                arguments.get("message", ""),
            )
        if tool_name == "control_screen":
            from screen_agent import control_screen

            return control_screen(
                arguments.get("task_description", ""),
                self.model_client,
                confirmation_callback=self._confirm_screen_action,
                status_callback=self.status_callback,
                vision_model=self.vision_model,
            )
        if tool_name in {"tell_time", "help", "search_web"}:
            return self.actions.execute(tool_name, arguments)
        raise ValueError(f"Unsupported tool: {tool_name}")

    def _browser(self):
        if self.browser is None:
            self.browser = BrowserAutomation(
                confirmation_callback=self.confirmation_callback
            )
        return self.browser

    def _edge_browser(self):
        if self.edge_browser is None:
            self.edge_browser = EdgeBrowserAutomation()
        return self.edge_browser

    def _confirm_screen_action(self, message):
        if self.confirmation_callback is not None:
            return bool(self.confirmation_callback(message))
        try:
            answer = input(f"{message}\nApprove? [y/N]: ")
        except (EOFError, KeyboardInterrupt):
            return False
        return answer.strip().casefold() == "y"

    def close(self):
        if self.browser is not None:
            self.browser.close()
        if self.edge_browser is not None:
            self.edge_browser.close()

    def _system_control(self, action, value):
        if not isinstance(action, str):
            raise ValueError("System control action must be text.")
        if action == "set_volume":
            return self.actions.execute("set_volume", {"level": value})
        if action == "set_brightness":
            return self.actions.execute("set_brightness", {"level": value})
        if action in {"battery", "cpu"}:
            try:
                import psutil
            except ImportError as error:
                raise RuntimeError(
                    "Status checks require psutil. Run: pip install -r requirements.txt"
                ) from error
            if action == "battery":
                battery = psutil.sensors_battery()
                if battery is None:
                    return "Battery status is unavailable on this device."
                return (
                    f"Battery is {battery.percent:.0f}% "
                    f"({'plugged in' if battery.power_plugged else 'on battery'})."
                )
            return f"CPU usage is {psutil.cpu_percent(interval=0.25):.0f}%."
        raise ValueError("Choose set_volume, set_brightness, battery, or cpu.")

    def _resolve_path(self, value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("A file path is required.")

        path = Path(value).expanduser()
        root = None
        if path.is_absolute():
            candidate = path
            for allowed_root in self.roots.values():
                try:
                    relative = candidate.relative_to(allowed_root)
                    root = allowed_root
                    break
                except ValueError:
                    continue
            if root is None or ".." in relative.parts:
                raise ValueError("File paths must stay inside Documents or Desktop.")
            if not relative.parts:
                raise ValueError("Choose a file inside Documents or Desktop.")
        else:
            parts = path.parts
            prefix = parts[0].casefold() if parts else ""
            if prefix in self.roots:
                root = self.roots[prefix]
                parts = parts[1:]
            else:
                root = self.roots["documents"]
            if not parts:
                raise ValueError("Choose a file inside Documents or Desktop.")
            candidate = root.joinpath(*parts)
            if ".." in parts:
                raise ValueError("File paths must stay inside Documents or Desktop.")

        if root is None:
            raise ValueError("File paths must stay inside Documents or Desktop.")
        cursor = candidate
        while cursor != root:
            if cursor.is_symlink():
                raise ValueError("Symbolic links are not allowed for file actions.")
            cursor = cursor.parent
        resolved = candidate.resolve()
        try:
            resolved.relative_to(root)
        except ValueError as error:
            raise ValueError("File paths must stay inside Documents or Desktop.") from error
        return resolved, root

    def _manage_files(self, action, path, content=None, destination=None):
        if not isinstance(action, str) or action not in {"create", "read", "move"}:
            raise ValueError("Choose create, read, or move for file actions.")
        target, source_root = self._resolve_path(path)

        if action == "read":
            if target.is_symlink() or not target.is_file():
                raise ValueError("The requested path is not a regular file.")
            if target.stat().st_size > self.MAX_READ_BYTES:
                raise ValueError(
                    f"Files larger than {self.MAX_READ_BYTES} bytes cannot be read."
                )
            return target.read_text(encoding="utf-8")

        if action == "create":
            if not isinstance(content, str):
                raise ValueError("Text content is required to create a file.")
            if len(content.encode("utf-8")) > self.MAX_FILE_BYTES:
                raise ValueError(
                    f"File content cannot exceed {self.MAX_FILE_BYTES} bytes."
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() or target.is_symlink():
                raise ValueError("A file already exists at that path.")
            target.write_text(content, encoding="utf-8")
            return f"Created {source_root.name}\\{target.relative_to(source_root)}."

        destination_path, destination_root = self._resolve_path(destination)
        if target.is_symlink() or not target.is_file():
            raise ValueError("The source must be a regular file.")
        if destination_path.exists() or destination_path.is_symlink():
            raise ValueError("The destination already exists.")
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(target), str(destination_path))
        return (
            f"Moved {source_root.name}\\{target.relative_to(source_root)} to "
            f"{destination_root.name}\\{destination_path.relative_to(destination_root)}."
        )
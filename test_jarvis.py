import json
import os
import queue
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from actions import ActionHandler
from intent import (
    IntentParser,
    _load_model_api_key,
    create_model_client,
    load_env_setting,
)
from main import handle_request, main, run_model_request, run_voice_assistant
from overlay_ui import JarvisOverlay
from screen_agent import _execute_actions, _validate_plan, control_screen
from tools import (
    BrowserAutomation,
    EdgeBrowserAutomation,
    TOOL_DEFINITIONS,
    ToolHandler,
)
from voice import confirm, listen
from wake_word import WakeWordDetector


class IntentParserTests(unittest.TestCase):
    def setUp(self):
        self.parser = IntentParser()

    def _configure_fake_cdp_browser(self, edge, playwright, context):
        browser = Mock()
        browser.contexts = [context]
        cdp_session = Mock()
        cdp_session.send.return_value = {
            "arguments": [
                f"--user-data-dir={edge.user_data_dir}",
                "--remote-debugging-port=9222",
                "--remote-debugging-address=127.0.0.1",
            ]
        }
        browser.new_browser_cdp_session.return_value = cdp_session
        playwright.chromium.connect_over_cdp.return_value = browser
        return browser

    def test_parses_supported_local_requests(self):
        self.assertEqual(self.parser.parse("What time is it?"), ("tell_time", {}))
        self.assertEqual(
            self.parser.parse("search the web for weather"),
            ("search_web", {"query": "weather"}),
        )
        self.assertEqual(
            self.parser.parse("open Notepad"),
            ("open_app", {"app": "notepad"}),
        )
        self.assertEqual(self.parser.parse("do something else"), ("unknown", {}))

    def test_voice_confirmation_requires_clear_affirmative_without_negation(self):
        with patch("voice.speak"), patch("voice.listen", return_value="yes, approve"):
            self.assertTrue(confirm("Approve this action?"))
        with patch("voice.speak"), patch(
            "voice.listen", return_value="no, don't approve"
        ):
            self.assertFalse(confirm("Approve this action?"))
        with patch("voice.speak"), patch("voice.listen", return_value="maybe"):
            self.assertFalse(confirm("Approve this action?"))

    def test_voice_confirmation_callback_reaches_model_request_and_speech_output(self):
        model_client = Mock()
        tool_handler = Mock()
        confirmation_callback = Mock()
        speech_callback = Mock()
        with patch("main.run_model_request", return_value="Completed.") as request:
            handle_request(
                "Do the task",
                self.parser,
                ActionHandler(),
                model_client,
                tool_handler,
                speech_callback,
                confirmation_callback,
            )

        request.assert_called_once_with(
            "Do the task",
            model_client,
            tool_handler,
            confirmation_callback=confirmation_callback,
        )
        speech_callback.assert_called_once_with("Completed.")

    def test_voice_assistant_uses_push_to_talk_and_dispatches_to_jarvis(self):
        actions = Mock()
        model_client = Mock()
        tool_handler = Mock()
        with patch("voice.listen", return_value="What time is it?"), patch(
            "voice.speak"
        ) as speak, patch("voice.confirm") as confirm, patch(
            "main.handle_request"
        ) as handle_request:
            with patch("builtins.input", side_effect=["", "exit"]):
                with patch("builtins.print"):
                    run_voice_assistant(
                        self.parser,
                        actions,
                        model_client,
                        tool_handler,
                    )

        self.assertIs(tool_handler.confirmation_callback, confirm)
        handle_request.assert_called_once_with(
            "What time is it?",
            self.parser,
            actions,
            model_client,
            tool_handler,
            speak,
            confirm,
        )

    def test_meta_voice_transcription_records_and_posts_wav(self):
        audio_device = ModuleType("sounddevice")
        audio_device.rec = Mock(return_value=b"recording")
        audio_device.wait = Mock()
        scipy = ModuleType("scipy")
        scipy.__path__ = []
        scipy_io = ModuleType("scipy.io")
        scipy_io.__path__ = []
        wavfile = ModuleType("scipy.io.wavfile")
        wavfile.write = Mock(side_effect=lambda buffer, _fs, _audio: buffer.write(b"wav"))
        response = Mock(status_code=200)
        response.json.return_value = {"transcript": "open calculator"}
        requests = ModuleType("requests")
        requests.RequestException = Exception
        requests.post = Mock(return_value=response)

        with patch.dict(
            sys.modules,
            {
                "requests": requests,
                "sounddevice": audio_device,
                "scipy": scipy,
                "scipy.io": scipy_io,
                "scipy.io.wavfile": wavfile,
            },
        ):
            with patch("voice._get_api_key", return_value="test-key"):
                with patch("builtins.print"):
                    transcript = listen(duration=2, fs=16000)

        self.assertEqual(transcript, "open calculator")
        audio_device.rec.assert_called_once_with(
            32000,
            samplerate=16000,
            channels=1,
            dtype="int16",
        )
        request = requests.post.call_args
        self.assertEqual(request.args[0], "https://api.meta.ai/v1/asr/transcribe")
        self.assertEqual(
            request.kwargs["headers"],
            {"Authorization": "Bearer test-key"},
        )
        request_part = request.kwargs["files"]["request"]
        self.assertEqual(request_part[2], "application/json")
        self.assertEqual(
            json.loads(request_part[1]),
            {
                "mode": "PUSH_TO_TALK",
                "model": "muse-voice-transcribe-1.0",
                "audioEncoding": "WAV",
            },
        )
        self.assertEqual(request.kwargs["files"]["audio"][1].read(), b"wav")

    def test_parses_local_volume_request(self):
        self.assertEqual(
            self.parser.parse("set volume to 40%"),
            ("set_volume", {"level": 40}),
        )

    def test_openai_client_uses_meta_endpoint(self):
        fake_client = Mock()
        openai_module = SimpleNamespace(OpenAI=Mock(return_value=fake_client))
        with patch.dict(os.environ, {"MODEL_API_KEY": "test-key"}, clear=True):
            with patch.dict(sys.modules, {"openai": openai_module}):
                client = create_model_client()

        openai_module.OpenAI.assert_called_once_with(
            api_key="test-key",
            base_url="https://api.meta.ai/v1",
        )
        self.assertIs(client, fake_client)

    def test_loads_model_key_from_local_env_file(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env_local"
            env_file.write_text('MODEL_API_KEY="test-key"\n', encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(_load_model_api_key(env_file), "test-key")

    def test_loads_vision_model_from_local_env_file(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env_local"
            env_file.write_text(
                'MODEL_API_VISION_MODEL="vision-model-id"\n',
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(
                    load_env_setting("MODEL_API_VISION_MODEL", env_file),
                    "vision-model-id",
                )

    def test_environment_value_overrides_local_env_file_setting(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env_local"
            env_file.write_text(
                "MODEL_API_VISION_MODEL=file-model\n",
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"MODEL_API_VISION_MODEL": "environment-model"},
                clear=True,
            ):
                self.assertEqual(
                    load_env_setting("MODEL_API_VISION_MODEL", env_file),
                    "environment-model",
                )

    def test_overlay_receives_picovoice_access_key_from_env_settings(self):
        overlay_module = ModuleType("overlay_ui")
        overlay_module.run_overlay = Mock()
        model_client = Mock()
        with patch("sys.argv", ["main.py", "--llm", "--overlay"]):
            with patch("main.configure_logging"):
                with patch("main.create_model_client", return_value=model_client):
                    with patch(
                        "main.load_env_setting",
                        side_effect=["vision-model", "picovoice-key"],
                    ):
                        with patch.dict(sys.modules, {"overlay_ui": overlay_module}):
                            main()

        overlay_module.run_overlay.assert_called_once_with(
            model_client,
            vision_model="vision-model",
            wake_word_access_key="picovoice-key",
        )

    def test_wake_word_wait_can_be_stopped_without_reading_audio(self):
        detector = WakeWordDetector.__new__(WakeWordDetector)
        detector.stream = Mock()
        detector.detector = Mock()
        stop_event = threading.Event()
        stop_event.set()

        self.assertFalse(detector.wait(stop_event))

        detector.stream.read.assert_not_called()
        detector.detector.process.assert_not_called()

    def test_overlay_submits_voice_transcript_and_keeps_listening_mode_on(self):
        overlay = JarvisOverlay.__new__(JarvisOverlay)
        overlay.voice_mode = True
        overlay.voice_var = Mock()
        overlay.voice_button = Mock()
        overlay._start_request = Mock()
        overlay._set_status = Mock()

        overlay._handle_voice_result("open calculator")

        overlay._start_request.assert_called_once_with(
            "open calculator",
            display_transcript=True,
        )
        self.assertTrue(overlay.voice_mode)
        overlay.voice_var.set.assert_not_called()
        overlay.voice_button.configure.assert_not_called()

    def test_overlay_parses_voice_approval_conservatively(self):
        self.assertTrue(JarvisOverlay._parse_voice_approval("yes, approve"))
        self.assertFalse(JarvisOverlay._parse_voice_approval("no, don't approve"))
        self.assertIsNone(JarvisOverlay._parse_voice_approval("maybe"))

    def test_continuous_listening_does_not_hide_request_result(self):
        overlay = JarvisOverlay.__new__(JarvisOverlay)
        overlay.events = queue.Queue()
        overlay.root = Mock()
        overlay.request_running = True
        overlay.request_feedback_shown = False
        overlay.request_idle = threading.Event()
        overlay.wake_resume = threading.Event()
        overlay.voice_resume = threading.Event()
        overlay.voice_mode = True
        overlay.voice_running = False
        overlay.wake_word_enabled = False
        overlay.input = Mock()
        overlay.voice_button = Mock()
        overlay.wake_button = Mock()
        overlay._restore_placeholder = Mock()
        overlay._update_submit_enabled = Mock()
        overlay._set_status = Mock()
        overlay.events.put(("request_result", "WhatsApp could not connect"))
        overlay.events.put(("request_finished", None))
        overlay.events.put(("voice_result", ""))
        overlay.events.put(("voice_listening", None))

        overlay._poll_worker_events()

        overlay._set_status.assert_called_once_with("WhatsApp could not connect")
        self.assertTrue(overlay.request_feedback_shown)
        self.assertTrue(overlay.voice_running)

    def test_whatsapp_tool_error_is_returned_without_model_paraphrase(self):
        class FakeOpenAIError(Exception):
            pass

        call = SimpleNamespace(
            id="call-whatsapp",
            function=SimpleNamespace(
                name="send_whatsapp_message",
                arguments=json.dumps(
                    {"contact_name": "Atharav", "message": "Hostel hai"}
                ),
            ),
        )
        tool_message = SimpleNamespace(
            tool_calls=[call],
            content=None,
            model_dump=lambda exclude_none: {"role": "assistant", "tool_calls": []},
        )
        client = Mock()
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=tool_message)]
        )
        handler = Mock()
        handler.requires_confirmation.return_value = False
        handler.execute.side_effect = RuntimeError(
            "Chrome is already running. Close all Chrome windows."
        )

        with patch.dict(
            sys.modules,
            {"openai": SimpleNamespace(OpenAIError=FakeOpenAIError)},
        ):
            result = run_model_request(
                "Send Hostel hai to Atharav on WhatsApp.",
                client,
                handler,
            )

        self.assertEqual(
            result,
            "WhatsApp message could not be sent: "
            "Chrome is already running. Close all Chrome windows.",
        )
        client.chat.completions.create.assert_called_once()

    def test_reports_misspelled_local_model_key(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env_local"
            env_file.write_text("MODLE_API_KEY=test-key\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "Rename it to MODEL_API_KEY"):
                    _load_model_api_key(env_file)

    def test_search_does_not_execute_commands(self):
        with patch("actions.webbrowser.open", return_value=True) as open_browser:
            message = ActionHandler().execute(
                "search_web", {"query": "notes & open calculator"}
            )
        self.assertIn("Searching the web", message)
        self.assertIn("notes+%26+open+calculator", open_browser.call_args.args[0])

    def test_unapproved_apps_are_not_launched(self):
        with patch("actions.subprocess.Popen") as popen:
            message = ActionHandler().execute("open_app", {"app": "powershell"})
        self.assertIn("approved apps", message)
        popen.assert_not_called()

    def test_approved_app_uses_argument_list_without_shell(self):
        with patch("actions.shutil.which", return_value="C:\\Windows\\System32\\notepad.exe"):
            with patch("actions.subprocess.Popen") as popen:
                ActionHandler().execute("open_app", {"app": "notepad"})
        popen.assert_called_once_with(
            ["C:\\Windows\\System32\\notepad.exe"], shell=False
        )

    def test_file_actions_stay_inside_configured_folder(self):
        actions = ActionHandler()
        with tempfile.TemporaryDirectory() as directory:
            actions.file_root = Path(directory).resolve()
            actions.execute("create_file", {"path": "notes.txt", "content": "hello"})
            self.assertEqual(
                (Path(directory) / "notes.txt").read_text(encoding="utf-8"), "hello"
            )
            with self.assertRaises(ValueError):
                actions.execute(
                    "create_file", {"path": "..\\outside.txt", "content": "no"}
                )
            actions.execute("delete_file", {"path": "notes.txt"})
            self.assertFalse((Path(directory) / "notes.txt").exists())

    def test_model_file_actions_stay_in_documents_or_desktop(self):
        handler = ToolHandler()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            documents = root / "Documents"
            desktop = root / "Desktop"
            documents.mkdir()
            desktop.mkdir()
            handler.roots = {
                "documents": documents.resolve(),
                "desktop": desktop.resolve(),
            }
            handler.execute(
                "manage_files",
                {"action": "create", "path": "notes.txt", "content": "hello"},
            )
            self.assertEqual(
                (documents / "notes.txt").read_text(encoding="utf-8"), "hello"
            )
            with self.assertRaises(ValueError):
                handler.execute(
                    "manage_files",
                    {
                        "action": "create",
                        "path": "..\\outside.txt",
                        "content": "no",
                    },
                )
            with self.assertRaises(ValueError):
                handler.execute(
                    "manage_files",
                    {
                        "action": "read",
                        "path": str(root / "outside.txt"),
                    },
                )

    def test_model_executes_multiple_tool_rounds_and_confirms_file_reads(self):
        class FakeOpenAIError(Exception):
            pass

        def tool_message(name, arguments, call_id):
            call = SimpleNamespace(
                id=call_id,
                function=SimpleNamespace(
                    name=name, arguments=json.dumps(arguments)
                ),
            )
            return SimpleNamespace(
                tool_calls=[call],
                content=None,
                model_dump=lambda exclude_none: {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": call.function.arguments,
                            },
                        }
                    ],
                },
            )

        responses = [
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=tool_message(
                            "open_application", {"app_name": "Chrome"}, "call-1"
                        )
                    )
                ]
            ),
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=tool_message(
                            "manage_files",
                            {"action": "read", "path": "Documents\\notes.txt"},
                            "call-2",
                        )
                    )
                ]
            ),
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            tool_calls=None,
                            content="Opened Chrome; the file read was cancelled.",
                        )
                    )
                ]
            ),
        ]
        client = Mock()
        client.chat.completions.create.side_effect = responses
        handler = Mock()
        handler.requires_confirmation.side_effect = (
            lambda name, arguments: name == "manage_files"
        )
        handler.execute.return_value = "Opened Chrome."
        fake_openai = SimpleNamespace(OpenAIError=FakeOpenAIError)

        with patch.dict(sys.modules, {"openai": fake_openai}):
            with patch("builtins.input", return_value="n"):
                result = run_model_request(
                    "Open Chrome and read my notes.", client, handler
                )

        self.assertEqual(result, "Opened Chrome; the file read was cancelled.")
        system_prompt = client.chat.completions.create.call_args_list[0].kwargs[
            "messages"
        ][0]["content"]
        self.assertIn("call play_youtube_video", system_prompt)
        self.assertIn("do not use search_web for YouTube playback", system_prompt)
        self.assertIn("app named exactly 'WhatsApp'", system_prompt)
        self.assertIn("search for 'WhatsApp' only", system_prompt)
        self.assertIn(
            "send_whatsapp_message only when the user explicitly requests WhatsApp Web",
            system_prompt,
        )
        self.assertIn(
            "Do not use control_screen as a fallback",
            system_prompt,
        )
        self.assertIn("control_screen", system_prompt)
        self.assertIn("separately approve the proposed actions", system_prompt)
        handler.execute.assert_called_once_with(
            "open_application", {"app_name": "Chrome"}
        )
        final_tool_message = client.chat.completions.create.call_args_list[2].kwargs[
            "messages"
        ][-1]
        self.assertEqual(final_tool_message["role"], "tool")
        self.assertIn("Cancelled by user", final_tool_message["content"])

    def test_model_logs_exact_tool_failure(self):
        class FakeOpenAIError(Exception):
            pass

        call = SimpleNamespace(
            id="call-1",
            function=SimpleNamespace(
                name="play_youtube_video",
                arguments='{"query":"test song"}',
            ),
        )
        tool_message = SimpleNamespace(
            tool_calls=[call],
            content=None,
            model_dump=lambda exclude_none: {"role": "assistant", "tool_calls": []},
        )
        client = Mock()
        client.chat.completions.create.side_effect = [
            SimpleNamespace(choices=[SimpleNamespace(message=tool_message)]),
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            tool_calls=None,
                            content="Playback failed.",
                        )
                    )
                ]
            ),
        ]
        handler = Mock()
        handler.requires_confirmation.return_value = False
        handler.execute.side_effect = RuntimeError("browser context was closed")

        with patch.dict(
            sys.modules,
            {"openai": SimpleNamespace(OpenAIError=FakeOpenAIError)},
        ):
            with self.assertLogs(level="WARNING") as captured:
                result = run_model_request("play a song", client, handler)

        self.assertEqual(result, "Playback failed.")
        self.assertIn(
            "Tool call play_youtube_video failed: browser context was closed",
            captured.output[0],
        )

    def test_ui_actions_require_typed_confirmation(self):
        actions = ActionHandler()
        with patch("builtins.input", return_value="yes"):
            with patch.object(actions, "_pyautogui") as pyautogui:
                handle_request("type private text", self.parser, actions)
        pyautogui.assert_not_called()

    def test_confirmed_ui_action_executes(self):
        actions = ActionHandler()
        automation = Mock()
        with patch("builtins.input", return_value="YES"):
            with patch.object(actions, "_pyautogui", return_value=automation):
                handle_request("type hello", self.parser, actions)
        automation.write.assert_called_once_with("hello", interval=0.02)

    def test_terminal_and_gui_execution_tools_are_not_exposed(self):
        tool_names = {tool["function"]["name"] for tool in TOOL_DEFINITIONS}
        self.assertTrue(
            {"execute_terminal", "click", "type_text", "press_key"}.isdisjoint(
                tool_names
            )
        )
        with self.assertRaises(ValueError):
            ToolHandler().execute("execute_terminal", {"command": "whoami"})

    def test_browser_tools_are_registered_and_refuse_running_chrome(self):
        tool_names = {tool["function"]["name"] for tool in TOOL_DEFINITIONS}
        self.assertTrue(
            {"play_youtube_video", "send_whatsapp_message"}.issubset(tool_names)
        )
        self.assertNotIn("play_youtube", tool_names)

        browser = BrowserAutomation()
        with patch.object(browser, "_chrome_is_running", return_value=True):
            with self.assertRaisesRegex(RuntimeError, "Chrome is already running"):
                browser._ensure_context()

    def test_browser_tools_report_missing_playwright_cleanly(self):
        browser_modules = {
            "playwright": None,
            "playwright.sync_api": None,
        }
        with patch.dict(sys.modules, browser_modules):
            with self.assertRaisesRegex(
                RuntimeError,
                "Browser automation is not installed.*requirements.txt",
            ):
                EdgeBrowserAutomation().play_youtube_video("test video")
            with self.assertRaisesRegex(
                RuntimeError,
                "Browser automation is not installed.*requirements.txt",
            ):
                BrowserAutomation().send_whatsapp_message("Mom", "Hello")

    def test_edge_connects_to_verified_local_cdp_profile(self):
        class FakePlaywrightError(Exception):
            pass

        edge = EdgeBrowserAutomation()
        playwright = Mock()
        context = Mock()
        self._configure_fake_cdp_browser(edge, playwright, context)
        playwright_manager = Mock()
        playwright_manager.start.return_value = playwright
        playwright_package = ModuleType("playwright")
        playwright_package.__path__ = []
        sync_api = ModuleType("playwright.sync_api")
        sync_api.Error = FakePlaywrightError
        sync_api.sync_playwright = Mock(return_value=playwright_manager)

        with patch.dict(
            sys.modules,
            {
                "playwright": playwright_package,
                "playwright.sync_api": sync_api,
            },
        ):
            with patch.object(edge, "_port_is_open", return_value=True):
                self.assertIs(edge._ensure_context("msedge"), context)
                edge.close()

        self.assertEqual(
            edge.user_data_dir,
            Path(__file__).resolve().parent / "jarvis_edge_data",
        )
        playwright.chromium.connect_over_cdp.assert_called_once_with(
            "http://127.0.0.1:9222",
            timeout=10_000,
        )
        context.close.assert_not_called()
        playwright.stop.assert_called_once_with()

    def test_edge_waits_for_default_cdp_context_instead_of_creating_one(self):
        class FakePlaywrightError(Exception):
            pass

        edge = EdgeBrowserAutomation()
        playwright = Mock()
        browser = self._configure_fake_cdp_browser(edge, playwright, Mock())
        browser.contexts = []
        playwright_manager = Mock()
        playwright_manager.start.return_value = playwright
        playwright_package = ModuleType("playwright")
        playwright_package.__path__ = []
        sync_api = ModuleType("playwright.sync_api")
        sync_api.Error = FakePlaywrightError
        sync_api.sync_playwright = Mock(return_value=playwright_manager)

        with patch.dict(
            sys.modules,
            {
                "playwright": playwright_package,
                "playwright.sync_api": sync_api,
            },
        ):
            with patch.object(edge, "_port_is_open", return_value=True):
                with patch("tools.time.sleep"):
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "default browser context did not become available",
                    ):
                        edge._ensure_context("msedge")

        browser.new_context.assert_not_called()
        playwright.stop.assert_called_once_with()

    def test_edge_launches_with_loopback_cdp_and_isolated_profile(self):
        edge = EdgeBrowserAutomation()
        with tempfile.TemporaryDirectory() as directory:
            edge.user_data_dir = Path(directory) / "jarvis_edge_data"
            with patch("tools.os.path.exists", return_value=False):
                with patch("tools.shutil.which", return_value="msedge.exe"):
                    with patch("tools.subprocess.Popen") as popen:
                        with patch.object(edge, "_port_is_open", return_value=True):
                            edge._launch_edge()

        self.assertEqual(
            popen.call_args.args[0],
            [
                "msedge.exe",
                "--remote-debugging-port=9222",
                "--remote-debugging-address=127.0.0.1",
                f"--user-data-dir={edge.user_data_dir}",
                "--enable-automation",
                "--no-first-run",
                "--no-default-browser-check",
                "--new-window",
                "about:blank",
            ],
        )

    def test_edge_refuses_a_cdp_endpoint_with_a_different_profile(self):
        class FakePlaywrightError(Exception):
            pass

        edge = EdgeBrowserAutomation()
        edge._playwright_error = FakePlaywrightError
        browser = Mock()
        session = Mock()
        session.send.return_value = {
            "arguments": [
                "--user-data-dir=C:\\OtherProfile",
                "--remote-debugging-port=9222",
                "--remote-debugging-address=127.0.0.1",
            ]
        }
        browser.new_browser_cdp_session.return_value = session

        with self.assertRaisesRegex(RuntimeError, "different Edge profile"):
            edge._verify_edge_endpoint(browser)

        session.detach.assert_called_once_with()

    def test_edge_reconnects_when_cdp_target_creation_fails(self):
        class FakePlaywrightError(Exception):
            pass

        edge = EdgeBrowserAutomation()
        stale_context = Mock()
        stale_context.pages = []
        stale_context.is_closed.return_value = False
        stale_context.browser.is_connected.return_value = True
        stale_playwright = Mock()
        edge._context = stale_context
        edge._playwright = stale_playwright
        edge._playwright_error = FakePlaywrightError
        stale_browser = Mock()
        stale_cdp = Mock()
        stale_cdp.send.side_effect = FakePlaywrightError(
            "Target page, context or browser has been closed"
        )
        stale_browser.new_browser_cdp_session.return_value = stale_cdp
        edge._browser = stale_browser

        page = Mock()
        page.url = "https://www.youtube.com/"
        page.is_closed.return_value = False
        fresh_context = Mock()
        fresh_context.pages = [page]
        fresh_context.is_closed.return_value = False
        fresh_context.browser.is_connected.return_value = True
        fresh_playwright = Mock()
        self._configure_fake_cdp_browser(edge, fresh_playwright, fresh_context)
        playwright_manager = Mock()
        playwright_manager.start.return_value = fresh_playwright
        playwright_package = ModuleType("playwright")
        playwright_package.__path__ = []
        sync_api = ModuleType("playwright.sync_api")
        sync_api.Error = FakePlaywrightError
        sync_api.sync_playwright = Mock(return_value=playwright_manager)

        with patch.dict(
            sys.modules,
            {
                "playwright": playwright_package,
                "playwright.sync_api": sync_api,
            },
        ):
            with patch.object(edge, "_port_is_open", return_value=True):
                with patch("tools.time.sleep"):
                    result = edge._page_for(
                        "https://www.youtube.com/",
                        "msedge",
                    )

        self.assertIs(result, page)
        stale_cdp.send.assert_called_once_with(
            "Target.createTarget",
            {"url": "https://www.youtube.com/"},
        )
        stale_cdp.detach.assert_called_once_with()
        stale_context.close.assert_not_called()
        stale_playwright.stop.assert_called_once_with()
        fresh_playwright.chromium.connect_over_cdp.assert_called_once()
        page.goto.assert_called_once_with(
            "https://www.youtube.com/",
            wait_until="domcontentloaded",
            timeout=20_000,
        )

    def test_edge_reports_a_clear_error_when_cdp_target_creation_fails_after_retry(self):
        class FakePlaywrightError(Exception):
            pass

        edge = EdgeBrowserAutomation()
        closed_page_error = FakePlaywrightError(
            "Target page, context or browser has been closed"
        )
        stale_context = Mock()
        stale_context.pages = []
        stale_context.is_closed.return_value = False
        stale_context.browser.is_connected.return_value = True
        edge._context = stale_context
        edge._playwright = Mock()
        edge._playwright_error = FakePlaywrightError
        stale_browser = Mock()
        stale_cdp = Mock()
        stale_cdp.send.side_effect = closed_page_error
        stale_browser.new_browser_cdp_session.return_value = stale_cdp
        edge._browser = stale_browser

        fresh_context = Mock()
        fresh_context.pages = []
        fresh_context.is_closed.return_value = False
        fresh_context.browser.is_connected.return_value = True
        fresh_playwright = Mock()
        fresh_browser = self._configure_fake_cdp_browser(
            edge,
            fresh_playwright,
            fresh_context,
        )
        target_cdp = Mock()
        target_cdp.send.side_effect = closed_page_error
        fresh_browser.new_browser_cdp_session.side_effect = [
            fresh_browser.new_browser_cdp_session.return_value,
            target_cdp,
        ]
        playwright_manager = Mock()
        playwright_manager.start.return_value = fresh_playwright
        playwright_package = ModuleType("playwright")
        playwright_package.__path__ = []
        sync_api = ModuleType("playwright.sync_api")
        sync_api.Error = FakePlaywrightError
        sync_api.sync_playwright = Mock(return_value=playwright_manager)

        with patch.dict(
            sys.modules,
            {
                "playwright": playwright_package,
                "playwright.sync_api": sync_api,
            },
        ):
            with patch.object(edge, "_port_is_open", return_value=True):
                with patch("tools.time.sleep"):
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "after restarting its managed context",
                    ):
                        edge._page_for("https://www.youtube.com/", "msedge")
        stale_cdp.detach.assert_called_once_with()
        target_cdp.detach.assert_called_once_with()

    def test_edge_restarts_context_when_reused_tab_closes_during_navigation(self):
        class FakePlaywrightError(Exception):
            pass

        edge = EdgeBrowserAutomation()
        stale_page = Mock()
        stale_page.url = "about:blank"
        stale_page.is_closed.return_value = False
        stale_page.goto.side_effect = FakePlaywrightError(
            "Target page, context or browser has been closed"
        )
        stale_context = Mock()
        stale_context.pages = [stale_page]
        stale_context.is_closed.return_value = False
        stale_context.browser.is_connected.return_value = True
        edge._context = stale_context
        stale_playwright = Mock()
        edge._playwright = stale_playwright
        edge._playwright_error = FakePlaywrightError

        fresh_page = Mock()
        fresh_page.url = "about:blank"
        fresh_page.is_closed.return_value = False
        fresh_context = Mock()
        fresh_context.pages = [fresh_page]
        fresh_context.is_closed.return_value = False
        fresh_context.browser.is_connected.return_value = True
        fresh_playwright = Mock()
        self._configure_fake_cdp_browser(edge, fresh_playwright, fresh_context)
        playwright_manager = Mock()
        playwright_manager.start.return_value = fresh_playwright
        playwright_package = ModuleType("playwright")
        playwright_package.__path__ = []
        sync_api = ModuleType("playwright.sync_api")
        sync_api.Error = FakePlaywrightError
        sync_api.sync_playwright = Mock(return_value=playwright_manager)

        with patch.dict(
            sys.modules,
            {
                "playwright": playwright_package,
                "playwright.sync_api": sync_api,
            },
        ):
            with patch.object(edge, "_port_is_open", return_value=True):
                result = edge._page_for("https://www.youtube.com/", "msedge")

        self.assertIs(result, fresh_page)
        stale_context.close.assert_not_called()
        stale_playwright.stop.assert_called_once_with()
        fresh_page.goto.assert_called_once_with(
            "https://www.youtube.com/",
            wait_until="domcontentloaded",
            timeout=20_000,
        )

    def test_edge_creates_default_context_target_when_no_page_exists(self):
        class FakePlaywrightError(Exception):
            pass

        edge = EdgeBrowserAutomation()
        context = Mock()
        page = Mock()
        page.url = "https://www.youtube.com/results?search_query=song"
        page.is_closed.return_value = False
        cdp = Mock()
        browser = Mock()
        browser.new_browser_cdp_session.return_value = cdp
        edge._browser = browser
        playwright_package = ModuleType("playwright")
        playwright_package.__path__ = []
        sync_api = ModuleType("playwright.sync_api")
        sync_api.Error = FakePlaywrightError

        with patch.dict(
            sys.modules,
            {
                "playwright": playwright_package,
                "playwright.sync_api": sync_api,
            },
        ):
            with patch.object(edge, "_ensure_context", return_value=context):
                with patch.object(
                    edge,
                    "_live_context_pages",
                    side_effect=[[]] * 16 + [[page]],
                ):
                    with patch("tools.time.sleep"):
                        result = edge._page_for(
                            "https://www.youtube.com/",
                            "msedge",
                        )

        self.assertIs(result, page)
        context.new_page.assert_not_called()
        browser.new_page.assert_not_called()
        cdp.send.assert_called_once_with(
            "Target.createTarget",
            {"url": "https://www.youtube.com/"},
        )
        cdp.detach.assert_called_once_with()
        page.wait_for_load_state.assert_called_once_with(
            "domcontentloaded",
            timeout=20_000,
        )

    def test_edge_reuses_existing_tab_before_creating_a_new_one(self):
        class FakePlaywrightError(Exception):
            pass

        edge = EdgeBrowserAutomation()
        context = Mock()
        page = Mock()
        page.url = "about:blank"
        page.is_closed.return_value = False
        context.pages = [page]
        context.is_closed.return_value = False
        context.browser.is_connected.return_value = True
        playwright_package = ModuleType("playwright")
        playwright_package.__path__ = []
        sync_api = ModuleType("playwright.sync_api")
        sync_api.Error = FakePlaywrightError

        with patch.dict(
            sys.modules,
            {
                "playwright": playwright_package,
                "playwright.sync_api": sync_api,
            },
        ):
            with patch.object(edge, "_ensure_context", return_value=context):
                result = edge._page_for("https://www.youtube.com/", "msedge")

        self.assertIs(result, page)
        context.new_page.assert_not_called()
        page.goto.assert_called_once_with(
            "https://www.youtube.com/",
            wait_until="domcontentloaded",
            timeout=20_000,
        )

    def test_edge_waits_for_initial_tab_before_navigating(self):
        class FakePlaywrightError(Exception):
            pass

        edge = EdgeBrowserAutomation()
        browser = Mock()
        edge._browser = browser
        context = Mock()
        page = Mock()
        page.url = "about:blank"
        page.is_closed.return_value = False
        context.new_page.return_value = Mock()
        playwright_package = ModuleType("playwright")
        playwright_package.__path__ = []
        sync_api = ModuleType("playwright.sync_api")
        sync_api.Error = FakePlaywrightError

        with patch.dict(
            sys.modules,
            {
                "playwright": playwright_package,
                "playwright.sync_api": sync_api,
            },
        ):
            with patch.object(edge, "_ensure_context", return_value=context):
                with patch.object(
                    edge,
                    "_live_context_pages",
                    side_effect=[[], [], [page]],
                ):
                    with patch("tools.time.sleep"):
                        result = edge._page_for(
                            "https://www.youtube.com/",
                            "msedge",
                        )

        self.assertIs(result, page)
        context.new_page.assert_not_called()
        browser.new_browser_cdp_session.assert_not_called()
        page.goto.assert_called_once_with(
            "https://www.youtube.com/",
            wait_until="domcontentloaded",
            timeout=20_000,
        )

    def test_play_youtube_video_skips_ads_and_uses_encoded_query(self):
        class FakePlaywrightError(Exception):
            pass

        class FakePlaywrightTimeout(FakePlaywrightError):
            pass

        edge = EdgeBrowserAutomation()
        page = Mock()
        results = Mock()
        results.first = Mock()
        ad_result = Mock()
        ad_result.evaluate.return_value = False
        video_result = Mock()
        video_result.evaluate.return_value = True
        video_result.get_attribute.return_value = "Song title"
        results.count.return_value = 2
        results.nth.side_effect = [ad_result, video_result]
        video = Mock()
        video_locator = Mock()
        video_locator.first = video
        page.locator.side_effect = {
            "ytd-video-renderer a#video-title": results,
            "video": video_locator,
        }.__getitem__
        playwright_package = ModuleType("playwright")
        playwright_package.__path__ = []
        sync_api = ModuleType("playwright.sync_api")
        sync_api.Error = FakePlaywrightError
        sync_api.TimeoutError = FakePlaywrightTimeout

        with patch.dict(
            sys.modules,
            {
                "playwright": playwright_package,
                "playwright.sync_api": sync_api,
            },
        ):
           with patch.object(
               edge,
               "_discard_context",
           ) as discard_context:
               with patch.object(
                   edge,
                   "_page_for",
                   return_value=page,
               ) as page_for:
                   result = edge.play_youtube_video("AC/DC & song")

        page_for.assert_called_once_with(
            "https://www.youtube.com/results?search_query=AC%2FDC+%26+song",
            "msedge",
        )
        discard_context.assert_called_once_with()
        ad_result.click.assert_not_called()
        video_result.click.assert_called_once_with(timeout=10_000)
        self.assertIn("Playing the first non-ad YouTube result: Song title", result)

    def test_tool_handler_defaults_youtube_browser_to_edge(self):
        handler = ToolHandler()
        handler.edge_browser = Mock()

        handler.execute("play_youtube_video", {"query": "a song"})

        handler.edge_browser.play_youtube_video.assert_called_once_with(
            "a song", "msedge"
        )

    def test_tool_handler_opens_edge_through_the_cdp_browser_manager(self):
        handler = ToolHandler()
        handler.actions = Mock()
        handler.edge_browser = Mock()

        result = handler.execute(
            "open_application",
            {"app_name": "Microsoft Edge"},
        )

        self.assertEqual(
            result,
            handler.edge_browser.open_edge.return_value,
        )
        handler.edge_browser.open_edge.assert_called_once_with()
        handler.actions.execute.assert_not_called()

    def test_control_screen_requires_screen_consent_and_action_approval(self):
        class FakePyAutoGUIError(Exception):
            pass

        class FakeFailSafeError(FakePyAutoGUIError):
            pass

        pyautogui = ModuleType("pyautogui")
        pyautogui.PyAutoGUIException = FakePyAutoGUIError
        pyautogui.FailSafeException = FakeFailSafeError
        pyautogui.size = Mock(return_value=(640, 480))
        pyautogui.click = Mock()
        pyautogui.doubleClick = Mock()
        pyautogui.write = Mock()
        pyautogui.hotkey = Mock()
        pyautogui.scroll = Mock()
        client = Mock()
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps(
                            {
                                "actions": [
                                    {"type": "click", "x": 20, "y": 30},
                                    {
                                        "type": "type_text",
                                        "text": "search text",
                                    },
                                ]
                            }
                        )
                    )
                )
            ]
        )
        approvals = []
        statuses = []

        with patch.dict(sys.modules, {"pyautogui": pyautogui}):
            with patch(
                "screen_agent._capture_screen",
                return_value=("c2NyZWVuc2hvdA==", 640, 480),
            ):
                result = control_screen(
                    "Search for a topic",
                    client,
                    confirmation_callback=lambda message: approvals.append(message)
                    or True,
                    status_callback=statuses.append,
                    vision_model="test-vision-model",
                )

        self.assertIn("Completed 2 approved", result)
        self.assertEqual(len(approvals), 2)
        self.assertIn("send the image", approvals[0])
        self.assertIn('"type": "click"', approvals[1])
        self.assertTrue(pyautogui.FAILSAFE)
        pyautogui.click.assert_called_once_with(20, 30)
        pyautogui.write.assert_called_once_with("search text", interval=0.02)
        self.assertEqual(client.chat.completions.create.call_args.kwargs["model"], "test-vision-model")
        user_content = client.chat.completions.create.call_args.kwargs["messages"][1][
            "content"
        ]
        self.assertIn("640x480", user_content[0]["text"])
        self.assertEqual(
            user_content[1]["image_url"]["url"],
            "data:image/jpeg;base64,c2NyZWVuc2hvdA==",
        )

    def test_screen_control_accepts_and_executes_large_scroll(self):
        actions, done = _validate_plan(
            {
                "actions": [
                    {"type": "scroll", "x": 320, "y": 240, "clicks": -80}
                ],
                "done": False,
            },
            640,
            480,
        )

        self.assertFalse(done)
        pyautogui = SimpleNamespace(FAILSAFE=False, scroll=Mock())
        with patch("screen_agent.time.sleep"):
            _execute_actions(actions, pyautogui, None)

        pyautogui.scroll.assert_called_once_with(-80, x=320, y=240)
        with self.assertRaisesRegex(ValueError, "from -100 to 100"):
            _validate_plan(
                {
                    "actions": [
                        {"type": "scroll", "x": 320, "y": 240, "clicks": -101}
                    ],
                    "done": False,
                },
                640,
                480,
            )

    def test_screen_control_accepts_page_down_for_long_pages(self):
        actions, done = _validate_plan(
            {
                "actions": [{"type": "press_key", "key": "PageDown"}],
                "done": False,
            },
            640,
            480,
        )

        self.assertFalse(done)
        self.assertEqual(actions, [{"type": "press_key", "key": "pagedown"}])
        pyautogui = SimpleNamespace(FAILSAFE=False, press=Mock())
        with patch("screen_agent.time.sleep"):
            _execute_actions(actions, pyautogui, None)

        pyautogui.press.assert_called_once_with("pagedown")

    def test_control_screen_continues_scrolling_past_six_steps(self):
        class FakePyAutoGUIError(Exception):
            pass

        pyautogui = ModuleType("pyautogui")
        pyautogui.PyAutoGUIException = FakePyAutoGUIError
        pyautogui.FailSafeException = FakePyAutoGUIError
        pyautogui.size = Mock(return_value=(640, 480))
        pyautogui.press = Mock()
        scroll_response = json.dumps(
            {
                "actions": [{"type": "press_key", "key": "pagedown"}],
                "done": False,
            }
        )
        finished_response = json.dumps({"actions": [], "done": True})
        client = Mock()
        client.chat.completions.create.side_effect = [
            SimpleNamespace(
                choices=[
                    SimpleNamespace(message=SimpleNamespace(content=scroll_response))
                ]
            )
            for _ in range(7)
        ] + [
            SimpleNamespace(
                choices=[
                    SimpleNamespace(message=SimpleNamespace(content=finished_response))
                ]
            )
        ]
        openai = ModuleType("openai")
        openai.OpenAIError = Exception

        with patch.dict(sys.modules, {"pyautogui": pyautogui, "openai": openai}):
            with patch(
                "screen_agent._capture_screen",
                return_value=("c2NyZWVuc2hvdA==", 640, 480),
            ):
                with patch("screen_agent.time.sleep"), patch("builtins.print"):
                    result = control_screen(
                        "Scroll to the compiler at the end of the page",
                        client,
                        confirmation_callback=lambda _message: True,
                        vision_model="test-vision-model",
                    )

        self.assertIn("7 step(s)", result)
        self.assertEqual(client.chat.completions.create.call_count, 8)
        self.assertEqual(pyautogui.press.call_count, 7)
        first_prompt = client.chat.completions.create.call_args_list[0].kwargs[
            "messages"
        ][1]["content"][0]["text"]
        self.assertIn("do not mark the task done until the requested section", first_prompt)

    def test_control_screen_captures_nothing_without_screen_consent(self):
        client = Mock()
        with patch(
            "screen_agent._capture_screen",
            side_effect=AssertionError("screenshot should not be captured"),
        ) as capture:
            result = control_screen(
                "Click the button",
                client,
                confirmation_callback=lambda message: False,
                vision_model="test-vision-model",
            )

        self.assertIn("cancelled", result)
        capture.assert_not_called()
        client.chat.completions.create.assert_not_called()

    def test_control_screen_rejects_out_of_bounds_model_coordinates(self):
        class FakePyAutoGUIError(Exception):
            pass

        class FakeFailSafeError(FakePyAutoGUIError):
            pass

        pyautogui = ModuleType("pyautogui")
        pyautogui.PyAutoGUIException = FakePyAutoGUIError
        pyautogui.FailSafeException = FakeFailSafeError
        pyautogui.size = Mock(return_value=(640, 480))
        pyautogui.click = Mock()
        client = Mock()
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content='{"actions":[{"type":"click","x":640,"y":30}]}'
                    )
                )
            ]
        )
        with patch.dict(sys.modules, {"pyautogui": pyautogui}):
            with patch(
                "screen_agent._capture_screen",
                return_value=("c2NyZWVuc2hvdA==", 640, 480),
            ):
                with self.assertRaisesRegex(RuntimeError, "outside the screen"):
                    control_screen(
                        "Click outside",
                        client,
                        confirmation_callback=lambda message: True,
                        vision_model="test-vision-model",
                    )

        pyautogui.click.assert_not_called()

    def test_control_screen_honors_pyautogui_failsafe(self):
        class FakePyAutoGUIError(Exception):
            pass

        class FakeFailSafeError(FakePyAutoGUIError):
            pass

        pyautogui = ModuleType("pyautogui")
        pyautogui.PyAutoGUIException = FakePyAutoGUIError
        pyautogui.FailSafeException = FakeFailSafeError
        pyautogui.size = Mock(return_value=(640, 480))
        pyautogui.click = Mock(side_effect=FakeFailSafeError())
        client = Mock()
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content='{"actions":[{"type":"click","x":10,"y":10}]}'
                    )
                )
            ]
        )
        with patch.dict(sys.modules, {"pyautogui": pyautogui}):
            with patch(
                "screen_agent._capture_screen",
                return_value=("c2NyZWVuc2hvdA==", 640, 480),
            ):
                with self.assertRaisesRegex(RuntimeError, "fail-safe stopped"):
                    control_screen(
                        "Click the button",
                        client,
                        confirmation_callback=lambda message: True,
                        vision_model="test-vision-model",
                    )

        self.assertTrue(pyautogui.FAILSAFE)
        pyautogui.click.assert_called_once_with(10, 10)

    def test_whatsapp_send_requires_confirmation_and_clears_cancelled_draft(self):
        class FakePlaywrightError(Exception):
            pass

        class FakePlaywrightTimeout(FakePlaywrightError):
            pass

        playwright_package = ModuleType("playwright")
        playwright_package.__path__ = []
        sync_api = ModuleType("playwright.sync_api")
        sync_api.Error = FakePlaywrightError
        sync_api.TimeoutError = FakePlaywrightTimeout

        for answer, should_send, voice_approval in (
            ("y", True, False),
            ("n", False, False),
            ("yes", True, True),
            ("no", False, True),
        ):
            with self.subTest(answer=answer, voice_approval=voice_approval):
                events = []
                approval_messages = []
                callback = None
                if voice_approval:
                    def callback(message):
                        events.append("confirm")
                        approval_messages.append(message)
                        return answer == "yes"

                browser = BrowserAutomation(confirmation_callback=callback)
                page = Mock()
                pane = Mock()
                search = Mock()
                search.first = search
                chat = Mock()
                chat.get_attribute.return_value = "Mom"
                titled_chats = Mock()
                titled_chats.count.return_value = 1
                titled_chats.nth.return_value = chat
                composer = Mock()
                composer.first = composer
                locators = {
                    "#pane-side": pane,
                    '#side [contenteditable="true"][data-tab="3"]': search,
                    "#pane-side span[title]": titled_chats,
                    '#main footer [contenteditable="true"]': composer,
                }
                page.locator.side_effect = locators.__getitem__
                composer.press.side_effect = lambda key: events.append(("press", key))

                with patch.dict(
                    sys.modules,
                    {
                        "playwright": playwright_package,
                        "playwright.sync_api": sync_api,
                    },
                ):
                    with patch.object(browser, "_page_for", return_value=page):
                        with patch(
                            "builtins.input",
                            side_effect=lambda prompt: events.append("confirm")
                            or answer,
                        ) as console_input:
                            result = browser.send_whatsapp_message("mom", "hello")

                if voice_approval:
                    console_input.assert_not_called()
                    self.assertIn("Contact: Mom", approval_messages[0])
                    self.assertIn("Message: hello", approval_messages[0])
                presses = [event for event in events if isinstance(event, tuple)]
                self.assertEqual(bool(presses), should_send)
                self.assertIn("confirm", events)
                if should_send:
                    self.assertEqual(presses, [("press", "Enter")])
                    self.assertIn("sent to Mom", result)
                    composer.press.assert_called_once_with("Enter")
                else:
                    self.assertIn("cancelled", result)
                    composer.press.assert_not_called()
                    composer.fill.assert_any_call("")


if __name__ == "__main__":
    unittest.main()

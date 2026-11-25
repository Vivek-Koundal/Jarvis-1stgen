# Jarvis for Windows

A small Windows assistant with explicit tools for app launching, system
controls, simple desktop input, and file operations restricted to Documents
and Desktop.
Text mode works without an API key; voice and Meta tool selection are optional.

## Requirements

- Python 3.11 or newer
- For terminal voice mode: a microphone, `MODEL_API_KEY`, and the audio
  packages in `requirements-voice.txt`
- For overlay wake-word listening: a microphone, `PICOVOICE_ACCESS_KEY`, and
  the audio packages in `requirements-voice.txt`
- For Meta Model API tool selection: a Meta Model API key and the packages in
  `requirements.txt`
- For the floating overlay: Tkinter (included with standard Windows Python)
- Volume, brightness, and desktop input tools require the optional packages
  listed in `requirements.txt`. Brightness support depends on the display.
- Battery and CPU status tools require `psutil`.

Create a virtual environment and install the main runtime dependencies:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

For an exact install matching the checked-in Python 3.11 Windows snapshot,
use `requirements.lock` instead. For voice features, install the voice
manifest (or its complete pinned snapshot):

```powershell
python -m pip install -r requirements-voice.txt
# Or use the voice-enabled lock file:
python -m pip install -r requirements-voice.lock
```

`requirements-voice.txt` includes the main manifest and the additional
microphone/audio packages. See [DEPENDENCIES.md](DEPENDENCIES.md) for what pip
installs versus what is supplied by Python or Windows.

Copy `.env.example` to `.env_local` and uncomment/fill only the settings for
features you plan to use. `.env_local` is ignored by Git; do not commit real
credentials. Text/local-parser mode needs no API key.

On Python 3.14, PyAudio may need Microsoft C++ Build Tools if pip cannot find a
compatible prebuilt wheel. Text and Meta mode do not require PyAudio.

Text assistant (no API keys or extra packages):

```powershell
python main.py
```

Voice assistant (push-to-talk, Meta speech transcription, and Windows speech
output):

```powershell
$env:MODEL_API_KEY = "your-meta-model-api-key"
python main.py --voice --llm
```

Press Enter at the terminal prompt and then speak, or type a request directly.
Voice commands are recorded and sent to Meta's speech transcription endpoint.
The floating overlay's separate Voice toggle continues to use Google's speech
recognition service. Do not use either voice feature if that conflicts with
your privacy needs. Voice-mode approvals accept spoken "yes"/"approve" and
reject spoken "no"/"cancel"; unclear responses are declined.

Use Meta Model API tool calling with the OpenAI-compatible SDK:

```powershell
$env:MODEL_API_KEY = "your-meta-model-api-key"
# Optional; defaults to muse-spark-1.3
$env:MODEL_API_MODEL = "muse-spark-1.3"
# Optional; defaults to Meta's official endpoint
$env:MODEL_API_BASE_URL = "https://api.meta.ai/v1"
python main.py --llm
```

Get the key from Meta's Model API dashboard. Do not put it in source code or
send it in chat. Alternatively, place `MODEL_API_KEY=your-key` in `.env` or
`.env_local`; both files are git-ignored. The implementation targets Meta
directly (`https://api.meta.ai/v1`, model `muse-spark-1.3`), not OpenRouter,
Groq, Together AI, or Fireworks. Those providers require their own key, base
URL, and model ID. Requests and approved file contents are sent to the
configured model provider. Combine `--voice` and `--llm` to use both.

The model can chain up to four tool rounds and eight tool calls per user
request, then summarizes the results. It cannot run arbitrary terminal
commands. Browser actions are limited to the YouTube and WhatsApp tasks below.
File reads and changes require confirmation in the terminal or overlay dialog;
file contents are sent to the model only after you approve a read.

## Floating screen-control overlay

Set `MODEL_API_VISION_MODEL` to a vision-capable model ID supported by the
configured API endpoint, either in the environment or in `.env` / `.env_local`.
The overlay uses the same API key and endpoint as regular tool calling. Start
it from the project directory:

```powershell
$env:MODEL_API_VISION_MODEL = "your-provider-vision-model-id"
python main.py --llm --overlay
```

The dark, translucent, always-on-top overlay opens at the bottom-right.
`Ctrl+Space` toggles its visibility. Type a request and press Enter or **Send**.
Set `PICOVOICE_ACCESS_KEY` in the environment or `.env_local` to enable
always-on local "Jarvis" wake-word detection; the **Wake** control starts or
stops it. After the wake word, Jarvis captures and submits one spoken request,
then speaks the response. The separate **Voice** toggle starts continuous
listening until switched off, automatically submitting each captured request
and showing the recognized text in the input. Voice mode is unavailable while
wake-word listening is on. While Voice mode is on, permission dialogs accept
spoken "approve" or "deny" (unclear replies are not accepted); the buttons
remain available. Speech transcription uses Google's speech recognition
service; wake-word responses use Windows text-to-speech. Install
`requirements-voice.txt` for the microphone dependency. Model, microphone
capture, screen capture, and mouse/keyboard work run on background threads so
the overlay remains responsive.

When a screen task is requested, Jarvis first asks permission to capture and
send the primary-screen image to the configured model provider. It then shows
the proposed mouse/keyboard actions for a separate approval before executing
them. Actions are limited to clicks, double-clicks, text entry, a bounded set
of hotkeys, Page Up/Down navigation, and scrolling (up to 100 wheel clicks per
scroll action). Jarvis can inspect and continue for up to 12 screen steps, so it
can navigate long pages and verify the requested section before acting.
Coordinates and action counts are validated.
PyAutoGUI's fail-safe is enabled (move the pointer to the top-left corner to
abort). Do not approve actions you have not reviewed. Screen images may contain
private information, so only use this with a provider and endpoint you trust.

## Browser automation

YouTube playback uses Playwright's CDP connection to a dedicated Microsoft Edge
process on `127.0.0.1:9222`, using the project-local `jarvis_edge_data` profile.
This is separate from your main Edge profile, so it can run while your regular
Edge window is open; sign in to YouTube in Jarvis's Edge window if needed. The
profile folder is git-ignored. Jarvis verifies that the local debugging endpoint
belongs to this profile before attaching; if another app owns port 9222, Jarvis
refuses to connect. Edge starts with an explicit `about:blank` tab, and Jarvis waits for the
default context and tab before navigating. When no tab is available, Jarvis
creates one in Edge's default context with the CDP `Target.createTarget`
command, then waits for Playwright to attach to it. It reuses an open tab when
possible and retries once with a fresh CDP connection if tab creation or
navigation fails. After each Edge
action, Jarvis disconnects its thread-bound Playwright client while leaving the
Edge window and playback open; later actions reconnect to the verified
endpoint. After updating the code, fully exit and restart Jarvis so the running
process loads the new browser manager. If the error persists, `assistant.log`
records which source file and profile were loaded plus the recovery details.
Asking Jarvis to "Open Edge" uses this same managed profile instead of starting
a separate Edge process.

For requests to use the installed WhatsApp app, Jarvis searches for the app
named "WhatsApp" and uses screen control with the same separate screenshot and
action approvals as other desktop apps. Ask for "WhatsApp Web" to use the
browser-based sender described below.

WhatsApp Web uses the default Chrome profile under
`%LOCALAPPDATA%\Google\Chrome\User Data`, so it can reuse that profile's login.
You can optionally set `JARVIS_CHROME_USER_DATA` to a different Chrome
user-data directory. Close every Chrome window before asking Jarvis to
automate WhatsApp; Jarvis will not close or attach to a Chrome instance that
is already running. If WhatsApp Web is not logged in, scan its QR code in the
opened Chrome window and retry.

The managed Edge and Chrome windows stay open after each action while Jarvis
is running. Exiting Jarvis disconnects from Edge and closes its Chrome session;
the browser profiles remain on disk. If Edge is closed while YouTube automation
is running, that attempt may fail; retrying will start or reconnect to a
verified Jarvis Edge session when possible.

Examples:

- "Play [song or video] on YouTube" opens the first non-ad result in Edge and
  attempts playback. YouTube sign-in requirements or playback restrictions
  may require manual interaction.
- "Click the search box and type ..." uses screen vision and requires separate
  approval before image upload and action execution. This is available in
  `--llm` mode, including the floating overlay.
- "Send 'hello' to Mom on WhatsApp Web" searches for one exact matching chat,
  places the message in the composer, and shows the actual recipient and
  message for review. In the overlay, approve or deny in the dialog (or by
  voice while Voice mode is on); in terminal mode, type `y` to send. Declining
  cancels and clears the draft. Check the recipient and text before approving.
- "Open WhatsApp and send 'hello' to Mom" uses screen control and
  requires separate approval before screen capture and before the proposed
  desktop actions.

## Supported commands

- "What time is it?"
- "Search the web for ..." / "Look up ..."
- "Open Notepad", "Open Calculator", "Open File Explorer", "Open Paint",
  "Open Chrome", "Open Edge", "Open Spotify", "Open Claude", or "Open Antigravity"
- "Play [song or video] on YouTube"
- "Send [message] to [contact] on WhatsApp" (requires explicit confirmation)
- "Set volume to 40%" and "Set brightness to 60%"
- Battery or CPU status requests
- Create, read, and move files under Documents or Desktop
- In local-parser mode: "Click at 600, 400", "Type hello", and "Press enter"
- "List files", "Create file notes.txt with content hello", "Move file notes.txt
  to archive\notes.txt", and "Delete file notes.txt"
- "Help"
- Type `exit` or press Ctrl+C to quit.

In local-parser mode, desktop clicks, typing, key presses, file creation/moves,
and deletions show their exact parameters and require typing `YES`. Meta tool
mode asks for approval before file reads or changes (in the terminal, or in the
overlay dialog). General-purpose raw mouse/keyboard tools are not exposed to
the Meta model; its screen-control tool only executes the validated actions
that you approve. LLM file tools are restricted to Documents and Desktop. The
legacy local file commands use
`Documents\JarvisFiles`, also inside Documents. App launching is restricted
to the allowlist in `actions.py`.
Requests and results are written to `assistant.log`; the log contains
transcripts, so protect or delete it if they may include private information.

## Tests

```powershell
python -m unittest
```

## Project files

- `main.py` — text/voice entry point and orchestration
- `intent.py` — local intent rules and Meta API client setup
- `tools.py` — OpenAI-compatible function definitions and tool implementations
- `overlay_ui.py` — optional floating Tkinter assistant window
- `screen_agent.py` — screen capture, vision planning, validation, and confirmed actions
- `actions.py` — app allowlist and legacy local actions
- `wake_word.py`, `stt.py`, `tts.py` — optional voice pipeline
- `test_jarvis.py` — focused tests for parsing and action safety

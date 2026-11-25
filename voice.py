import io
import json
import re


TRANSCRIBE_URL = "https://api.meta.ai/v1/asr/transcribe"
_tts = None


def _get_api_key():
    from intent import _load_model_api_key

    api_key = _load_model_api_key()
    if not api_key:
        raise RuntimeError(
            "Meta speech transcription requires MODEL_API_KEY in the environment, "
            ".env, or .env_local."
        )
    return api_key


def listen(duration=5, fs=16_000):
    if duration <= 0 or fs <= 0:
        raise ValueError("Recording duration and sample rate must be positive.")
    try:
        import requests
        import sounddevice as sd
        from scipy.io.wavfile import write
    except ImportError as error:
        raise RuntimeError(
            "Voice audio support is not installed. Run: "
            "python -m pip install -r requirements-voice.txt"
        ) from error

    print("Listening...")
    recording = sd.rec(
        int(duration * fs),
        samplerate=fs,
        channels=1,
        dtype="int16",
    )
    sd.wait()

    wav_buffer = io.BytesIO()
    write(wav_buffer, fs, recording)
    wav_buffer.seek(0)
    headers = {"Authorization": f"Bearer {_get_api_key()}"}
    files = {
        "request": (
            None,
            json.dumps(
                {
                    "mode": "PUSH_TO_TALK",
                    "model": "muse-voice-transcribe-1.0",
                    "audioEncoding": "WAV",
                }
            ),
            "application/json",
        ),
        "audio": ("command.wav", wav_buffer, "audio/wav"),
    }
    try:
        response = requests.post(
            TRANSCRIBE_URL,
            headers=headers,
            files=files,
            timeout=60,
        )
    except requests.RequestException as error:
        raise RuntimeError(
            f"Meta speech transcription request failed: {error}"
        ) from error
    if response.status_code != 200:
        raise RuntimeError(
            f"Meta speech transcription failed with HTTP {response.status_code}."
        )
    try:
        payload = response.json()
    except ValueError as error:
        raise RuntimeError("Meta speech transcription returned invalid JSON.") from error
    transcript = payload.get("transcript") if isinstance(payload, dict) else None
    if not isinstance(transcript, str):
        raise RuntimeError("Meta speech transcription response has no text field.")
    transcript = transcript.strip()
    if transcript:
        print(f"You said: {transcript}")
    return transcript


def speak(text):
    global _tts
    print(f"Jarvis: {text}")
    if _tts is None:
        from tts import TextToSpeech

        _tts = TextToSpeech()
        _tts.engine.setProperty("rate", 175)
    _tts.speak(text)


def confirm(message):
    print(message)
    speak("Approval required. Say yes to approve, or no to cancel.")
    reply = listen(duration=3)
    words = set(re.findall(r"[a-z]+(?:'[a-z]+)?", reply.casefold()))
    if words.intersection(
        {"no", "not", "never", "cancel", "deny", "reject", "don't"}
    ):
        return False
    return bool(words.intersection({"yes", "approve", "approved"}))

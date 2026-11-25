import io
import wave


class WakeWordDetector:
    def __init__(self, access_key=None, wake_words=("jarvis", "hey jarvis")):
        try:
            import numpy as np
            import sounddevice as sd
            import speech_recognition as sr
        except ImportError as error:
            raise RuntimeError(
                "Voice audio support is not installed. Run: "
                "python -m pip install sounddevice numpy SpeechRecognition"
            ) from error
        self.np = np
        self.sd = sd
        self.sr = sr
        self.recognizer = sr.Recognizer()
        self.wake_words = tuple(w.casefold() for w in wake_words)

    def wait(self, stop_event=None):
        sample_rate = 16000
        chunk_seconds = 2.0
        while True:
            if stop_event is not None and stop_event.is_set():
                return False
            recording = self.sd.rec(
                int(chunk_seconds * sample_rate),
                samplerate=sample_rate,
                channels=1,
                dtype="int16",
            )
            self.sd.wait()
            if stop_event is not None and stop_event.is_set():
                return False

            # Skip quiet background noise without hitting recognition
            if int(self.np.abs(recording).mean()) < 150:
                continue

            wav_buffer = io.BytesIO()
            with wave.open(wav_buffer, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(recording.tobytes())
            wav_buffer.seek(0)

            with self.sr.AudioFile(wav_buffer) as source:
                audio = self.recognizer.record(source)

            try:
                heard = self.recognizer.recognize_google(audio).casefold()
            except (self.sr.UnknownValueError, self.sr.RequestError):
                continue

            if any(word in heard for word in self.wake_words):
                return True

    def close(self):
        try:
            self.sd.stop()
        except Exception:
            pass
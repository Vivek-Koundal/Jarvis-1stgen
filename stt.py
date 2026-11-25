import io
import wave


class SpeechToText:
    def __init__(self):
        try:
            import numpy as np
            import sounddevice as sd
            import speech_recognition as sr
        except ImportError as error:
            raise RuntimeError(
                "Speech recognition dependencies are missing. Run: "
                "python -m pip install SpeechRecognition sounddevice numpy"
            ) from error
        self.np = np
        self.sd = sd
        self.sr = sr
        self.recognizer = sr.Recognizer()

    def listen_and_transcribe(self, duration=5, sample_rate=16000):
        try:
            recording = self.sd.rec(
                int(duration * sample_rate),
                samplerate=sample_rate,
                channels=1,
                dtype="int16",
            )
            self.sd.wait()
        except Exception as error:
            raise RuntimeError(f"Microphone unavailable: {error}") from error

        # Skip silent recordings quickly
        if int(self.np.abs(recording).mean()) < 120:
            return ""

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
            return self.recognizer.recognize_google(audio).strip()
        except self.sr.UnknownValueError:
            return ""
        except self.sr.RequestError as error:
            raise RuntimeError(
                f"Speech recognition service is unavailable: {error}"
            ) from error
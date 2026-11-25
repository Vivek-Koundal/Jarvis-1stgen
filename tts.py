class TextToSpeech:
    def __init__(self):
        try:
            import pyttsx3
        except ImportError as error:
            raise RuntimeError(
                "Text-to-speech is not installed. Run: pip install -r requirements.txt"
            ) from error
        self.engine = pyttsx3.init()

    def speak(self, text):
        self.engine.say(text)
        self.engine.runAndWait()

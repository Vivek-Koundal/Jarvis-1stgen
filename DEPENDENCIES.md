# Dependency and environment guide

## Python packages

The root `requirements.txt` is the runtime manifest for the main assistant.
Install it in a project virtual environment:

```powershell
python -m pip install -r requirements.txt
```

`requirements-voice.txt` includes `requirements.txt` and adds the packages used
for microphone capture and voice transcription. Install it instead when voice
features are needed:

```powershell
python -m pip install -r requirements-voice.txt
```

The pinned `requirements.lock` and `requirements-voice.lock` files are
reproducible snapshots of the dependency closures tested in the included
Python 3.11.9 Windows environment. The voice lock includes the base packages.
Use the matching lock for a pinned install:

```powershell
python -m pip install -r requirements.lock
# Or, for the complete voice-enabled setup:
python -m pip install -r requirements-voice.lock
```

These are pip-compatible requirements files; they do not install Python or
Windows components. The lock files capture versions, not package hashes, and
should be regenerated and tested when dependencies are intentionally updated.

## Components supplied by the operating system / Python

The following are not separate pip requirements:

- Python standard-library modules such as `ctypes`, `json`, `logging`,
  `subprocess`, `threading`, `tkinter`, and `urllib`.
- Windows APIs used for desktop controls and speech output.
- PowerShell, used by the setup examples in `README.md`.
- Microsoft Edge for the managed YouTube browser workflow. Google Chrome is
  only needed for WhatsApp Web automation.

Install regular Windows Python 3.11 or newer rather than the embeddable Python
package. Ensure the Python installer includes Tcl/Tk if the floating overlay
is needed; `tkinter` is supplied with Python on supported Windows installs,
not by pip. Browser installation and sign-in are separate from Python package
installation. Some displays do not support brightness changes.

## Configuration and private local data

`.env.example` contains placeholder examples only. Copy it to `.env_local` and
configure the keys you actually need; `.env_local` and `.env` are ignored by
Git. Never commit API keys, access tokens, logs, browser profiles, or local
assistant data. Text/local-parser mode does not need API credentials. Meta
model features require `MODEL_API_KEY`; wake-word listening additionally
requires `PICOVOICE_ACCESS_KEY`.

The current environment used to capture the lock files is a developer
environment. It is not a claim that those Python packages are preinstalled on
a new computer: the `requirements*.txt` files install the pip packages, while
the list above describes the Python/Windows components supplied outside pip.

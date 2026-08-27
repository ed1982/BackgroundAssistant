# Star Trek Computer

A cross-platform (macOS + Windows) **background voice assistant**. It runs
silently in the system tray, continuously transcribes everything you say
*locally*, and when it hears your wake word (default: **"computer"**), it
waits for you to finish speaking, asks an LLM (local or cloud) about the
recent conversation plus your command — and **speaks the answer back**.

```
you:  "…did you hear that? computer, what is the current status?"
        (transcribed continuously; wake word detected)
        (waits ~1.5 s of silence = end of your question)
computer: "All systems are nominal, captain."   (spoken aloud)
```

See [plan.md](plan.md) for the full design document.

## How it works

1. **Audio** — microphone captured at 16 kHz mono (`sounddevice`/PortAudio).
2. **Endpointing** — `webrtcvad` splits the stream into utterances (700 ms of
   silence ends one; 360 ms pre-roll keeps onsets unclipped).
3. **Transcription** — every utterance is transcribed locally by
   `faster-whisper` (default model `base.en`, int8 on CPU). The last 2 minutes
   of transcript are kept in a rolling buffer.
4. **Wake word** — each completed utterance is checked for the trigger word
   (word-boundary, case-insensitive). On a match, the text *after* the trigger
   becomes your command; further speech extends it until you pause.
5. **LLM** — the recent transcript window + your command are sent to:
   - **Ollama** (local, default) or any **OpenAI-compatible API** (cloud),
   - a **mock** backend for tests/self-tests.
6. **Speech** — the response is spoken with macOS `say` (default) or SAPI5 via
   `pyttsx3` on Windows. While the computer thinks and speaks, your mic is
   drained so it never hears itself.

## Requirements

- Python **3.10–3.12** (macOS or Windows)
- A microphone; on first run the OS will ask for mic permission — allow it.
- One-time network access to download the whisper model (~75 MB for `base.en`).
  After that everything runs offline (with a local LLM).

## Quick start

```bash
# 1. Create a virtualenv and install dependencies
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. (Optional) configure — defaults work out of the box
cp config.example.json config.json

# 3. (Optional) verify your audio chain with a recorded WAV
python main.py --selftest my_recording.wav

# 4. Run it (a tray icon appears; listening starts automatically)
python main.py
```

### Choosing a local LLM (recommended, fully private)

[Ollama](https://ollama.com):

```bash
# macOS: brew install ollama   |   Windows: installer from ollama.com
ollama pull llama3.2           # or any model you like
```

The default config already points at `http://localhost:11434` with model
`llama3.2`.

### Using a cloud LLM instead (e.g. OpenAI)

In `config.json`:

```json
"llm": {
  "backend": "openai_compatible",
  "base_url": "https://api.openai.com/v1",
  "model": "gpt-5-mini",
  "api_key_env": "OPENAI_API_KEY"
}
```

**Note:** a ChatGPT subscription (Free/Plus/Pro) does **not** include API
access. You need an API key from <https://platform.openai.com/api-keys>
(same OpenAI login) plus billing/credits on the account — API usage is
billed separately from any ChatGPT plan.

Then `export OPENAI_API_KEY=sk-…` (macOS/Linux, e.g. in `~/.zshrc`) or
`setx OPENAI_API_KEY sk-…` (Windows). The key is read from the environment
only — never stored in the config file. If you run the app as a login item,
put the `export` in that startup script too.

Model choices (all valid as of 2026-08, see <https://developers.openai.com/api/docs/models>):

| Model | Input / output price (per 1M tokens) | Notes |
| --- | ---: | --- |
| `gpt-5-mini` (default) | $0.25 / $2 | Near-frontier quality, low latency — good all-round pick for a voice assistant. |
| `gpt-5-nano` | $0.05 / $0.40 | Fastest and cheapest; fine for short spoken answers. |
| `gpt-5.6-luna` | $0.20 / $1.20 | Newest cost-optimized model, 1.1M context. |
| `gpt-4o-mini` | — | Older but still available; safe fallback. |

Any OpenAI-compatible endpoint works (OpenAI, Groq, LM Studio, llama.cpp
server, …) — just change `base_url`/`model`. Verify your setup with:

```bash
.venv/bin/python -c "from starcop.config import load_config; from starcop.llm import make_llm; print(make_llm(load_config('config.json').llm).ask('', 'Say hello in one short sentence.'))"
```

With a missing/invalid key the app still runs; LLM calls fail with HTTP 401
and it speaks the fallback apology instead.

## Running at login (background, all the time)

- **macOS**: System Settings → General → Login Items → add a small script,
  e.g. `~/starcop-start.sh`:
  ```bash
  #!/bin/bash
  cd /path/to/StarTrekComputer && .venv/bin/python main.py >> starcop.log 2>&1
  ```
  (For a no-Dock-icon experience, build an app bundle with `LSUIElement=true`
  — see Packaging below.)
- **Windows**: press `Win+R`, type `shell:startup`, and drop a shortcut there
  whose target is `.venv\Scripts\pythonw.exe main.py` (working directory =
  this folder). `pythonw` keeps it out of the console.

## Tray menu

- **Status** — live state: Idle / Awaiting command… / Thinking… / Speaking…
- **Start/Stop listening** — toggle the microphone pipeline.
- **Speak test** — verifies TTS output ("Aye aye, captain…").
- **Quit**.

## Configuration reference (`config.json`)

All keys are optional; defaults shown. Copy `config.example.json` to start.

| Key | Default | Meaning |
|---|---|---|
| `trigger_words` | `["computer"]` | Wake words/aliases (word-boundary matched). |
| `language` | `"en"` | Whisper language hint (`null` = auto-detect). |
| `whisper_model` | `"base.en"` | `tiny.en`, `base.en`, `small.en`, … (bigger = more accurate, slower). |
| `compute_type` | `"int8"` | CTranslate2 compute type. |
| `audio_device` | `null` | Input device index/name (`python main.py --list-devices`). |
| `vad_aggressiveness` | `2` | webrtcvad 0–3 (higher = stricter speech filtering). |
| `pre_roll_ms` / `end_silence_ms` | `360` / `700` | Endpointing: pre-roll kept; silence that ends an utterance. |
| `min_utterance_ms` / `max_utterance_ms` | `300` / `30000` | Utterance length guards. |
| `command_end_silence_ms` | `1500` | Silence after the wake word that ends your command. |
| `max_command_wait_ms` | `12000` | Hard cap from wake word to dispatch. |
| `context_seconds` | `120` | Rolling transcript window loaded into the LLM. |
| `llm.backend` | `"ollama"` | `ollama` \| `openai_compatible` \| `mock`. |
| `llm.base_url` / `llm.model` | Ollama defaults | Endpoint and model name. |
| `llm.api_key_env` | `"OPENAI_API_KEY"` | Env var holding the API key. |
| `tts.engine` | `"auto"` | `auto` (say on macOS, pyttsx3 on Windows) \| `say` \| `pyttsx3` \| `mock`. |
| `tts.rate` / `tts.voice` | `185` / `null` | Speech rate (wpm) and optional voice name. |
| `log_file` / `log_level` | `"starcop.log"` / `"INFO"` | Logging. |

Notes:
- `pyttsx3` on **macOS** additionally needs `pip install pyobjc`; the macOS
  default engine is `say`, so this is only relevant if you opt in.
- Wake-word matching is transcript-based: any utterance that *contains* the
  trigger word wakes the assistant. Pick a word you rarely say in normal
  conversation, or add aliases to narrow it down.

## CLI

```bash
python main.py                     # run the background assistant (tray icon)
python main.py --selftest FILE.wav # real audio chain on a WAV (mono 16-bit,
                                   #   16 kHz); prints transcript + what would
                                   #   be sent to the LLM / spoken. Exit 0 = wake
                                   #   word detected, 1 = not detected.
python main.py --smoke             # build the tray app without audio, then exit
python main.py --list-devices      # list input devices
python main.py --config PATH       # use a specific config file
```

To make a self-test WAV on macOS: `say -o t.aiff "computer, hello" &&
afconvert -f WAVE -d LEI16@16000 -c 1 t.aiff t.wav`. On Windows, record any
WAV and convert with ffmpeg: `ffmpeg -i in.wav -ar 16000 -ac 1 -sample_fmt s16 t.wav`.

## Testing

```bash
pip install -r requirements-dev.txt
pytest                 # unit tests (fast, no heavy deps needed)
pytest -m integration  # real-audio end-to-end test (macOS; downloads whisper model)
```

The unit suite covers the wake-word matcher, transcript buffer, endpointing
state machine, full pipeline state machine (with fake clock/LLM/TTS), LLM
backends against a local HTTP server, TTS engines, and config loading.

## Packaging (optional)

- **PyInstaller** works on both OSes; collect the CTranslate2/faster-whisper
  data files (`--collect-all faster_whisper --collect-all ctranslate2`) and
  the PortAudio library bundled with `sounddevice`.
- **macOS**: set `LSUIElement` to true in the bundle's Info.plist so the app
  has no Dock icon (pure background/accessory app).
- **Windows**: `pythonw` or a PyInstaller `--noconsole` build keeps it out of
  the console; add to `shell:startup` for boot-time launch.

## Privacy

Audio is processed locally and never uploaded. The only network traffic is:
the one-time whisper model download, and LLM requests — to your local Ollama
by default, or to a cloud endpoint only if you configure one.

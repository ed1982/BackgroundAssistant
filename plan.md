# Star Trek Computer — Design Document

A cross-platform (macOS + Windows) background app that continuously transcribes
everything you say, listens for a configurable wake word (default: **"computer"**),
waits for you to finish speaking, feeds the recent conversation + your command into a
local or cloud LLM, and speaks the answer back to you.

---

## 1. Goals / Non-goals

### Goals
- Run silently in the background (system-tray only, no main window) on macOS and Windows.
- Continuous local speech-to-text of ambient speech (privacy-first: audio never leaves
  the machine unless a cloud LLM is explicitly configured).
- Wake-word detection on the *transcript* (no separate wake-word model needed): when the
  trigger word appears in a completed utterance, enter command mode.
- Reasonable endpointing: after the wake word, wait until the user stops talking
  (silence-based) or a hard time cap is reached, then act.
- Load the last meaningful window of transcribed conversation (rolling buffer) plus the
  command into an LLM — pluggable backends: **Ollama (local)**, any
  **OpenAI-compatible API (cloud or local server)**, and a **mock** for tests.
- Speak the response with an offline TTS engine (macOS `say` by default; SAPI5 via
  pyttsx3 on Windows).
- Deterministic, unit-testable core: the whole decision pipeline is a pure state machine
  with injectable clock, transcriber, LLM and TTS.

### Non-goals (v1)
- Barge-in / interrupting the computer while it is speaking.
- Multi-user diarisation, non-English optimisation (language is configurable but the
  default model is English).
- GUI settings editor — configuration is a JSON file.
- Native installers (PyInstaller/py2app recipes are documented, not built in-repo).

## 2. Architecture Overview

```
                 ┌────────────────────────────────────────────────────────────┐
                 │                        Worker thread                       │
 mic ──▶ AudioCapture ──frames──▶ UtteranceSegmenter ──utterance audio──▶ Transcriber
 (sounddevice,   (queue of        (webrtcvad 30 ms frames +      (faster-whisper,
 16 kHz mono     int16 30 ms       endpointing state machine)    local CPU, int8)
 int16 chunks)   frames)                │                                   │ text + ts
                                        ▼                                   ▼
                              ┌─────────────────────────────────────────────────────┐
                              │  Pipeline (state machine) + TranscriptBuffer        │
                              │  IDLE ─▶ AWAITING_COMMAND ─▶ THINKING ─▶ SPEAKING   │
                              │  WakeWordMatcher checks every completed utterance   │
                              └───────────────┬─────────────────────────────────────┘
                                              │ context (last N s) + query
                                              ▼
                                       LLM backend ──response text──▶ TTS engine
                                   (ollama / openai-compatible / mock)   (say / pyttsx3)
```

- **AudioCapture** (`starcop/audio.py`): `sounddevice.InputStream`, 16 kHz mono int16.
  The PortAudio callback only appends bytes to an internal buffer and pushes exact
  30 ms frames (960 bytes) onto a `queue.Queue` — it never blocks.
- **UtteranceSegmenter** (`starcop/segmenter.py`): VAD-driven endpointing.
  - Pre-roll ring buffer (~360 ms) so utterance onsets are not clipped.
  - Speech start: first voiced frame → begin collecting (pre-roll included).
  - Utterance end: `end_silence_ms` (default 700 ms) of continuous unvoiced frames,
    or a hard `max_utterance_ms` (30 s) cap.
  - Utterances shorter than `min_utterance_ms` (300 ms) are dropped (coughs, clicks).
- **Transcriber** (`starcop/transcriber.py`): `faster-whisper` (CTranslate2), model
  size configurable (default `base.en`, int8 on CPU). Transcribes one utterance at a
  time; returns trimmed text.
- **TranscriptBuffer** (`starcop/transcript.py`): rolling window of
  `(wall-clock ts, text)` items. Pruned by age (`context_seconds`, default 120 s) and
  total character budget (default 4000). Renders as timestamped lines for the prompt.
- **WakeWordMatcher** (`starcop/wakeword.py`): normalises text (lowercase, strip
  punctuation) and does word-boundary regex matching against the configured trigger
  words/aliases. `split_command()` returns `(trigger, text-after-trigger)` so
  "hey computer what time is it" yields command `"what time is it"`.
- **Pipeline** (`starcop/pipeline.py`): the state machine (below). Pure logic —
  injectable clock, transcriber, LLM, TTS. Emits state-change callbacks (used by the
  tray UI).
- **Runner** (`starcop/runner.py`): worker thread. Pulls frames, runs VAD +
  segmentation, feeds completed utterances to the pipeline, and ticks the pipeline
  clock. While the state is THINKING/SPEAKING it *drains and discards* audio so the
  computer never transcribes its own voice.
- **LLM backends** (`starcop/llm.py`): stdlib `urllib` HTTP (no extra dependency).
  - `ollama`: `POST {base_url}/api/chat` (default `http://localhost:11434`).
  - `openai_compatible`: `POST {base_url}/chat/completions` (OpenAI, Groq, LM Studio,
    llama.cpp server, …). API key read from the env var named in config.
  - `mock`: canned responses (tests, offline self-test).
- **TTS engines** (`starcop/tts.py`):
  - `say`: macOS `say(1)` subprocess (default on darwin — zero extra deps, good voices).
  - `pyttsx3`: SAPI5 on Windows (default there); engine lives on a dedicated thread
    with an internal queue because pyttsx3 is not thread-safe.
  - `mock`: logs instead of speaking (tests).
- **Tray app** (`starcop/app.py`): PySide6 `QSystemTrayIcon`. Menu: live status line
  (Idle / Listening / Thinking / Speaking), Start/Stop listening, Speak test, Quit.
  State changes arrive from the worker thread via a Qt signal (queued connection).

## 3. Pipeline State Machine

```
                 utterance contains trigger word
        ┌──────────┐  ────────────────────────────────▶  ┌────────────────────┐
        │   IDLE   │                                     │ AWAITING_COMMAND   │
        └──────────┘ ◀───────────────────────────────    └────────────────────┘
             ▲        response spoken (or error)                │  silence ≥ command_end_silence_ms
             │                                                  │  OR elapsed > max_command_wait_ms
        ┌──────────┐   LLM response ready                      ▼
        │ SPEAKING │ ◀──────────────────────────────  ┌────────────┐
        └──────────┘                                  │  THINKING  │
                                                      └────────────┘
```

- **IDLE**: every completed utterance is transcribed and appended to the buffer.
  If it matches a trigger word → **AWAITING_COMMAND**; `command_parts` starts with the
  text *after* the trigger (may be empty); deadline = now + `command_end_silence_ms`.
- **AWAITING_COMMAND**: each new utterance is appended to `command_parts` and extends
  the deadline (now + `command_end_silence_ms`). A *re-trigger* resets
  `command_parts` to the new post-trigger text. Dispatch happens when:
  - no speech for `command_end_silence_ms` (default 1500 ms), **or**
  - `max_command_wait_ms` (default 12 s) has elapsed since the trigger (hard cap).
- **THINKING**: build prompt = system persona + recent transcript window + command;
  call the LLM (blocking, on the worker thread). On error: log it and speak a short
  apology instead of failing silently.
- **SPEAKING**: TTS speaks the response (blocking). Audio frames are drained/discarded
  for the whole THINKING+SPEAKING span, so the computer's own voice can never
  re-trigger itself. Then back to **IDLE**.

### Prompt construction (`starcop/llm.py`)
```
system:  You are the ship's computer aboard a starship (Star Trek style). Calm,
         precise, helpful. Answer in 1–3 short spoken-style sentences; plain text
         only — no markdown, lists or emoji. You are given the recent conversation
         transcript for context and the user's command (words said after calling
         your name). If the command is empty, respond helpfully to the most recent
         conversation.
user:    Recent conversation (oldest first):
         14:03:22  hey, did you hear that?
         14:03:41  computer, what is the current status
         The user just said: what is the current status
```

## 4. Configuration (`config.json`, all keys optional)

| Key | Default | Meaning |
|---|---|---|
| `trigger_words` | `["computer"]` | Wake words/aliases, word-boundary matched on normalised transcript text. |
| `language` | `"en"` | Whisper language hint (`null` = auto-detect). |
| `whisper_model` | `"base.en"` | faster-whisper model size (`tiny.en`…`large-v3`). |
| `compute_type` | `"int8"` | CTranslate2 compute type. |
| `audio_device` | `null` | Input device index/name for sounddevice (`--list-devices`). |
| `vad_aggressiveness` | `2` | webrtcvad 0–3 (higher = more aggressive filtering). |
| `pre_roll_ms` / `end_silence_ms` | `360` / `700` | Endpointing: pre-roll kept before speech; silence that ends an utterance. |
| `min_utterance_ms` / `max_utterance_ms` | `300` / `30000` | Utterance length guards. |
| `command_end_silence_ms` | `1500` | Silence after the wake word that ends the command. |
| `max_command_wait_ms` | `12000` | Hard cap from trigger to dispatch. |
| `context_seconds` | `120` | Rolling transcript window loaded into the LLM. |
| `llm.backend` | `"ollama"` | `ollama` \| `openai_compatible` \| `mock`. |
| `llm.base_url` | `http://localhost:11434` | Ollama URL or OpenAI-compatible root. |
| `llm.model` | `"llama3.2"` | Model name for the chosen backend. |
| `llm.api_key_env` | `"OPENAI_API_KEY"` | Env var holding the API key (cloud backends). |
| `llm.timeout_s` | `120` | HTTP timeout. |
| `tts.engine` | `"auto"` | `auto` (say on macOS, pyttsx3 on Windows) \| `say` \| `pyttsx3` \| `mock`. |
| `tts.rate` / `tts.voice` | `185` / `null` | Speech rate (wpm) and optional voice name. |
| `log_file` / `log_level` | `"starcop.log"` / `"INFO"` | Logging. |

Config resolution: built-in defaults ← `config.json` (next to the launch dir or
`--config PATH`) deep-merged. `${ENV_VAR}` strings are expanded (used for API keys).

## 5. Threading Model

| Thread | Responsibility |
|---|---|
| PortAudio callback (C) | Copy mic samples → 30 ms frames → `queue.Queue`. Never blocks. |
| Worker (`Runner`) | VAD + segmentation, transcription, pipeline ticks, LLM call, TTS. Single consumer ⇒ no locks needed on the transcript buffer or pipeline state. |
| pyttsx3 engine thread | Owns the SAPI5/NSSpeechSynthesizer event loop; receives (text, Event) jobs. |
| Qt main thread | Tray icon + menu only; receives state changes via queued signal. |

## 6. Error Handling & Robustness

- **Mic unavailable / permission denied** → clear log message + tray status "Error";
  app stays alive (user can fix permissions and press Start).
- **Whisper model download** happens on first run (needs network once); afterwards fully offline.
- **LLM unreachable / timeout** → `LLMError` caught in pipeline; a short apology is
  spoken and state returns to IDLE. Listening never stops because of an LLM failure.
- **TTS init failure** → responses are logged instead of spoken; app continues.
- **Long utterances** hard-capped at 30 s so a noisy room can't wedge the pipeline.
- **Self-triggering** prevented by draining audio during THINKING/SPEAKING.

## 7. Testing Strategy

Unit tests (no heavy deps, run anywhere with `pytest`):
- `test_wakeword.py` — normalisation, case/punctuation insensitivity, word boundaries
  ("computer" ≠ "computers"), aliases, `split_command` behaviour.
- `test_transcript.py` — rolling window pruning by age and char budget, rendering.
- `test_segmenter.py` — endpointing state machine with a scripted fake VAD: pre-roll
  inclusion, silence endpoint, min-length drop, max-length flush.
- `test_pipeline.py` — full state machine with fake transcriber/LLM/TTS and a fake
  clock: no-trigger idle, trigger+command in one utterance, multi-utterance command
  with silence endpoint, re-trigger reset, hard time cap, LLM-error fallback.
- `test_llm.py` — prompt construction; Ollama + OpenAI-compatible backends against a
  local in-process HTTP server; HTTP error → `LLMError`.
- `test_tts.py` — engine selection (`auto` per OS), `say` command construction
  (subprocess monkeypatched), pyttsx3 queueing with a stub engine.
- `test_config.py` — defaults, deep merge, `${ENV}` expansion, bad-file fallback.

Integration test (`tests/test_integration_audio.py`, marked `integration`):
- Generate real speech with macOS `say` → convert to 16 kHz mono WAV (`afconvert`) →
  run it through the *real* webrtcvad + segmenter + faster-whisper + wake-word
  matcher. Asserts the trigger word is detected and command text recovered.
- Same path is exposed as `python main.py --selftest <wav>` for users on any OS
  (record a WAV of "computer, …" and verify the whole audio chain).

Smoke test: `python main.py --smoke` constructs the tray app and pipeline without
touching the mic, then exits (verifies PySide6 wiring).

## 8. Packaging & Distribution

- **Run from source (both OSes)**: Python 3.10–3.12 venv + `pip install -r requirements.txt`,
  then `python main.py`. See README for per-OS details (mic permissions, login items /
  startup folder).
- **Local LLM**: Ollama (`ollama pull llama3.2`) — zero cloud dependency, fully private.
- **Cloud LLM**: any OpenAI-compatible endpoint; key via environment variable only.
- **Native bundles** (documented, optional): PyInstaller one-file recipe notes for both
  OSes; macOS `LSUIElement` (accessory app, no Dock icon) for background behaviour.

## 9. Milestones

1. **M1 — Design** (this document).
2. **M2 — Core logic**: config, wakeword, transcript buffer, segmenter, pipeline, LLM
   backends, TTS engines + full unit test suite green.
3. **M3 — Runtime**: audio capture, whisper transcriber, runner thread, tray app,
   `main.py` CLI (config/selftest/smoke/list-devices).
4. **M4 — Verification**: unit tests green; real-audio integration test on this Mac
   (`say`-generated WAV through the full chain); smoke test of the tray app.
5. **M5 — Review & hardening**: code review pass (threading, edge cases), fixes,
   README + example config finalised.

### Status (2026-08-25)

| Milestone | State |
|---|---|
| M1 Design | ✅ this document |
| M2 Core logic + unit tests | ✅ 54 tests passing (53 unit + 1 integration) |
| M3 Runtime | ✅ all modules + `main.py` CLI implemented |
| M4 Verification | ✅ integration test green (real `say` audio → webrtcvad → whisper → wake word); `--selftest` verified end-to-end; `--smoke` tray test green |
| M5 Review & hardening | ✅ review pass done: `segmenter.flush()` for stream tails, `SayTts.stop()` kills in-flight speech on quit, webrtcvad → webrtcvad-wheels (Python 3.12 compat), README + example config |

Review-pass fixes worth noting:
- `UtteranceSegmenter.flush()` — file/stream tails are no longer lost (self-test +
  integration test rely on it).
- `SayTts` now tracks its subprocess and exposes `stop()`; quit kills in-flight speech.
- `webrtcvad` replaced by the maintained `webrtcvad-wheels` fork (prebuilt wheels;
  the original breaks on Python 3.12 via `pkg_resources`).
- Wake-word command extraction trims leftover edge punctuation ("Computer!" → "").

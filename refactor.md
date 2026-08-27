# BackgroundAssistant — Deep Refactoring Plan

**Status:** **implemented, 2026-08-27.** Everything that can be built and tested without a
Mac in the loop is done and committed; see §16 for the record, the deviations, and the short
list of steps that can only happen on the machine itself.
**Supersedes:** `plan.md` (the original design document, which describes the version this
replaced).
**Date:** 2026-08-27

---

## 0. TL;DR

The engine underneath this app is good. The core is a genuinely well-factored, well-tested
state machine — 54 tests, clean dependency injection, sensible separation of pure logic from
I/O. **None of that is being thrown away.**

What is wrong is everything *around* the engine: it crashes on quit, it can't be given an API
key without editing a shell profile, it writes every private conversation you have to a
plaintext file, it has no settings UI, no window, no icon, no installer, and no version
control. It is an engine without a car.

This plan rebuilds the car.

| | Today | After |
|---|---|---|
| Install | clone, venv, pip, edit JSON, edit `~/.zshrc` | drag `BackgroundAssistant.app` to Applications |
| API key | `export OPENAI_API_KEY` in a shell you don't control | Preferences → Keychain |
| Provider | edit `config.json`, restart | dropdown, with "Test connection" |
| Settings | hand-edited JSON | Preferences window |
| Responses | spoken, then gone | spoken + a real chat window with searchable history |
| Follow-ups | impossible — every question is one-shot | full multi-turn, by voice or typing |
| Quit | raises `TypeError` | works |
| Your speech | written to `starcop.log` forever | never touches the disk unless you asked a question |
| Version control | none | git + public GitHub repo |

---

## 1. What exists today (verified by reading every file)

### 1.1 Module map

```
main.py                  CLI entry: --selftest / --smoke / --list-devices / --config
starcop/
  __init__.py            version string
  config.py       162 L  dataclass config, defaults, deep-merge, ${ENV} expansion
  audio.py         79 L  sounddevice InputStream → queue of 30 ms int16 frames
  vad.py           39 L  webrtcvad wrapper
  segmenter.py     94 L  VAD endpointing state machine → complete utterances
  transcriber.py   52 L  faster-whisper, lazy model load
  transcript.py    57 L  rolling (timestamp, text) buffer, pruned by age + chars
  wakeword.py      60 L  word-boundary regex matcher, split_command()
  pipeline.py     140 L  IDLE → AWAITING_COMMAND → THINKING → SPEAKING state machine
  llm.py          161 L  Ollama + OpenAI-compatible + Mock backends over urllib
  tts.py          154 L  macOS `say`, pyttsx3, Mock
  runner.py        49 L  the single worker thread that drives everything
  app.py          115 L  PySide6 tray icon + 4-item menu
tests/           ~54 tests, all passing
```

### 1.2 How it actually runs

One `Runner` thread pulls 30 ms frames off a `queue.Queue`, feeds them to the VAD segmenter,
and when an utterance completes it calls `pipeline.feed_utterance()` — which **synchronously**
runs Whisper, then (if triggered) **synchronously** calls the LLM, then **synchronously**
blocks on TTS. Meanwhile the PortAudio callback keeps pushing frames onto an unbounded queue.
During `THINKING` and `SPEAKING` the worker drains and discards frames so the assistant never
hears itself.

### 1.3 What is genuinely good and must be preserved

- **The pure-logic core.** `segmenter.py`, `pipeline.py`, `wakeword.py`, `transcript.py` and
  `config.py` have zero hard dependencies on audio, models or network. Everything is injected.
  This is why there are 54 fast tests and why this refactor is tractable at all.
- **The injectable clock.** `Pipeline` takes `clock: Callable[[], float]`. Time-dependent
  behaviour is tested deterministically. Keep this pattern everywhere new.
- **Lazy heavy imports.** `faster_whisper`, `sounddevice`, `PySide6` are all imported inside
  functions, so the test suite runs in under a second and `--smoke` works without a mic.
- **The failure philosophy.** Every layer catches its own exceptions and keeps listening. An
  LLM 401 does not stop the microphone. This is correct for an always-on daemon and should
  survive the refactor intact.
- **The self-test harness.** `--selftest file.wav` runs real audio through the real chain with
  mock LLM/TTS. This is the single most useful debugging tool in the repo. Extend it, don't
  drop it.

---

## 2. Findings — what is actually broken

Each item below was verified against the source and, where noted, against `starcop.log`.

### 2.1 Critical

**F1 — `Runner` shadows a private `Thread` attribute; Stop and Quit both crash.**
`starcop/runner.py:23` sets `self._stop = threading.Event()`. `threading.Thread` already
defines `_stop` as a *method*, called internally by `Thread._wait_for_tstate_lock()` during
`join()`. Every `stop()` therefore raises:

```
File ".../threading.py", line 1171, in _wait_for_tstate_lock
    self._stop()
TypeError: 'Event' object is not callable
```

This appears 5+ times in `starcop.log`, from both `_quit` (`app.py:102`) and the Stop menu
item (`app.py:79`). Consequence: the app cannot be stopped cleanly; the mic stream and the
worker are left running; the user force-quits. **Fix: rename to `_stop_event`.** One line.
It also argues for composition over inheritance — the new `Runner` will *hold* a thread
rather than *be* one.

**F2 — The API key can only come from an environment variable the app will never see.**
`config.py:82` reads `os.environ.get(self.api_key_env)`. A GUI app launched from Finder, a
Login Item, or a `.app` bundle inherits `launchd`'s environment, not your shell's — so
`~/.zshrc` exports are invisible. Result, from the log:

```
ERROR starcop.pipeline: LLM failed: HTTP 401 from https://api.openai.com/v1/chat/completions
```

…five separate times, each of which the user experienced as the assistant saying *"I'm sorry,
I could not process that."* with no indication that the real problem was authentication. This
is the single biggest usability defect and the reason for the Preferences requirement.

**F3 — Every word spoken near the machine is written to disk in plaintext, forever.**
`pipeline.py:77` logs `log.info("heard: %r", text)`. The default `log_level` is `INFO` and the
default `log_file` is `starcop.log`, with no rotation. The current file is 131 KB and contains
verbatim private conversation — medical talk, arguments, profanity. It is world-readable,
never truncated, and sits in the project folder. **This is the most serious problem in the
repo** and it is a direct consequence of an otherwise-reasonable logging decision.

**F4 — Nothing is under version control.** `~/Code/git/StarTrekComputer` is not a git
repository (`fatal: not a git repository`). ~2,000 lines of working code with no history, no
branches, no way to bisect a regression, and no backup. Refactoring on top of this is
reckless.

### 2.2 Architecture

**F5 — One thread does four jobs, none of which should block the others.**
`Runner.run()` (`runner.py:25-44`) performs VAD, Whisper inference, the LLM HTTP call and the
blocking TTS on the same thread that is supposed to be consuming the audio queue. Whisper on
CPU takes 0.3–2 s per utterance; an LLM call takes 1–10 s; TTS takes as long as the answer.
The audio queue (`audio.py:25`) is **unbounded**, so it grows without limit during any of
these. The log shows the consequence directly:

```
INFO faster_whisper: Processing audio with duration 00:21.210
INFO faster_whisper: Processing audio with duration 00:18.450
INFO faster_whisper: Processing audio with duration 00:17.760
```

Twenty-one-second "utterances" are not how people speak. They are a backlog being processed
in one lump. This is the lag.

**F6 — The wake word cannot fire until you stop talking *and* Whisper finishes.**
Detection happens in `pipeline.feed_utterance()`, i.e. after `end_silence_ms` (700 ms) of
silence has ended the utterance *and* after transcription. Best case latency from saying
"Computer" to any acknowledgement is roughly 1–3 seconds, and there is **no feedback at all**
during that time — no chime, no icon change, nothing. The user cannot tell whether they were
heard, so they repeat themselves, which re-triggers.

**F7 — The segmenter is not reset when audio is dropped.**
`runner.py:37-38` `continue`s past `segmenter.process_frame()` during THINKING/SPEAKING, but
never calls `segmenter.reset()`. A half-captured utterance from before the trigger survives
in `_buf` and gets glued onto whatever is spoken after the answer finishes. Also, `tick()` is
skipped on that path, so deadline logic only advances when the queue happens to be empty.

**F8 — Bare `except Exception` around transcription hides real failures.**
`pipeline.py:70` catches everything and drops the utterance. A missing model, a corrupt
download, or an OOM looks identical to a bad frame: silence. Needs typed exceptions and a
surfaced error state.

**F9 — No conversation memory.** `_dispatch()` (`pipeline.py:112`) builds a fresh two-message
prompt every time. "Computer, what's the weather" → answer → "Computer, what about tomorrow?"
produces a model with no idea what "tomorrow" refers to. There is no `conversation_id`, no
message history, nothing to build a chat window on top of.

**F10 — Nothing is cancellable.** Once `_dispatch()` starts, the LLM call runs to its 120 s
timeout and TTS speaks the entire answer. There is no way to interrupt a wrong or long answer
short of quitting the app (which, per F1, crashes).

### 2.3 Packaging blockers

**F11 — Config and log paths are relative to the code directory.**
`config.py:141-142` resolves `config.json` as `os.path.join(os.path.dirname(__file__), "..")`
— inside the app bundle once packaged, which is read-only and signed (writing there breaks the
signature). `log_file` defaults to the bare relative path `"starcop.log"`, which for a
Finder-launched `.app` resolves against `/`. Both must move to the OS's application-support
directory before packaging is possible at all.

**F12 — No icon, no bundle metadata, no build script.**
`app.py:43` uses `QStyle.SP_ComputerIcon`, a generic system glyph — deliberately, to avoid
binary assets in the repo. There is no `.icns`, no `Info.plist`, no `LSUIElement`, no
`NSMicrophoneUsageDescription`, no entitlements, no spec file, no DMG.

**F13 — Log written twice.** `starcop-start.sh` redirects stdout into `starcop.log` while
`setup_logging()` (`main.py:30-33`) already attaches both a `StreamHandler` *and* a
`FileHandler` to the same path. Every line appears twice, as visible in the first 30 lines of
the log.

**F14 — The venv is inconsistent.** `.venv/pyvenv.cfg` claims `version = 3.9.6` with
`home = /Users/edmartin/.venv/bin`, while `.venv/lib` contains `python3.12` and the log shows
uv-managed CPython 3.12.14. This works by accident. The rebuild should pin a known interpreter.

### 2.4 Behaviour

**F15 — Any sentence containing the trigger word fires it.** `wakeword.py:31` matches
`\bcomputer\b` anywhere. "My computer is broken" wakes the assistant. Documented as a
trade-off in the docstring, but combined with F6 (no feedback) it is invisible when it
happens.

**F16 — Only two of the three natural phrasings work.** `split_command()` (`wakeword.py:47`)
always returns the text *after* the trigger. So:
- "Computer, what is the answer?" → works.
- "Something has happened, Computer, what is the answer?" → works.
- "…what is the answer, **Computer**?" → returns an empty command, waits 1.5 s, then sends an
  empty query. The single most natural Star Trek phrasing is the one that fails.

**F17 — Deaf while speaking.** Per `runner.py:37`, all audio is discarded during SPEAKING.
Barge-in is impossible by construction.

**F18 — `say` sounds dated**, and `pyttsx3` on macOS needs `pyobjc` which isn't in
`requirements.txt`.

---

## 3. Agreed decisions

Settled in conversation on 2026-08-27. These are the constraints the implementation must meet.

| # | Decision | Consequence |
|---|---|---|
| D1 | **Name: `BackgroundAssistant`.** Full rename including the folder. | `~/Code/git/BackgroundAssistant`, package `bgassist`, bundle ID `com.edmartin.backgroundassistant`. No Star Trek references in name, branding or icon. |
| D2 | **🖖 easter egg.** When the trigger word is exactly `computer`, show 🖖 beside it in Preferences and the tray. | Credits the original idea without using the IP. |
| D3 | **Chat UI: Qt WebEngine + HTML/CSS.** | Modern chat UI is achievable; one process; +~180 MB bundle; hardened-runtime entitlements needed for Chromium's JIT. |
| D4 | **Signing-ready but unsigned for now.** Developer ID later. | Build with hardened runtime + entitlements + a `--notarize` flag that's a no-op until a certificate exists. Ad-hoc signed DMG in the meantime. |
| D5 | **Ambient transcript never touches disk.** Only exchanges you actually triggered — and their context snapshot — are persisted, kept until you delete them, encrypted at rest. | RAM-only rolling buffer; encrypted SQLite; key in Keychain; no transcript in logs at any level. |
| D6 | **Whisper on both platforms by default**, because it reads the mic like any recorder and coexists with macOS Voice Control. Apple's native speech recognition offered as a Preferences option, clearly labelled as possibly conflicting. | Two STT backends behind one interface; needs a coexistence spike (see §9). |
| D7 | **Providers: presets + generic.** OpenAI first, then local (LM Studio / Ollama / Pinokio), then Claude. Plus custom OpenAI-compatible. | Four backends; "Detect local servers" button; "Test connection" button. |
| D8 | **Trigger grammar: mechanical timing, LLM-decided scope.** | The parser decides *when* to dispatch; the model decides *what* was asked, from a transcript with the trigger position marked. |
| D9 | **Chat window stays hidden** until opened from the tray, a hotkey, or a notification click. | Answers are still spoken by default. |
| D10 | **Typed and spoken share one conversation.** Push-to-talk button in the window. | Requires the conversation store (F9) before the window can be built. |
| D11 | **Local neural TTS (Piper)** with the OS voice as fallback. | +~60 MB per voice, offline, free per use. |
| D12 | **Barge-in: the trigger word stops speech.** | Requires listening while speaking — see §5.4.2. |
| D12a | **An interruption is a conversational turn.** The assistant message stored and replayed to the model is the **spoken prefix only**, marked interrupted — never the full generated answer. | The model's picture of the exchange matches the user's. Makes late detection harmless (§5.4.3) and downgrades S2/S5 from design risks to polish risks. |
| D13 | **DMG + launch-at-login toggle.** No auto-update. | `create-dmg`; `SMAppService` on macOS 13+; registry Run key on Windows. |
| D14 | **git init + public GitHub repo.** | Baseline commit of today's state *before* any refactoring. Releases can host the DMG. |
| D15 | **History panel: conversations, auto-titled.** | LLM generates a short title after the first exchange. |
| D16 | **Bundle a small Whisper model**, offer larger downloads in Preferences. | DMG ~400–600 MB. Works offline the moment it's installed. |
| D17a | **No global hotkey by default.** Preferences offers an optional "Open chat with a shortcut" field, unset out of the box. | Nothing to collide with Spotlight or anyone's muscle memory on a fresh install. `platform/hotkey.py` still ships — it is just dormant until a shortcut is assigned. |
| D17b | **Calm ship's-computer persona is the shipped default.** Measured, precise, one to three spoken-length sentences, no markdown. | The behaviour, not the branding — no IP concern (D1 governs naming and artwork). Fully editable in Preferences, with the default restorable. |
| D17 | **No diarisation.** Every human voice in the room is `user`; every reply is `assistant`. Standard two-role chat history. | Deferred to §15. Keep the door open: the transcript buffer stores a `speaker` field that is always `"user"` for now, so adding diarisation later is additive rather than a schema migration. |
| D18 | **Icon: "Attend"** — an open ring broken at the base with a solid centre. Deep slate ground, soft aqua mark. | Two shapes only, so it survives every size and the flattening to a black-and-alpha template image. Four tray states come from the same two shapes: **idle** small centre · **listening** centre dilates · **thinking** the gap travels round the ring · **speaking** the gap opens wider, centre pulsing. Wired to the existing `State` enum, which already emits exactly these transitions. |
| D18a | **Halo rejected — wifi collision.** Arcs stacked over a dot is the AirPort/wifi glyph and would have sat inches from the real one in the same menu bar. | Recorded because it is the kind of mistake worth not repeating: a mark must be checked against *the icons it will sit beside*, not only against itself. |

---

## 4. Target architecture

```
┌──────────────────────── Qt main thread ────────────────────────┐
│  TrayIcon        PreferencesWindow        ChatWindow           │
│   (menu,          (QWebEngineView)        (QWebEngineView)     │
│    status,         └── QWebChannel ──┐     └── QWebChannel ──┐ │
│    🖖)                                │                       │ │
└───────────────────────────────────────┼───────────────────────┼─┘
                                        │  queued Qt signals    │
┌───────────────────────────────────────▼───────────────────────▼─┐
│                          EventBus (thread-safe)                  │
│   state changes · partial transcripts · tokens · errors · audio  │
└──┬──────────┬───────────┬────────────┬───────────┬───────────────┘
   │          │           │            │           │
┌──▼───┐  ┌───▼────┐  ┌───▼─────┐  ┌───▼─────┐ ┌───▼──────┐
│Audio │→ │Segment │→ │Transcribe│→ │Orchestr.│→│ Speaker  │
│thread│  │ thread │  │  thread  │  │ thread  │ │  thread  │
└──┬───┘  └────────┘  └──────────┘  └────┬────┘ └──────────┘
   │  bounded          bounded            │
   │  ring buffer      utterance q        │  cancellable
   │                                      ▼
   │                              ┌───────────────┐
   └──────── wake spotter ───────▶│ LLM (stream)  │
             (always on,          └───────┬───────┘
              even while speaking)        │
                                  ┌───────▼────────┐
                                  │ ConversationStore│
                                  │ (encrypted SQLite)│
                                  └──────────────────┘
```

### 4.1 Package layout

```
BackgroundAssistant/
├── bgassist/
│   ├── core/                    # pure logic, zero I/O, fully unit-tested
│   │   ├── segmenter.py         # (from starcop/segmenter.py, unchanged)
│   │   ├── transcript.py        # (from starcop/transcript.py, + redaction)
│   │   ├── trigger.py           # REPLACES wakeword.py — position-aware grammar
│   │   ├── orchestrator.py      # REPLACES pipeline.py — + cancel, + conversation
│   │   └── events.py            # NEW — event types and the EventBus
│   ├── audio/
│   │   ├── capture.py           # (from audio.py) + bounded ring buffer
│   │   ├── vad.py               # (unchanged)
│   │   └── spotter.py           # NEW — always-on wake-word spotter (barge-in)
│   ├── stt/
│   │   ├── base.py              # NEW — Transcriber protocol
│   │   ├── whisper.py           # (from transcriber.py) + model management
│   │   └── apple.py             # NEW, optional — SFSpeechRecognizer via pyobjc
│   ├── llm/
│   │   ├── base.py              # NEW — protocol, streaming, cancellation
│   │   ├── openai.py            # (from llm.py) + SSE streaming
│   │   ├── anthropic.py         # NEW — /v1/messages, x-api-key
│   │   ├── local.py             # NEW — Ollama/LM Studio + server detection
│   │   ├── mock.py              # (from llm.py)
│   │   └── prompts.py           # NEW — system prompt + trigger-marked context
│   ├── tts/
│   │   ├── base.py              # NEW — protocol with stop()
│   │   ├── piper.py             # NEW — local neural voices
│   │   ├── system.py            # (from tts.py — say + pyttsx3)
│   │   └── mock.py
│   ├── settings/
│   │   ├── schema.py            # typed settings, defaults, validation
│   │   ├── store.py             # JSON in app-support dir, observable
│   │   ├── secrets.py           # NEW — keyring wrapper
│   │   └── migrate.py           # NEW — old config.json + env var → new store
│   ├── storage/
│   │   ├── conversations.py     # NEW — encrypted SQLite
│   │   └── crypto.py            # NEW — AES-GCM, key in Keychain
│   ├── ui/
│   │   ├── tray.py              # (from app.py) — richer menu + state icons
│   │   ├── chat_window.py       # NEW — QWebEngineView host
│   │   ├── prefs_window.py      # NEW — QWebEngineView host
│   │   ├── bridge.py            # NEW — QWebChannel Python↔JS API
│   │   └── web/                 # NEW — the HTML/CSS/JS, bundled, no CDN
│   │       ├── chat.html  chat.css  chat.js
│   │       ├── prefs.html prefs.css prefs.js
│   │       └── vendor/          # marked.js, highlight.js — vendored locally
│   ├── platform/
│   │   ├── paths.py             # NEW — platformdirs wrapper
│   │   ├── login_item.py        # NEW — SMAppService / registry Run key
│   │   └── hotkey.py            # NEW — global shortcut
│   ├── logging_setup.py         # NEW — rotating, redacting, no transcripts
│   └── app.py                   # composition root
├── assets/  icon.png → icon.icns / icon.ico, chime.wav, voices/
├── build/   backgroundassistant.spec, build_macos.sh, build_windows.ps1,
│            entitlements.plist, Info.plist.template, dmg_settings.py
├── tests/   (existing 54 + new)
├── refactor.md  README.md  pyproject.toml
```

### 4.2 Why threads and queues rather than asyncio

The existing code is threaded and its tests are synchronous. Introducing an asyncio loop
*and* Qt *and* blocking C extensions (CTranslate2, PortAudio) in the same process adds a class
of bug — loop-affinity errors, `run_in_executor` starvation, Qt/asyncio integration shims —
that buys nothing here. There are five long-lived workers, not five thousand connections.
Threads with bounded queues and one `EventBus` is the right size of machine for this problem,
and it lets every existing test keep working.

### 4.3 Backpressure

Every queue between stages becomes bounded, with an explicit and *logged* drop policy:

| Queue | Bound | On overflow |
|---|---|---|
| audio frames | 10 s of audio | drop oldest frame, increment a counter, log once per 5 s |
| utterances | 8 | drop oldest, emit `AudioBacklogEvent` → tray shows a warning |
| LLM/TTS | 1 in flight | new trigger cancels the previous |

Today's unbounded queue is why lag compounds rather than degrading. A dropped frame is a
better failure than a growing backlog.

---

## 5. The interesting design problems

### 5.1 Trigger grammar (F16, D8)

All three of these must work:

1. `"Computer, what is the answer?"` — trigger leading
2. `"Something has happened, Computer, what is the answer?"` — trigger medial
3. `"Something has happened, what is the answer, Computer?"` — trigger **trailing**

Form 3 is the one that fails today, and it is different in kind: the question has already been
asked, so there is nothing to wait for. Two decisions are needed and they should not be
confused with each other.

**Decision A — when to dispatch (mechanical, must be instant, cannot involve the LLM):**

```python
class TriggerPosition(enum.Enum):
    LEADING  = "leading"   # trigger in the first ~3 words
    MEDIAL   = "medial"    # trigger with meaningful words on both sides
    TRAILING = "trailing"  # nothing meaningful after the trigger
```

- `TRAILING` → **dispatch immediately.** The user has finished speaking. No 1.5 s wait.
  This alone makes the app feel dramatically more responsive for the most natural phrasing.
- `LEADING` / `MEDIAL` → wait for `command_end_silence_ms`, extending on further speech,
  capped at `max_command_wait_ms`. As today.

"Nothing meaningful after the trigger" means: only punctuation, filler (`um`, `uh`, `please`),
or fewer than two words.

**Decision B — what was actually asked (delegated to the LLM, per D8):**

Rather than slicing the string ourselves, hand the model the transcript with the trigger
position marked and let it work out the intent:

```
Recent conversation (oldest first, times are local):
[14:03:22]  something has happened to the warp core
[14:03:41]  what is the answer «ASSISTANT-NAME-SPOKEN»
```

with a system instruction along the lines of:

> The user addresses you by name. The marker «ASSISTANT-NAME-SPOKEN» shows where in their
> speech they said it. What they are asking may come before the marker, after it, or on both
> sides. Work out the actual question and answer that — do not comment on the marker.

This is robust to phrasings nobody anticipated, costs nothing extra (it is the same request),
and removes a whole category of string-slicing bugs. The trade-off — accepted in D8 — is that
scoping is now non-deterministic and cannot be unit-tested by assertion. **So we test the two
layers separately:** `TriggerPosition` classification gets an exhaustive table test; scope
quality gets a small fixture corpus checked by hand and re-run when the prompt changes.

**False positives (F15):** `LEADING` and `TRAILING` are strong signals of address. A `MEDIAL`
trigger in a long utterance ("I told him my computer is broken and he laughed") is the risky
case. Mitigation: for `MEDIAL`, require the trigger to be adjacent to a clause boundary
(comma, or a pause detected by the segmenter). Configurable strictness in Preferences:
*Relaxed / Balanced / Strict*.

### 5.2 Perceived latency

Four changes, compounding:

1. **Dispatch immediately on `TRAILING`** — removes the 1.5 s command-end wait entirely for
   the most common phrasing.
2. **Stream the LLM response** (SSE) and **speak sentence-by-sentence** — begin speaking the
   first sentence while the rest is still generating. Cuts time-to-first-audio from
   *whole-response* to *first-sentence*, typically 3–8 s → under 1 s.
3. **Acknowledge instantly** — a soft chime and a menu-bar icon change the moment the trigger
   is spotted, before any transcription completes. Fixes F6's silence.
4. **Transcribe on a dedicated thread with a bounded queue** — audio capture is never blocked,
   so the 21-second-utterance pathology cannot recur.

### 5.3 The wake spotter

A small always-on acoustic keyword model (`openWakeWord`, ONNX, a few % of one core) runs
directly on the frame stream, in parallel with — not instead of — the transcript-based
grammar. It provides:

- The **instant** acknowledgement chime (item 3 above), ~100 ms after the word.
- **Barge-in while speaking** (§5.4), which the transcript path cannot provide.

The transcript-based `TriggerParser` remains the authority on grammar and scope, because it
sees words. The spotter is an early-warning system, not a replacement. If openWakeWord proves
unreliable for a custom word, the fallback is transcript-only detection with a ~1 s barge-in
delay — degraded but functional. **This is the one new dependency with real technical risk;
it is spiked in Phase 2 before anything is built on it.**

### 5.4 Barge-in (D12) — interruption as a conversational turn

There are two separable problems here, and conflating them is what made this look harder than
it is:

- **Detection** — telling "the user said the trigger word" apart from "the speakers are
  playing our own voice". Genuinely hard, addressed in §5.4.2.
- **Consequence** — what the conversation looks like afterwards. This is where the design
  matters most, and it is settled by D12a below.

#### 5.4.1 The truncation record (D12a)

An interruption is not an error to be recovered from. It is **a turn in the conversation**, and
the assistant's next answer must be built on what the user *actually heard* — not on what was
generated.

```
user       "Find out X please, Computer"
assistant  ▸ speaks: "X is defined as…"          ← cut here
user       "Computer, I'm sorry I meant Y"
```

The history sent on the second request is:

```
user       Find out X please
assistant  X is defined as                        ← the SPOKEN PREFIX, not the full answer
user       I'm sorry I meant Y
```

This is the whole point. Had we stored the complete generated answer, the model would believe
it had already defined X, and "I'm sorry I meant Y" would be interpreted against a
conversation the user never experienced. **The model's picture of the exchange must match the
user's picture of the exchange.** Storing the spoken prefix is what guarantees that.

Implementation:

- `Message` gains `full_text`, `spoken_upto` (character offset) and `interrupted: bool`.
- Sentence-chunked TTS (§5.2) already knows which chunks completed and which was killed, so
  the offset is a byproduct of a mechanism the plan already needs. Granularity is one sentence
  — ample.
- **LLM history sends only `full_text[:spoken_upto]`**, suffixed with a marker the system
  prompt explains (`… [interrupted]`), so the model knows it was cut off rather than having
  produced a strangely truncated sentence.
- **The chat window shows both**: the spoken prefix rendered normally, the unspoken remainder
  dimmed behind a "show what it was about to say" disclosure. Honest, and occasionally useful.

**Do not transcribe the assistant's own voice.** "Record the answer" means *record the string
we handed to TTS* — we already have it exactly. Running Whisper on our own playback would be
wasteful and would inject the assistant's words into the ambient buffer as though a person in
the room had said them.

**Retro-transcription for the user's leading words.** Because full transcription is disabled
during SPEAKING (§5.4.2, layer 1), any words the user speaks *before* the trigger are not
captured — so "…what did you mean, Computer?" would lose its question. Fix: at the moment of
the cut, retro-transcribe the last ~5 seconds from the audio ring buffer, which §4.3 already
maintains for backpressure. This costs one extra Whisper call on an interruption and nothing
otherwise, and it makes all three trigger phrasings (§5.1) work during barge-in as well as
from idle. The retro-transcribed audio contains the tail of our own playback mixed in; since
we know exactly what we were saying, that text can be subtracted before the result reaches the
buffer.

#### 5.4.2 Detection, layered cheapest-first

1. **Only the spotter listens during SPEAKING.** Full transcription stays disabled; only one
   word matters. (See retro-transcription above for how the surrounding words are recovered.)
2. **Self-echo suppression by content.** If the text currently being spoken contains the
   trigger word, ignore spotter hits for the duration of that chunk ± a margin. The assistant
   knows precisely what it is saying and when it is saying it.
3. **Confidence threshold raised during SPEAKING.** Speaker output returns attenuated and
   room-coloured; a higher bar removes most self-triggers.
4. **Headphone detection.** If output is not the built-in speaker there is no acoustic path —
   drop back to normal sensitivity.
5. **Escape hatch.** Stop in the tray, Stop in the chat window, Esc when the window has focus.
   Always available regardless of how well 1–4 perform.

Full acoustic echo cancellation is explicitly **out of scope**.

#### 5.4.3 Why this de-risks the feature

Under D12a, **detection latency degrades gracefully instead of breaking correctness.** If the
spotter fires 1 second late, playback simply cuts 1 second later — and the transcript is still
right, because `spoken_upto` is recorded at the moment of the actual cut, whenever that is.

The consequence is that **openWakeWord is no longer load-bearing** (§5.3). Transcript-based
detection at roughly 1 second becomes an acceptable primary mechanism rather than a degraded
fallback, and the spotter becomes a nice-to-have that sharpens the cut. S2 and S5 drop from
"could invalidate the design" to "could cost some polish" — see the revised risk table in §12.

#### 5.4.4 Edge cases

- **Interrupted during THINKING, before any audio played.** There is no assistant turn to
  record. Cancel the in-flight request; the superseded question is stored and marked
  `superseded`, but is *not* sent as a turn — the model should not see a question that was
  never answered as though it were part of the dialogue. (See open question 12.)
- **The barge-in is a new topic, not a correction.** "Computer, what's the weather" → speaking
  → "Computer, actually what time is it". History then carries a truncated, irrelevant weather
  answer. This is fine and needs no special handling — it is an honest record and models cope
  with it easily.
- **Repeated barge-ins.** Each produces its own truncated turn. The conversation stays
  coherent; nothing accumulates unboundedly beyond the normal context window.
- **Trigger word inside the answer itself.** Handled by layer 2 above.

### 5.5 Cancellation (F10)

`ask()` becomes a generator yielding tokens and taking a `threading.Event` cancel token,
checked between chunks; the HTTP response is closed on cancel. TTS gains a real `stop()`
(macOS `say` already has one; Piper needs its playback stream killed) which must return the
character offset reached, feeding `spoken_upto` in §5.4.1. A new trigger, the Stop button, or
Esc cancels whatever is in flight and returns to IDLE, recording the truncation.

---

## 6. Settings, secrets and privacy

### 6.1 Where things live

| What | macOS | Windows |
|---|---|---|
| Settings (JSON) | `~/Library/Application Support/BackgroundAssistant/settings.json` | `%APPDATA%\BackgroundAssistant\settings.json` |
| Conversations (encrypted SQLite) | same dir, `conversations.db` | same |
| Logs (rotating) | `~/Library/Logs/BackgroundAssistant/` | `%LOCALAPPDATA%\BackgroundAssistant\logs\` |
| Models | `~/Library/Application Support/BackgroundAssistant/models/` | `%LOCALAPPDATA%\...\models\` |
| **API keys** | **Keychain** | **Credential Manager** |
| **DB key** | **Keychain** | **Credential Manager** |

Resolved by `platformdirs`. Directories created `0700`, files `0600`. Fixes F11.

### 6.2 Secrets

`keyring` (one small, well-maintained dependency) talks to the macOS Keychain and the Windows
Credential Manager natively — no plaintext key on disk on either platform, and macOS will
prompt for access the first time. Service `com.edmartin.backgroundassistant`, one account per
provider so several keys can coexist:

```python
keyring.set_password("com.edmartin.backgroundassistant", "openai",    key)
keyring.set_password("com.edmartin.backgroundassistant", "anthropic", key)
```

- Keys are never written to `settings.json`, never logged, never sent to the chat window
  (the UI receives `"sk-…4f2a"`, a display stub, and posts a new key only on save).
- The `${ENV_VAR}` expansion in `config.py` is **removed**, not extended — it is the mechanism
  that produced F2.
- **Migration (first run):** if the old `config.json` exists, import its settings; if
  `OPENAI_API_KEY` is set in the environment, offer to move it into the Keychain, then tell
  the user they can delete the export from their shell profile.

### 6.3 The privacy model (D5) — stated precisely

- **Ambient speech** — transcribed to a RAM-only rolling buffer (default 120 s). Never written
  to disk. Never logged, at any level, in any build.
- **A triggered exchange** — persisted: your question, the answer, and a snapshot of the
  transcript context that was actually sent. Kept until you delete it.
- **Everything else evaporates** when the buffer rolls or the app quits.

Encryption at rest: message bodies and context snapshots are encrypted with AES-256-GCM
(`cryptography`), the key generated once and stored in the Keychain. Chosen over SQLCipher
because it needs no native build and no PyInstaller gymnastics. Titles and timestamps stay
plaintext so the history list can be rendered without decrypting everything.

Logging (fixes F3, F13):
- A single rotating handler (1 MB × 3). The stdout handler is dropped for bundled builds, and
  `starcop-start.sh` is deleted — so no more double-writing.
- **A `RedactingFilter` that drops any record carrying transcript content**, enforced by a
  test asserting that no log record from a full mock session contains the spoken text.
- A "Debug: include transcripts in logs" toggle, off by default, that warns when enabled and
  automatically switches itself off after 24 hours.
- Preferences: "Reveal logs", "Delete all conversations", "Delete everything".

### 6.4 Preferences window

Same web stack as the chat window (D3), so there is one design language and one place to
maintain it. Tabs:

- **General** — trigger word (with **🖖 when it is `computer`**, per D2), sensitivity
  Relaxed/Balanced/Strict, launch at login, global hotkey, tray behaviour.
- **AI** — provider dropdown (OpenAI · Claude · Local · Custom), API key field (write-only,
  masked, "Test connection"), model dropdown populated live from `/v1/models`, system-prompt
  editor with a "Restore Star Trek personality" preset, temperature, context window seconds.
- **Voice** — output engine (Piper / system), voice picker with preview, rate, chime on/off,
  speak-typed-answers toggle, output device.
- **Listening** — input device, STT engine (Whisper / Apple, the latter labelled *may conflict
  with Voice Control*), model size with a download button and progress, VAD aggressiveness,
  the timing values from today's config.
- **Privacy** — retention, encryption status, transcript-debug toggle, delete buttons.
- **Advanced** — log level, reveal logs/data folder, export settings, factory reset, version.

### 6.5 Local server discovery (D7)

Directly addressing "I haven't figured out how to use the LM Link and exposed network URL
features":

- A **Detect local servers** button probes `localhost` on 1234 (LM Studio), 11434 (Ollama),
  8080 (llama.cpp), 5000, 8000 (Pinokio-hosted backends vary), hits `/v1/models` on each and
  lists what it finds with the model names it reports.
- Reference values baked into the UI help text:
  - **LM Studio** — Developer tab → *Start Server*; base URL `http://localhost:1234/v1`; API
    key can be anything. Enable *Serve on Local Network* only if you want other machines to
    reach it.
  - **Ollama** — `http://localhost:11434/v1` for the OpenAI-compatible shim, or
    `http://localhost:11434` for the native API. No key.
  - **Pinokio** — depends on which app you launched; use *Detect* and read the port from the
    app's own UI.
- **Test connection** sends a one-token request and reports the actual outcome — latency, model
  name, or the real error. No more silent 401s spoken as an apology.

---

## 7. The chat window

Frameless-titlebar Qt window hosting a `QWebEngineView`, hidden by default (D9), opened by
tray, hotkey, or clicking a notification. Native fullscreen supported. Python↔JS over
`QWebChannel` — **no local HTTP server, no open port**, which is both simpler and safer.

**Left rail (D15):** conversation list, newest first, each with an auto-generated 3–5 word
title (a cheap second LLM call after the first exchange, using the same provider) and a
relative timestamp. Search box filtering titles and decrypted bodies. New-chat button.
Right-click → rename / delete / export as Markdown. A voice exchange continues the most recent
conversation if it is under ~10 minutes old, otherwise starts a new one.

**Main pane:** message list, markdown rendered client-side with a **vendored** `marked.js` +
`highlight.js` (no CDN — a bundled app must work offline and the CSP forbids remote origins).
Streaming tokens appear as they arrive. Per-message: copy, speak-again, and — on user messages
— a disclosure showing exactly what ambient context was sent, which makes the privacy model
visible rather than a claim.

**Composer:** text input (Enter sends, Shift+Enter newline), a **push-to-talk mic button**
(D10) that listens on demand and bypasses the wake word, a Stop button while thinking or
speaking, and a compact provider/model indicator.

**Design:** dark by default with a light theme following the system, generous type, one
restrained accent colour. The 🖖 appears next to the trigger word wherever it is displayed
(D2). Explicitly **not** LCARS — D1 rules out Star Trek visual references.

---

## 8. Packaging

### 8.1 macOS (priority)

- **PyInstaller** (better maintained than py2app for Qt + native extensions) → `.app`, onedir.
- `Info.plist`: `LSUIElement = true` (menu bar only, no Dock icon), `NSMicrophoneUsageDescription`
  ("BackgroundAssistant listens for your trigger word so it can answer questions."),
  `CFBundleIdentifier = com.edmartin.backgroundassistant`, `LSMinimumSystemVersion = 13.0`
  (needed for `SMAppService`).
- Entitlements — QtWebEngine's Chromium needs JIT under hardened runtime:
  `com.apple.security.device.audio-input`, `com.apple.security.cs.allow-jit`,
  `com.apple.security.cs.allow-unsigned-executable-memory`,
  `com.apple.security.cs.disable-library-validation`.
- **arm64-only.** Universal2 would require universal wheels for `ctranslate2` and
  `onnxruntime`, which are not reliably available. Note in the README that Intel Macs need to
  run from source.
- Hidden imports / data collection: `--collect-all faster_whisper --collect-all ctranslate2`,
  the bundled Piper voice, the `bgassist/ui/web` tree, the PortAudio dylib from `sounddevice`.
- `codesign --force --options runtime --entitlements` — ad-hoc now (D4), Developer ID later;
  `build_macos.sh --notarize` submits to `notarytool` and staples when a certificate exists.
- **DMG** via `create-dmg`: background image, the app, and an Applications symlink positioned
  for drag-and-drop.
- **Launch at login:** `SMAppService.mainApp.register()` through pyobjc, with a LaunchAgent
  plist fallback for macOS 12.
- **Icon:** 1024×1024 master → `iconutil` → `.icns`. Brief: a single confident mark that reads
  at 16 px in the menu bar — a soft waveform or listening-ring, monochrome-capable for the
  tray (template image so it inverts correctly in dark mode), with a distinct "listening"
  variant. No Trek references (D1).

**Expected size:** ~400–600 MB installed (PySide6 + WebEngine ≈ 250 MB, CTranslate2 +
onnxruntime ≈ 80 MB, bundled model ≈ 75 MB, Piper voice ≈ 60 MB). Documented in the README so
it isn't a surprise.

**First launch (unsigned, D4):** Gatekeeper will complain. The DMG carries a short
`Read Me First.txt` and the README shows the right-click → Open path and the
Settings → Privacy & Security → *Open Anyway* path. This disappears the moment a Developer ID
certificate exists.

### 8.2 Windows (lower priority)

PyInstaller `--noconsole` onedir → **Inno Setup** installer (per-user, no admin). Launch at
login via `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`. TTS: Piper, SAPI5 fallback.
Secrets: Credential Manager, same `keyring` call. Unsigned means a SmartScreen warning —
documented, same trade-off as macOS. Same codebase; anything platform-specific lives behind
`bgassist/platform/`.

---

## 9. Spikes to run first

Time-boxed, done **before** the phases that depend on them, because each could invalidate a
design decision.

| # | Question | Box | If it fails |
|---|---|---|---|
| S1 | Does Whisper capture coexist with macOS Voice Control, both active, no degradation? (D6) | 2 h | Voice Control coexistence becomes a documented limitation; Preferences gains a "pause while Voice Control is active" mode |
| S2 | Does openWakeWord detect a custom "computer" reliably, and at what CPU cost and false-positive rate? (§5.3) | 4 h | Transcript-only triggering; barge-in cuts ~1 s later but stays *correct* (D12a) — a polish loss, not a design failure |
| S3 | Does a PyInstaller bundle with QtWebEngine launch under hardened runtime with the entitlements above? (§8.1) | 4 h | Fall back to a `QDialog`-based native Preferences and reconsider D3 for the chat window |
| S4 | Piper voice quality and latency vs `say`, on this machine (D11) | 2 h | Keep system voices and ship a curated voice picker |
| S5 | Does barge-in self-trigger on built-in speakers with layers 1–4? (§5.4.2) | 3 h | Barge-in is headphones-only, documented; Stop button everywhere — and the Stop button now produces a correct truncation record too (D12a), so interrupting by hand is conversationally identical to interrupting by voice |
| S6 | `keyring` behaviour inside a signed vs ad-hoc-signed bundle (Keychain ACLs are identity-bound) | 1 h | Prompt-on-every-launch is acceptable short-term; resolved by real signing |

S6 is worth calling out: Keychain items are bound to the signing identity, so **an ad-hoc
signed build will re-prompt when the signature changes between rebuilds**. Annoying during
development, resolved by D4's eventual certificate.

---

## 10. Phases

Each phase ends with a green test suite and a working app. No phase leaves the tree broken.

### Phase 0 — Safety net *(half a day)*
1. `git init`; `.gitignore` for `.venv/`, `__pycache__/`, `*.log`, `config.json`, `dist/`,
   `build/`, `*.db`, `.DS_Store`; **commit today's state verbatim as the baseline.**
2. Publish to GitHub **via GitHub Desktop** — Ed's normal tool, and the path of least
   resistance: `Add ▸ Add Existing Repository…`, point it at the folder, then
   `Publish repository`. **Untick "Keep this code private"** for a public repo (D14). No CLI,
   no credential wrangling, no need for anyone to know the account name — Desktop already has
   it. `gh repo create` remains a fallback if the CLI turns out to still be authenticated.
3. ~~Delete `starcop.log`~~ — **done, 2026-08-27.** Removed before it could be committed.
4. Rebuild the venv against a pinned Python 3.12 (F14); `pyproject.toml` replaces the
   requirements files.
5. Verify all 54 tests pass on the rebuilt environment. *This is the baseline everything else
   is measured against.*

> **Operational note — what an agent session can and cannot do here.**
> A Cowork session reaches this folder through a bridge into an isolated Linux VM with the
> folder mounted; it is *not* a shell on the Mac. Consequences for Phase 0:
> - `git` **is** available in the VM, so `git init`, `add` and `commit` work fine on the
>   mounted folder — the local repo can be created by an agent.
> - `gh` is **not** installed there, and macOS keychain credentials are not reachable from it,
>   so **publishing and pushing must happen on the Mac**. Ed uses **GitHub Desktop**, which is
>   the recommended route (step 2) — it needs no CLI auth and no account name. An agent creates
>   the local repo and the baseline commit; Desktop publishes it.
> - The venv rebuild (step 4) and the test run (step 5) execute macOS arm64 binaries and
>   therefore also **must run on the Mac**, not in the VM. An agent can write the commands and
>   read the output, but cannot run them.
> - **Renaming the connected folder (Phase 1, step 6) severs the bridge.** Do it *last* in any
>   session, and expect to reconnect the folder in the desktop app afterwards.

### Phase 1 — Rename and stabilise *(1 day)*
6. Rename `~/Code/git/StarTrekComputer` → `BackgroundAssistant`; `starcop` → `bgassist`;
   all user-facing strings (D1). One mechanical commit, separate from behaviour changes.
7. **Fix F1** — `_stop` → `_stop_event`, and make `Runner` hold a thread rather than subclass
   one. Add a regression test that starts and stops the runner 10 times.
8. **Fix F7** — reset the segmenter when audio is dropped; tick the pipeline on every path.
9. **Fix F3/F13** — the new logging module, the `RedactingFilter`, rotation, and the test
   asserting no transcript reaches any log record. Delete `starcop-start.sh`.
10. **Fix F11** — `platform/paths.py`; settings and logs move to the app-support directory.
11. Fix F8 — typed exceptions from the transcriber, surfaced as an error state.

*Deliverable: the app you have today, but it quits cleanly, keeps no record of your
conversations, and is ready to be packaged.*

### Phase 2 — Settings, secrets, Preferences *(2–3 days)*
12. `settings/` — typed schema, observable JSON store, validation, live-apply where possible.
13. `settings/secrets.py` — keyring; remove `${ENV}` expansion.
14. `settings/migrate.py` — import the old `config.json` and offer to move `OPENAI_API_KEY`
    into the Keychain.
15. Provider backends: streaming OpenAI, **new Anthropic**, local with **server detection**,
    custom. One `LLMBackend` protocol with streaming and cancellation.
16. Preferences window (D3), all six tabs, "Test connection", "Detect local servers", 🖖 (D2).
17. Richer tray menu: status with state icon, Preferences, Open chat, Start/Stop, Quit.

*Deliverable: **F2 is dead.** You can install a key and pick a provider without touching a
terminal. Run S1, S2, S4, S6 during this phase.*

### Phase 3 — Engine rearchitecture *(3–4 days)*
18. `core/events.py` — EventBus and event types.
19. Split the worker into audio / segmenter / transcriber / orchestrator / speaker threads with
    bounded queues and the drop policy of §4.3 (fixes F5).
20. `core/trigger.py` — position-aware grammar (fixes F16), sensitivity levels (F15),
    exhaustive classification tests.
21. `llm/prompts.py` — trigger-marked context (D8) and the fixture corpus.
22. Streaming LLM → sentence-chunked TTS (§5.2).
23. Cancellation everywhere (F10).
24. Wake spotter + instant chime + tray state (§5.3), gated on S2 — now optional polish, not a
    prerequisite.
25. **Truncation record (§5.4.1, D12a)** — `spoken_upto` from the TTS stop path, interrupted
    messages, prefix-only LLM history, retro-transcription from the ring buffer. *Build this
    before the detection work*: it is the part that makes interruption useful, it is testable
    without any acoustics, and the Stop button exercises the whole path.
26. Barge-in detection (§5.4.2), gated on S5.
27. `storage/` — encrypted conversation store, multi-turn history (fixes F9).

*Deliverable: it feels fast, all three phrasings work, it can be interrupted, and it remembers
the last thing it said.*

### Phase 4 — Chat window *(2–3 days)*
28. `ui/bridge.py` — QWebChannel API surface.
29. Chat window shell, hidden by default, hotkey and tray to open (D9).
30. The web UI of §7: conversation rail, streaming messages, markdown, composer,
    push-to-talk (D10), context disclosure.
31. Auto-titling, search, delete, export.
32. Light/dark following the system.

### Phase 5 — Voice *(1 day, gated on S4)*
33. Piper integration + bundled voice + voice picker with preview (D11).
34. Chime/earcons; graceful fallback to system voices.

### Phase 6 — macOS packaging *(2–3 days, gated on S3)*
35. Icon (D18): draw "Attend" at 1024×1024 on the macOS grid with correct bezel padding →
    `.icns` ladder + Windows `.ico`; export the four tray states separately as **template
    images** (`@1x`/`@2x`, pure black + alpha) so macOS tints them.
36. PyInstaller spec, `Info.plist`, entitlements, hidden imports.
37. `build_macos.sh` — build, sign (ad-hoc now, Developer ID later), DMG, optional notarize.
38. Launch-at-login via `SMAppService`.
39. Clean-machine test: install from the DMG, grant the mic prompt, add a key, ask a question.
40. README rewrite; GitHub release with the DMG attached.

### Phase 7 — Windows *(2–3 days)*
41. Platform layer completion, PyInstaller spec, Inno Setup, Run-key login item, test on a
    clean Windows machine.

**Rough total: 14–20 working days.** Phases 0–2 alone (3–4 days) eliminate every critical
finding and most of the day-to-day pain; they are worth doing even if everything after them
slips.

---

## 11. Testing

Keep all 54 existing tests. They are good and they are the safety net for everything above.

New coverage:

| Area | Tests |
|---|---|
| Trigger grammar | table-driven over all three phrasings × sensitivity levels × edge cases (trigger only, trigger twice, trigger + filler) |
| Trigger scope | fixture corpus of marked transcripts, reviewed by hand, re-run on prompt change |
| Settings store | defaults, round-trip, validation, live-apply, corrupt-file recovery |
| Migration | old `config.json` + env var → new store, idempotent |
| Secrets | `keyring` fake; assert no key ever reaches settings, logs or the UI bridge |
| Encryption | round-trip, wrong key fails, DB unreadable without the Keychain entry |
| **Redaction** | **run a full mock session, assert no log record contains the spoken text** |
| Streaming | SSE chunk parsing, mid-stream cancel, sentence chunking for TTS |
| Backpressure | flood the queues, assert bounded memory and a drop event |
| Conversations | create, append, title, search, delete, 10-minute continuation rule |
| Barge-in | state machine with a fake spotter: stop mid-speech, self-echo ignored |
| **Truncation record** | **`spoken_upto` accuracy across chunk boundaries; LLM history carries the prefix and never the full text; interrupted-during-THINKING produces no assistant turn; repeated interruptions stay coherent; retro-transcription subtracts our own playback text** |
| Runner lifecycle | start/stop 10× without exception (the F1 regression) |
| Packaging | smoke-test the built `.app`: launches, tray appears, `--smoke` exits 0 |

`--selftest` is extended to accept a WAV plus an expected phrasing and to print the
classification decision, so audio-chain debugging stays a one-liner.

---

## 12. Risks

| Risk | Likelihood | Impact | Response |
|---|---|---|---|
| QtWebEngine won't run under hardened runtime with our entitlements | medium | high | S3 spike before Phase 4; fallback is native Qt Preferences and reconsidering D3 |
| openWakeWord unreliable for "computer" | medium | **low** (was medium) | D12a makes late detection harmless; S2 spike; fallback is transcript-only at ~1 s |
| Barge-in self-triggers on speakers | medium | **low** (was medium) | S5 spike; fallback is headphones-only voice barge-in + the Stop button, which produces an identical truncation record |
| ctranslate2 / onnxruntime don't bundle cleanly | medium | high | arm64-only; explicit `--collect-all`; worst case ships as a source install |
| Bundle exceeds 600 MB | medium | low | download the model on first run instead of bundling |
| Keychain re-prompts on every ad-hoc rebuild | **high** | low | expected (S6); resolved by a real certificate |
| Whisper contends with Voice Control | low | medium | S1 spike; fallback is a documented limitation + a pause mode |
| Scope creep across 7 phases | **high** | high | Phases 0–2 are the contract; everything after is separately re-committed to |

---

## 13. Open questions

Answers wanted before implementation starts, but none of them block Phase 0.

~~1. GitHub account name~~ — **resolved.** Publishing goes through GitHub Desktop (Phase 0,
   step 2), which already holds the account. Nothing further needed.
~~2. Icon direction~~ — **resolved, see D18/D18a.** "Attend" chosen from five concepts.
~~3. Global hotkey~~ — **resolved, see D17a.** None by default; optional field in Preferences.
~~4. System prompt~~ — **resolved, see D17b.** Calm persona ships as the default, editable.
~~5. Retention~~ — **resolved: yes, and it is off by default.** Preferences → Privacy has
   "Delete conversations automatically after a while" with a day count. Unticked out of the
   box, so the shipped behaviour is exactly what you asked for: kept until you delete them.
~~6. Multiple keys~~ — **resolved: several stored at once**, as assumed. One keychain
   account per provider, so switching provider in the dropdown never means re-entering a key.
~~7. Notifications~~ — **resolved: available, off by default.** For an ambient app a banner
   per answer is noise, so it is a General checkbox; when it is on, clicking the notification
   opens the chat window.
~~8. Intel Mac support~~ — **resolved: arm64 only**, documented in the README. Nothing in
   the code is architecture-specific; only the bundle is. If an Intel machine turns up, it
   runs from source.
~~9. Windows timing~~ — **resolved: the platform layer landed now, the build waits.**
   `bgassist/platform/` and the Inno Setup script are written and tested, because doing it
   alongside was nearly free; actually building and testing on Windows waits until macOS has
   settled.
~~10. The transcript context disclosure~~ — **resolved: kept, collapsed.** It is a
   `<details>` under each of your own messages, so it costs one line until you open it, and it
   makes the privacy model something you can check rather than something you are told.
~~11. Unspoken remainder~~ — **resolved: yes, dimmed behind a disclosure**, as assumed.
    The message is marked *interrupted*, the spoken prefix renders normally, and "Show what it
    was about to say" reveals the rest.
~~12. Interrupted during THINKING~~ — **resolved: shown, greyed, marked "cancelled".**
    Vanishing would make the window disagree with what you remember happening. It is stored
    with `superseded = 1` and `history_from_messages()` skips it, so the model never sees it.
~~13. Speaker labels / diarisation~~ — **resolved, see D17.**

---

## 14. Future improvements (explicitly out of scope)

Noted so they are not forgotten, and so the design does not accidentally foreclose them.

- **Speaker diarisation (D17).** Tell voices apart so the model knows *who* said what — "you
  said X but she said Y". Needs an embedding model (pyannote or similar) plus a clustering
  step, and raises its own privacy questions since voice prints are biometric data. The
  `speaker` field in the transcript buffer is reserved now so this stays an additive change.
- Barge-in with full acoustic echo cancellation, removing the headphones caveat (§5.4.2).
- Auto-update via Sparkle (D13 deferred it).
- Universal2 / Intel Mac builds (§8.1).
- Wake-word personalisation — training the spotter on the user's own voice saying the word.
- Tool use — letting the assistant actually *do* things (calendar, timers, home control)
  rather than only answer. A large change in scope and risk; deliberately not started.

---

## 15. Appendix — findings index

| ID | Severity | Where | One line |
|---|---|---|---|
| F1 | critical | `runner.py:23` | `_stop` shadows `Thread._stop`; Stop and Quit raise `TypeError` |
| F2 | critical | `config.py:82` | API key only from an env var a GUI app never sees → silent 401s |
| F3 | critical | `pipeline.py:77` | Every utterance logged verbatim to an unrotated plaintext file |
| F4 | critical | repo | No version control at all |
| F5 | high | `runner.py:25`, `audio.py:25` | One thread does everything; unbounded audio queue; lag compounds |
| F6 | high | `pipeline.py:66` | Wake word can't fire until silence + transcription; no feedback meanwhile |
| F7 | high | `runner.py:37` | Segmenter not reset when audio is dropped; `tick()` skipped on that path |
| F8 | medium | `pipeline.py:70` | Bare `except` around transcription hides real failures |
| F9 | high | `pipeline.py:112` | No conversation memory; every question is one-shot |
| F10 | high | `pipeline.py:112` | Nothing is cancellable once dispatched |
| F11 | critical* | `config.py:141`, `main.py:33` | Config and log paths relative to code dir — blocks packaging entirely |
| F12 | high | `app.py:43` | No icon, no bundle metadata, no build script |
| F13 | low | `starcop-start.sh` | Log written twice |
| F14 | low | `.venv/pyvenv.cfg` | venv claims 3.9.6, contains 3.12 |
| F15 | medium | `wakeword.py:31` | Any sentence containing the trigger word fires it |
| F16 | high | `wakeword.py:47` | Trailing-trigger phrasing sends an empty query |
| F17 | medium | `runner.py:37` | Deaf while speaking; barge-in impossible |
| F18 | low | `tts.py:38` | `say` sounds dated; `pyobjc` missing from requirements |

\* critical *for the stated goal* — the app works today, but cannot be packaged at all until
this is fixed.


---

## 16. Implementation record (2026-08-27)

Written after the fact, in the same spirit as the rest of this document: what was actually
built, where it departed from the plan and why, and what is left.

### 16.1 What landed

Three commits on top of a verbatim baseline of the old tree:

| Commit | Contents |
|---|---|
| `Baseline` | The pre-refactor tree exactly as it was, so there is something to bisect back to. |
| `Rebuild the engine as bgassist` | Phases 1–3 engine-side: the rename and split, F1/F3/F5/F7/F8/F9/F10/F11/F13/F15/F16, the truncation record. |
| `Add the UI, the composition root, packaging and the icon` | Phases 2/4/5/6/7: Preferences, chat window, tray, icon, CLI, build scripts. |
| `Push-to-talk, spotter sensitivity, and the rest of the test matrix` | The remaining §11 rows and the two barge-in layers that had been stubbed. |

**Tests: 53 → 250**, running in about thirty seconds with none of the heavy dependencies
installed. Every row of the §11 table has coverage, including the two that matter most:
a full mock session asserting that no log record contains the spoken text, and the truncation
record asserting that LLM history carries the spoken prefix and never the full answer.

### 16.2 Deviations from the plan, and why

- **`--check`, a new headless end-to-end mode.** `--selftest` needs a WAV, a Whisper model and
  a machine with an audio stack; `--check` drives the whole app with fakes — trigger grammar,
  orchestrator, responder, conversation store, settings bridge — and runs anywhere, including
  the Linux VM this work was done in. It is now the first thing `build_macos.sh` runs.
- **The `leading_window` rule was wrong and was replaced.** "Trigger in the first ~3 words"
  classified *"my computer is broken"* as LEADING, which bypasses the sensitivity policy
  entirely and leaves F15 half-fixed. LEADING now means *addressed*: nothing before the trigger
  but filler. "hey computer" and "so, computer" still lead; "my computer" is medial and needs a
  clause boundary. This is the one place where writing the tests changed the design.
- **"yes" and "no" are not filler words.** They were, briefly, and *"the computer says no"*
  woke the assistant, because with "no" discounted there was nothing meaningful after the
  trigger and it read as TRAILING.
- **The icon is drawn in code, not stored.** `bgassist/ui/icons.py` renders the "Attend" mark
  and writes PNG bytes itself, so the repository still contains no binary assets and
  `tools/make_icons.py` regenerates the whole ladder on any machine. The app icon sits on a
  macOS squircle with bezel padding; the tray states are black-and-alpha template images with a
  thicker stroke, because a 1.4 px ring disappears in the menu bar.
- **A key-file fallback for machines with no keychain.** `SecretStore` degrades to memory when
  `keyring` has no backend — which would silently make every stored conversation unreadable on
  quit, since the database key would go with it. The conversation key now falls back to a 0600
  file in the data directory, and Preferences → Privacy says which of the two is in use.
- **Interruption during the first sentence stores no assistant turn.** Granularity is one
  sentence (as §5.4.1 says), so a chunk killed part-way through does not count as heard. If
  that was the only chunk, `spoke_anything` is false and the §5.4.4 rule applies: the question
  is marked superseded and no assistant turn is recorded.
- **`marked.js` and `highlight.js` were not vendored.** Fetching them would have meant reaching
  the network for a bundled asset. `ui/web/vendor/markdown.js` is a small renderer written for
  this app instead — paragraphs, emphasis, inline and fenced code, lists, links, everything else
  escaped. Spoken-style answers are short and unformatted; if that ever stops being true, a real
  renderer can be dropped in behind the same call.

### 16.3 What could not be done from here, and why

This session reached the folder through a bridge into an isolated Linux VM. Everything that
needs macOS itself, real audio hardware, or Ed's credentials is left as a short list:

1. **Publish to GitHub** (Phase 0, step 2) — GitHub Desktop: `Add ▸ Add Existing Repository…`,
   point it at `~/Code/git/BackgroundAssistant`, `Publish repository`, untick "Keep this code
   private". The local repository and its three commits are ready and waiting.
2. **Rebuild the venv** against a pinned 3.12 and `pip install -e '.[dev,macos]'` (Phase 0,
   step 4). `pyproject.toml` has replaced both requirements files. The old `.venv` still claims
   3.9.6 in its `pyvenv.cfg` (F14).
3. **Run the suite and `--check` on the Mac** — they pass on Linux with no heavy dependencies;
   they should be confirmed with the real ones installed.
4. **The spikes** (§9). S1 (Voice Control coexistence), S2 (openWakeWord), S4 (Piper quality)
   and S5 (barge-in self-triggering) all need a microphone and speakers. The code is written so
   that each of them failing is a settings default, not a redesign: the spotter is off by
   default and degrades to a null object, Piper falls back to the system voice, barge-in is a
   checkbox, and Apple speech recognition is an option rather than the default.
   **S3 (QtWebEngine under the hardened runtime) is the one with teeth** — the entitlements are
   in `build/entitlements.plist` and the fallback, if it fails, is a native `QDialog`
   Preferences window and reconsidering D3.
   **S6** will bite during development: an ad-hoc signature changes on every rebuild, so the
   Keychain will re-prompt. Expected; a certificate fixes it.
5. **Build and install from the DMG** (Phase 6, step 39) — `build/build_macos.sh`.
6. **Download a Piper voice** into `assets/voices/` if you want the neural voice bundled; the
   app runs on the system voice until then.

### 16.4 Where to look

- The interesting logic: `core/trigger.py` (grammar), `core/orchestrator.py` (the state
  machine), `core/responder.py` (streaming, speaking, cancelling), `engine.py` (threads and
  queues).
- The privacy claims: `logging_setup.py`, `core/transcript.py`, `storage/crypto.py`, and
  `tests/test_logging.py`, which is the one that would fail if F3 ever came back.
- The thing that fixes the day-to-day pain: `ui/web/prefs.html` and `ui/bridge.py`.

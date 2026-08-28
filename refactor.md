# Background Assistant — design and rationale

**What this is:** the reasoning behind the way the app is built. It was written
as a plan, and it was implemented; it is kept because the *why* of a decision is
the part that cannot be recovered from the code, and because a dozen source
files point at its section numbers.

**How to read it.** §3 is the decisions and §4–§8 are the design. §2 explains
the numbered findings (F1…F18) that source comments refer to. §16 is what
actually happened when it was built, including the things that only appeared on
real hardware. The section numbers are not contiguous — the project-management
scaffolding has been deleted, and the surviving numbers are left where they were
because forty source comments point at them.

**Status:** implemented and shipping. macOS builds a signed-ad-hoc `.app` and a
DMG; Windows is written but has never been run. Date of the plan: 2026-08-27.

---

## 0. In one paragraph

The engine underneath this app was always good — a well-factored, well-tested
state machine with clean dependency injection. What was wrong was everything
around it: it crashed on quit, it could not be given an API key without editing
a shell profile, it wrote every private conversation to a plaintext file, and it
had no settings, no window, no icon, no installer and no version control. It was
an engine without a car. This is the car.

| | Before | Now |
|---|---|---|
| Install | clone, venv, pip, edit JSON, edit `~/.zshrc` | drag `Background Assistant.app` to Applications |
| API key | `export OPENAI_API_KEY` in a shell the app never sees | Preferences → Keychain |
| Provider | edit `config.json`, restart | dropdown, with "Test connection" |
| Responses | spoken, then gone | spoken, plus a searchable chat window |
| Follow-ups | impossible — every question was one-shot | full multi-turn, by voice or typing |
| Quit | raised `TypeError` | works |
| Your speech | written to `starcop.log` for ever | never touches the disk unless you asked a question |
| Version control | none | git, and a public repo |

---

## 1. What this replaced

A single-package app (`starcop`) of about 2,000 lines: audio capture, a VAD
segmenter, faster-whisper, a wake-word regex, a four-state pipeline, two LLM
backends, two TTS engines and a four-item tray menu, driven by **one worker
thread** that did all of it in sequence. 54 tests, all passing.

Three things about it were right and were kept:

- **The pure-logic core.** The segmenter, the trigger matching, the transcript
  buffer and the state machine had no I/O in them and took everything by
  injection, including the clock. That is why the tests were fast and
  deterministic, and it is why this rebuild was tractable at all.
- **Lazy heavy imports.** `faster_whisper`, `sounddevice` and `PySide6` are
  imported inside functions, so the suite runs in seconds without them.
- **The failure philosophy.** Every layer caught its own exceptions and kept
  listening. An LLM 401 does not stop the microphone. That is correct for an
  always-on daemon and it survived intact.

Everything else — the packaging, the settings, the privacy story, the
concurrency — is new. The rest of this document is why.

---

## 2. The findings

Source comments refer to these by number. Each was verified against the code
and, where noted, against a real `starcop.log`.

**Critical**

- **F1 — Stop and Quit both crashed.** `Runner` subclassed `threading.Thread`
  and set `self._stop = threading.Event()`, shadowing `Thread._stop`, which the
  interpreter calls internally during `join()`. Every stop raised
  `TypeError: 'Event' object is not callable` — five times in the log. The mic
  stream and worker were left running and the user force-quit. *Fixed: the
  engine holds its threads rather than being one, and the attribute is
  `_stop_event`.*
- **F2 — The API key could only come from an environment variable the app would
  never see.** A GUI app launched from Finder or a Login Item inherits
  `launchd`'s environment, not your shell's, so `~/.zshrc` exports are
  invisible. The log shows five 401s, each of which the user experienced as
  *"I'm sorry, I could not process that"* with no hint that the real problem was
  authentication. The single biggest usability defect, and the reason
  Preferences exists. *Fixed: Keychain, and errors that name the actual cause.*
- **F3 — Every word spoken near the machine was written to disk in plaintext,
  for ever.** `pipeline.py` logged `"heard: %r"` at INFO, to an unrotated file
  in the project folder. The file held verbatim private conversation — medical
  talk, arguments, profanity. The most serious problem in the repository, and a
  direct consequence of an otherwise reasonable logging decision. *Fixed: §6.3.*
- **F4 — Nothing was under version control.** Two thousand lines of working
  code, no history, no way to bisect, no backup.
- **F11 — Config and log paths were relative to the code directory**, which
  inside a signed `.app` is read-only and writing there breaks the signature.
  Critical *for shipping*: the app worked, but could not be packaged at all.
  *Fixed: `platform/paths.py`.*

**Architecture**

- **F5 — One thread did four jobs**, none of which should block the others: VAD,
  Whisper, the LLM call and blocking TTS, all on the thread that was supposed to
  be draining the audio queue — which was **unbounded**. The log shows the
  consequence directly: `Processing audio with duration 00:21.210`. Twenty-one
  second "utterances" are not how people speak; they are a backlog being
  processed in one lump. That was the lag. *Fixed: §4.3.*
- **F6 — The wake word could not fire until you stopped talking *and* Whisper
  finished** — one to three seconds, with no feedback of any kind in between, so
  people repeated themselves, which re-triggered. *Fixed: §5.2.*
- **F7 — The segmenter was not reset when audio was dropped**, so half an
  utterance from before the trigger was glued onto whatever was said after the
  answer. `tick()` was skipped on that path too, so deadlines only advanced when
  the queue happened to be empty.
- **F8 — A bare `except Exception` around transcription hid real failures.** A
  missing model, a corrupt download and a bad frame all looked identical:
  silence. *Fixed: typed errors in `stt/base.py`.*
- **F9 — No conversation memory.** Every question built a fresh two-message
  prompt. "What about tomorrow?" meant nothing.
- **F10 — Nothing was cancellable.** Once dispatched, the LLM call ran to its
  120-second timeout and TTS spoke the whole answer, with no way to interrupt
  short of quitting — which crashed (F1).

**Packaging and behaviour**

- **F12 — No icon, no bundle metadata, no build script.** *Fixed: §8, D18.*
- **F13 — The log was written twice**, once by a shell redirect and once by a
  `FileHandler` on the same path.
- **F14 — The venv was inconsistent**: `pyvenv.cfg` claimed 3.9.6 while `lib`
  contained 3.12. It worked by accident.
- **F15 — Any sentence containing the trigger word fired it.** "My computer is
  broken" woke the assistant — invisibly, because of F6. *Fixed: §5.1.*
- **F16 — Only two of the three natural phrasings worked.** `split_command()`
  always returned the text *after* the trigger, so *"…what is the answer,
  **Computer**?"* sent an empty query. The most natural phrasing was the one
  that failed. *Fixed: §5.1.*
- **F17 — Deaf while speaking.** All audio was discarded during SPEAKING, so
  barge-in was impossible by construction. *Fixed: §5.4.*
- **F18 — `say` sounds dated**, and `pyttsx3` on macOS needs `pyobjc`, which was
  not in the requirements.

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
| D17b | **Shipped persona: complete, and as short as the question allows.** It is a voice in a room where other people are talking, asked in briefly; it answers what was asked and adds nothing — no caveats, no second fact, no offer of more. | An extra helpful fact is not a bonus when you are speaking into someone else's conversation; it is an interruption, and it is what makes an assistant tiresome to have in the room. The prompt is also sent with every request, so it is kept under a thousand characters and there is a test for that. Fully editable in Preferences, with the default restorable. |
| D17 | **No diarisation.** Every human voice in the room is `user`; every reply is `assistant`. Standard two-role chat history. | Deferred to §15. Keep the door open: the transcript buffer stores a `speaker` field that is always `"user"` for now, so adding diarisation later is additive rather than a schema migration. |
| D18 | **Icon: "Attend"** — an open ring broken at the base with a solid centre. Deep slate ground, soft aqua mark. | Two shapes only, so it survives every size and the flattening to a black-and-alpha template image. Four tray states come from the same two shapes: **idle** small centre · **listening** centre dilates · **thinking** the gap travels round the ring · **speaking** the gap opens wider, centre pulsing. Wired to the existing `State` enum, which already emits exactly these transitions. |
| D18a | **Halo rejected — wifi collision.** Arcs stacked over a dot is the AirPort/wifi glyph and would have sat inches from the real one in the same menu bar. | Recorded because it is the kind of mistake worth not repeating: a mark must be checked against *the icons it will sit beside*, not only against itself. |

---

---

## 4. Architecture

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
prompt for access the first time.

**Everything lives in one keychain item**, `com.edmartin.backgroundassistant` /
`secrets`, holding a JSON object of name → secret. The obvious design — an item per
provider, plus one for the database key — is the wrong one, because macOS grants access
per *item*, not per application: first run asked three separate times and "Always Allow"
only ever covered the prompt in front of you. One item is one grant. Several keys still
coexist; they are keys in the object rather than items in the keychain, and Preferences
reading all five providers costs one read rather than five. Secrets written under the old
layout are folded in and the old items removed the first time the app starts.

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

---

## 8. Packaging

### 8.1 macOS (priority)

- **PyInstaller** (better maintained than py2app for Qt + native extensions) → `.app`, onedir.
- `Info.plist`: `LSUIElement = true` (menu bar only, no Dock icon), `NSMicrophoneUsageDescription`
  ("Background Assistant listens for your trigger word so it can answer questions."),
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

---

## 9. What the spikes answered

Six questions were time-boxed before the work that depended on them. Building
and running the app answered three of them outright; three need a person in a
room with a microphone, and are still open.

| # | Question | Answer |
|---|---|---|
| S3 | Does a PyInstaller bundle with QtWebEngine launch under the hardened runtime? | **Yes.** The entitlements in `build/entitlements.plist` are sufficient: the built app passes its own `--smoke`, which constructs the tray and runs the event loop. D3 stands; the native-Preferences fallback was not needed. |
| S6 | How does `keyring` behave inside an ad-hoc signed bundle? | **Badly, as predicted, and now handled.** macOS refuses to overwrite a keychain item created under a different code signature — error −25244 — and an ad-hoc build gets a new signature on every rebuild. The store now reads its own write back and reports a key that did not stick rather than losing it silently. A Developer ID certificate ends this. |
| S2 | Is openWakeWord reliable for a custom trigger word? | **Not needed to find out.** Under D12a a late cut is still a correct cut, so the spotter is off by default and degrades to a null object. Transcript triggering is the primary path and works. |
| S1 | Does Whisper capture coexist with macOS Voice Control? | Open — needs a microphone. Apple's recogniser is already offered as an alternative in Preferences → Listening. |
| S4 | Piper voice quality against `say`? | Open — needs ears. Piper is optional and falls back automatically; "Automatic" prefers Tessa among the system voices. |
| S5 | Does barge-in self-trigger through the built-in speakers? | Open — needs a room. Layers 1–4 of §5.4.2 are implemented; if it proves unreliable, barge-in is a checkbox and the Stop button produces an identical truncation record. |

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

---

## 13. Questions that were open, and their answers

| # | Question | Answer |
|---|---|---|
| 1 | GitHub account | Published through GitHub Desktop. |
| 2 | Icon direction | "Attend" — D18, D18a. |
| 3 | Global hotkey | None by default; an optional field — D17a. |
| 4 | System prompt | Calm persona ships as the default, editable — D17b. |
| 5 | Retention | Auto-delete exists, **off** by default. Conversations are kept until deleted. |
| 6 | Multiple keys | Several stored at once, one keychain account per provider. |
| 7 | Notifications | Available, **off** by default; clicking one opens the chat window. |
| 8 | Intel Macs | arm64 only. Nothing in the code is architecture-specific; only the bundle is. |
| 9 | Windows timing | The platform layer landed with everything else; the build waits. |
| 10 | Context disclosure | Kept, collapsed, under each of your own messages. |
| 11 | Unspoken remainder | Shown dimmed behind a disclosure. |
| 12 | Interrupted during THINKING | Shown greyed and marked *cancelled*, stored with `superseded = 1`, never sent to the model. |
| 13 | Diarisation | Deferred; the `speaker` field is reserved — D17. |

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

---

## 16. What happened when it was built

### 16.1 Where it departed from the plan

- **`--check`, a headless end-to-end mode.** `--selftest` needs a WAV, a model
  and an audio stack; `--check` drives the whole app with fakes — trigger
  grammar, orchestrator, responder, conversation store, settings bridge — and
  runs anywhere. It is the first thing `build_macos.sh` runs.
- **The "leading" rule was wrong and was replaced.** "Trigger in the first ~3
  words" classified *"my computer is broken"* as leading, which bypasses the
  sensitivity policy and leaves F15 half-fixed. LEADING now means *addressed*:
  nothing before the trigger but filler. Writing the tests changed the design.
- **"yes" and "no" are not filler words.** They were, briefly, and *"the
  computer says no"* woke the assistant — with "no" discounted there was nothing
  meaningful after the trigger, so it read as TRAILING.
- **The icon is drawn in code**, so the repository still contains no binary
  assets and `tools/make_icons.py` regenerates the ladder anywhere.
- **A key-file fallback for machines with no keychain**, because a
  keychain-less `SecretStore` keeps keys in memory, which would have made every
  stored conversation unreadable after a quit.
- **`marked.js` was not vendored.** Fetching it would have meant reaching the
  network for a bundled asset; `ui/web/vendor/markdown.js` is a small renderer
  written for this app instead.

### 16.2 What only appeared on real hardware

Six bugs that no amount of reasoning found, in the order they turned up. They
are recorded because each one is a class of mistake, not a typo.

1. **A test can reach the real Keychain.** The `Application` fixture did not
   pass a secret store, so on macOS it built one — reading, writing and deleting
   entries under the account the shipped app keeps the user's API key in. It hid
   on Linux, where `keyring` is usually absent. `conftest.py` now makes the
   system keychain unreachable from any test, and asserts it.
2. **Patching `sys.platform` is global.** A test set it to `win32` to check
   voice selection; `bgassist.tts.sys` *is* the `sys` module, so `shutil.which()`
   inside the new Piper attempt took the Windows path on a Mac. The platform is
   now a parameter, not a global read.
3. **A provider will refuse a preference.** Newer OpenAI models accept only the
   default temperature and returned HTTP 400 — the request refused over a
   *preference*, not over the question. Backends now read what the provider
   objected to and retry without it, rather than keeping a table of model names
   that would be wrong within the month.
4. **A reasoning model spends its budget before answering.** Hidden reasoning is
   billed against `max_completion_tokens`, so a budget sized for a spoken
   sentence produced an empty completion and *"I have nothing to report"*. An
   empty answer that hit the limit is retried once with room to think.
5. **Finder loses `rm -rf` a race.** The build ends by opening Finder on
   `dist/`, so the next run's `rm` emptied the folder while Finder wrote
   `.DS_Store` back into it, and the closing `rmdir` failed. Directories are now
   renamed out of the way and deleted under a name nothing is watching.
6. **A packaging hook can be wrong about you.** `pyinstaller-hooks-contrib`
   copies metadata for the distribution named `webrtcvad`; we depend on
   `webrtcvad-wheels`, so the hook raised and aborted analysis. `build/hooks/`
   overrides it — user hooks outrank contributed ones.

Two smaller ones, both found by looking rather than testing: `iconutil` rejects
an `.iconset` containing any filename it does not recognise, and the markdown
renderer turned *"1066. Harold was killed at Hastings"* into list item 1066.

### 16.3 What is left

- **Three spikes need a person in a room**: Voice Control coexistence (S1),
  Piper against the system voices (S4), and whether barge-in self-triggers
  through the built-in speakers (S5). See §9. Each is a settings default if it
  goes badly, not a redesign.
- **Windows** is written — `bgassist/platform/`, the PyInstaller spec, the Inno
  Setup script — and has never been run.
- **A Developer ID certificate** would end the right-click-to-open dance and the
  keychain re-prompting (S6). `build_macos.sh --notarize` is already wired for
  it; set `CODESIGN_IDENTITY`.
- **Piper voices** are supported but none is bundled; drop a `.onnx` into
  `assets/voices/`.

### 16.4 Where to look

- The interesting logic: `core/trigger.py` (grammar), `core/orchestrator.py`
  (the state machine), `core/responder.py` (streaming, speaking, cancelling),
  `engine.py` (threads and queues).
- The privacy claims: `logging_setup.py`, `core/transcript.py`,
  `storage/crypto.py`, and `tests/test_logging.py` — the test that would fail if
  F3 ever came back.
- The thing that fixes the day-to-day pain: `ui/web/prefs.html` and
  `ui/bridge.py`.

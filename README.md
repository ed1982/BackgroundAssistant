# BackgroundAssistant

An always-on voice assistant that sits in your menu bar, listens for a trigger
word, and answers out loud.

Say *"Computer, what time is it in Tokyo?"* — or *"what time is it in Tokyo,
Computer?"*, which is the more natural phrasing and works just as well — and it
answers. It hears the conversation in the room, so *"…what about Berlin?"* has
something to refer to. Nothing you say is written to disk unless you actually
triggered a question.

Speech recognition runs locally on your machine. Only the exchanges you trigger
are sent anywhere, and you can see exactly what was sent.

---

## What it does

| | |
|---|---|
| **Trigger** | Any word you choose, in any of the three natural positions: leading, mid-sentence, or trailing. A trailing trigger answers immediately, because you have already finished asking. |
| **Listening** | Whisper, locally, on your machine. Apple's recogniser is available as an option. |
| **Answering** | OpenAI, Claude, a local server (LM Studio · Ollama · llama.cpp · Pinokio), or any OpenAI-compatible URL. Answers stream and are spoken sentence by sentence, so speech starts in about a second. |
| **Interrupting** | Say the trigger word while it is talking and it stops. What you heard is what it remembers saying. |
| **Remembering** | Multi-turn conversations with a searchable history, encrypted on disk. |
| **Privacy** | Ambient speech lives in memory only. Triggered exchanges are stored until you delete them. Keys live in the Keychain. |

## Install

**From a release:** download the DMG, drag the app to Applications, and read
`Read Me First.txt` inside the DMG — the build is not signed with a paid Apple
certificate yet, so the first launch needs right-click → Open.

**From source:**

```bash
git clone <this repo> && cd BackgroundAssistant
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'          # add ,macos or ,windows for platform extras
python main.py
```

Requires Python 3.10–3.12. macOS is arm64; Intel Macs need to run from source.

## First run

The icon appears in the **menu bar**, not the Dock.

1. Open **Preferences → AI**, pick a provider, and paste an API key. It goes
   into the system Keychain — never into a file, never into a log. Press
   **Test connection** and it will tell you exactly what happened, including
   what a failure actually was.
2. If you run a model locally, press **Detect local servers** instead. It probes
   the usual ports (LM Studio 1234, Ollama 11434, llama.cpp 8080, 8000, 5000)
   and lists what each one says it has.
3. Grant microphone access when macOS asks.
4. Say your trigger word.

Everything else has a sensible default. There is no global shortcut unless you
set one, and no notifications unless you turn them on.

## The trigger word

All three of these work:

- "**Computer**, what is the answer?"
- "Something has happened, **computer**, what is the answer?"
- "What is the answer, **computer**?" ← answers immediately, no pause

"My computer is broken" does **not** wake it. Preferences → General has three
sensitivities: *Relaxed* wakes on any mention, *Balanced* (the default) requires
the trigger to be addressed to it, and *Strict* only accepts it at the start or
the end of a sentence.

If your trigger word happens to be `computer`, there is a 🖖 next to it.

## Privacy, precisely

- **What you say near the machine** is transcribed into a rolling in-memory
  buffer (two minutes by default). It is never written to disk, never logged,
  and it evaporates when the buffer rolls or the app quits.
- **An exchange you triggered** is stored: your question, the answer, and a
  snapshot of the context that was actually sent — which the chat window will
  show you, per message. Kept until you delete it.
- **Message bodies are encrypted** with AES-256-GCM; the key is in the Keychain.
  Titles and timestamps stay readable so the history list renders without
  decrypting everything.
- **Logs never contain transcripts.** There is a debug toggle that changes this,
  it warns you, and it switches itself off after 24 hours.
- **Keys are never written to the settings file**, never logged, and never sent
  to the app's own UI — Preferences shows `sk-…4f2a` and nothing more.

Preferences → Privacy has *Delete all conversations* and *Delete everything*.

## Interrupting it

Say the trigger word while it is speaking, press Stop in the tray or the chat
window, or press Esc with the window focused.

When you interrupt, it records **what you actually heard** — not what it had
generated. So this works:

> "Find out X please, Computer" → *"X is defined as…"* ← you cut in
> "Computer, I'm sorry I meant Y"

The second question is answered against a conversation in which it said "X is
defined as" and nothing more, which is the conversation you experienced. The
chat window will show you the rest of what it was about to say, dimmed, if you
want it.

## Command line

```
python main.py                     run the assistant
python main.py --check             end-to-end check with fakes: no mic, no network
python main.py --selftest file.wav run a recording through the real audio chain
python main.py --selftest x.wav --expect trailing
python main.py --doctor            what is installed, and where things live
python main.py --list-devices      input devices
python main.py --smoke             build the tray UI and exit
```

`--check` is the fastest way to know the whole app is wired up correctly; it
runs anywhere, including a Linux box with no audio stack at all.

## Where things live

| | macOS | Windows |
|---|---|---|
| Settings | `~/Library/Application Support/BackgroundAssistant/settings.json` | `%APPDATA%\BackgroundAssistant\` |
| Conversations | the same folder, `conversations.db` (encrypted) | the same |
| Logs | `~/Library/Logs/BackgroundAssistant/` (rotating, 1 MB × 3) | `%LOCALAPPDATA%\BackgroundAssistant\logs\` |
| Models | the data folder, `models/` | the same |
| API keys | Keychain | Credential Manager |

Nothing is written next to the code. Set `BGASSIST_HOME` to override all of it,
which is what the test suite does.

Upgrading from the earlier version of this project: the old `config.json` is
imported automatically on first run, and if `OPENAI_API_KEY` is set in the
environment the key is copied into the Keychain — after which you can remove
that export from your shell profile.

## Development

```bash
python -m pytest -q        # ~190 tests, about 15 seconds, no heavy deps needed
python -m pytest -m integration   # the real audio chain (macOS, downloads a model)
python main.py --check
```

The pure-logic core — the segmenter, the trigger grammar, the orchestrator, the
transcript buffer and the settings — has no I/O in it and takes everything by
injection, including the clock. That is why the tests are fast and deterministic
and why heavy dependencies are imported lazily, inside functions.

```
bgassist/
  core/       segmenter · trigger grammar · orchestrator · responder · events
  audio/      capture (bounded queues) · VAD · wake-word spotter · chime
  stt/        whisper · apple · the transcriber protocol
  llm/        openai · anthropic · local + discovery · prompts · streaming
  tts/        piper · system voices · sentence chunking
  settings/   schema · store · keychain · migration
  storage/    encrypted conversations · AES-256-GCM
  ui/         tray · chat window · preferences · the QWebChannel bridge
  platform/   paths · login item · hotkey · notifications
  engine.py   the worker threads and the queues between them
```

Design and rationale: [`refactor.md`](refactor.md). The original design
document, describing the version this replaced, is [`plan.md`](plan.md).

## Building

See [`build/README.md`](build/README.md). Expect 400–600 MB installed, most of
it Qt WebEngine and the speech model.

## Licence

MIT.

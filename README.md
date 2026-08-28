# Background Assistant

### It already heard the question.

An assistant that sits in your menu bar, hears the room, and answers when you
say the word — without making you repeat what you just said.

You are arguing about when the Battle of Hastings was. Neither of you is sure.
Instead of unlocking a phone, opening an app, and typing out the question you
have both just said aloud, you say:

> **"Computer?"**

And it answers. It was already listening.

That is the whole idea. *"Hey Siri, when was the Battle of Hastings"* still
makes you ask the question. This lets you **stop** asking it.

**Three ways to use it**

1. **Say the word, nothing else.** It answers whatever was just being
   discussed.
2. **Ask it however it comes out.** "Computer, what year was that?" — or
   "what year was that, computer?", which answers the instant you stop
   speaking, because you have already finished asking.
3. **Follow up without explaining again.** "Computer, what about the year
   after?" It still has the thread.

**It is not recording you.** The last couple of minutes live in memory and
nowhere else. Only the exchanges you actually trigger are stored — in a
history you can read and delete at any time. Nothing else is kept.

---

## Install

There is no download yet — it needs an Apple Developer certificate first, and
without one macOS would refuse to open a DMG from the internet outright. So
you build it, which takes one double-click and about five minutes:

1. Clone this repository, then run once:
   `python3.12 -m venv .venv && source .venv/bin/activate && pip install -e '.[dev,macos]'`
2. Double-click **`Build Background Assistant.command`** in Finder. Terminal
   opens to show progress — there is nothing to type — and Finder opens on the
   finished DMG. (Details and the shell equivalent: [`build/README.md`](build/README.md).)
3. Open the DMG and drag the app onto **Applications**.
4. **Right-click the app → Open → Open.** Once only. The build is signed
   ad-hoc rather than with a paid certificate, so macOS cannot check it and a
   plain double-click gets refused.
5. Say **yes** to the microphone prompt.

The icon appears in the **menu bar**, not the Dock. Open **Preferences → AI**,
choose a provider, paste an API key — it goes into your Keychain, never a file
— and press **Test connection**.

Running a model locally instead? Press **Detect local servers**: it probes the
usual ports (LM Studio 1234, Ollama 11434, llama.cpp 8080) and lists what
answers.

macOS 13+, Apple silicon. Intel Macs can run it from source.

---

## What is in it

| | |
|---|---|
| **Listening** | Whisper, locally. Apple's recogniser is available as an option. |
| **Answering** | OpenAI, Claude, a local server, or any OpenAI-compatible URL. Answers stream and are spoken sentence by sentence, so speech starts in about a second. |
| **Interrupting** | Say the trigger word while it is talking and it stops. What you heard is what it remembers saying. |
| **Remembering** | Multi-turn conversations, searchable, encrypted on disk. |
| **Staying quiet** | No Dock icon, no window, no notifications unless you ask. |

## The trigger word

"My computer is broken" does **not** wake it. Preferences → General has three
sensitivities: *Relaxed* wakes on any mention, *Balanced* (the default)
requires the word to be addressed to it, and *Strict* accepts it only at the
start or end of a sentence.

## Privacy, precisely

- **What you say near the machine** is transcribed into a rolling in-memory
  buffer (two minutes by default), never written to disk, never logged, and
  gone when the buffer rolls or the app quits.
- **An exchange you triggered** is stored: your question, the answer, and a
  snapshot of the context that was sent — which the chat window shows you, per
  message. Kept until you delete it, and every conversation has a delete
  button.
- **Message bodies are encrypted** with AES-256-GCM, the key in your Keychain.
- **Logs never contain transcripts.** There is a debug toggle that changes
  that; it warns you and turns itself off after 24 hours.
- **Keys are never written to the settings file**, never logged, and never
  shown back to the UI — Preferences displays `sk-…4f2a` and nothing more.

Preferences → Privacy has *Delete all conversations* and *Delete everything*.

## Interrupting it

Say the trigger word while it is speaking, press Stop, or press Esc. It then
records **what you actually heard** — not what it had generated — so this
works:

> "Find out X please, Computer" → *"X is defined as…"* ← you cut in
> "Computer, I'm sorry I meant Y"

The second question is answered against the conversation you experienced, in
which it said "X is defined as" and no more. The chat window will show you the
rest of what it was about to say, dimmed, if you want it.

## Voices

It speaks with a macOS system voice, preferring **Tessa**. For something
better, install an *Enhanced* or *Premium* voice in **System Settings →
Accessibility → Spoken Content → System Voice → Manage Voices**; it appears in
Preferences → Voice immediately.

Siri's own voices (Pippa, Jamie, Nicky) cannot be used. Apple does not expose
them to `say` or to the speech APIs, so no third-party app can reach them —
there is nothing to work around. Piper, a local neural voice, is supported as
an option: drop a `.onnx` voice into `assets/voices/`.

## Command line

```
python main.py                     run the assistant
python main.py --check             end-to-end check with fakes: no mic, no network
python main.py --selftest file.wav run a recording through the real audio chain
python main.py --doctor            what is installed, and where things live
```

`--check` is the fastest way to know the whole app is wired up correctly; it
runs anywhere, including a Linux box with no audio stack at all.

## Where things live

| | macOS | Windows |
|---|---|---|
| Settings | `~/Library/Application Support/BackgroundAssistant/settings.json` | `%APPDATA%\BackgroundAssistant\` |
| Conversations | the same folder, `conversations.db` (encrypted) | the same |
| Logs | `~/Library/Logs/BackgroundAssistant/` (rotating, 1 MB × 3) | `%LOCALAPPDATA%\…\logs\` |
| API keys | Keychain | Credential Manager |

Those folders keep the unspaced name deliberately: what the app is *called*
can change, and a rename should not leave your settings and conversations
behind in a folder nothing looks in any more. Nothing is written next to the
code, and `BGASSIST_HOME` overrides all of it — which is what the test suite
does.

## Development

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev,macos]'
python -m pytest -q        # ~270 tests, about 30 seconds, no heavy deps needed
python main.py --check
```

The pure-logic core — segmenter, trigger grammar, orchestrator, transcript
buffer, settings — has no I/O in it and takes everything by injection,
including the clock. That is why the tests are fast and deterministic, and why
heavy dependencies are imported lazily inside functions.

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

Design and rationale: [`refactor.md`](refactor.md). Building and packaging:
[`build/README.md`](build/README.md). Expect 400–600 MB installed, most of it
Qt WebEngine and the speech model.

## Licence

MIT.

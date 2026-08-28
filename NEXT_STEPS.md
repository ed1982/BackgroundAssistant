# What is left for you to do on the Mac

The refactor in `refactor.md` is implemented and committed. Everything below needs macOS,
real audio hardware, or your credentials, so it could not be done from the agent session —
it ran in an isolated Linux VM with this folder mounted.

Nothing here is urgent except (1). The app runs from source today.

---

### 1. Publish the repository (five minutes)

There are three commits waiting, starting from a verbatim snapshot of the old tree.

Open **GitHub Desktop** → `Add` ▸ `Add Existing Repository…` → point it at
`~/Code/git/BackgroundAssistant` → `Publish repository` → **untick "Keep this code private"**
if you want it public (D14).

No CLI, no token, no account name needed — Desktop already has all of that.

### 2. Rebuild the environment (F14)

The existing `.venv` claims Python 3.9.6 in `pyvenv.cfg` while containing 3.12, and works by
accident. `pyproject.toml` has replaced `requirements.txt` and `requirements-dev.txt`.

```bash
cd ~/Code/git/BackgroundAssistant
rm -rf .venv
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev,macos]'
```

### 3. Confirm it still passes with the real dependencies installed

```bash
python -m pytest -q          # 250 passing, 1 skipped (the integration test)
python -m pytest -m integration   # the real audio chain; downloads a whisper model
python main.py --check       # end-to-end with fakes
python main.py --doctor      # what is installed, and where things live
```

### 4. Run it

```bash
python main.py
```

The icon appears in the **menu bar**. Open Preferences → AI, choose a provider, paste your key
(it goes into the Keychain), press **Test connection**. Then say *"Computer, what time is it?"*
— and then try *"what time is it, computer?"*, which is the phrasing the old version answered
with an empty query.

Your old `config.json` is imported automatically the first time, and if `OPENAI_API_KEY` is
still exported in your shell, running from a terminal once will copy it into the Keychain —
after which you can delete that line from `~/.zshrc`.

### 5. The spikes (§9 of `refactor.md`)

These need a microphone and speakers, so they are yours. The code is arranged so that each one
failing is a settings default rather than a redesign:

| Spike | What to check | If it fails |
|---|---|---|
| S1 | Does Whisper capture coexist with Voice Control? | Documented limitation; Apple recognition is already offered in Preferences → Listening. |
| S2 | Is openWakeWord reliable for your trigger word? | It is **off by default** and degrades to a null object. Transcript triggering is the primary path. |
| S3 | Does the built app launch under the hardened runtime with QtWebEngine? | **The one with teeth.** Entitlements are in `build/entitlements.plist`. Fallback is a native Preferences dialog. |
| S4 | Piper voice quality against `say` | Piper is optional and falls back to the system voice automatically. |
| S5 | Does barge-in self-trigger through the speakers? | Turn off Preferences → Listening → "Let the trigger word interrupt an answer". Stop in the tray, the window and Esc all still work. |
| S6 | Keychain behaviour under an ad-hoc signature | It will re-prompt on every rebuild. Expected; a Developer ID certificate fixes it. |

### 6. Build the app

**Double-click `Build BackgroundAssistant.command`** in the project folder.
Terminal opens to show progress, there is nothing to type, and when it finishes
Finder opens with the DMG selected. PyInstaller installs itself into the venv
if it is missing, and the DMG is made with `hdiutil`, so Homebrew is not
needed. (`brew install create-dmg` gets you a prettier window layout if you
happen to want one.)

Equivalent from a shell:

```bash
build/build_macos.sh
```

The script regenerates the icons, runs the tests and `--check`, builds the `.app`, signs it
ad-hoc with the hardened runtime and entitlements, smoke-tests the built binary and makes the
DMG. Add `--notarize` (and set `CODESIGN_IDENTITY`) once you have a certificate.

Expect 400–600 MB and a few minutes. `build/README.md` has the details.

### 6b. Install it, and never open a terminal again

1. Double-click **BackgroundAssistant.dmg**.
2. Drag **BackgroundAssistant** onto the **Applications** shortcut in that window.
3. Eject the disk image, then open Applications and **right-click BackgroundAssistant →
   Open → Open**. That right-click is needed exactly once: the build is not signed with a
   paid Apple certificate, so a plain double-click gets refused the first time.
   (If you miss the dialog: System Settings → Privacy & Security → scroll down →
   *Open Anyway*.)
4. Say **yes** to the microphone prompt.
5. The icon appears in the **menu bar**, not the Dock — there is no window and no Dock icon
   by design. Everything is in that menu: Open chat, Preferences, Start/Stop listening, Quit.
6. Preferences → **General** → tick *Launch at login* if you want it always there.

From then on it is an ordinary Mac app.

### 7. Optional

- **A Piper voice**: drop `<name>.onnx` and `<name>.onnx.json` into `assets/voices/`, then pick
  it in Preferences → Voice. Until then the system voice is used.
- **A GitHub release** with the DMG attached, once you are happy with it.
- **Windows**: `build/build_windows.ps1` and `build/installer.iss` are written but have never
  been run. The platform layer is in `bgassist/platform/`.

---

### If something is wrong

`python main.py --doctor` first, then the log: **Preferences → Advanced → Reveal logs**, or
`~/Library/Logs/BackgroundAssistant/`. It rotates at 1 MB and it contains no transcripts —
if you need them for debugging, Preferences → Privacy has a toggle that warns you and turns
itself off after 24 hours.

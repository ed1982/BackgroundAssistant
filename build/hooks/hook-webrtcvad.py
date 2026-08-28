"""Replaces the contributed hook for webrtcvad, which cannot see our metadata.

``pyinstaller-hooks-contrib`` ships a hook that does ``copy_metadata('webrtcvad')``.
We depend on **webrtcvad-wheels** — the maintained fork that publishes prebuilt
wheels, because the original needs a C toolchain and does not build on 3.12.
The import name is the same, so the hook fires; the *distribution* name is not,
so ``copy_metadata`` raises ``PackageNotFoundError`` and takes the whole build
down before analysis has finished.

User hooks have HOOK_PRIORITY_USER_HOOKS (1000) against the contributed hooks'
-1000, and PyInstaller keeps only the highest-priority hook per module, so this
file replaces that one rather than running alongside it.

Nothing in this app reads webrtcvad's metadata, so copying it is a nicety: we
try both names and shrug if neither is there.
"""
from PyInstaller.utils.hooks import copy_metadata

datas = []
for distribution in ("webrtcvad-wheels", "webrtcvad"):
    try:
        datas = copy_metadata(distribution)
        break
    except Exception:  # noqa: BLE001 - absent metadata is not a build failure
        continue

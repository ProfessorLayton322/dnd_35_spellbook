# Agent instructions

## Commit your work

Commit changes yourself, without waiting to be asked. Once a change is finished, commit everything it touched: code, tests, and docs.

## Rebuild the packages after each commit

After every commit, rebuild both packages so `dist/` matches the latest commit:

- Windows installer: `python3.13 packaging/windows/build.py` writes `dist/DnD35Spellbook-<version>-Setup.exe`
- Android APK: `packaging/android/build.sh` writes `dist/DnD35Spellbook-<version>.apk`

Setup for both builds is in the README section "Building the Windows and Android packages". The two builds can run at the same time. If a build fails, fix it or report the error; don't leave `dist/` out of date without saying so. `dist/` is gitignored, so build outputs are never committed.

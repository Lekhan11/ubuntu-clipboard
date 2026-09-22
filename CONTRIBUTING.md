# Contributing

Thanks for considering a contribution! This project is a small, single-file GTK
application, so contributions are expected to stay small and focused.

## Ground rules

- **Keep it a single file.** `clipboard.py` is intentionally self-contained so
  the project can be cloned and run in one step. Avoid adding new modules unless
  a feature genuinely doesn't fit (e.g. a large test suite) and you explain why
  in the PR.
- **Python 3 + PyGObject (GTK 3) only.** No new third-party Python dependencies
  without discussion — everything runs on system packages (`python3-gi`).
  GTK 4 is a possible future port but not the current target.
- **Wayland first.** Many GTK clipboard APIs behave differently on X11 vs
  Wayland. If you change clipboard logic, test on both when you can.
- **Local-only data.** The history lives in the user's home directory and must
  never be uploaded, phoned home, or required for the app to run. No network
  access, no analytics.

## Setup

```bash
git clone https://github.com/Lekhan11/ubuntu-clipboard
cd ubuntu-clipboard
sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-gdkpixbuf-2.0 ydotool
python3 clipboard.py          # run in the foreground
```

Run it from your checkout for development; the daemon will find its own
shortcut slot and leave existing custom shortcuts untouched.

## Development workflow

1. Fork the repo and create a branch off `main`:
   `git checkout -b fix/some-behavior main`
2. Make your change and run the app to verify it.
3. Commit with a short, imperative message ("Fix popup focus on Wayland").
4. Push the branch and open a pull request against `main`.

## Pull requests

- **One change per PR.** "Fix search highlight" and "add a settings menu" are
  two PRs.
- **Describe what and why** in the PR description: what the user sees
  before/after, what you tested (X11 and/or Wayland), and any screenshots of
  UI changes.
- **Keep the README in sync.** If you change a command, keybinding, dependency,
  or file location, update `README.md` and `ubuntu-clipboard.desktop` in the
  same PR.
- **No secrets or personal paths** in code, docs, or `.desktop` files — the
  paths in this repo are placeholders resolved at install time.

## Reporting bugs

Open an issue using the template. Good reports include:

- Ubuntu version and session type (Wayland or X11 — check
  `echo $XDG_SESSION_TYPE`)
- The exact command you ran and its full error output
  (the daemon logs to `~/.local/share/ubuntu-clipboard/manager.log` — include it)
- What you expected to happen

## Ideas worth working on

- Per-item "copy back without pasting"
- Copy a selection (Shift+arrow) for multi-paste
- Search in image entries (OCR)
- GTK 4 / libadwaita port
- Config file or UI for max items / image retention

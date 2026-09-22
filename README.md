# Ubuntu Clipboard History

![CI](https://github.com/Lekhan11/ubuntu-clipboard/actions/workflows/ci.yml/badge.svg)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A small Windows-style clipboard history for Ubuntu GNOME. It stores copied text and images locally, then opens a searchable history with **Super+Shift+V**.

## Features

- Records copied text and images from the clipboard
- Keeps up to 200 entries in a local SQLite database
- Search text history
- Select an entry with the keyboard and press Enter to paste
- Pin entries with Ctrl+P; delete with Delete
- Image previews
- Runs on GNOME Wayland by polling the clipboard

## Getting started

```bash
git clone https://github.com/Lekhan11/ubuntu-clipboard
cd ubuntu-clipboard
./install.sh --start     # checks deps, wires up autostart + shortcut, starts it
```

`install.sh` points your autostart entry at your own checkout, so you don't need
to edit any paths. If you'd rather do it by hand, the steps below.

## Install dependencies

```bash
sudo apt update
sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-gdkpixbuf-2.0 ydotool
```

`ydotool` is used to send Ctrl+V after choosing an item. If it is not available or is not configured, the selected item is still copied back to the clipboard; press Ctrl+V yourself.

On systems where `ydotool` needs the input daemon, start/configure `ydotoold` according to your Ubuntu release.

## Run

The manager must keep running for history to be recorded. Start it detached so it survives closing the terminal (replace `<repo-dir>` with wherever you cloned the project):

```bash
setsid -f python3 <repo-dir>/clipboard.py
```

Or run it in the foreground to watch the log:

```bash
cd <repo-dir>
python3 clipboard.py
```

Only one manager runs at a time. Launching it again while one is already running just shows/hides the popup and exits — it does not start a second copy.

### Start it at login

```bash
mkdir -p ~/.config/autostart
cp <repo-dir>/ubuntu-clipboard.desktop ~/.config/autostart/
```

Then edit the `Exec=` line to point at your copy of `clipboard.py` (or just
re-run `./install.sh`, which does this for you).

Use **Super+Shift+V** to open/close the history. The shortcut is registered automatically on first launch; to repair or re-register it by hand:

```bash
python3 <repo-dir>/clipboard.py --install-shortcut
```

If GNOME does not activate it immediately, log out and back in, or add this command manually in **Settings → Keyboard → Custom Shortcuts**:

```text
python3 <repo-dir>/clipboard.py --toggle
```

On Wayland the manager keeps a 1×1 invisible window mapped: without a mapped surface the compositor never offers clipboard data, so polling would record nothing.

## Data and privacy

History is stored at `~/.local/share/ubuntu-clipboard/history.db`. It is never uploaded. Images older than seven days are removed unless pinned. Delete the database to clear all history.

## Contributing

Pull requests are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) for the
ground rules (single-file project, Python 3 + GTK 3, Wayland-first, local-only
data). Good first issues are labeled `good-first-issue` on the [issue tracker](https://github.com/Lekhan11/ubuntu-clipboard/issues).

## License

[MIT](LICENSE)

## Keyboard controls

| Key | Action |
|---|---|
| Super+Shift+V | Open or close history |
| Up/Down | Select an entry |
| Enter | Paste selected entry |
| Ctrl+P | Pin/unpin selected entry |
| Delete | Delete selected entry |
| Esc | Close history |

#!/usr/bin/env python3
"""
ubuntu-clipboard — a Windows-style (Win+V) clipboard history for Ubuntu.

Requirements:
  - Python 3, PyGObject (gir1.2-gtk-3.0) with GTK 3, GLib, Gdk, GdkPixbuf

Run:  python3 clipboard.py
"""

import os
import sys
import time
import warnings
import sqlite3
import hashlib
import subprocess
import signal

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib, Gio, GdkPixbuf

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(GLib.get_user_data_dir(), "ubuntu-clipboard")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "history.db")

MAX_ITEMS = 200
IMAGE_MAX_DIM = 240  # preview thumbnail max dimension
IMAGE_EXPIRE_DAYS = 7  # images auto-purged after a week (they're big)


# ---------------------------------------------------------------- database

def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS history (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               kind TEXT NOT NULL,             -- 'text' | 'image'
               content BLOB NOT NULL,          -- utf-8 bytes or PNG bytes
               hash TEXT NOT NULL,
               created_at REAL NOT NULL,
               pinned INTEGER DEFAULT 0
           )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_created ON history(created_at DESC)")
    return conn


class Store:
    def __init__(self):
        self.db = _db()

    def add(self, kind, data, h, created=None, skip_dedupe=False):
        now = created or time.time()
        c = self.db.cursor()
        # dedupe: same content already exists -> move to top, keep pin state
        if not skip_dedupe:
            c.execute("SELECT id, pinned FROM history WHERE hash=?", (h,))
            row = c.fetchone()
            if row:
                c.execute("UPDATE history SET created_at=? WHERE id=?", (now, row[0]))
                self.db.commit()
                return "moved", row[0]
        c.execute(
            "INSERT INTO history (kind, content, hash, created_at) VALUES (?,?,?,?)",
            (kind, data, h, now),
        )
        self.db.commit()
        # cap history for non-pinned entries
        c.execute(
            "DELETE FROM history WHERE pinned=0 AND id NOT IN "
            "(SELECT id FROM history WHERE pinned=0 ORDER BY created_at DESC LIMIT ?)",
            (MAX_ITEMS,),
        )
        self.db.commit()
        return "added", c.lastrowid

    def list(self, limit=MAX_ITEMS):
        c = self.db.cursor()
        c.execute(
            "SELECT id, kind, content, created_at, pinned FROM history "
            "ORDER BY pinned DESC, created_at DESC LIMIT ?",
            (limit,),
        )
        return c.fetchall()

    def toggle_pin(self, item_id):
        c = self.db.cursor()
        c.execute("UPDATE history SET pinned = 1 - pinned WHERE id=?", (item_id,))
        self.db.commit()

    def delete(self, item_id):
        self.db.cursor().execute("DELETE FROM history WHERE id=?", (item_id,))
        self.db.commit()

    def purge_old_images(self):
        cutoff = time.time() - IMAGE_EXPIRE_DAYS * 86400
        c = self.db.cursor()
        c.execute("DELETE FROM history WHERE kind='image' AND pinned=0 AND created_at<?", (cutoff,))
        self.db.commit()


# ---------------------------------------------------------------- clipboard

class ClipboardMonitor:
    """Polls the clipboard (works on Wayland, where signal-based APIs miss
    clipboard updates from other apps)."""

    def __init__(self, on_change):
        self.on_change = on_change
        self.clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        self._last = None
        self._grab()
        GLib.timeout_add(700, self._poll)

    def _grab(self):
        # request_targets gives us the advertised MIME targets without
        # claiming ownership of the clipboard. The callback signature is
        # (clipboard, atoms, user_data) in GTK 3.
        self.clipboard.request_targets(self._targets_cb)

    def _targets_cb(self, clipboard, atoms, user_data=None):
        if not atoms:
            return
        names = {atom.name() for atom in atoms}
        have_image = bool(names & {"image/png", "image/jpeg", "image/bmp"})
        have_text = bool(names & {"UTF8_STRING", "STRING", "text/plain", "text/plain;charset=utf-8"})
        self._last = "image" if have_image else "text" if have_text else None
        if have_image:
            clipboard.request_image(self._image_cb)
        elif have_text:
            clipboard.request_text(self._text_cb)

    def _poll(self):
        self._grab()
        return True

    def _text_cb(self, clipboard, text, user_data=None):
        if text and text.strip():
            self.on_change("text", text)

    def _image_cb(self, clipboard, pixbuf, user_data=None):
        if pixbuf is None:
            return
        # savev() writes to a *filename*; save_to_bufferv() returns PNG bytes.
        try:
            ok, data = pixbuf.save_to_bufferv("png", [], [])
        except Exception:
            return
        if ok and data:
            self.on_change("image", bytes(data))


# ---------------------------------------------------------------- popup UI

POPUP_W, POPUP_H = 460, 520


class ClipboardRow(Gtk.FlowBoxChild):
    def __init__(self, item, app):
        super().__init__()
        self.item_id, self.kind, self.content, self.created, self.pinned = item
        self.app = app

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_top(6); box.set_margin_bottom(6)
        box.set_margin_start(8); box.set_margin_end(8)

        if self.kind == "text":
            text = self.content.decode("utf-8", "replace")
            label = Gtk.Label()
            label.set_text(text if len(text) <= 300 else text[:300] + "…")
            label.set_ellipsize(3)  # END
            label.set_line_wrap(True)
            label.set_lines(4)
            label.set_xalign(0.0)
            label.set_max_width_chars(48)
            box.pack_start(label, False, False, 0)
        else:
            try:
                loader = GdkPixbuf.PixbufLoader.new()
                loader.write(self.content)
                loader.close()
                pix = loader.get_pixbuf()
            except Exception:
                pix = None
            if pix:
                w, h = pix.get_width(), pix.get_height()
                scale = min(IMAGE_MAX_DIM / w, IMAGE_MAX_DIM / h, 1.0)
                pix = pix.scale_simple(
                    max(1, int(w * scale)), max(1, int(h * scale)),
                    GdkPixbuf.InterpType.BILINEAR)
                box.pack_start(Gtk.Image.new_from_pixbuf(pix), False, False, 0)
            else:
                box.pack_start(Gtk.Label(label="(image)"), False, False, 0)

        meta = Gtk.Label()
        from datetime import datetime
        when = datetime.fromtimestamp(self.created).strftime("%b %d %H:%M")
        pin = "  📌" if self.pinned else ""
        meta.set_markup(f"<small>{when}{pin}</small>")
        meta.set_xalign(0.0)
        box.pack_start(meta, False, False, 0)

        frame = Gtk.Frame()
        frame.add(box)
        self.add(frame)

    def do_button_press_event(self, event):
        if event.button == 3:  # right click
            self.app.show_row_menu(self)
            return True
        return Gtk.FlowBoxChild.do_button_press_event(self, event)

    def do_activate(self):
        self.app.paste_item(self)


class ClipboardWindow(Gtk.Window):
    def __init__(self, app):
        super().__init__(title="Clipboard History")
        self.app = app
        self.set_default_size(POPUP_W, POPUP_H)
        self.set_resizable(True)
        try:
            self.set_keep_above(True)
        except Exception:
            pass
        self.connect("delete-event", self.on_delete)
        self.connect("key-press-event", self.on_key)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        outer.set_border_width(8)
        self.add(outer)

        self.search = Gtk.SearchEntry()
        self.search.connect("search-changed", lambda e: self.populate(e.get_text()))
        self.search.connect("activate", lambda e: self.activate_selected())
        self.search.connect("key-press-event", self.on_search_key)
        outer.pack_start(self.search, False, False, 0)

        hint = Gtk.Label()
        hint.set_markup("<small>Enter paste · Del delete · Ctrl+P pin · Esc close</small>")
        hint.set_xalign(0.0)
        outer.pack_start(hint, False, False, 0)

        self.scroller = Gtk.ScrolledWindow()
        self.scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroller.set_vexpand(True)
        outer.pack_start(self.scroller, True, True, 0)

        self.flow = Gtk.FlowBox()
        self.flow.set_valign(Gtk.Align.START)
        self.flow.set_max_children_per_line(1)
        self.flow.set_min_children_per_line(1)
        self.flow.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.flow.connect("child-activated", lambda fb, child: self.app.paste_item(child))
        self.scroller.add(self.flow)

    def on_delete(self, *a):
        self.app.hide_popup()
        return True

    def rows(self):
        return [c for c in self.flow.get_children() if isinstance(c, ClipboardRow)]

    def selected_item(self):
        # Gtk.FlowBox has get_selected_children(); there is no get_selected_child().
        for child in self.flow.get_selected_children() or []:
            if isinstance(child, ClipboardRow):
                return child
        children = self.rows()
        return children[0] if children else None

    def row_index(self, row):
        children = self.rows()
        return children.index(row) if row in children else -1

    def move_selection(self, key):
        """Move the highlighted row with the arrow keys while the search entry keeps
        focus (Gtk.FlowBox only handles them when it is the focused widget)."""
        children = self.rows()
        if not children:
            return False
        idx = self.row_index(self.selected_item())
        if key == Gdk.KEY_Up:
            new = max(0, idx - 1)
        elif key == Gdk.KEY_Down:
            new = min(len(children) - 1, idx + 1)
        elif key == Gdk.KEY_Page_Up:
            new = max(0, idx - 10)
        elif key == Gdk.KEY_Page_Down:
            new = min(len(children) - 1, idx + 10)
        elif key == Gdk.KEY_Home:
            new = 0
        elif key == Gdk.KEY_End:
            new = len(children) - 1
        else:
            return False
        self.flow.select_child(children[new])
        self.scroll_to_child(children[new])
        return True

    def scroll_to_child(self, child):
        if not child.get_mapped():
            return
        alloc = child.get_allocation()
        adj = self.scroller.get_vadjustment()
        top = adj.get_value()
        bottom = top + adj.get_page_size()
        if alloc.y < top:
            adj.set_value(alloc.y)
        elif alloc.y + alloc.height > bottom:
            adj.set_value(alloc.y + alloc.height - adj.get_page_size())

    def activate_selected(self):
        child = self.selected_item()
        if child:
            self.app.paste_item(child)

    def on_search_key(self, entry, event):
        key = event.keyval
        if key in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.activate_selected()
            return True
        if key == Gdk.KEY_Delete:
            child = self.selected_item()
            if child:
                self.app.delete_item(child)
            return True
        if key == Gdk.KEY_p and event.state & Gdk.ModifierType.CONTROL_MASK:
            child = self.selected_item()
            if child:
                self.app.toggle_pin_item(child)
            return True
        if key == Gdk.KEY_Escape:
            self.app.hide_popup()
            return True
        if key in (Gdk.KEY_Up, Gdk.KEY_Down, Gdk.KEY_Page_Up, Gdk.KEY_Page_Down,
                   Gdk.KEY_Home, Gdk.KEY_End):
            return self.move_selection(key)
        return False

    def on_key(self, w, event):
        key = event.keyval
        if key == Gdk.KEY_Escape:
            self.app.hide_popup()
            return True
        if key in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.activate_selected()
            return True
        if key == Gdk.KEY_Delete:
            child = self.selected_item()
            if child:
                self.app.delete_item(child)
            return True
        if key == Gdk.KEY_p and event.state & Gdk.ModifierType.CONTROL_MASK:
            child = self.selected_item()
            if child:
                self.app.toggle_pin_item(child)
            return True
        return False

    def populate(self, query="", select_index=0):
        self.flow.foreach(lambda c: self.flow.remove(c))
        q = (query or "").lower()
        for item in self.app.store.list():
            if q and item[1] == "text":
                if q not in item[2].decode("utf-8", "replace").lower():
                    continue
            elif q and item[1] == "image":
                continue  # no OCR: search only matches text entries
            child = ClipboardRow(item, self.app)
            self.flow.add(child)
        self.flow.show_all()
        children = self.rows()
        if children:
            idx = min(max(0, select_index), len(children) - 1)
            self.flow.select_child(children[idx])
            self.scroll_to_child(children[idx])


class ClipboardApp:
    def __init__(self):
        self.store = Store()
        self.store.purge_old_images()
        self.win = ClipboardWindow(self)
        self.win.set_position(Gtk.WindowPosition.CENTER)
        self.win.hide()
        self._suppress = False
        self._last_hash = None

        # Must exist before the monitor starts polling: see _start_keepalive_window().
        self._keepalive = self._start_keepalive_window()

        self.monitor = ClipboardMonitor(self._on_clipboard_change)

        # tray/status icon (AppIndicator if available)
        self._setup_tray()

        # bind global shortcut via GNOME settings (gsettings is the reliable
        # Wayland way without an X11 global grab)
        self._install_shortcut()

    # ---- Wayland keepalive

    def _start_keepalive_window(self):
        """Keep a mapped 1x1 invisible window alive for the whole session.

        On Wayland a client with no mapped surface is never offered clipboard data:
        Gdk returns an empty target list, so polling records nothing at all while
        the popup is hidden. A tiny invisible toplevel gives GTK a wl_data_device
        that keeps receiving selection offers from other applications."""
        w = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        w.set_default_size(1, 1)
        w.set_size_request(1, 1)
        w.set_decorated(False)
        w.set_skip_taskbar_hint(True)
        w.set_skip_pager_hint(True)
        w.set_accept_focus(False)
        w.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                w.set_opacity(0.0)
        except Exception:
            pass
        w.add(Gtk.Label(label=""))
        w.show_all()
        w.move(-32000, -32000)
        return w

    # ---- clipboard handling

    def _on_clipboard_change(self, kind, data):
        if self._suppress:  # our own paste-back, don't re-record
            return
        if kind == "text":
            raw = data.encode("utf-8")
        else:
            raw = data
        h = hashlib.sha256(kind.encode() + raw).hexdigest()
        # Polling sees the same owner repeatedly; only persist actual changes.
        if h == self._last_hash:
            return
        self._last_hash = h
        self.store.add(kind, raw, h)
        # Refresh an already-open popup immediately; otherwise the new item
        # only appears after the next manual reopen.
        if self.win.get_visible():
            self.win.populate(self.win.search.get_text())

    # ---- paste

    def paste_item(self, row):
        kind, content = row.kind, row.content
        self._suppress = True

        cb = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        if kind == "text":
            cb.set_text(content.decode("utf-8", "replace"), -1)
        else:
            loader = GdkPixbuf.PixbufLoader.new()
            loader.write(content)
            loader.close()
            cb.set_image(loader.get_pixbuf())
        cb.store()

        # Claim the clipboard BEFORE hiding: on Wayland a set-selection request
        # from a client with no focused surface is silently dropped, which left
        # the previously copied text on the clipboard for Ctrl+V to paste.
        self._last_hash = hashlib.sha256(kind.encode() + content).hexdigest()
        self.hide_popup()

        # Give the clipboard manager time to hide and restore focus before
        # sending Ctrl+V to the application that opened the popup.
        GLib.timeout_add(300, self._synthesize_paste)

    def _synthesize_paste(self):
        # ydotool is Wayland-compatible. If it is unavailable, the item is
        # still placed on the clipboard and can be pasted with Ctrl+V.
        try:
            subprocess.Popen(
                ["ydotool", "key", "29:1", "47:1", "47:0", "29:0"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            pass
        GLib.timeout_add(400, lambda: (setattr(self, "_suppress", False), False)[1])

    # ---- row actions

    def show_row_menu(self, row):
        menu = Gtk.Menu()
        for label, fn in (
            ("Paste", lambda *_: self.paste_item(row)),
            ("Toggle pin", lambda *_: self.toggle_pin_item(row)),
            ("Delete", lambda *_: self.delete_item(row)),
        ):
            mi = Gtk.MenuItem(label=label)
            mi.connect("activate", fn)
            menu.append(mi)
        menu.show_all()
        menu.popup_at_pointer(None)

    def toggle_pin_item(self, row):
        idx = self.win.row_index(row)
        self.store.toggle_pin(row.item_id)
        self.win.populate(self.win.search.get_text(), select_index=idx)

    def delete_item(self, row):
        idx = self.win.row_index(row)
        self.store.delete(row.item_id)
        self.win.populate(self.win.search.get_text(), select_index=idx)

    # ---- popup show/hide

    def hide_popup(self, *a):
        self.win.hide()

    def show_popup(self, *a):
        self.win.populate(self.win.search.get_text())
        self.win.search.set_text("")
        self.win.show_all()
        self.win.present()
        self.win.search.grab_focus()
        children = self.win.flow.get_children()
        if children:
            self.win.flow.select_child(children[0])

    def toggle_popup(self, *a):
        if self.win.get_visible():
            self.hide_popup()
        else:
            self.show_popup()

    # ---- global shortcut

    def _install_shortcut(self):
        """Bind Super+Shift+V through GNOME's own media-keys settings. This works on
        Wayland because mutter injects the activation for us."""
        def try_keybinding(settings):
            if settings is None:
                print("Could not set keybinding — bind Super+Shift+V manually to "
                      "`ubuntu-clipboard --toggle` in Settings > Keyboard",
                      file=sys.stderr)
                return
            custom = "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding"
            path = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom0/"
            s = Gio.Settings.new_with_path(custom, path)
            all_keys = settings.get_strv("custom-keybindings")
            if path not in all_keys:
                all_keys.append(path)
                settings.set_strv("custom-keybindings", all_keys)
            s.set_string("name", "Clipboard history")
            s.set_string("command", f"{sys.executable} {os.path.abspath(__file__)} --toggle")
            s.set_string("binding", "<Super><Shift>v")
            Gio.Settings.sync()
        subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), "--install-shortcut"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        GLib.timeout_add(1000, lambda: True)  # keep loop alive

    # ---- tray

    def _setup_tray(self):
        try:
            gi.require_version("AppIndicator3", "0.1")
            from gi.repository import AppIndicator3
            icon = AppIndicator3.Indicator.new(
                "ubuntu-clipboard", "edit-paste-symbolic",
                AppIndicator3.IndicatorCategory.APPLICATION_STATUS)
            icon.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
            menu = Gtk.Menu()
            for label, fn in (
                ("Show history", lambda *_: self.show_popup()),
                ("Quit", lambda *_: Gtk.main_quit()),
            ):
                mi = Gtk.MenuItem(label=label)
                mi.connect("activate", fn)
                menu.append(mi)
            menu.show_all()
            icon.set_menu(menu)
            self.indicator = icon
        except (ImportError, ValueError):
            self.indicator = None


SHORTCUT_SCHEMA = "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding"
SHORTCUT_BASE = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/"


def _shortcut_settings():
    media = Gio.Settings.new("org.gnome.settings-daemon.plugins.media-keys")
    paths = [p for p in media.get_strv("custom-keybindings") if p]
    return media, paths


def shortcut_is_bound():
    """True when one of the custom keybindings already launches this manager."""
    _media, paths = _shortcut_settings()
    for path in paths:
        s = Gio.Settings.new_with_path(SHORTCUT_SCHEMA, path)
        if "clipboard.py" in s.get_string("command"):
            return True
    return False


def install_shortcut(verbose=True):
    media, paths = _shortcut_settings()
    target = None
    blank = None
    for path in paths:
        s = Gio.Settings.new_with_path(SHORTCUT_SCHEMA, path)
        command, binding = s.get_string("command"), s.get_string("binding")
        if "clipboard.py" in command:
            target = path          # ours already: refresh it
            break
        if not command and not binding and blank is None:
            blank = path           # an empty slot left over from a failed setup
    if target is None:
        target = blank
    if target is None:
        # Never overwrite somebody else's shortcut: take the first free customN.
        used = {p.rstrip("/").rsplit("/", 1)[-1] for p in paths}
        for i in range(100):
            if f"custom{i}" not in used:
                target = f"{SHORTCUT_BASE}custom{i}/"
                media.set_strv("custom-keybindings", paths + [target])
                break
        else:
            print("no free custom-keybinding slot available", file=sys.stderr)
            return False
    s = Gio.Settings.new_with_path(SHORTCUT_SCHEMA, target)
    s.set_string("name", "Ubuntu Clipboard History")
    s.set_string("command", f"{sys.executable} {os.path.abspath(__file__)} --toggle")
    s.set_string("binding", "<Super><Shift>v")
    Gio.Settings.sync()
    bound = bool(s.get_string("command")) and bool(s.get_string("binding"))
    if verbose:
        if bound:
            print(f"Bound Super+Shift+V at {target}\n"
                  "  name:    " + s.get_string("name") + "\n"
                  "  command: " + s.get_string("command") + "\n"
                  "  binding: " + s.get_string("binding") + "\n"
                  "If it does not fire yet, log out and back in.")
        else:
            print("could not write the shortcut; add it manually in "
                  "Settings -> Keyboard -> Custom Shortcuts", file=sys.stderr)
    return bound


def _set_process_name(name):
    """Give the daemon a recognizable name in top/ps (comm is capped at 15 chars)."""
    try:
        import setproctitle
        setproctitle.setproctitle(name)
    except ImportError:
        import ctypes
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.prctl(15, name.encode(), 0, 0, 0)  # PR_SET_NAME


def single_instance():
    """Only one manager at a time; secondary invocations just toggle the popup.

    The lock file must not be truncated before the lock is held: opening it with
    "w" wiped the running instance's pid whenever a second copy was started,
    which left --toggle (Super+Shift+V) unable to find the daemon."""
    import fcntl
    os.makedirs(DATA_DIR, exist_ok=True)
    lock_path = os.path.join(DATA_DIR, "lock")
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None  # already running
    # The lock is ours now, so publishing our pid is safe.
    os.ftruncate(fd, 0)
    os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, str(os.getpid()).encode("ascii"))
    os.fsync(fd)
    return fd


def _is_daemon(pid):
    """True if pid is a running clipboard.py manager (not a --toggle helper)."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            args = [a for a in fh.read().split(b"\x00") if a]
    except OSError:
        return False
    if not args:
        return False
    if any(a == b"ubuntu_clip" for a in args):
        return True  # daemon renamed itself via setproctitle
    if not any(a.endswith(b"clipboard.py") for a in args):
        return False
    return not any(a == b"--toggle" for a in args)


def _find_daemon_pid():
    """Pid of the running manager: from the lock file, else by scanning /proc."""
    try:
        with open(os.path.join(DATA_DIR, "lock"), "r", encoding="utf-8") as f:
            pid = int(f.read().strip())
        if _is_daemon(pid):
            return pid
    except (OSError, ValueError):
        pass
    for entry in os.listdir("/proc"):
        if entry.isdigit() and int(entry) != os.getpid() and _is_daemon(int(entry)):
            return int(entry)
    return None


def _try_autostart():
    """Spawn the manager as a detached session leader (via --restart).

    The manager ends up reparented to init, so it outlives the terminal or
    session that started it — the hotkey self-heals right after login.
    """
    if _find_daemon_pid() is not None:
        return
    subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), "--restart"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(3.0)  # give startup time; the manager installs its handlers early


def main():
    if "--install-shortcut" in sys.argv:
        install_shortcut()
        return
    if "--restart" in sys.argv and not os.environ.get("UBUNTU_CLIP_DAEMON"):
        # Detach fully — new session, new process, fds to /dev/null — then
        # re-exec with the env guard set, so the long-lived manager is a
        # grandchild of this short-lived helper, not its child.
        try:
            os.setsid()
        except OSError:
            pass  # already a session leader
        child = os.fork()
        if child == 0:
            devnull = os.open(os.devnull, os.O_RDWR)
            os.dup2(devnull, 0)
            os.dup2(devnull, 1)
            os.dup2(devnull, 2)
            os.execve(sys.executable,
                      [sys.executable, os.path.abspath(__file__), "--restart"],
                      dict(os.environ, UBUNTU_CLIP_DAEMON="1"))
            os._exit(1)  # unreachable
        os._exit(0)
    if "--toggle" in sys.argv:
        # Ask the running manager to show/hide its popup.
        pid = _find_daemon_pid()
        if pid is None:
            # Nobody is running — start one, then toggle. This is what makes
            # Super+Shift+V work right after a logout/login.
            _try_autostart()
            pid = _find_daemon_pid()
        if pid is None:
            print("ubuntu-clipboard is not running and could not be started.\n"
                  f"  Try manually:\n"
                  f"    python3 {os.path.abspath(__file__)}\n"
                  "  Check ~/.local/share/ubuntu-clipboard/manager.log for errors.",
                  file=sys.stderr)
            return
        try:
            os.kill(pid, signal.SIGUSR1)
        except OSError as e:
            print(f"could not signal the manager (pid {pid}): {e}", file=sys.stderr)
        return
    lock = single_instance()
    if lock is None:
        # Already running — this copy just toggles the popup and exits.
        print("ubuntu-clipboard is already running; toggling its popup instead.\n"
              "(Start it once at login — see the README's Startup Applications note.)",
              file=sys.stderr)
        os.system(f"{sys.executable} {os.path.abspath(__file__)} --toggle")
        return

    # Autostart runs with no terminal; keep a log so failures are visible.
    try:
        _log = open(os.path.join(DATA_DIR, "manager.log"), "a")
        os.dup2(_log.fileno(), sys.stderr.fileno())
        os.dup2(_log.fileno(), sys.stdout.fileno())
    except OSError:
        pass

    # Install the signal handlers before the heavy startup work, so an early
    # SIGUSR1/SIGTERM isn't dropped to the default (terminate) handler. The
    # pipe hands the signal to the GLib loop so Super+Shift+V responds at once
    # instead of on the next ~700 ms poll.
    wake_r, wake_w = os.pipe()
    os.set_blocking(wake_r, False)

    def on_sigusr1(sig, frame):
        try:
            os.write(wake_w, b"\x01")
        except OSError:
            pass

    def on_wake(fd, _condition):
        try:
            os.read(fd, 4096)
        except OSError:
            pass
        app.toggle_popup()
        return True

    signal.signal(signal.SIGUSR1, on_sigusr1)
    signal.signal(signal.SIGTERM, lambda *a: Gtk.main_quit())
    signal.signal(signal.SIGINT, lambda *a: Gtk.main_quit())

    _set_process_name("ubuntu_clip")

    app = ClipboardApp()

    # The README promises the hotkey is registered on first launch; make that true
    # (and self-heal a blank/broken entry left behind by an earlier version).
    try:
        if not shortcut_is_bound():
            print("Registering the Super+Shift+V shortcut…", file=sys.stderr)
            install_shortcut(verbose=False)
    except Exception as e:
        print(f"could not register Super+Shift+V: {e}", file=sys.stderr)

    GLib.io_add_watch(wake_r, GLib.PRIORITY_DEFAULT, GLib.IO_IN, on_wake)
    Gtk.main()


if __name__ == "__main__":
    main()

"""
maica_ui.py -- the desktop app's windows (tkinter, so nothing extra to
install on Windows or Mac): Settings, the first-run Setup guide, and the
firmware updater. maica_app.py opens each in its own process
(maica_app.py --settings / --setup / --firmware), which keeps them clear of
the tray icon's event loop on both systems.
"""

import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from tkinter import colorchooser, messagebox, ttk

import settings as settings_mod
from app_paths import bundled, config_dir
from app_version import VERSION

PAD = {"padx": 10, "pady": 4}


def _tell_app_to_reload():
    try:
        import event_server
        event_server.post({"source": "app", "kind": "event", "cmd": "reload"}, timeout=1)
    except Exception:
        pass


def _open(path_or_url: str):
    if path_or_url.startswith(("http", "ms-settings:", "x-apple")):
        if sys.platform == "win32" and path_or_url.startswith("ms-settings:"):
            os.startfile(path_or_url)
        elif sys.platform == "darwin" and path_or_url.startswith("x-apple"):
            subprocess.run(["open", path_or_url])
        else:
            webbrowser.open(path_or_url)
    elif sys.platform == "win32":
        os.startfile(path_or_url)
    else:
        subprocess.run(["open", path_or_url])


def _window(title: str):
    root = tk.Tk()
    root.title(title)
    root.resizable(False, False)
    try:
        ttk.Style().theme_use("vista" if sys.platform == "win32" else "aqua" if sys.platform == "darwin" else "clam")
    except tk.TclError:
        pass
    if sys.platform == "darwin":  # opened from the menu bar: come to the front, not behind the app in use
        root.lift()
        root.attributes("-topmost", True)
        root.after(300, lambda: root.attributes("-topmost", False))
        root.focus_force()
    return root


HEADING_FONT = ("Segoe UI", 11, "bold") if sys.platform == "win32" else ("Helvetica", 14, "bold")


# -- Settings -------------------------------------------------------------------

def run_settings():
    s = settings_mod.load()
    face = s["face"]
    root = _window("MAICA settings")
    vars_ = {}

    def section(text, row):
        ttk.Label(root, text=text, font=("Segoe UI", 10, "bold")).grid(row=row, column=0, columnspan=3, sticky="w",
                                                                       padx=10, pady=(12, 2))

    def num(label, key, lo, hi, row, src=face, unit=""):
        ttk.Label(root, text=label).grid(row=row, column=0, sticky="w", **PAD)
        v = tk.IntVar(value=int(src.get(key, 0)))
        ttk.Spinbox(root, from_=lo, to=hi, textvariable=v, width=6).grid(row=row, column=1, sticky="w", **PAD)
        if unit:
            ttk.Label(root, text=unit).grid(row=row, column=2, sticky="w")
        vars_[key] = (src, v)

    def check(label, key, row, src=s):
        v = tk.BooleanVar(value=bool(src.get(key, True)))
        ttk.Checkbutton(root, text=label, variable=v).grid(row=row, column=0, columnspan=3, sticky="w", **PAD)
        vars_[key] = (src, v)

    r = 0
    section("MAICA", r); r += 1
    ttk.Label(root, text="Name").grid(row=r, column=0, sticky="w", **PAD)
    name = tk.StringVar(value=face.get("name", "MAICA"))
    ttk.Entry(root, textvariable=name, width=14).grid(row=r, column=1, columnspan=2, sticky="w", **PAD); r += 1
    num("Brightness", "brightness", 5, 100, r, unit="%"); r += 1
    num("Eye size", "eye_size", 70, 130, r, unit="%"); r += 1
    num("Gets bored after", "bored_minutes", 1, 240, r, unit="minutes idle"); r += 1
    num("Falls asleep after", "sleep_minutes", 2, 600, r, unit="minutes idle"); r += 1
    num("Screen off after sleeping", "screen_off_minutes", 0, 600, r, unit="minutes (0 = never)"); r += 1
    num("Turn off with no computer", "no_computer_off_minutes", 0, 600, r, unit="minutes (rev-2 board; 0 = never)"); r += 1

    section("Colours", r); r += 1
    colors = dict(face.get("colors", {}))
    names = [("idle", "Idle"), ("analyzing", "Working"), ("thinking", "Thinking"), ("question", "Asking you")]
    swatches = ttk.Frame(root)
    swatches.grid(row=r, column=0, columnspan=3, sticky="w", padx=10); r += 1
    buttons = {}
    for i, (key, label) in enumerate(names):
        def pick(k=key):
            c = colorchooser.askcolor("#" + colors.get(k, "888888"), title=f"{dict(names)[k]} colour")[1]
            if c:
                colors[k] = c.lstrip("#").upper()
                buttons[k].configure(bg=c)
        buttons[key] = tk.Button(swatches, text=label, width=11, bg="#" + colors.get(key, "888888"), command=pick)
        buttons[key].grid(row=0, column=i, padx=2, pady=2)

    section("Behaviour", r); r += 1
    check("After a reply you haven't looked at, keep a \"done, unread\" look", "unread", r); r += 1
    check("Celebrate when a long task finishes", "cheer", r); r += 1
    check("Tint the rim with the colour of the app it's showing", "accent", r); r += 1
    check("Double-tap MAICA to jump to the app that needs you", "double_tap_jump", r); r += 1
    voice = s["voice"]
    check("Push-to-talk: type what you say into the app in front (rev-2 board)", "enabled", r, src=voice); r += 1
    check("...and press Enter after it", "auto_send", r, src=voice); r += 1

    section("Connection", r); r += 1
    ttk.Label(root, text="Connect by").grid(row=r, column=0, sticky="w", **PAD)
    mode = tk.StringVar(value=s["light"].get("mode", "classic-bt"))
    ttk.Combobox(root, textvariable=mode, values=["classic-bt", "port", "console"], width=12,
                 state="readonly").grid(row=r, column=1, columnspan=2, sticky="w", **PAD); r += 1
    ttk.Label(root, text="Bluetooth name").grid(row=r, column=0, sticky="w", **PAD)
    btname = tk.StringVar(value=s["light"].get("name", "ClaudeStatusMatrix"))
    ttk.Entry(root, textvariable=btname, width=20).grid(row=r, column=1, columnspan=2, sticky="w", **PAD); r += 1
    ttk.Label(root, text="Serial port (\"port\" mode)").grid(row=r, column=0, sticky="w", **PAD)
    port = tk.StringVar(value=s["light"].get("port", ""))
    ttk.Entry(root, textvariable=port, width=20).grid(row=r, column=1, columnspan=2, sticky="w", **PAD); r += 1

    def save():
        face["name"] = name.get().strip()[:12] or "MAICA"
        face["colors"] = colors
        for key, (src, v) in vars_.items():
            try:
                src[key] = v.get()
            except tk.TclError:
                pass
        s["light"].update(mode=mode.get(), name=btname.get().strip(), port=port.get().strip())
        settings_mod.save(s)
        _tell_app_to_reload()
        root.destroy()

    bar = ttk.Frame(root)
    bar.grid(row=r, column=0, columnspan=3, sticky="e", padx=10, pady=12)
    ttk.Button(bar, text="Cancel", command=root.destroy).pack(side="right", padx=4)
    ttk.Button(bar, text="Save", command=save).pack(side="right", padx=4)
    root.mainloop()


# -- First-run setup ---------------------------------------------------------------

def install_claude_code_hooks():
    """Register the status hook in ~/.claude/settings.json (only adds ours)."""
    import claude_code_hook_installer as inst
    if getattr(sys, "frozen", False):
        return inst.install()
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "claude_code_status_hook.py")
    py = "py -3.11" if sys.platform == "win32" else f'"{sys.executable}"'
    path = inst.default_settings_path()
    try:
        with open(path, encoding="utf-8") as f:
            before = json.load(f)
    except FileNotFoundError:
        before = {}
    after, changed = inst.merge_hooks(before, f'{py} "{script}"')
    if changed:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        inst._write_settings(path, after)
    return True


def extension_dir(browser: str) -> str:
    for base in (bundled("extension"), os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "extension")):
        d = os.path.join(base, "dist", browser)
        if os.path.isdir(d):
            return os.path.normpath(d)
    return ""


def run_setup():
    root = _window("Set up MAICA")
    steps = []
    current = [0]
    body = ttk.Frame(root, padding=16)
    body.grid(row=0, column=0, sticky="nsew")
    nav = ttk.Frame(root, padding=(16, 0, 16, 12))
    nav.grid(row=1, column=0, sticky="ew")

    def text(frame, t, bold=False):
        ttk.Label(frame, text=t, wraplength=440, justify="left",
                  font=HEADING_FONT if bold else None).pack(anchor="w", pady=4)

    def step(build):
        steps.append(build)

    @step
    def welcome(f):
        text(f, f"Welcome to MAICA {VERSION}", bold=True)
        text(f, "MAICA shows what your AI apps are doing -- working, thinking, asking you something, done, or "
                "running low on usage. This takes about two minutes: connect MAICA, then tell it which AI apps you use.")
        from app_paths import running_from_disk_image
        if running_from_disk_image():
            text(f, "First, move MAICA into your Applications folder: drag MAICA from this window (or Downloads) "
                    "into Applications, then open it from there. Run from here, the things that remember where "
                    "MAICA is -- Claude Code's link, Start at login -- would break once this copy goes away.", bold=True)
            ttk.Button(f, text="Open Applications", command=lambda: _open("/Applications")).pack(anchor="w", pady=6)

    @step
    def connect(f):
        text(f, "1. Connect MAICA", bold=True)
        text(f, "Switch MAICA on, then pair it: it appears as \"ClaudeStatusMatrix\" in your computer's Bluetooth "
                "settings." + (" On a Mac, click Connect next to it; if macOS asks whether MAICA may use Bluetooth, "
                               "allow it." if sys.platform == "darwin" else ""))
        status = ttk.Label(f, text="")
        def bt():
            _open("ms-settings:bluetooth" if sys.platform == "win32" else
                  "x-apple.systempreferences:com.apple.BluetoothSettings")
        def test():
            from claude_light_tracker import find_classic_bt_port
            try:
                port = find_classic_bt_port(settings_mod.load()["light"].get("name", "ClaudeStatusMatrix"))
                status.configure(text=f"Found it on {port}.")
            except Exception as e:
                status.configure(text=str(e))
        row = ttk.Frame(f); row.pack(anchor="w", pady=6)
        ttk.Button(row, text="Open Bluetooth settings", command=bt).pack(side="left", padx=(0, 6))
        ttk.Button(row, text="Check", command=test).pack(side="left")
        status.pack(anchor="w")

    @step
    def coding(f):
        text(f, "2. Coding agents", bold=True)
        text(f, "Claude Code: MAICA follows it through Claude Code's own hooks -- set them up here (it only adds "
                "MAICA's entries to ~/.claude/settings.json). Codex needs nothing: MAICA reads its session logs.")
        status = ttk.Label(f, text="")
        def hooks():
            from app_paths import running_from_disk_image
            if running_from_disk_image():
                status.configure(text="Move MAICA into Applications and open it from there first (see the first "
                                      "page) -- Claude Code would be pointed at a copy that goes away.")
                return
            try:
                install_claude_code_hooks()
                status.configure(text="Claude Code is set up. New sessions are picked up automatically.")
            except Exception as e:
                status.configure(text=f"Couldn't set it up: {e}")
        ttk.Button(f, text="Set up Claude Code", command=hooks).pack(anchor="w", pady=6)
        status.pack(anchor="w")

    @step
    def browser(f):
        text(f, "3. Chats in your browser", bold=True)
        text(f, "For ChatGPT, Claude, Gemini, Copilot, Perplexity, Grok, DeepSeek and more in a browser, add the "
                "MAICA extension. Until it's in the stores: open your browser's extensions page (address below), "
                "turn on Developer mode, choose \"Load unpacked\" and pick the folder this opens.")
        row = ttk.Frame(f); row.pack(anchor="w", pady=6)
        for label, browser_, url in (("Chrome / Edge / Brave", "chrome", "chrome://extensions"),
                                     ("Firefox", "firefox", "about:debugging#/runtime/this-firefox")):
            def go(b=browser_, u=url):
                d = extension_dir(b)
                if d:
                    _open(d)
                root.clipboard_clear()
                root.clipboard_append(u)
                messagebox.showinfo("MAICA", f"Copied {u} -- paste it into your browser's address bar.")
            ttk.Button(row, text=label, command=go).pack(side="left", padx=(0, 6))
        if sys.platform == "darwin":
            text(f, "Desktop apps (Claude, ChatGPT, Copilot, LM Studio...) need one permission -- the next step.")
        else:
            text(f, "Desktop apps (Claude, ChatGPT, Copilot, LM Studio...) need nothing extra on Windows.")

    if sys.platform == "darwin":
        @step
        def access(f):
            text(f, "4. Let MAICA see your apps", bold=True)
            text(f, "On a Mac, MAICA reads the desktop apps (Claude, ChatGPT, Copilot, LM Studio...) through "
                    "Accessibility. Turn MAICA on in the list this opens -- if it isn't listed, click + and pick "
                    "MAICA in Applications.")
            status = ttk.Label(f, text="")
            def check():
                try:
                    import mac_ax
                    ok = mac_ax.is_trusted()
                except Exception:
                    ok = False
                status.configure(text="MAICA can see your apps." if ok else
                                 "Not yet -- turn MAICA on in the list, then check again. (A change can need "
                                 "MAICA restarting: menu bar icon > Restart MAICA.)")
            row = ttk.Frame(f); row.pack(anchor="w", pady=6)
            ttk.Button(row, text="Open Accessibility settings", command=lambda: _open(
                "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility")).pack(side="left",
                                                                                                    padx=(0, 6))
            ttk.Button(row, text="Check", command=check).pack(side="left")
            status.pack(anchor="w")
            text(f, "Push-to-talk types into the app in front: the first time, macOS asks whether MAICA may "
                    "control System Events -- allow it.")

    @step
    def done(f):
        text(f, "All set", bold=True)
        text(f, "MAICA lives in your system tray / menu bar. Its menu has Settings (name, colours, brightness...), "
                "this guide again, and firmware updates.")
        import autostart
        v = tk.BooleanVar(value=autostart.is_enabled())
        def flip():
            (autostart.enable if v.get() else autostart.disable)()
        ttk.Checkbutton(f, text="Start MAICA when I log in", variable=v, command=flip).pack(anchor="w", pady=6)

    back = ttk.Button(nav, text="Back")
    nxt = ttk.Button(nav, text="Next")

    def show(i):
        current[0] = i
        for w in body.winfo_children():
            w.destroy()
        steps[i](body)
        back.configure(state="normal" if i > 0 else "disabled")
        nxt.configure(text="Finish" if i == len(steps) - 1 else "Next")

    back.configure(command=lambda: show(current[0] - 1))
    nxt.configure(command=lambda: root.destroy() if current[0] == len(steps) - 1 else show(current[0] + 1))
    nxt.pack(side="right", padx=4)
    back.pack(side="right", padx=4)
    root.minsize(500, 300)
    show(0)
    root.mainloop()


# -- Firmware --------------------------------------------------------------------

def run_firmware():
    """Flash MAICA over USB: the bundled firmware, or -- running from source
    with the Arduino IDE installed -- the sketch built fresh. If MAICA doesn't
    switch into download mode by itself, it says which buttons to press and
    the next Flash goes ahead without the reset (see firmware_update)."""
    import firmware_update as fw
    root = _window("Flash MAICA's firmware")
    f = ttk.Frame(root, padding=14)
    f.pack(fill="both", expand=True)
    ttk.Label(f, text="Plug MAICA into this computer with a USB data cable, pick its port and press Flash. "
                      "It takes under a minute; don't unplug it meanwhile. MAICA's Bluetooth pairing and "
                      "settings are kept.", wraplength=480, justify="left").pack(anchor="w")

    row = ttk.Frame(f); row.pack(anchor="w", pady=(10, 4))
    ttk.Label(row, text="Port").pack(side="left", padx=(0, 6))
    port = tk.StringVar()
    combo = ttk.Combobox(row, textvariable=port, width=44, state="readonly")
    combo.pack(side="left")

    def refresh():
        ports = fw.board_ports()
        combo.configure(values=[d for _, d in ports])
        # Only MAICA's own chip is picked for you; anything else, you choose.
        if ports and "CP210x" in ports[0][1]:
            combo.current(0)
        elif not ports:
            port.set("")

    ttk.Button(row, text="Refresh", command=refresh).pack(side="left", padx=6)

    can_build, have_bundled = fw.can_build(), fw.bundled_parts() is not None
    source = tk.StringVar(value="build" if can_build else "bundled")
    screen = tk.StringVar(value="round" if fw.sketch_screen_round() else "square")
    src = ttk.Frame(f); src.pack(anchor="w", pady=4)
    if can_build:
        ttk.Radiobutton(src, text="Build the sketch (maica_status_classicbt) and flash it", value="build",
                        variable=source).pack(anchor="w")
        scr = ttk.Frame(src); scr.pack(anchor="w", padx=22)
        ttk.Label(scr, text="Screen:").pack(side="left")
        ttk.Radiobutton(scr, text="Square", value="square", variable=screen).pack(side="left", padx=4)
        ttk.Radiobutton(scr, text="Round", value="round", variable=screen).pack(side="left", padx=4)
    if have_bundled:
        ttk.Radiobutton(src, text=f"The firmware that came with MAICA {VERSION}", value="bundled",
                        variable=source).pack(anchor="w")
    if not can_build and not have_bundled:
        ttk.Label(src, text="No firmware found: this copy has none bundled, and the Arduino IDE isn't installed "
                            "to build it.", foreground="#b00020", wraplength=480).pack(anchor="w")

    in_download = tk.BooleanVar(value=False)
    ttk.Checkbutton(f, text="MAICA is already in download mode (I held BOOT, tapped RESET, let go of BOOT)",
                    variable=in_download).pack(anchor="w", pady=(6, 0))
    hint = ttk.Label(f, text="", foreground="#b00020", wraplength=480, justify="left")
    hint.pack(anchor="w", pady=(6, 0))

    log = tk.Text(f, width=72, height=14, state="disabled")
    log.pack(fill="both", expand=True, pady=8)
    lines = queue.Queue()
    built = {}  # (screen) -> parts, so a retry after the button press doesn't build again

    def pump():
        while not lines.empty():
            item = lines.get()
            if isinstance(item, tuple):  # ("done", result)
                finish(item[1])
                continue
            log.configure(state="normal")
            log.insert("end", item + chr(10))
            log.see("end")
            log.configure(state="disabled")
        root.after(100, pump)

    def finish(result):
        btn.configure(state="normal")
        if result == "ok":
            hint.configure(text="Flashed. MAICA is restarting.", foreground="#1b7f3b")
            in_download.set(False)
        elif result == "needs-boot":
            hint.configure(text=fw.BOOT_STEPS, foreground="#b00020")
            in_download.set(True)  # the next press: no reset dance
        elif result == "busy":
            hint.configure(text=fw.BUSY_STEPS, foreground="#b00020")
        elif result == "no-firmware":
            hint.configure(text="Nothing to flash -- see the log.", foreground="#b00020")
        else:
            hint.configure(text="It didn't work -- see the log. Check the cable is a data cable.",
                           foreground="#b00020")

    def go():
        dev = port.get().split(" - ")[0]
        if not dev:
            hint.configure(text="Pick MAICA's port first (plugged in? press Refresh).", foreground="#b00020")
            return
        btn.configure(state="disabled")
        hint.configure(text="")
        use_build, round_screen, reset = source.get() == "build", screen.get() == "round", not in_download.get()

        def work():
            if use_build:
                parts = built.get(round_screen) or fw.build(round_screen, log=lines.put)
                if parts:
                    built.clear()
                    built[round_screen] = parts
            else:
                parts = fw.bundled_parts()
            if not parts:
                lines.put(("done", "no-firmware"))
                return
            lines.put(f"Flashing {dev}" + (" (already in download mode)" if not reset else "") + "...")
            lines.put(("done", fw.flash(dev, parts, reset=reset, log=lines.put)))

        threading.Thread(target=work, daemon=True).start()

    btn = ttk.Button(f, text="Flash", command=go)
    btn.pack(anchor="e")
    refresh()
    pump()
    root.mainloop()

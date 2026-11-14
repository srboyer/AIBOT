"""
build.py -- regenerates the extension's manifests from the shared rules.

    py -3.11 extension/build.py

  - copies Windows/maica_rules.json into the extension (the rules it falls
    back to when the desktop app isn't running);
  - writes manifest.json (Chrome, Edge, Brave, Opera, Vivaldi -- load this
    folder unpacked, or zip it for the Chrome Web Store / Edge Add-ons) with
    the content script matched to every site in the rules;
  - writes dist/firefox/ (Firefox wants a background "scripts" list instead
    of a service worker, and an add-on id) and zips both into dist/;
  - draws the toolbar icons.

Safari: run Apple's converter on dist/chrome on a Mac --
    xcrun safari-web-extension-converter dist/chrome
which produces an Xcode project to sign and ship in the Mac app.
"""

import json
import os
import shutil
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
RULES = os.path.join(HERE, "..", "Windows", "maica_rules.json")
VERSION = "1.1.0"
FILES = ["background.js", "content.js", "popup.html", "popup.js", "maica_rules.json",
         "icon16.png", "icon48.png", "icon128.png"]


def matches(rules):
    out = []
    for s in rules["sites"]:
        for h in s["hosts"]:
            path = "/chat*" if s.get("path", "").startswith("^/chat") else "/*"
            out.append(f"https://{h}{path}")
    return sorted(set(out))


def manifest(rules, firefox=False):
    m = {
        "manifest_version": 3,
        "name": "MAICA",
        "version": VERSION,
        "description": "Shows what your AI chats are doing on MAICA, your desk robot: working, thinking, "
                       "asking you something, done, or out of tokens.",
        "icons": {"16": "icon16.png", "48": "icon48.png", "128": "icon128.png"},
        "action": {"default_popup": "popup.html", "default_title": "MAICA"},
        "permissions": ["storage"],
        "host_permissions": matches(rules) + ["http://127.0.0.1:47823/*"],
        "content_scripts": [{"matches": matches(rules), "js": ["content.js"], "run_at": "document_idle"}],
        "background": {"service_worker": "background.js"},
    }
    if firefox:
        m["background"] = {"scripts": ["background.js"]}
        m["browser_specific_settings"] = {"gecko": {"id": "maica@maica.local", "strict_min_version": "121.0"}}
    return m


def icons():
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("Pillow not installed: skipping icons")
        return
    for size in (16, 48, 128):
        s = size * 4
        img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse((0, 0, s - 1, s - 1), fill=(40, 220, 90, 255))
        ew, eh = s * 0.13, s * 0.22
        for cx in (s * 0.33, s * 0.67):
            d.rounded_rectangle((cx - ew, s * 0.47 - eh, cx + ew, s * 0.47 + eh), radius=ew, fill=(8, 22, 12, 255))
        img.resize((size, size), Image.LANCZOS).save(os.path.join(HERE, f"icon{size}.png"))


def main():
    shutil.copy(RULES, os.path.join(HERE, "maica_rules.json"))
    with open(RULES, encoding="utf-8") as f:
        rules = json.load(f)
    icons()
    with open(os.path.join(HERE, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest(rules), f, indent=2)
    dist = os.path.join(HERE, "dist")
    for name, ff in (("chrome", False), ("firefox", True)):
        out = os.path.join(dist, name)
        os.makedirs(out, exist_ok=True)  # (rmtree is unreliable inside OneDrive folders)
        for old in os.listdir(out):
            os.remove(os.path.join(out, old))
        for fn in FILES:
            shutil.copy(os.path.join(HERE, fn), out)
        with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest(rules, firefox=ff), f, indent=2)
        with zipfile.ZipFile(os.path.join(dist, f"maica-{name}-{VERSION}.zip"), "w", zipfile.ZIP_DEFLATED) as z:
            for fn in os.listdir(out):
                z.write(os.path.join(out, fn), fn)
    print(f"manifest.json + dist/ written for {len(rules['sites'])} sites")


if __name__ == "__main__":
    sys.exit(main())

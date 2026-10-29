"""
Mini Player — widget de bureau pour Windows 10/11 : lecteur + égaliseur.

Contrôle tout ce qui passe par les contrôles média de Windows
(Spotify, Deezer, YouTube dans Firefox/Chrome, Media Player, VLC…).

Lancement : double-clic sur mini_player.pyw.
Au premier lancement, s'il manque des modules, le script propose de les installer tout seul.

Égaliseur : il agit sur le son de tout le PC via Equalizer APO (gratuit) :
    https://sourceforge.net/projects/equalizerapo/
Sans Equalizer APO, l'égaliseur s'affiche mais ne modifie pas le son.

Clavier :
    - Alt + Retour arrière (partout dans Windows) : masquer / afficher le player

Souris :
    - glisser une zone vide : déplacer le widget
    - curseurs de l'égaliseur : glisser, molette, double-clic = remise à 0
    - barre de progression : clic pour se déplacer dans le morceau
    - haut-parleur : couper / remettre le son de l'appli musicale uniquement
    - curseur à côté : volume de l'appli musicale (glisser ou molette)
    - clic droit : menu (premier plan, opacité, quitter)
"""

import os
import subprocess
import sys
import tkinter as tk
from tkinter import messagebox

REQUIRED = [
    "pillow",
    "winrt-runtime",
    "winrt-Windows.Foundation",
    "winrt-Windows.Media.Control",
    "winrt-Windows.Storage.Streams",
    "pycaw",
]


def ensure_deps():
    """Installe les modules manquants avec le même Python que celui qui lance le script."""
    try:
        import PIL  # noqa: F401
        import winrt.windows.media.control  # noqa: F401
        import winrt.windows.storage.streams  # noqa: F401
        import pycaw.pycaw  # noqa: F401
        return
    except ImportError as err:
        missing = err.name or str(err)

    root = tk.Tk()
    root.withdraw()
    if getattr(sys, "frozen", False):  # version .exe : rien à installer, build incomplet
        messagebox.showerror("Mini Player", f"Module manquant dans l'exécutable : {missing}\n"
                             "Relance build_exe.bat pour reconstruire l'appli.")
        raise SystemExit(1)
    if not messagebox.askyesno(
        "Mini Player",
        f"Il manque des modules Python ({missing}).\n\n"
        "Les installer maintenant ? Ça prend environ une minute.",
    ):
        raise SystemExit(1)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    res = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--upgrade", *REQUIRED],
        capture_output=True, text=True, creationflags=flags,
    )
    if res.returncode != 0:
        messagebox.showerror("Mini Player", "L'installation a échoué :\n\n"
                             + (res.stderr or res.stdout)[-900:])
        raise SystemExit(1)
    root.destroy()
    subprocess.Popen([sys.executable, *sys.argv], creationflags=flags)
    raise SystemExit(0)


MUTEX_NAME = "Local\\MiniPlayerSingleInstance"
SHOW_EVENT_NAME = "Local\\MiniPlayerShow"
_mutex = None


def single_instance():
    """Un seul player à la fois : si un autre tourne déjà, on lui demande de s'afficher."""
    global _mutex
    if os.name != "nt":
        return
    import ctypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = ctypes.c_void_p
    k32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    k32.OpenEventW.restype = ctypes.c_void_p
    k32.OpenEventW.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_wchar_p]
    k32.SetEvent.argtypes = [ctypes.c_void_p]
    _mutex = k32.CreateMutexW(None, False, MUTEX_NAME)
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        ev = k32.OpenEventW(0x0002, False, SHOW_EVENT_NAME)  # EVENT_MODIFY_STATE
        if ev:
            k32.SetEvent(ev)
        raise SystemExit(0)


single_instance()
ensure_deps()

import asyncio  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import math  # noqa: E402
import queue  # noqa: E402
import random  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
import tkinter.font as tkfont  # noqa: E402
import webbrowser  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from pathlib import Path  # noqa: E402

from PIL import Image, ImageDraw, ImageFilter, ImageOps, ImageTk  # noqa: E402
from winrt.windows.media.control import (  # noqa: E402
    GlobalSystemMediaTransportControlsSessionManager as MediaManager,
    GlobalSystemMediaTransportControlsSessionPlaybackStatus as PlaybackStatus,
)
from winrt.windows.storage.streams import Buffer, InputStreamOptions  # noqa: E402

PYCAW_ERROR = None
try:  # volume par appli + nom du périphérique de sortie
    import comtypes
    from pycaw.pycaw import AudioUtilities
    try:
        from pycaw.pycaw import IAudioMeterInformation
    except Exception:
        IAudioMeterInformation = None
except Exception as _err:
    comtypes = AudioUtilities = IAudioMeterInformation = None
    PYCAW_ERROR = repr(_err)


# ======================= Apparence =======================
W, H, RADIUS = 520, 486, 22
KEY, KEY_RGB = "#010101", (1, 1, 1)      # couleur rendue transparente (coins)
BASE = (14, 15, 12)
FG = "#eef0e6"
SUB = "#a3a797"
# Couleurs d'accent : remplacées au lancement par celles de Windows (voir set_palette)
DEFAULT_ACCENT = ("#c9dc8a", "#8e9c5e", "#4d5735", "#e4ecc4", "#1a1c14")
ACCENT, ACCENT_DIM, GLOW, KNOB, DARK_TXT = DEFAULT_ACCENT
TRACK = "#3b3e34"
CHIP = "#2a2d25"
BTN = "#24271f"
BTN_TXT = "#d9dccf"
SEP = "#2f322a"
ICON_FONT = "Segoe MDL2 Assets"

# Pochette « vinyle »
ART_CX, ART_CY, ART_R = 104, 106, 82
ART_BOX = (ART_CX - ART_R, ART_CY - ART_R, ART_CX + ART_R, ART_CY + ART_R)
SPIN_DEG_PER_S = 50

# Colonne de droite
RX0, RX1 = 214, W - 26
TITLE_Y, ARTIST_Y, CHIP_Y = 34, 60, 90
PY, TIME_Y, TRANSPORT_Y = 128, 148, 184
SEP_Y, EQ_LABEL_Y = 222, 250

# Égaliseur
BANDS = [31, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]
BAND_LABELS = ["31", "63", "125", "250", "500", "1k", "2k", "4k", "8k", "16k"]
MAX_DB = 12.0
SL_TOP, SL_BOT = 282, 380
SL_MID, SL_HALF = (SL_TOP + SL_BOT) / 2, (SL_BOT - SL_TOP) / 2
SL_LABEL_Y = 398
PRESET_ROWS_Y = (428, 462)
PRESET_H = 26
PRESETS = {
    "Flat":    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    "Bass":    [6, 5, 4, 2, 0, 0, 0, 0, 0, 0],
    "Treble":  [0, 0, 0, 0, 0, 1, 2, 4, 5, 6],
    "Vocal":   [-2, -2, -1, 1, 3, 4, 3, 1, 0, -1],
    "Pop":     [-1, 1, 3, 4, 2, 0, -1, -1, 1, 2],
    "Rock":    [5, 4, 2, -1, -2, -1, 2, 3, 4, 5],
    "Jazz":    [3, 2, 1, 2, -1, -1, 0, 1, 2, 3],
    "Classic": [4, 3, 2, 1, -1, -1, 0, 2, 3, 4],
}

# Raccourci global pour masquer / afficher (Alt + Inser)
HOTKEY_MODS = 0x0001          # MOD_ALT (Ctrl = 0x0002, Shift = 0x0004, Win = 0x0008)
HOTKEY_VK = 0x08              # VK_BACK (Retour arrière) — Inser = 0x2D
HOTKEY_LABEL = "Alt+Retour arrière"

# Animation façon Apple (apparition avec ressort, disparition en douceur)
SHOW_MS, HIDE_MS = 420, 220
SHOW_SLIDE, HIDE_SLIDE = 22, 14      # pixels de glissement
EQ_ANIM_MS = 480                     # durée du glissement d'un curseur d'égaliseur
EQ_STAGGER_MS = 28                   # décalage entre deux bandes (effet vague)

CONFIG = Path.home() / ".mini_player.json"
DEFAULTS = {"x": None, "y": None, "topmost": True, "alpha": 1.0,
            "gains": [0.0] * 10, "preset": "Flat", "autostart": True,
            "use_accent": True}


# ======================= Utilitaires =======================
def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def rgb_hex(rgb):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(v)))) for v in rgb)


def mix(a, b, t):
    """Mélange deux couleurs (t = 0 → a, t = 1 → b)."""
    return tuple(x + (y - x) * t for x, y in zip(a, b))


def luminance(rgb):
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def windows_accent():
    """Teintes de la couleur d'accentuation Windows : (claire2, claire3, normale) ou None."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer\Accent") as k:
            pal, _ = winreg.QueryValueEx(k, "AccentPalette")
        shades = [tuple(pal[i * 4:i * 4 + 3]) for i in range(7)]
        # 0 = Light3, 1 = Light2, 2 = Light1, 3 = accent, 4..6 = Dark1..3
        return shades[1], shades[0], shades[3]
    except Exception:
        pass
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\DWM") as k:
            abgr, _ = winreg.QueryValueEx(k, "AccentColor")
        base = (abgr & 0xFF, (abgr >> 8) & 0xFF, (abgr >> 16) & 0xFF)
        return mix(base, (255, 255, 255), 0.45), mix(base, (255, 255, 255), 0.7), base
    except Exception:
        return None


def set_palette(accent):
    """accent = résultat de windows_accent(), ou None pour le vert d'origine."""
    global ACCENT, ACCENT_DIM, GLOW, KNOB, DARK_TXT
    if not accent:
        ACCENT, ACCENT_DIM, GLOW, KNOB, DARK_TXT = DEFAULT_ACCENT
        return
    light2, light3, base = accent
    # assez clair pour ressortir sur le fond sombre
    while luminance(light2) < 0.28:
        light2 = mix(light2, (255, 255, 255), 0.15)
    ACCENT = rgb_hex(light2)
    ACCENT_DIM = rgb_hex(mix(light2, BASE, 0.38))
    GLOW = rgb_hex(mix(light2, BASE, 0.68))
    KNOB = rgb_hex(mix(light3, (255, 255, 255), 0.35))
    DARK_TXT = "#111111" if luminance(light2) > 0.35 else "#ffffff"


def fmt(seconds):
    s = int(max(0, seconds))
    return f"{s // 60:02d}:{s % 60:02d}"


def round_rect(canvas, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(pts, smooth=True, **kw)


def pick_font(root, *names):
    families = set(tkfont.families(root))
    for n in names:
        if n in families:
            return n
    return "Consolas"


def app_name(aumid):
    a = (aumid or "").lower()
    table = [("spotify", "Spotify"), ("firefox", "Firefox"), ("chrome", "Chrome"),
             ("msedge", "Edge"), ("brave", "Brave"), ("opera", "Opera"),
             ("deezer", "Deezer"), ("zunemusic", "Media Player"), ("vlc", "VLC"),
             ("applemusic", "Apple Music"), ("appleinc", "Apple Music"), ("itunes", "iTunes"), ("tidal", "Tidal"), ("musicbee", "MusicBee"),
             ("foobar", "foobar2000"), ("amazon", "Amazon Music"), ("soundcloud", "SoundCloud")]
    for key, name in table:
        if key in a:
            return name
    base = os.path.basename(aumid or "").split("!")[0]
    if base.lower().endswith(".exe"):
        base = base[:-4]
    if len(base) == 16 and all(ch in "0123456789abcdefABCDEF" for ch in base):
        return "Firefox"  # Firefox s'identifie par un hash
    return base[:18] or ""


def make_disc(data):
    """Pochette découpée en disque (RGBA, bords lissés)."""
    d = ART_R * 2
    big = d * 4
    if data:
        try:
            img = ImageOps.fit(Image.open(io.BytesIO(data)).convert("RGB"), (d, d))
            mask = Image.new("L", (big, big), 0)
            ImageDraw.Draw(mask).ellipse((0, 0, big - 1, big - 1), fill=255)
            img.putalpha(mask.resize((d, d), Image.LANCZOS))
            return img
        except Exception:
            pass
    # disque vinyle par défaut
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    dr = ImageDraw.Draw(img)
    c = big / 2
    dr.ellipse((0, 0, big - 1, big - 1), fill=(30, 32, 27, 255))
    r = big * 0.2
    while r < big / 2 - 8:
        dr.ellipse((c - r, c - r, c + r, c + r), outline=(42, 45, 38, 255), width=3)
        r += 13
    lr = big * 0.17
    dr.ellipse((c - lr, c - lr, c + lr, c + lr), fill=hex_rgb(ACCENT_DIM) + (255,))
    return img.resize((d, d), Image.LANCZOS)


def make_background(data):
    """Fond flouté à partir de la pochette, assombri, coins arrondis."""
    img = None
    if data:
        try:
            small = ImageOps.fit(Image.open(io.BytesIO(data)).convert("RGB"), (W // 4, H // 4))
            img = small.filter(ImageFilter.GaussianBlur(7)).resize((W, H), Image.BICUBIC)
            img = Image.blend(img, Image.new("RGB", (W, H), BASE), 0.78)
        except Exception:
            img = None
    if img is None:
        img = Image.new("RGB", (W, H), BASE)
    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, W - 1, H - 1), RADIUS, fill=255)
    out = Image.new("RGB", (W, H), KEY_RGB)
    out.paste(img, (0, 0), mask)
    return out


# ======================= Media (thread) =======================
class MediaBackend:
    def __init__(self, out_queue):
        self.q = out_queue
        self.manager = None
        self._key = None
        self._art_ok = False
        self._art_tries = 0
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self._run, daemon=True).start()
        if AudioUtilities is not None:
            threading.Thread(target=self._device_loop, daemon=True).start()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._poll())

    async def _poll(self):
        self.manager = await MediaManager.request_async()
        while True:
            try:
                await self._update()
            except Exception:
                pass
            await asyncio.sleep(0.5)

    async def _update(self):
        session = self.manager.get_current_session()
        if session is None:
            msg = {"empty": True}
            if self._key is not None:
                self._key = None
                msg["art"] = None
            self.q.put(("media", msg))
            return

        props = await session.try_get_media_properties_async()
        info = session.get_playback_info()
        tl = session.get_timeline_properties()

        playing = info.playback_status == PlaybackStatus.PLAYING
        title = props.title or "Sans titre"
        artist = props.artist or props.album_artist or ""

        duration = (tl.end_time - tl.start_time).total_seconds()
        position = (tl.position - tl.start_time).total_seconds()
        if playing:
            try:
                position += (datetime.now(timezone.utc) - tl.last_updated_time).total_seconds()
            except Exception:
                pass
        if duration > 0:
            position = max(0.0, min(position, duration))
        else:
            duration = position = 0.0

        aumid = session.source_app_user_model_id
        msg = {"title": title, "artist": artist, "playing": playing,
               "position": position, "duration": duration, "app": app_name(aumid),
               "aumid": aumid}

        key = (title, artist, aumid)
        if key != self._key:
            self._key = key
            self._art_ok = False
            self._art_tries = 0
            msg["art"] = None
        if not self._art_ok and self._art_tries < 8:
            self._art_tries += 1
            data = await self._read_thumb(props.thumbnail)
            if data:
                msg["art"] = data
                self._art_ok = True

        self.q.put(("media", msg))

    @staticmethod
    async def _read_thumb(ref):
        if ref is None:
            return None
        try:
            stream = await ref.open_read_async()
            size = int(stream.size)
            if not size:
                return None
            buf = await stream.read_async(Buffer(size), size, InputStreamOptions.READ_AHEAD)
            return bytes(memoryview(buf))
        except Exception:
            return None

    def _device_loop(self):
        try:
            comtypes.CoInitialize()
        except Exception:
            pass
        last = None
        while True:
            name = None
            try:
                dev = AudioUtilities.GetSpeakers()
                name = getattr(dev, "FriendlyName", None)
                if not name:
                    name = AudioUtilities.CreateDevice(dev).FriendlyName
            except Exception:
                name = None
            if name:
                name = name.split(" (")[-1].rstrip(")") if "(" in name else name
            if name != last:
                last = name
                self.q.put(("device", name))
            time.sleep(3)

    def command(self, name, *args):
        async def run():
            if self.manager is None:
                return
            session = self.manager.get_current_session()
            if session is not None:
                await getattr(session, name)(*args)

        asyncio.run_coroutine_threadsafe(run(), self.loop)


# ======================= Raccourci clavier global =======================
# ======================= Volume de l'appli musicale =======================
APP_PROCESSES = {
    "Spotify": ["spotify.exe"], "Firefox": ["firefox.exe"], "Chrome": ["chrome.exe"],
    "Edge": ["msedge.exe"], "Brave": ["brave.exe"], "Opera": ["opera.exe", "opera_gx.exe"],
    "Deezer": ["deezer.exe"], "Media Player": ["microsoft.media.player.exe"],
    "VLC": ["vlc.exe"], "Apple Music": ["applemusic.exe", "amplibraryagent.exe"],
    "iTunes": ["itunes.exe"], "Tidal": ["tidal.exe"],
    "MusicBee": ["musicbee.exe"], "foobar2000": ["foobar2000.exe"],
    "Amazon Music": ["amazon music.exe"], "YouTube Music": ["youtube music.exe"],
}


def processes_for(app, aumid):
    procs = set(APP_PROCESSES.get(app, []))
    base = os.path.basename(aumid or "").split("!")[0].lower()
    if base.endswith(".exe"):
        procs.add(base)
    elif app:
        procs.add(app.lower() + ".exe")
    return procs


class AppVolume:
    """Volume / muet de la seule appli qui joue la musique (mélangeur de volume Windows)."""

    def __init__(self, out_queue):
        self.q = out_queue
        self.cmds = queue.Queue()
        self.procs = set()
        self.app = ""
        self.playing = False
        self.learned = {}   # appli -> processus trouvé par la détection de secours
        if AudioUtilities is not None:
            threading.Thread(target=self._loop, daemon=True).start()
        else:
            reason = f"module pycaw indisponible ({PYCAW_ERROR})"
            log("volume : " + reason)
            self.q.put(("volume", (None, False, False, reason)))

    def set_target(self, procs, app="", playing=False):
        self.procs = set(procs)
        self.app = app or ""
        self.playing = playing
        if self.app in self.learned:
            self.procs.add(self.learned[self.app])

    def set_volume(self, level):
        self.cmds.put(("vol", max(0.0, min(1.0, level))))

    def toggle_mute(self):
        self.cmds.put(("mute", None))

    def _volumes(self):
        """Sessions audio de l'appli : nom exact du processus, sinon nom approchant."""
        sessions = []
        for s in AudioUtilities.GetAllSessions():
            try:
                if s.Process:
                    sessions.append((s.Process.name().lower(), s.SimpleAudioVolume))
            except Exception:
                pass
        found = [v for name, v in sessions if name in self.procs]
        if not found:
            keys = {p[:-4] if p.endswith(".exe") else p for p in self.procs}
            keys |= {self.app.lower().replace(" ", "")} if self.app else set()
            keys.discard("")
            for name, v in sessions:
                base = name[:-4] if name.endswith(".exe") else name
                if any(k in base or base in k for k in keys if len(k) >= 3):
                    found.append(v)
        if not found and self.playing and self.app:
            name = self._loudest_session()
            if name:
                self.learned[self.app] = name
                self.procs.add(name)
                log(f"volume : {self.app} associé au processus {name} (détection par le son)")
                found = [v for n, v in sessions if n == name]
        return found, sorted({name for name, _ in sessions})

    IGNORED = {"discord.exe", "explorer.exe", "teams.exe", "ms-teams.exe", "zoom.exe",
               "steam.exe", "steamwebhelper.exe", "audiodg.exe", "svchost.exe"}

    def _loudest_session(self):
        """Processus qui joue actuellement le plus fort (hors applis d'appel/système)."""
        if IAudioMeterInformation is None:
            return None
        best, best_peak = None, 0.002
        for s in AudioUtilities.GetAllSessions():
            try:
                if not s.Process:
                    continue
                name = s.Process.name().lower()
                if name in self.IGNORED:
                    continue
                meter = s._ctl.QueryInterface(IAudioMeterInformation)
                peak = max(meter.GetPeakValue() for _ in range(3))
                if peak > best_peak:
                    best, best_peak = name, peak
            except Exception:
                pass
        return best

    def _loop(self):
        try:
            comtypes.CoInitialize()
        except Exception:
            pass
        last, last_reason = None, None
        while True:
            pending = []
            try:
                pending.append(self.cmds.get(timeout=0.3))
                while True:
                    pending.append(self.cmds.get_nowait())
            except queue.Empty:
                pass
            reason = ""
            try:
                if not self.procs:
                    vols, seen = [], []
                    reason = "aucune musique en cours"
                else:
                    vols, seen = self._volumes()
                if vols:
                    levels = [v for k, v in pending if k == "vol"]
                    if levels:
                        for sv in vols:
                            sv.SetMasterVolume(levels[-1], None)
                            sv.SetMute(0, None)
                    if sum(1 for k, _ in pending if k == "mute") % 2:
                        muted = not vols[0].GetMute()
                        for sv in vols:
                            sv.SetMute(int(muted), None)
                    state = (round(vols[0].GetMasterVolume(), 3), bool(vols[0].GetMute()), True, "")
                else:
                    if self.procs:
                        reason = (f"aucun son trouvé pour {self.app or ', '.join(sorted(self.procs))}.\n"
                                  f"Applis qui ont du son : {', '.join(seen) or 'aucune'}")
                    state = (None, False, False, reason)
            except Exception as err:
                reason = f"erreur : {err!r}"
                state = (None, False, False, reason)
            if reason != last_reason:
                last_reason = reason
                if reason:
                    log("volume : " + reason.replace("\n", " | "))
            if state != last:
                last = state
                self.q.put(("volume", state))


# ======================= Démarrage avec Windows =======================
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "MiniPlayer"


def launch_command():
    if getattr(sys, "frozen", False):  # version .exe
        return f'"{sys.executable}"'
    pyw = Path(sys.executable).with_name("pythonw.exe")
    exe = pyw if pyw.exists() else Path(sys.executable)
    return f'"{exe}" "{Path(__file__).resolve()}"'


def set_autostart(enabled):
    """Ajoute / retire le player des applis lancées à l'ouverture de session."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if enabled:
                winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, launch_command())
            else:
                try:
                    winreg.DeleteValue(k, RUN_NAME)
                except FileNotFoundError:
                    pass
        return True
    except Exception as err:
        log(f"démarrage auto impossible : {err}")
        return False


def show_listener(out_queue):
    """Attend qu'un second lancement demande au player de s'afficher."""
    import ctypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateEventW.restype = ctypes.c_void_p
    k32.CreateEventW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_bool, ctypes.c_wchar_p]
    k32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    k32.WaitForSingleObject.restype = ctypes.c_uint32
    ev = k32.CreateEventW(None, False, False, SHOW_EVENT_NAME)
    if not ev:
        return
    while True:
        if k32.WaitForSingleObject(ev, 0xFFFFFFFF) == 0:
            out_queue.put(("show", None))


def resource(name):
    """Fichier embarqué dans le .exe, ou à côté du script (ou dans assets/)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    for candidate in (base / name, base / "assets" / name):
        if candidate.exists():
            return candidate
    return base / name


LOG = Path.home() / ".mini_player.log"


def log(text):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {text}\n")
    except Exception:
        pass


def hotkey_thread(out_queue):
    """Raccourci global : RegisterHotKey, et si la touche est déjà prise par une
    autre appli, un hook clavier bas niveau qui la capte quand même."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    MOD_NOREPEAT, WM_HOTKEY, HOTKEY_ID = 0x4000, 0x0312, 1

    user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    user32.RegisterHotKey.restype = wintypes.BOOL
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                   wintypes.UINT, wintypes.UINT]
    user32.GetMessageW.restype = wintypes.BOOL
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short

    hook_ref = None  # garde le callback en vie
    if user32.RegisterHotKey(None, HOTKEY_ID, HOTKEY_MODS | MOD_NOREPEAT, HOTKEY_VK):
        log(f"raccourci {HOTKEY_LABEL} enregistré (RegisterHotKey)")
    else:
        log(f"RegisterHotKey a échoué (erreur {ctypes.get_last_error()}), "
            "passage au hook clavier")

        WH_KEYBOARD_LL = 13
        WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x100, 0x101, 0x104, 0x105

        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                        ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                        ("dwExtraInfo", ctypes.c_size_t)]

        LRESULT = ctypes.c_ssize_t
        HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE,
                                             wintypes.DWORD]
        user32.SetWindowsHookExW.restype = wintypes.HHOOK
        user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM,
                                          wintypes.LPARAM]
        user32.CallNextHookEx.restype = LRESULT
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE

        mod_keys = [(0x0001, (0x12,)), (0x0002, (0x11,)), (0x0004, (0x10,)),
                    (0x0008, (0x5B, 0x5C))]
        state = {"down": False}

        def mods_ok():
            for flag, vks in mod_keys:
                pressed = any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in vks)
                if bool(HOTKEY_MODS & flag) != pressed:
                    return False
            return True

        def proc(n_code, w_param, l_param):
            if n_code == 0:
                kb = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if kb.vkCode == HOTKEY_VK:
                    if w_param in (WM_KEYDOWN, WM_SYSKEYDOWN):
                        if not state["down"] and mods_ok():
                            out_queue.put(("toggle", None))
                        state["down"] = True
                    elif w_param in (WM_KEYUP, WM_SYSKEYUP):
                        state["down"] = False
            return user32.CallNextHookEx(None, n_code, w_param, l_param)

        hook_ref = HOOKPROC(proc)
        if not user32.SetWindowsHookExW(WH_KEYBOARD_LL, hook_ref,
                                        kernel32.GetModuleHandleW(None), 0):
            log(f"hook clavier impossible (erreur {ctypes.get_last_error()})")
            out_queue.put(("hotkey_failed", None))
            return
        log("hook clavier actif")

    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
            out_queue.put(("toggle", None))
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


# ======================= Equalizer APO =======================
class EqualizerAPO:
    FILE = "mini_player_eq.txt"
    URL = "https://sourceforge.net/projects/equalizerapo/"

    def __init__(self):
        self.config_dir = self._find()

    @staticmethod
    def _find():
        candidates = []
        try:
            import winreg
            for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                try:
                    k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\EqualizerAPO",
                                       0, winreg.KEY_READ | view)
                except OSError:
                    continue
                for value, sub in (("ConfigPath", ""), ("InstallPath", "config")):
                    try:
                        p, _ = winreg.QueryValueEx(k, value)
                        candidates.append(Path(p) / sub)
                    except OSError:
                        pass
        except ImportError:
            pass
        for env in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
            if os.environ.get(env):
                candidates.append(Path(os.environ[env]) / "EqualizerAPO" / "config")
        for c in candidates:
            if c.is_dir():
                return c
        return None

    def apply(self, gains):
        if self.config_dir is None:
            return "missing"
        try:
            preamp = -max(0.0, max(gains))
            curve = "; ".join(f"{f} {g:g}" for f, g in zip(BANDS, gains))
            (self.config_dir / self.FILE).write_text(
                "# Généré par Mini Player — ne pas modifier à la main\n"
                f"Preamp: {preamp:g} dB\nGraphicEQ: {curve}\n", encoding="utf-8")
            main = self.config_dir / "config.txt"
            text = main.read_text(encoding="utf-8", errors="ignore") if main.exists() else ""
            if self.FILE not in text:
                with open(main, "a", encoding="utf-8") as f:
                    f.write(f"\nInclude: {self.FILE}\n")
            return "saved"
        except PermissionError:
            return "denied"
        except Exception:
            return "error"


# ======================= Interface =======================
class MiniPlayer:
    def __init__(self):
        self.cfg = self.load_cfg()
        self.accent_src = windows_accent() if self.cfg.get("use_accent", True) else None
        set_palette(self.accent_src)
        self.gains = [float(g) for g in self.cfg["gains"]][:10] + [0.0] * (10 - len(self.cfg["gains"]))
        self.preset = self.cfg.get("preset", "Flat")
        self.disp = list(self.gains)      # valeurs affichées (animées)
        self.eq_job = None
        self.q = queue.Queue()
        self.backend = MediaBackend(self.q)
        self.volume = AppVolume(self.q)
        self.vol_level, self.vol_muted, self.vol_avail = None, False, False
        self.vol_reason = ""
        self.apo = EqualizerAPO()

        self.media = {"empty": True}
        self.device = None
        self.pos_base, self.pos_t = 0.0, time.monotonic()
        self.angle = 0.0
        self.last_frame = time.monotonic()
        self.drag = None
        self._moved = False
        self.apply_job = None
        self.eq_status = "saved"
        self.hidden = False
        log("démarrage du Mini Player")
        threading.Thread(target=hotkey_thread, args=(self.q,), daemon=True).start()
        threading.Thread(target=show_listener, args=(self.q,), daemon=True).start()

        r = self.root = tk.Tk()
        r.title("Mini Player")
        try:
            r.iconbitmap(default=str(resource("mini_player.ico")))
        except Exception:
            pass
        r.overrideredirect(True)
        r.configure(bg=KEY)
        r.attributes("-transparentcolor", KEY)
        r.attributes("-topmost", self.cfg["topmost"])
        r.attributes("-alpha", self.cfg["alpha"])
        x, y = self.cfg["x"], self.cfg["y"]
        if x is None or y is None:
            x, y = r.winfo_screenwidth() - W - 40, 40
        r.geometry(f"{W}x{H}+{x}+{y}")
        self.rest = (x, y)
        self.anim_job = None
        self.anim_alpha, self.anim_dy = 0.0, float(SHOW_SLIDE)
        r.attributes("-alpha", 0.0)
        self.place(self.anim_dy)

        mono = pick_font(r, "JetBrains Mono", "Cascadia Mono", "Cascadia Code", "Consolas")
        self.f_title = tkfont.Font(family=mono, size=15, weight="bold")
        self.f_artist = tkfont.Font(family=mono, size=10)
        self.f_chip = tkfont.Font(family=mono, size=9)
        self.f_via = tkfont.Font(family=mono, size=9, slant="italic")
        self.f_time = tkfont.Font(family=mono, size=9)
        self.f_eq = tkfont.Font(family=mono, size=12)
        self.f_small = tkfont.Font(family=mono, size=8)
        self.f_btn = tkfont.Font(family=mono, size=9)

        c = self.c = tk.Canvas(r, width=W, height=H, bg=KEY, highlightthickness=0, bd=0)
        c.pack()

        # fond + pochette
        self.bg_img = make_background(None)
        self.bg_tk = ImageTk.PhotoImage(self.bg_img)
        self.bg_item = c.create_image(0, 0, anchor="nw", image=self.bg_tk)
        self.disc = make_disc(None)
        self.art_item = c.create_image(ART_BOX[0], ART_BOX[1], anchor="nw")
        self.ring = c.create_oval(ART_BOX[0] - 2, ART_BOX[1] - 2, ART_BOX[2] + 2,
                                  ART_BOX[3] + 2, outline=ACCENT, width=3)
        c.create_oval(ART_CX - 11, ART_CY - 11, ART_CX + 11, ART_CY + 11,
                      fill="#0b0c09", outline="#33372c", width=2)
        self.draw_disc()

        # textes
        self.t_title = c.create_text(RX0, TITLE_Y, anchor="w", fill=FG, font=self.f_title)
        self.t_artist = c.create_text(RX0, ARTIST_Y, anchor="w", fill=SUB, font=self.f_artist)

        # progression
        c.create_line(RX0, PY, RX1, PY, fill=TRACK, width=6, capstyle="round")
        self.p_fill = c.create_line(RX0, PY, RX0, PY, fill=ACCENT, width=6, capstyle="round")
        self.p_knob = c.create_oval(0, 0, 0, 0, fill=ACCENT, outline="")
        self.t_pos = c.create_text(RX0, TIME_Y, anchor="w", fill=SUB, font=self.f_time)
        self.t_dur = c.create_text(RX1, TIME_Y, anchor="e", fill=SUB, font=self.f_time)

        # boutons de lecture
        self.btn_pos = {"prev": (RX0 + 12, TRANSPORT_Y), "play": (RX0 + 60, TRANSPORT_Y),
                        "next": (RX0 + 108, TRANSPORT_Y)}
        self.draw_transport()

        # volume de l'appli (haut-parleur = muet, curseur = volume)
        self.vol_icon_x = RX0 + 158
        self.vs0, self.vs1 = RX0 + 182, RX1 - 4
        self.vol_icon = c.create_text(self.vol_icon_x, TRANSPORT_Y, text="",
                                      font=(ICON_FONT, 13))
        c.create_line(self.vs0, TRANSPORT_Y, self.vs1, TRANSPORT_Y, fill=TRACK, width=4,
                      capstyle="round")
        self.vol_fill = c.create_line(self.vs0, TRANSPORT_Y, self.vs0, TRANSPORT_Y,
                                      fill=ACCENT, width=4, capstyle="round")
        self.vol_knob = c.create_oval(0, 0, 0, 0, fill=KNOB, outline="")
        self.render_volume()

        # séparateur + égaliseur
        c.create_line(22, SEP_Y, W - 22, SEP_Y, fill=SEP, width=1)
        self.eq_label = c.create_text(26, EQ_LABEL_Y, anchor="w", text="Equalizer", fill=ACCENT,
                                      font=self.f_eq)
        x0, x1 = 46, W - 46
        self.band_x = [x0 + (x1 - x0) * i / 9 for i in range(10)]
        self.band_bars = []
        for i, bx in enumerate(self.band_x):
            c.create_line(bx, SL_TOP, bx, SL_BOT, fill=TRACK, width=3, capstyle="round")
            self.band_bars.append(c.create_line(bx, SL_MID, bx, SL_BOT, fill=ACCENT_DIM,
                                                width=8, capstyle="round"))
            c.create_text(bx, SL_LABEL_Y, text=BAND_LABELS[i], fill=SUB, font=self.f_small)
        self.curve_glow = c.create_line(0, 0, 0, 0, fill=GLOW, width=6, smooth=True,
                                        capstyle="round")
        self.curve = c.create_line(0, 0, 0, 0, fill=ACCENT, width=2, smooth=True,
                                   capstyle="round")
        self.knobs = [c.create_oval(0, 0, 0, 0, fill=KNOB, outline="") for _ in BANDS]

        # boutons de presets
        self.preset_boxes = {}
        names = list(PRESETS)
        bw = (W - 44 - 24) / 4
        for idx, name in enumerate(names):
            row, col = divmod(idx, 4)
            bx1 = 22 + col * (bw + 8)
            by = PRESET_ROWS_Y[row]
            box = (bx1, by - PRESET_H / 2, bx1 + bw, by + PRESET_H / 2)
            self.preset_boxes[name] = box

        # souris
        c.bind("<ButtonPress-1>", self.on_press)
        c.bind("<B1-Motion>", self.on_drag)
        c.bind("<ButtonRelease-1>", self.on_release)
        c.bind("<Double-Button-1>", self.on_double)
        c.bind("<Motion>", self.on_hover)
        c.bind("<MouseWheel>", self.on_wheel)
        c.bind("<Button-3>", self.show_menu)
        self.build_menu()

        self.render_text()
        self.render_chips()
        self.render_eq(noise=0)
        self.render_presets()
        self.schedule_apply(delay=50)
        r.after(40, self.tick)
        r.after(2000, self.watch_accent)
        r.after(120, lambda: self.animate(self.cfg["alpha"], 0.0, SHOW_MS, spring=True))

    # ---------- config ----------
    @staticmethod
    def load_cfg():
        cfg = dict(DEFAULTS)
        try:
            cfg.update(json.loads(CONFIG.read_text(encoding="utf-8")))
        except Exception:
            pass
        return cfg

    def save_cfg(self):
        self.cfg.update(x=self.rest[0], y=self.rest[1],
                        gains=self.gains, preset=self.preset)
        try:
            CONFIG.write_text(json.dumps(self.cfg), encoding="utf-8")
        except Exception:
            pass

    # ---------- menu ----------
    def build_menu(self):
        self.v_top = tk.BooleanVar(value=self.cfg["topmost"])
        self.v_alpha = tk.DoubleVar(value=self.cfg["alpha"])
        self.v_auto = tk.BooleanVar(value=self.cfg.get("autostart", True))
        set_autostart(self.v_auto.get())  # met aussi à jour le chemin si le fichier a bougé
        m = self.menu = tk.Menu(self.root, tearoff=0)
        m.add_checkbutton(label="Toujours au premier plan", variable=self.v_top,
                          command=self.apply_topmost)
        sub = tk.Menu(m, tearoff=0)
        for a in (1.0, 0.95, 0.85, 0.75, 0.6):
            sub.add_radiobutton(label=f"{int(a * 100)} %", variable=self.v_alpha, value=a,
                                command=self.apply_alpha)
        m.add_cascade(label="Opacité", menu=sub)
        m.add_command(label="Masquer", accelerator=HOTKEY_LABEL, command=self.toggle_visible)
        m.add_checkbutton(label="Lancer au démarrage de Windows", variable=self.v_auto,
                          command=self.apply_autostart)
        self.v_accent = tk.BooleanVar(value=self.cfg.get("use_accent", True))
        m.add_checkbutton(label="Couleur d'accentuation Windows", variable=self.v_accent,
                          command=self.apply_accent_setting)
        m.add_separator()
        m.add_command(label="Quitter", command=self.quit)

    def show_menu(self, e):
        try:
            self.menu.tk_popup(e.x_root, e.y_root)
        finally:
            self.menu.grab_release()

    def apply_topmost(self):
        self.cfg["topmost"] = self.v_top.get()
        self.root.attributes("-topmost", self.cfg["topmost"])
        self.save_cfg()

    def apply_alpha(self):
        self.cfg["alpha"] = self.v_alpha.get()
        self.root.attributes("-alpha", self.cfg["alpha"])
        self.anim_alpha = self.cfg["alpha"]
        self.save_cfg()

    def apply_accent_setting(self):
        self.cfg["use_accent"] = self.v_accent.get()
        self.save_cfg()
        self.retheme(windows_accent() if self.cfg["use_accent"] else None)

    def watch_accent(self):
        """Suit la couleur d'accentuation en direct (changement dans Paramètres)."""
        if self.cfg.get("use_accent", True):
            accent = windows_accent()
            if accent != self.accent_src:
                self.retheme(accent)
        self.root.after(2000, self.watch_accent)

    def retheme(self, accent):
        self.accent_src = accent
        set_palette(accent)
        c = self.c
        c.itemconfig(self.ring, outline=ACCENT)
        c.itemconfig(self.eq_label, fill=ACCENT)
        c.itemconfig(self.p_fill, fill=ACCENT)
        c.itemconfig(self.p_knob, fill=ACCENT)
        c.itemconfig(self.curve, fill=ACCENT)
        c.itemconfig(self.curve_glow, fill=GLOW)
        for b in self.band_bars:
            c.itemconfig(b, fill=ACCENT_DIM)
        for k in self.knobs:
            c.itemconfig(k, fill=KNOB)
        if not getattr(self, "has_art", False):
            self.disc = make_disc(None)
            self.draw_disc()
        self.draw_transport()
        self.render_volume()
        self.render_chips()
        self.render_presets()

    def apply_autostart(self):
        self.cfg["autostart"] = self.v_auto.get()
        if not set_autostart(self.cfg["autostart"]):
            messagebox.showwarning("Mini Player", "Impossible de modifier le démarrage auto.\n"
                                   f"Détails dans {LOG}")
        self.save_cfg()

    def place(self, dy):
        self.root.geometry(f"+{self.rest[0]}+{self.rest[1] + int(round(dy))}")

    def animate(self, alpha_to, dy_to, ms, spring, done=None):
        """Anime l'opacité et le glissement vertical ; repart de l'état courant
        si une animation est déjà en cours (on peut inverser en plein milieu)."""
        if self.anim_job:
            self.root.after_cancel(self.anim_job)
        a0, d0, t0 = self.anim_alpha, self.anim_dy, time.monotonic()

        def step():
            t = min(1.0, (time.monotonic() - t0) * 1000 / ms)
            if spring:   # sortie douce + petit rebond amorti
                ea = 1 - (1 - t) ** 3
                ed = 1.0 if t >= 1 else 1 - math.exp(-7 * t) * math.cos(9 * t)
            else:        # accélération progressive
                ea = t * t
                ed = t * t * t
            self.anim_alpha = a0 + (alpha_to - a0) * ea
            self.anim_dy = d0 + (dy_to - d0) * ed
            self.root.attributes("-alpha", max(0.0, min(1.0, self.anim_alpha)))
            self.place(self.anim_dy)
            if t < 1:
                self.anim_job = self.root.after(8, step)
            else:
                self.anim_job = None
                if done:
                    done()

        step()

    def toggle_visible(self):
        r = self.root
        if self.hidden:
            self.anim_alpha, self.anim_dy = 0.0, float(SHOW_SLIDE)
            r.attributes("-alpha", 0.0)
            self.place(self.anim_dy)
            r.deiconify()
            r.overrideredirect(True)
            r.attributes("-topmost", self.cfg["topmost"])
            r.lift()
            self.hidden = False
            self.animate(self.cfg["alpha"], 0.0, SHOW_MS, spring=True)
        elif self.anim_job and self.anim_alpha_target_is_zero():
            # déjà en train de disparaître : on fait demi-tour
            self.animate(self.cfg["alpha"], 0.0, SHOW_MS, spring=True)
            self._hiding = False
        else:
            self.save_cfg()
            self._hiding = True
            self.animate(0.0, float(HIDE_SLIDE), HIDE_MS, spring=False, done=self._finish_hide)

    def anim_alpha_target_is_zero(self):
        return getattr(self, "_hiding", False)

    def _finish_hide(self):
        self._hiding = False
        self.root.withdraw()
        self.hidden = True
        self.anim_dy = 0.0
        self.place(0)

    def quit(self):
        self.save_cfg()
        self.root.destroy()

    # ---------- dessin ----------
    def fit(self, text, font, width):
        if font.measure(text) <= width:
            return text
        while text and font.measure(text + "…") > width:
            text = text[:-1]
        return text + "…"

    def set_art(self, data):
        self.bg_img = make_background(data)
        self.bg_tk = ImageTk.PhotoImage(self.bg_img)
        self.c.itemconfig(self.bg_item, image=self.bg_tk)
        self.disc = make_disc(data)
        self.has_art = bool(data)
        self.draw_disc()

    def draw_disc(self):
        disc = self.disc.rotate(-self.angle % 360, resample=Image.BICUBIC) if self.angle else self.disc
        frame = self.bg_img.crop(ART_BOX)
        frame.paste(disc, (0, 0), disc)
        self.disc_tk = ImageTk.PhotoImage(frame)
        self.c.itemconfig(self.art_item, image=self.disc_tk)

    def draw_transport(self):
        c = self.c
        c.delete("transport")
        col = BTN_TXT
        px, py = self.btn_pos["prev"]
        c.create_rectangle(px - 9, py - 9, px - 6, py + 9, fill=col, outline="", tags="transport")
        c.create_polygon(px + 8, py - 9, px + 8, py + 9, px - 5, py, fill=col, outline="",
                         tags="transport")
        nx, ny = self.btn_pos["next"]
        c.create_rectangle(nx + 6, ny - 9, nx + 9, ny + 9, fill=col, outline="", tags="transport")
        c.create_polygon(nx - 8, ny - 9, nx - 8, ny + 9, nx + 5, ny, fill=col, outline="",
                         tags="transport")
        cx, cy = self.btn_pos["play"]
        if self.media.get("playing"):
            round_rect(c, cx - 12, cy - 14, cx - 3, cy + 14, 3, fill=ACCENT, outline="",
                       tags="transport")
            round_rect(c, cx + 3, cy - 14, cx + 12, cy + 14, 3, fill=ACCENT, outline="",
                       tags="transport")
        else:
            c.create_polygon(cx - 9, cy - 14, cx - 9, cy + 14, cx + 14, cy, fill=ACCENT,
                             outline="", tags="transport")

    def render_text(self):
        m = self.media
        if m.get("empty"):
            title, artist = "Rien en lecture", "Lance une musique"
        else:
            title = m["title"]
            artist = f"BY {m['artist']}" if m["artist"] else ""
        self.c.itemconfig(self.t_title, text=self.fit(title, self.f_title, RX1 - RX0))
        self.c.itemconfig(self.t_artist, text=self.fit(artist, self.f_artist, RX1 - RX0))
        self.draw_transport()

    def render_chips(self):
        c = self.c
        c.delete("chip")
        x = RX0
        if self.device:
            text = self.fit(self.device, self.f_chip, 170)
            w = 30 + self.f_chip.measure(text) + 10
            round_rect(c, x, CHIP_Y - 12, x + w, CHIP_Y + 12, 8, fill=CHIP, outline="",
                       tags="chip")
            c.create_text(x + 14, CHIP_Y, text="", fill=ACCENT, font=(ICON_FONT, 9),
                          tags="chip")
            c.create_text(x + 28, CHIP_Y, text=text, anchor="w", fill=FG, font=self.f_chip,
                          tags="chip")
            x += w + 12
        app = self.media.get("app")
        if app and not self.media.get("empty"):
            c.create_text(x, CHIP_Y, text=f"VIA {app}", anchor="w", fill=SUB, font=self.f_via,
                          tags="chip")

    def current_pos(self):
        m = self.media
        dur = m.get("duration", 0) or 0
        pos = self.pos_base + ((time.monotonic() - self.pos_t) if m.get("playing") else 0)
        return (max(0.0, min(pos, dur)) if dur else 0.0), dur

    def render_volume(self):
        c = self.c
        level = self.vol_level if self.vol_level is not None else 0.0
        if not self.vol_avail:
            icon_col, fill_col, knob_col = TRACK, TRACK, TRACK
        elif self.vol_muted:
            icon_col, fill_col, knob_col = SUB, ACCENT_DIM, SUB
        else:
            icon_col, fill_col, knob_col = BTN_TXT, ACCENT, KNOB
        c.itemconfig(self.vol_icon, fill=icon_col,
                     text="" if self.vol_muted or (self.vol_avail and level == 0) else "")
        xv = self.vs0 + (self.vs1 - self.vs0) * level
        c.coords(self.vol_fill, self.vs0, TRANSPORT_Y, xv, TRANSPORT_Y)
        c.itemconfig(self.vol_fill, fill=fill_col)
        c.coords(self.vol_knob, xv - 6, TRANSPORT_Y - 6, xv + 6, TRANSPORT_Y + 6)
        c.itemconfig(self.vol_knob, fill=knob_col)

    def set_volume_from_x(self, x):
        if not self.vol_avail:
            return
        level = max(0.0, min(1.0, (x - self.vs0) / (self.vs1 - self.vs0)))
        self.vol_level, self.vol_muted = level, False
        self.volume.set_volume(level)
        self.render_volume()

    def render_progress(self):
        pos, dur = self.current_pos()
        frac = pos / dur if dur else 0
        xp = RX0 + (RX1 - RX0) * frac
        self.c.coords(self.p_fill, RX0, PY, xp, PY)
        self.c.coords(self.p_knob, xp - 8, PY - 8, xp + 8, PY + 8)
        self.c.itemconfig(self.t_pos, text=fmt(pos) if dur else "--:--")
        self.c.itemconfig(self.t_dur, text=fmt(dur) if dur else "--:--")

    def gain_y(self, g):
        return SL_MID - g / MAX_DB * SL_HALF

    def y_gain(self, y):
        g = (SL_MID - y) / SL_HALF * MAX_DB
        return max(-MAX_DB, min(MAX_DB, round(g * 2) / 2))

    def render_eq(self, noise):
        c = self.c
        ys = [self.gain_y(g) for g in self.disp]
        for i, bx in enumerate(self.band_x):
            c.coords(self.band_bars[i], bx, ys[i], bx, SL_BOT)
            c.coords(self.knobs[i], bx - 8, ys[i] - 8, bx + 8, ys[i] + 8)
        pts = []
        steps = 7
        for i in range(9):
            for k in range(steps):
                t = k / steps
                tt = (1 - math.cos(t * math.pi)) / 2
                x = self.band_x[i] + (self.band_x[i + 1] - self.band_x[i]) * t
                y = ys[i] + (ys[i + 1] - ys[i]) * tt
                if noise and k:
                    y += random.uniform(-noise, noise)
                pts += [x, y]
        pts += [self.band_x[-1], ys[-1]]
        c.coords(self.curve_glow, *pts)
        c.coords(self.curve, *pts)
        for k in self.knobs:
            c.tag_raise(k)

    def render_presets(self):
        c = self.c
        c.delete("preset")
        for name, (x1, y1, x2, y2) in self.preset_boxes.items():
            active = name == self.preset
            round_rect(c, x1, y1, x2, y2, 7, fill=ACCENT if active else BTN, outline="",
                       tags="preset")
            c.create_text((x1 + x2) / 2, (y1 + y2) / 2, text=name, font=self.f_btn,
                          fill=DARK_TXT if active else BTN_TXT, tags="preset")
        # nom du preset + pastille d'état
        c.delete("pill")
        label = self.preset or "Custom"
        c.create_text(W - 26, EQ_LABEL_Y, text=label, anchor="e", fill=FG, font=self.f_artist,
                      tags="pill")
        status = {"saved": ("Saved", ACCENT), "pending": ("…", SUB),
                  "missing": ("Installer APO", "#e0b46a"), "denied": ("Accès refusé", "#e08a6a"),
                  "error": ("Erreur", "#e08a6a")}[self.eq_status]
        text, col = status
        tw = self.f_small.measure(text)
        px2 = W - 26 - self.f_artist.measure(label) - 14
        px1 = px2 - tw - 20
        round_rect(c, px1, EQ_LABEL_Y - 11, px2, EQ_LABEL_Y + 11, 8, fill=CHIP, outline="",
                   tags="pill")
        c.create_text((px1 + px2) / 2, EQ_LABEL_Y, text=text, fill=col, font=self.f_small,
                      tags="pill")
        self.pill_box = (px1, EQ_LABEL_Y - 11, px2, EQ_LABEL_Y + 11)

    # ---------- égaliseur ----------
    def stop_eq_anim(self):
        if self.eq_job:
            self.root.after_cancel(self.eq_job)
            self.eq_job = None
        self.disp = list(self.gains)

    def set_gain(self, i, g):
        if self.eq_job:          # on touche un curseur pendant l'animation : on la termine
            self.stop_eq_anim()
        if self.gains[i] != g:
            self.gains[i] = g
            self.disp[i] = g
            self.preset = next((n for n, v in PRESETS.items() if v == self.gains), None)
            self.render_eq(noise=0)
            self.schedule_apply()

    def set_preset(self, name):
        self.gains = [float(v) for v in PRESETS[name]]
        self.preset = name
        self.animate_eq()
        self.schedule_apply()

    def animate_eq(self):
        """Fait glisser chaque curseur vers sa nouvelle valeur, bande après bande,
        avec un léger rebond amorti. Repart des positions affichées."""
        if self.eq_job:
            self.root.after_cancel(self.eq_job)
        start = list(self.disp)
        target = list(self.gains)
        t0 = time.monotonic()
        total = EQ_ANIM_MS + EQ_STAGGER_MS * (len(BANDS) - 1)

        def step():
            elapsed = (time.monotonic() - t0) * 1000
            for i in range(len(BANDS)):
                t = (elapsed - i * EQ_STAGGER_MS) / EQ_ANIM_MS
                if t <= 0:
                    e = 0.0
                elif t >= 1:
                    e = 1.0
                else:
                    e = 1 - math.exp(-6 * t) * math.cos(8 * t)
                self.disp[i] = start[i] + (target[i] - start[i]) * e
            self.render_eq(noise=3.0 if self.media.get("playing") else 0)
            if elapsed < total:
                self.eq_job = self.root.after(8, step)
            else:
                self.eq_job = None
                self.disp = list(target)
                self.render_eq(noise=0)

        step()

    def schedule_apply(self, delay=250):
        if self.apply_job:
            self.root.after_cancel(self.apply_job)
        if self.eq_status != "missing":
            self.eq_status = "pending"
        self.render_presets()
        self.apply_job = self.root.after(delay, self.apply_eq)

    def apply_eq(self):
        self.apply_job = None
        self.eq_status = self.apo.apply(self.gains)
        self.render_presets()
        self.save_cfg()

    # ---------- souris ----------
    def hit(self, x, y):
        for name, (bx, by) in self.btn_pos.items():
            if (x - bx) ** 2 + (y - by) ** 2 <= 22 ** 2:
                return (name,)
        if (x - self.vol_icon_x) ** 2 + (y - TRANSPORT_Y) ** 2 <= 15 ** 2:
            return ("mute",)
        if self.vs0 - 8 <= x <= self.vs1 + 8 and abs(y - TRANSPORT_Y) <= 12:
            return ("vol",)
        if RX0 - 8 <= x <= RX1 + 8 and abs(y - PY) <= 12:
            return ("seek",)
        if SL_TOP - 14 <= y <= SL_BOT + 14:
            for i, bx in enumerate(self.band_x):
                if abs(x - bx) <= 16:
                    return ("band", i)
        for name, (x1, y1, x2, y2) in self.preset_boxes.items():
            if x1 <= x <= x2 and y1 <= y <= y2:
                return ("preset", name)
        px1, py1, px2, py2 = getattr(self, "pill_box", (0, 0, 0, 0))
        if px1 <= x <= px2 and py1 <= y <= py2:
            return ("pill",)
        return None

    def on_hover(self, e):
        self.c.configure(cursor="hand2" if self.hit(e.x, e.y) else "")

    def on_press(self, e):
        self._moved = False
        target = self.hit(e.x, e.y)
        if target and target[0] == "band":
            self.drag = target
            self.set_gain(target[1], self.y_gain(e.y))
        elif target and target[0] == "vol":
            self.drag = target
            self.set_volume_from_x(e.x)
        elif target:
            self.drag = target
        else:
            self.drag = ("window", e.x_root, e.y_root, self.root.winfo_x(), self.root.winfo_y())

    def on_drag(self, e):
        if not self.drag:
            return
        if self.drag[0] == "band":
            self.set_gain(self.drag[1], self.y_gain(e.y))
        elif self.drag[0] == "vol":
            self.set_volume_from_x(e.x)
        elif self.drag[0] == "window":
            _, sx, sy, wx, wy = self.drag
            dx, dy = e.x_root - sx, e.y_root - sy
            if abs(dx) + abs(dy) > 3:
                self._moved = True
            if self._moved:
                self.root.geometry(f"+{wx + dx}+{wy + dy}")

    def on_release(self, e):
        target, self.drag = self.drag, None
        if not target:
            return
        kind = target[0]
        if kind == "window":
            if self._moved:
                self.rest = (self.root.winfo_x(), self.root.winfo_y())
                self.save_cfg()
            return
        if self.hit(e.x, e.y) != target and kind not in ("band", "vol"):
            return
        if kind == "mute":
            if self.vol_avail:
                self.vol_muted = not self.vol_muted
                self.volume.toggle_mute()
                self.render_volume()
            elif self.vol_reason:
                messagebox.showinfo("Mini Player — volume", "Le volume n'est pas disponible : "
                                    + self.vol_reason)
            return
        if kind == "prev":
            self.backend.command("try_skip_previous_async")
        elif kind == "next":
            self.backend.command("try_skip_next_async")
        elif kind == "play":
            self.backend.command("try_toggle_play_pause_async")
            if not self.media.get("empty"):
                self.pos_base, _ = self.current_pos()
                self.pos_t = time.monotonic()
                self.media["playing"] = not self.media.get("playing")
                self.draw_transport()
        elif kind == "seek":
            dur = self.media.get("duration", 0)
            if dur:
                frac = max(0.0, min(1.0, (e.x - RX0) / (RX1 - RX0)))
                self.backend.command("try_change_playback_position_async",
                                     int(frac * dur * 10_000_000))
                self.pos_base, self.pos_t = frac * dur, time.monotonic()
        elif kind == "preset":
            self.set_preset(target[1])
        elif kind == "pill" and self.eq_status == "missing":
            webbrowser.open(EqualizerAPO.URL)

    def on_double(self, e):
        target = self.hit(e.x, e.y)
        if target and target[0] == "band":
            self.set_gain(target[1], 0.0)

    def on_wheel(self, e):
        target = self.hit(e.x, e.y)
        if target and target[0] in ("vol", "mute") and self.vol_avail:
            level = (self.vol_level or 0.0) + (0.05 if e.delta > 0 else -0.05)
            x = self.vs0 + (self.vs1 - self.vs0) * max(0.0, min(1.0, level))
            self.set_volume_from_x(x)
            return
        if target and target[0] == "band":
            i = target[1]
            step = 0.5 if e.delta > 0 else -0.5
            self.set_gain(i, max(-MAX_DB, min(MAX_DB, self.gains[i] + step)))

    # ---------- boucle ----------
    def tick(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "show":
                    if self.hidden:
                        self.toggle_visible()
                    self.root.lift()
                    continue
                if kind == "toggle":
                    log("raccourci reçu → " + ("affichage" if self.hidden else "masquage"))
                    self.toggle_visible()
                    continue
                if kind == "hotkey_failed":
                    self.root.after(500, lambda: messagebox.showwarning(
                        "Mini Player", f"Impossible d'activer le raccourci {HOTKEY_LABEL}.\n"
                        f"Détails dans {LOG}\n\nTu peux masquer le player via le clic droit."))
                    continue
                if kind == "device":
                    self.device = payload
                    self.render_chips()
                    continue
                if kind == "volume":
                    level, muted, avail, self.vol_reason = payload
                    dragging_vol = self.drag and self.drag[0] == "vol"
                    if not dragging_vol:
                        self.vol_level, self.vol_muted, self.vol_avail = level, muted, avail
                    else:
                        self.vol_avail = avail
                    self.render_volume()
                    continue
                msg = payload
                if "art" in msg:
                    self.set_art(msg["art"])
                changed = (msg.get("title"), msg.get("artist"), msg.get("playing"),
                           msg.get("app"), msg.get("empty")) != (
                    self.media.get("title"), self.media.get("artist"),
                    self.media.get("playing"), self.media.get("app"), self.media.get("empty"))
                self.media = msg
                self.pos_base, self.pos_t = msg.get("position", 0.0), time.monotonic()
                self.volume.set_target(
                    [] if msg.get("empty") else processes_for(msg.get("app"), msg.get("aumid")),
                    msg.get("app", ""), bool(msg.get("playing")))
                if changed:
                    self.render_text()
                    self.render_chips()
        except queue.Empty:
            pass

        now = time.monotonic()
        dt, self.last_frame = now - self.last_frame, now
        playing = bool(self.media.get("playing"))
        if self.hidden:  # pas d'animation quand le player est masqué
            self.root.after(200, self.tick)
            return
        if playing:
            self.angle = (self.angle + SPIN_DEG_PER_S * dt) % 360
            self.draw_disc()
            if not self.drag or self.drag[0] != "band":
                self.render_eq(noise=3.0)
        elif getattr(self, "_was_playing", False):
            self.render_eq(noise=0)
        self._was_playing = playing
        self.render_progress()
        self.root.after(40, self.tick)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    MiniPlayer().run()

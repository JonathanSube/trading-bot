#!/usr/bin/env python3
"""Faehrt den Homeserver nach Handelsschluss (19 Uhr) automatisch runter und
weckt ihn per RTC-Weckalarm rechtzeitig vor dem naechsten Handelstag wieder
auf (08:50 Uhr) - Wochenenden werden uebersprungen, da an Sa/So kein Handel
stattfindet (Nutzerwunsch 15.09.2026). Muss als root laufen (rtcwake/
Shutdown brauchen Root-Rechte), siehe deploy/signal-bot-poweroff.service.

Der eigentliche Shutdown/Wake ist am 15.09.2026 live getestet worden
(2-Minuten-Testlauf per `sudo rtcwake -m off -s 120` auf diesem MSI-Board,
BIOS-Weckalarm bestaetigt funktionsfaehig).

FIX (22.09.2026): `rtcwake -m off` faehrt intern ueber systemd-logind
herunter, das bei einer aktiv eingeloggten Desktop-Sitzung (GNOME) mit
"Operation inhibited by ... user session inhibited" scheitert - live
beobachtet, als der Nutzer den Shutdown manuell bei eingeloggter Sitzung
ausprobiert hat. Deshalb jetzt zweigeteilt: erst per `rtcwake -m no` NUR
den Weckalarm setzen (kein Shutdown-Versuch), dann separat per
`systemctl poweroff -i` herunterfahren - das `-i` (--check-inhibitors=no)
ist hier bewusst, da dieser Shutdown ausdruecklich gewollt ist, egal ob
gerade eine Desktop-Sitzung eingeloggt ist."""

import subprocess
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Berlin")
WAKE_TIME = time(8, 50)


def next_wake() -> datetime:
    now = datetime.now(TZ)
    candidate = (now + timedelta(days=1)).replace(
        hour=WAKE_TIME.hour, minute=WAKE_TIME.minute, second=0, microsecond=0
    )
    while candidate.weekday() >= 5:  # 5=Samstag, 6=Sonntag
        candidate += timedelta(days=1)
    return candidate


def main() -> None:
    wake_at = next_wake()
    epoch = int(wake_at.timestamp())
    print(f"[poweroff] Naechster Weckzeitpunkt: {wake_at.isoformat()} (epoch {epoch})")
    subprocess.run(["rtcwake", "-m", "no", "-t", str(epoch)], check=True)
    subprocess.run(["systemctl", "poweroff", "-i"], check=True)


if __name__ == "__main__":
    main()

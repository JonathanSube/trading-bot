# Signal-Bot auf dem Homeserver (Ubuntu)

Ersetzt den bisherigen Betrieb über GitHub Actions (minütlicher externer
cron-job.org-Trigger) durch einen Dauerprozess auf deinem Server, der alle
`POLL_INTERVAL_SECONDS` (3 Sekunden, siehe `scripts/run_signal_bot.py`)
reagiert statt bisher etwa jede Minute. Der eigentliche Ablauf pro
Durchlauf ist unverändert - nur der Takt und die Verbindung sind jetzt
dauerhaft statt pro Lauf neu aufgebaut.

**WICHTIG - zuerst den alten Trigger stoppen:** Bevor dieser Dienst
gestartet wird, muss der externe cron-job.org-Job, der bisher
`workflow_dispatch` auf `.github/workflows/signal-bot.yml` ausgelöst hat,
**deaktiviert oder gelöscht** werden (Login bei cron-job.org). Der
GitHub-Actions-Cron selbst ist im Workflow bereits deaktiviert (nur noch
manuell startbar) - das reicht aber NICHT, wenn cron-job.org weiterhin
`workflow_dispatch` extern auslöst. Zwei gleichzeitig laufende Prozesse auf
demselben Telegram-/cTrader-Konto könnten sich Nachrichten oder
Positionen gegenseitig wegschnappen bzw. doppelt reagieren - das gab es
schon einmal (siehe trading-bot-spec.md, Doppelkauf-Vorfälle).

## 1) Repository klonen

```bash
git clone https://github.com/JonathanSube/trading-bot.git
cd trading-bot
```

## 2) Python-Umgebung einrichten

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 3) `.env` mit den Zugangsdaten anlegen

Im Repo-Root eine Datei `.env` erstellen (wird automatisch geladen, siehe
`load_dotenv(ROOT / ".env")` in `scripts/run_signal_bot.py`, ist bereits in
`.gitignore`):

```
CTRADER_CLIENT_ID=...
CTRADER_CLIENT_SECRET=...
CTRADER_REFRESH_TOKEN=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
TELEGRAM_API_ID=...
TELEGRAM_API_HASH=...
TELEGRAM_USER_SESSION=...
GEMINI_API_KEY=...
SIGNAL_CHANNEL=...
```

Die Werte stehen aktuell als GitHub-Actions-Secrets im Repo (Settings ->
Secrets and variables -> Actions) - einmalig dort abschreiben und hier
eintragen. `CTRADER_ACCESS_TOKEN`/`CTRADER_ACCESS_TOKEN_EXPIRES_AT` und
`GH_SECRETS_PAT` NICHT nötig: Der Access-Token-Zwischenspeicher über
GitHub-Secrets (`tradingbot/ctrader.py::_persist_secret`) war nur nötig,
weil jeder GitHub-Actions-Lauf in einem frischen, leeren Container
startete - auf dem Homeserver bleibt der Prozess durchgehend am Laufen,
der Token lebt einfach im Arbeitsspeicher.

## 4) Git-Push-Zugang für die Fernüberwachung einrichten

Der Bot pusht weiterhin regelmäßig (alle ~60s) `signal_state.json`,
`signal_trades.csv` und `signal_channel_log.csv` nach GitHub - nicht mehr
für die eigene Funktion nötig, sondern nur, damit die bestehende
Überwachung (liest den Zustand per `git fetch` aus dem Repo) weiter
funktioniert. Dafür braucht der Homeserver Push-Rechte:

```bash
git remote set-url origin https://<PAT>@github.com/JonathanSube/trading-bot.git
git config user.name "signal-bot"
git config user.email "signal-bot@homeserver.local"
```

`<PAT>` = ein GitHub Personal Access Token mit Schreibrecht auf dieses
Repo (Settings -> Developer settings -> Personal access tokens).

## 5) systemd-Dienst einrichten

`deploy/signal-bot.service` nach `/etc/systemd/system/signal-bot.service`
kopieren und darin `USERNAME`/die beiden Pfade an dein System anpassen,
dann:

```bash
sudo cp deploy/signal-bot.service /etc/systemd/system/signal-bot.service
sudo systemctl daemon-reload
sudo systemctl enable --now signal-bot
```

`Restart=always` sorgt dafür, dass der Dienst nach einem Absturz (z. B.
ein cTrader-Verbindungsfehler) automatisch neu startet und sich neu
verbindet - siehe den `run_forever()`-Docstring in
`scripts/run_signal_bot.py` zur Begründung, warum es dafür keinen eigenen
Reconnect-Code im Skript gibt.

## 6) Kontrollieren

```bash
sudo systemctl status signal-bot
journalctl -u signal-bot -f
```

## Stoppen (Kill-Switch, ohne den Dienst anzuhalten)

Wie bisher: eine leere Datei `STOP` im Repo-Root anlegen - der Bot merkt
das beim nächsten Durchlauf, unternimmt dann nichts mehr (keine neuen
Einstiege, kein Kanal-Abruf) und trennt die Verbindung, bis die Datei
wieder entfernt wird. Bereits offene Positionen werden dadurch NICHT
automatisch geschlossen (Stop/Ziel liegen weiterhin beim Broker) - der
Kill-Switch war schon vorher rein ein "keine weitere Aktion mehr"-Schalter,
kein Notausstieg. Der systemd-Dienst selbst läuft dabei weiter (kein
Neustart nötig).

# Spezifikation: Paper-Trading-Bot "Opening Range Breakout"

**Zweck:** Eine im Backtest geprüfte Strategie über mindestens einen Monat im
Papierhandel verifizieren. Kein echtes Geld. Ziel ist nicht Gewinn, sondern die
Frage: liefert die Live-Ausführung dieselben Zahlen wie der Backtest?

**Änderungshinweis:** Diese Spec beschrieb ursprünglich "Wickless Candle
Retest". Diese Strategie hielt einer Nachprüfung über den vollen,
durchgehenden Zeitraum 2021–2026 nicht stand (vermutlich
In-Sample-Overfitting bei der Parameterwahl). Die vollständige Herleitung,
warum und wie das gefunden wurde, steht am Ende in Abschnitt 9 als
Änderungsprotokoll. Ersetzt durch Opening Range Breakout, hergeleitet und
mit sauberer Train/Test-Trennung geprüft in
[research/FINDINGS.md](research/FINDINGS.md). Ab hier gilt für die neue
Strategie erneut: exakt so implementieren, nichts hinzufügen.

---

## 1. Die Strategie (exakt so implementieren, nichts hinzufügen)

**Instrument:** QQQ
**Zeitfenster:** 5-Minuten-Kerzen
**Handelszeit:** nur reguläre US-Session, 09:30–16:00 ET

### Eröffnungsspanne

- Die ersten **6 abgeschlossenen 5-Min-Kerzen** des Handelstags (09:30–10:00)
  definieren die Eröffnungsspanne: `spanne_hoch = max(high)`,
  `spanne_tief = min(low)` dieser 6 Kerzen.

### Einstieg

- Ab der 7. Kerze des Tages (10:00 Uhr): Für jede abgeschlossene Kerze
  prüfen, ob ihr `close` außerhalb der Eröffnungsspanne liegt.
  - **Long**, wenn `close > spanne_hoch`
  - **Short**, wenn `close < spanne_tief`
- **Höchstens ein Trade pro Tag** — der erste Ausbruch (in welche Richtung
  auch immer) löst aus, danach an diesem Tag keine weiteren Einstiege mehr,
  auch nicht bei einem Ausbruch in die Gegenrichtung.
- Einstieg zur **Eröffnung der auf die Ausbruchskerze folgenden Kerze**
  (kein Blick in die Zukunft: die Ausbruchskerze muss erst abgeschlossen
  sein, bevor gehandelt wird). Market-Order zu dieser Eröffnung, kein
  Limit — anders als bei der alten Strategie wird hier kein exaktes Level
  erneut angelaufen, sondern der Ausbruch selbst gehandelt.

### Stop und Ziel

- **Stop:** Gegenseite der Eröffnungsspanne (Long: `spanne_tief`, Short:
  `spanne_hoch`)
- **Risiko:** `|entry - stop|`
- **Ziel:** `entry +/- 2.0 * risiko` (festes CRV 2:1)
- Als **Bracket-Order** bei Alpaca platzieren (Entry + Stop + Take-Profit in
  einer Order), aus demselben Grund wie zuvor: Alpaca überwacht Stop und
  Ziel serverseitig, tickgenau, unabhängig davon, ob der Bot gerade läuft.

### Positionsgröße

- Risiko pro Trade: **1 % des Kontostands**, gleiche Formel wie zuvor:
  `stückzahl = floor((kontostand * 0.01) / risiko_pro_aktie)`
- **Wichtiger Unterschied zur alten Strategie:** Der Stop-Abstand ist bei
  dieser Strategie viel größer (Ø 3,26 Punkte über den Backtest-Zeitraum,
  gegenüber Ø 0,375 bei der alten Strategie), das ergibt bei gleicher
  1-%-Risiko-Formel im Schnitt das 1,6-fache, im Extremfall bis zum
  5,9-fachen des Kontostands als Positionsgröße (siehe
  [research/FINDINGS.md](research/FINDINGS.md), Abschnitt "Lohnt sich ein
  Bot"). Das ist stillschweigend Margin, keine vollständig gedeckte
  Position. Deshalb zusätzlich: **Positionsgröße zusätzlich auf die
  verfügbare Kaufkraft (`buying_power`) des Kontos deckeln**, sonst
  lehnt Alpaca die Order ab. Für den Papierhandel unkritisch (kein
  echtes Geld, keine Nachschusspflicht), vor jedem Einsatz mit echtem
  Geld aber eine bewusste Entscheidung wert, ob dieser Hebel gewollt ist.
- Wenn Stückzahl < 1 → Trade auslassen und protokollieren.

### Tagesende

- Alle offenen Positionen um **15:55 ET** glattstellen (Market-Order).
- Keine Übernachtpositionen.

---

## 2. Ausführungsmodell — kein Dauerprozess nötig

Der Bot muss **nicht** durchgehend laufen. Alles, was zeitkritisch ist (Stop-
und Zielüberwachung während eines offenen Trades), liegt als Bracket-Order
bereits beim Broker. Der Bot selbst muss nur alle 5 Minuten kurz aktiv werden
und:

1. Prüfen, ob die zuletzt abgeschlossene Kerze ein neues Setup ist (Regel oben)
   → falls ja, Limit-Order platzieren
2. Prüfen, ob eine offene Order ihre 10-Kerzen-Frist überschritten hat
   → falls ja, Order stornieren
3. Um 15:55 ET: offene Positionen schließen

Das passt zu einem **geplanten Skript statt einem Dauerprozess** (siehe
Deployment unten) — es muss zwischen den Läufen nichts "mitbekommen", weil
die eigentliche Trade-Überwachung nicht bei ihm liegt.

---

## 3. Sicherheitsschalter (nicht optional)

Der Bot muss sich selbst abschalten können. Alle Grenzen als Config-Werte:

| Auslöser | Standardwert | Reaktion |
|---|---|---|
| Tagesverlust | −3 % des Kontostands | Handel für den Tag einstellen |
| Gesamtverlust | −15 % vom Startkapital | Bot dauerhaft stoppen, manuelles Reset nötig |
| Verlustserie | 8 Verlusttrades hintereinander | Handel für den Tag einstellen |
| Trades pro Tag | max. 15 | keine neuen Einstiege mehr |
| API-Fehler | 5 Fehler in Folge | Bot stoppen, Alarm |
| Datenlücke | > 10 Min ohne neue Kerze | keine neuen Einstiege, warnen |

Bei jedem Stopp: offene Positionen schließen, Grund protokollieren,
Benachrichtigung senden (Telegram-Bot oder E-Mail — simpel halten).

**Kill-Switch:** Eine Datei oder ein Repository-Secret `STOP` beendet den
Handel — bei GitHub Actions z. B. ein Flag, das der Workflow zu Beginn jedes
Laufs prüft und bei dem er sich sofort beendet, bevor irgendeine Order
angefasst wird.

---

## 4. Technischer Aufbau

- **Sprache:** Python 3.11+
- **Broker/Daten:** Alpaca Paper Trading API (`alpaca-py`), REST reicht —
  kein WebSocket-Stream nötig, da kein Dauerprozess (siehe Abschnitt 2).
  Bars per REST abrufen, letzte abgeschlossene 5-Min-Kerze auswerten.
- **Zustand persistent halten** (Datei im Repository oder externer Storage,
  z. B. ein simples JSON/SQLite, das bei jedem Lauf gelesen und geschrieben
  wird): offene Setups mit Entstehungs-Kerzenindex, offene Trades, Tages-PnL,
  Zähler für die Sicherheitsschalter. Da jeder Lauf ein neuer Prozess ist
  (siehe Deployment), **muss** der Zustand explizit gespeichert werden —
  nichts darf nur im Arbeitsspeicher stehen.
- **Zeitzonen:** Intern alles in UTC, Session-Logik in `America/New_York`.
  US-Feiertage und verkürzte Handelstage über den Alpaca-Marktkalender abfragen,
  nicht selbst hartkodieren.

### Protokollierung (der eigentliche Zweck des Projekts)

Jeder Trade in eine CSV/Log-Datei im Repository mit:
`zeitstempel, richtung, level, entry_geplant, entry_tatsächlich, slippage,
stop, ziel, stückzahl, risiko, exit_grund, exit_preis, pnl, pnl_in_R, dauer,
lauf_verspätung` (Differenz zwischen geplanter und tatsächlicher Ausführung
des Workflow-Laufs — siehe Deployment-Hinweis zur Taktungsgenauigkeit)

**`slippage` und `lauf_verspätung` sind die wichtigsten Spalten.** Der
Backtest unterstellt Ausführung exakt am Level und exakte 5-Minuten-Taktung.
Bei durchschnittlich 0,375 Punkten Risiko pro Trade frisst schon eine
Slippage von 0,03 Punkten spürbar vom erwarteten Vorteil — und eine
verzögerte Erkennung eines Setups wirkt genauso wie Slippage. Genau diese
beiden Zahlen entscheiden, ob die Strategie real haltbar ist.

---

## 5. Auswertung nach einem Monat

Automatisch berechnen und ausgeben:

- Anzahl Trades, Trefferquote, Ø R pro Trade, Gesamt-R
- Ø Slippage in Punkten und in R, Ø Lauf-Verspätung
- Verteilung: Median-Trade, Anteil der besten 3 Trades am Gesamtgewinn
- Vergleich gegen die Backtest-Erwartung (siehe unten)
- Anzahl übersprungener/verfallener Setups und die Gründe

### Erwartungswerte aus dem Backtest (1.390 Trades, durchgehend 2021–2026,
Twelve-Data-5-Min-Bars, siehe [research/FINDINGS.md](research/FINDINGS.md))

Im Unterschied zur alten Tabelle: durchgehender Zeitraum statt Stichproben,
mit sauberer Train/Test-Trennung geprüft (Training 2021–2024, Test
2025–2026, siehe FINDINGS.md für beide Werte getrennt).

| Kennzahl | Backtest (gesamt 2021–2026) |
|---|---|
| Trefferquote | 51,1 % (Breakeven bei 2:1 CRV: 33,3 %) |
| Ø Risiko | 3,257 Punkte |
| brutto Punkte/Trade | +0,184 |
| brutto R/Trade | +0,069 |
| Gesamt (netto, 0,02 Pkt Kosten) | +228,63 Punkte |
| Gesamt (netto, 0,05 Pkt Kosten) | +186,93 Punkte |
| Trades pro Tag | ~0,98 (max. 1) |

**Abweichungen, die ernst zu nehmen wären:** deutlich weniger Trades als
erwartet (Erkennung der Eröffnungsspanne oder des Ausbruchs stimmt nicht,
oder der Workflow läuft nicht zuverlässig), Trefferquote unter 40 %
(deutlich näher an der 33,3-%-Gewinnschwelle als im Backtest, Ausführung
weicht ab), oder Slippage über 0,1 Punkte (mehr als die Hälfte des
durchschnittlichen Vorteils pro Trade aufgebraucht).

**Wichtig zur Einordnung, aus [research/FINDINGS.md](research/FINDINGS.md):**
Ohne Hebel (Positionsgröße auf eigenes Kapital gedeckelt statt reiner
1-%-Risiko-Formel) hätte diese Strategie über 2021–2026 mit +79,9 % (11,0 %
p. a.) schlechter abgeschnitten als einfaches Buy-and-Hold von QQQ (+128,3 %,
15,8 % p. a.), allerdings bei deutlich kleinerem maximalen Rückgang (8,2 %
gegen 37,6 %). Erst mit dem oben beschriebenen Hebel (Ø 1,6-fach) übertrifft
der Bot Buy-and-Hold (+145,0 %, 17,3 % p. a.) bei weiterhin kleinerem
Rückgang (12,5 %) — allerdings ohne Berücksichtigung von Margin-Zinsen. Der
Sinn dieses Bots ist damit in erster Linie Risikoreduktion mit ähnlicher
oder (gehebelt) leicht besserer Rendite als Buy-and-Hold, kein
Wundermittel für außergewöhnliche Gewinne.

---

## 6. Deployment: GitHub Actions statt Dauer-Server

**Keine Kreditkarte nötig, kein eigener Rechner muss laufen.** Passt zum
Ausführungsmodell aus Abschnitt 2, weil der Bot ohnehin keinen Dauerprozess
braucht.

- Workflow mit `on: schedule: cron:` — alle 5 Minuten während der US-Session
  auslösen (in UTC umrechnen, inkl. Sommer-/Winterzeit-Verschiebung beachten)
- **Repository ist öffentlich** (Entscheidung): Die Strategie stammt aus
  einer öffentlichen Quelle und wurde nur in der Parameterwahl selbst
  getestet, ein späterer Umstieg auf echtes Geld würde ohnehin eine andere
  Umsetzung brauchen (kein Alpaca). Öffentliche Repositories haben
  unbegrenzt kostenlose Actions-Minuten, das Minutenbudget ist damit kein
  Thema. Enthält keine sensiblen Daten außer den API-Keys, die als
  **GitHub Secrets** hinterlegt werden (nie im Code oder in Commits)
- Zustand (offene Setups, Tages-PnL, Sicherheitsschalter-Zähler) wird am Ende
  jedes Laufs zurück ins Repository committet oder in einen externen Storage
  geschrieben — sonst weiß der nächste Lauf nichts vom vorherigen
- **Wichtiger Kompromiss, den der Monatstest sichtbar machen soll:** GitHub
  garantiert die 5-Minuten-Taktung nicht exakt — bei hoher Auslastung der
  Actions-Infrastruktur kann ein Lauf mehrere Minuten später starten. Das ist
  der Hauptgrund für die `lauf_verspätung`-Spalte im Log. Falls sich das im
  Test als relevantes Problem zeigt, ist der Umstieg auf einen Dauerprozess
  (z. B. Oracle Cloud Always Free) die Rückfalloption — dafür wäre dann aber
  eine Kartenverifizierung nötig.
- Täglicher Statusbericht per Telegram/E-Mail (z. B. als letzter Schritt im
  letzten Lauf des Tages): Anzahl Trades, Tages-PnL, aktive
  Sicherheitsschalter, Ø Slippage, Ø Lauf-Verspätung

---

## 7. Ausdrücklich NICHT bauen

- Keine automatische Parameteroptimierung. Die Konfiguration ist eingefroren
  (5-Min-Kerzen, 0,5× Stop, 1,5:1 Ziel, 10-Kerzen-Verfallsfrist). Nachjustieren
  während des Live-Tests macht das Ergebnis wertlos.
- Keine zusätzlichen Filter, Indikatoren oder "Verbesserungen" (auch kein
  Trend-Filter — der wurde separat getestet und hat das Ergebnis verschlechtert).
- Keine Anbindung an ein echtes Geldkonto. Ausschließlich Paper-API-Endpunkte.
  Die Live-URL gehört nicht in den Code.
- Keine Skalierung der Positionsgröße nach Gewinnserien.

---

## 8. Reihenfolge der Umsetzung

1. Setup-Erkennung gegen historische Bars, Ergebnis mit den Backtest-Zahlen
   abgleichen (muss ungefähr 5,5 Trades/Tag finden)
2. Zustandsspeicherung zwischen Workflow-Läufen (ohne die geht nichts anderes)
3. Sicherheitsschalter und Kill-Switch
4. Order-Platzierung gegen die Paper-API, erst manuell ausgelöst
5. GitHub-Actions-Workflow mit Zeitplan
6. Protokollierung und Auswertung inkl. Lauf-Verspätung

Schritt 1 vor allen anderen. Wenn die Erkennung nicht dieselben Setups findet
wie im Backtest, testet der Bot etwas anderes als das, was geprüft wurde.

---

## 9. Änderungsprotokoll: Review, Datenfeed-Untersuchung, Strategiewechsel

**Historisches Protokoll.** Dieser Abschnitt entstand während der
Entwicklung der ursprünglichen Strategie "Wickless Candle Retest" und
dokumentiert den Weg von der ersten Review über die Datenfeed-Untersuchung
bis zum Befund, der zum Wechsel auf Opening Range Breakout geführt hat
(siehe Änderungshinweis oben und [research/FINDINGS.md](research/FINDINGS.md)
für die neue Strategie). Bleibt stehen als Nachvollzug der Entscheidung,
ist aber nicht mehr die aktuelle Handlungsgrundlage. Infrastruktur-Punkte
weiter unten (Kill-Switch, Tages-Reset, parallele Läufe usw.) gelten
weiterhin, unabhängig von der Strategie.

### Pattern-Day-Trader-Regel — geprüft

Die Strategie erzeugt bei ~5,5 Trades/Tag ausschließlich Day-Trades. Die alte
FINRA-PDT-Regel wurde laut Alpaca zum 4. Juni 2026 plattformweit abgeschafft
und durch ein "Intraday Margin Framework" ersetzt (siehe
[Alpaca-Blog](https://alpaca.markets/blog/finra-retires-the-pdt-rule-introducing-alpacas-new-intraday-margin-framework/)),
alte PDT-Felder in der API laut Blog bis zum 6. Juli 2026 entfernt. Der
eigene Paper-Account bestätigt das: `pattern_day_trader` liefert `None`
statt `true`/`false` (Abfrage vom 19.08.2026 über
`TradingClient.get_account()`, Cash 100.000 $, Status aktiv), das Feld ist
also nicht mehr befüllt. Das Risiko ist damit erledigt, nicht nur
wahrscheinlich gering.

### Nachholen verpasster Kerzen

Abschnitt 2 sieht vor, bei jedem Lauf "die zuletzt abgeschlossene Kerze" zu
prüfen. Fällt ein GitHub-Actions-Lauf aus oder verspätet er sich über eine
ganze Kerze hinaus, wird die dazwischenliegende Kerze nie ausgewertet — kein
Logeintrag, kein Fehler, einfach ein verpasstes Setup. Der Bot sollte bei
jedem Lauf alle Kerzen seit dem zuletzt verarbeiteten Kerzenindex nachholen,
nicht nur die letzte.

### Log für nicht ausgelöste Setups

Abschnitt 5 soll am Monatsende die Anzahl übersprungener/verfallener Setups
und die Gründe ausgeben. Das Trade-Log aus Abschnitt 4 enthält aber nur
tatsächlich ausgeführte Trades — ein Setup, das nie auslöst, taucht darin
nicht auf. Braucht ein zweites Log oder ein Statusfeld je Setup (ausgelöst /
verfallen / übersprungen-zu-klein / …), sonst lässt sich diese Auswertung
nicht berechnen.

### Parallele Workflow-Läufe

Falls ein Lauf einmal länger braucht als die 5-Minuten-Lücke bis zum
nächsten, fehlt eine Absicherung gegen überlappende Läufe (in GitHub Actions
über `concurrency:` lösbar). Ohne das könnten zwei Läufe gleichzeitig
denselben Zustand lesen/schreiben und im schlimmsten Fall doppelt Orders
auslösen.

### Tages-Reset der Sicherheitsschalter-Zähler

Die Zähler für Tagesverlust, Trades/Tag und Verlustserie werden persistent
gespeichert (Abschnitt 4), aber wann sie auf null zurückgesetzt werden, steht
nicht im Dokument. Braucht einen expliziten Reset zu Handelstagbeginn, sonst
bleibt entweder ein Tagesstopp über Nacht hängen oder die Zähler laufen
tagelang mit. Der Gesamtverlust-Zähler aus Abschnitt 3 bleibt davon
ausgenommen, der ist bewusst dauerhaft.

### Kill-Switch-Mechanismus festlegen

Abschnitt 3 lässt offen, ob der Kill-Switch eine Datei im Repository oder ein
Repository-Secret ist. Beides hat unterschiedliche Handhabung unterwegs, eine
Datei lässt sich z. B. über die GitHub-App vom Handy in Sekunden ändern. Vor
Schritt 3 im Bauplan (Abschnitt 8) festlegen, welches der beiden es wird.

### Öffentliches vs. privates Repository — entschieden

Repository ist öffentlich, Begründung siehe Abschnitt 6. Die recherchierten
Zahlen dazu ([GitHub-Doku](https://docs.github.com/en/billing/concepts/product-billing/github-actions)):
private Repos im Free-Plan bekommen 2.000 Actions-Minuten/Monat, bei 78
Läufen/Handelstag über ~21 Handelstage wären das ~1.638 Minuten geschätzter
Bedarf, knapp aber ausreichend gewesen. Mit dem öffentlichen Repository ist
das Minutenbudget ohnehin kein Thema mehr.

### Technischer Schutz gegen Live-Endpunkt

Abschnitt 7 verbietet die Live-URL im Code als Regel. Ließe sich zusätzlich
technisch erzwingen, etwa mit einem Check beim Start, der die konfigurierte
Basis-URL gegen den bekannten Paper-Endpunkt von Alpaca prüft und sonst
abbricht, statt sich allein auf Disziplin zu verlassen.

### Statistische Aussagekraft der Monatsauswertung

Bei ~5,5 Trades/Tag kommen in einem Monat ~115 Trades zusammen. Das ist bei
50,6 % Trefferquote eine kleine Stichprobe: Allein durch normale Schwankung
kann die Trefferquote in diesem Zeitraum leicht mehrere Prozentpunkte in
beide Richtungen abweichen, auch wenn Setup-Erkennung und Ausführung
einwandfrei laufen. Der erste Monat zeigt eher, ob grob etwas schiefläuft,
als dass er die Strategie endgültig bestätigt oder widerlegt.

### Datenfeed weicht vom Backtest ab, und kostet auf IEX die Edge

Validierung mit echten Alpaca-Daten (IEX-Feed, Free-Tier, siehe
[scripts/validate_setup_detection.py](scripts/validate_setup_detection.py)):
Die Setup-Erkennung findet 11,9-12,7 Trades/Tag, mehr als doppelt so viel
wie die Backtest-Erwartung (~5,5/Tag). Geprüft und ausgeschlossen: kein
Einzeltag-Ausreißer (stabil über 41-252 Handelstage in mehreren
Stichproben) und keine Setup/Trade-Verwechslung (die genannten Zahlen sind
bereits die durch `simulate_entries()` gefilterten ausgelösten Trades, die
Rohzählung der Setups liegt nochmal höher). Im direkten Vergleich über
identischen Zeitraum (1.6.-19.8., 41 gemeinsame Handelstage): yfinance
4,86 Trades/Tag, Alpaca IEX 12,71 Trades/Tag. Gleicher Code, exakt
derselbe Zeitraum, unterschiedliche Datenquelle, der Unterschied liegt am
Feed. Stichprobe zur selben Kerze (29.06., 09:30) belegt das auf
Bar-Ebene: yfinance O/H/L/C 713,99/716,96/711,88/716,79 gegen Alpaca
714,04/716,81/711,97/716,81, nah beieinander, aber nicht identisch, weil
beide aus unterschiedlichen zugrundeliegenden Trades gebaut sind. Genau an
der 2%-Docht-Schwelle reichen solche Differenzen, um eine Kerze mal als
Setup zu werten und mal nicht.

IEX deckt nur einen kleinen Teil des gesamten QQQ-Handelsvolumens ab
(im Schnitt 83-107 Trades je 5-Min-Bar in der Stichprobe, für ein so
liquides Symbol dünn). Bei weniger zugrundeliegenden Trades pro Kerze
steigt die Chance, dass Hoch/Tief zufällig mit Open/Close zusammenfallen,
also mehr scheinbar dochtlose Kerzen durch Stichprobenrauschen statt durch
ein echtes Muster.

Das trifft den Kern des Projekts: Abschnitt 4 sieht Alpacas REST-API als
Datenquelle für den Live-Bot vor, im Free-Tier ist das genau dieser
IEX-Feed. Wenn schon die Setup-Erkennung auf einer anderen Datengrundlage
läuft als der Backtest, misst der Monatstest nicht mehr nur
Ausführungsqualität (Slippage, Lauf-Verspätung), sondern zusätzlich eine
andere Signalhäufigkeit, und genau diese Vermischung soll der Test laut
Abschnitt 5 eigentlich vermeiden.

Zwei bereits angebundene Alternativ-Datenquellen (Alpha Vantage, Financial
Modeling Prep) wurden geprüft, beide verlangen für Intraday-Daten ein
bezahltes Abo, keine davon lieferte einen Testwert.

**Vollständiger Backtest auf IEX-Daten (nicht nur Setup-Zählung), siehe
[scripts/backtest_strategy.py](scripts/backtest_strategy.py):** Mit exakt
denselben Regeln aus Abschnitt 1 (Entry, Stop, Ziel, Tagesende, nichts
verändert) über 252 Handelstage, 2999 Trades:

| Kennzahl | IEX-Backtest | Spec-Backtest |
|---|---|---|
| Trefferquote | 36,4 % | 50,6 % |
| Ø Risiko | 0,386 Punkte | 0,375 Punkte |
| brutto Punkte/Trade | −0,035 | +0,100 |
| brutto R/Trade | −0,091 | +0,266 |
| Gesamt R | −271,93 | (positiv, siehe oben) |

36,4 % liegt unter der Breakeven-Trefferquote bei 1,5:1 CRV (40 %) und
unter der Schwelle, die Abschnitt 5 selbst als ernstzunehmende Abweichung
nennt. Gegengeprüft: Bei Kerzen, die im selben 5-Min-Bar sowohl Stop als
auch Ziel berühren (5,1 % aller Trades, 153 von 2999), wurde bislang
konservativ "Stop zuerst" angenommen, mangels Tick-Daten nicht anders
entscheidbar. Mit der optimistischen Gegenprobe ("Ziel zuerst") steigt die
Trefferquote auf 41,5 % und Gesamt-R auf +110,57, aber selbst das ist nur
+0,037 R/Trade, ein Bruchteil der erwarteten +0,266. Die Modellannahme
verändert also das Ausmaß, nicht die Richtung: Auf IEX-Daten ist die vom
Backtest behauptete Edge weitgehend weg, nicht nur die Trade-Frequenz
anders. Erkennung, Entry- und Exit-Logik sind unit-getestet und
entsprechen Abschnitt 1, das Ergebnis ist also kein Implementierungsfehler.

Twelve Data ebenfalls geprüft (ohne Account, nur deren Support-Doku):
Auch dort deckt der Standard-Feed, inklusive Free-Tier und sogar dem
bezahlten Basic-Plan, nur ~5 % des US-Handelsvolumens ab, aus Börsen ohne
Lizenzpflicht statt vollem Konsolidierungstape. Echte konsolidierte Daten
gäbe es dort nur über eine Sondervereinbarung mit dem Vertrieb. Damit
vermutlich kein besserer Ausgangspunkt als der bestehende IEX-Feed.

**Nachtrag, Twelve Data direkt getestet (echter API-Key, siehe
[scripts/compare_data_sources.py](scripts/compare_data_sources.py) und
`tradingbot/data.py:load_twelvedata_bars`):** Über volle 251 Handelstage
trifft Twelve Data die Backtest-Frequenz fast exakt (5,36 Trades/Tag ggü.
5,5 erwartet, mit Abstand die beste Übereinstimmung bisher), aber
Trefferquote (36,9 %) und Ø R/Trade (−0,082) sind fast identisch schlecht
wie bei Alpaca IEX (36,4 % / −0,091 über 252 Tage), obwohl die Frequenz bei
IEX mehr als doppelt so hoch liegt (11,90/Tag). Zwei Quellen mit fast
gegensätzlicher Frequenz-Abweichung landen bei fast demselben, klar
negativen Ergebnis.

Das ändert die Diagnose: Es sieht nicht mehr in erster Linie nach einem
Datenfeed-Problem aus. Über ein ganzes Jahr und mit einer Quelle, deren
Frequenz fast exakt passt, bleibt die Trefferquote klar unter der
40-%-Gewinnschwelle bei 1,5:1 CRV. Das lässt sich mit besseren Daten allein
vermutlich nicht beheben. Naheliegendste Erklärung: Die Strategie hat im
letzten Jahr real nicht die im Backtest (2021-2026, 5-Jahres-Schnitt)
ausgewiesene Edge gezeigt, das könnte normale Schwankung über die
Jahre sein oder ein Hinweis, dass sich die zugrundeliegende Marktstruktur
seit dem Backtest-Zeitraum verändert hat. Offen und nur vom Nutzer zu
beantworten: Auf welchem Zeitraum/welcher Datengrundlage beruhte die
eigene Voruntersuchung, mit der die 10-Kerzen-Frist und die übrigen
Parameter gewählt wurden, und deckt sie das letzte Jahr mit ab?

**Nachtrag, komplette 2021-2026-Spanne auf Twelve Data getestet** (Nutzer
bestätigt: eigene Voruntersuchung lief ebenfalls 2021 bis heute, siehe
[scripts/backtest_by_year.py](scripts/backtest_by_year.py)), 109.691 Bars,
8010 Trades:

| Jahr | Tage | Trades/Tag | Trefferquote | Ø R/Trade | Gesamt R |
|---|---|---|---|---|---|
| 2021 | 252 | 6,16 | 36,8 % | −0,084 | −130,38 |
| 2022 | 251 | 5,31 | 39,1 % | −0,031 | −41,42 |
| 2023 | 250 | 5,28 | 40,0 % | −0,006 | −8,35 |
| 2024 | 252 | 6,04 | 40,6 % | +0,013 | +19,59 |
| 2025 | 250 | 5,88 | 36,8 % | −0,086 | −125,96 |
| 2026 (bis Aug.) | 157 | 5,18 | 36,1 % | −0,096 | −78,40 |
| **Gesamt** | | | **38,3 %** | **−0,046** | **−364,92** |

Kein einziges Jahr kommt in die Nähe der behaupteten 50,6 % Trefferquote
oder +0,266 R/Trade, das beste Jahr (2024) liegt bei +0,013 R/Trade,
praktisch Nullsummenspiel, alle anderen sind klar negativ. Damit sind
beide bisherigen Erklärungen widerlegt: Es ist weder ein reines
Datenfeed-Problem (Twelve Data trifft die Frequenz gut, siehe oben), noch
eine ungewöhnlich schwache jüngste Phase innerhalb eines sonst starken
Zeitraums (jedes Jahr seit 2021 liegt in einem engen 36-41-%-Band, keine
Verschlechterung erkennbar, es war nie in der Nähe der 50,6 %).

Trade-Anzahl liegt mit 8010 über den 5,6 Jahren zudem mehr als doppelt so
hoch wie die 3602 Trades, die der Spec-Backtest über "10 Zeiträume
2021-2026" nennt, ein Hinweis, dass die "10 Zeiträume" möglicherweise
keine durchgehende Abdeckung des gesamten Zeitraums waren, sondern eine
Auswahl daraus.

**Geklärt (Nutzerangabe):** Der Original-Backtest lief über dieselbe
Twelve-Data-API, dieselben Parameter (Symbol, Intervall, Zeitzone,
reguläre Session) wie meine Tests hier, kein Datenquellen-Unterschied. Der
Unterschied liegt in der Stichprobe: 10 Fenster à ca. 3,5 Monate, verteilt
über Juni 2021 bis August 2026, nicht durchgehend, sondern Stichproben,
wegen der Limits im kostenlosen Twelve-Data-Plan (649 Handelstage, 3602
Trades insgesamt).

Damit erklärt sich die Abweichung: Laut Abschnitt 1 wurde die
10-Kerzen-Verfallsfrist "getestet (1 bis 200 Kerzen, auf 10 Zeiträumen
2021–2026) und ist das Gesamtgewinn-Optimum" - also aus 200 Kandidaten
gezielt der Wert gewählt, der auf genau diesen 10 Fenstern den größten
Gewinn zeigt. Ein so gewählter Parameter muss auf der Stichprobe, auf der
er gesucht wurde, gut aussehen, das ist kein Beleg für eine echte Edge,
sondern das erwartbare Ergebnis der Suche selbst (klassisches
In-Sample-Overfitting, kein Out-of-Sample-Test). Der Test hier lief mit
demselben eingefrorenen Parameter über den vollen durchgehenden Zeitraum
(8010 statt 3602 Trades) und zeigt in allen sechs Jahren ein negatives bis
bestenfalls neutrales Ergebnis, nie in der Nähe der 50,6 %. Genau das
Muster, das bei überangepassten Parametern zu erwarten ist: stark auf der
Stichprobe, auf der optimiert wurde, schwach außerhalb davon. Nicht mit
letzter Sicherheit bewiesen (die exakten 10 Original-Fenster wurden hier
nicht nachgestellt), aber die Konsistenz über alle sechs Jahre macht Zufall
als Erklärung unwahrscheinlich.

Entscheidung damit final: Kein Datenfeed-Problem, kein "letztes Jahr war
schwach", sondern vermutlich In-Sample-Overfitting bei der
Parameterwahl (10-Kerzen-Frist über 200 Kandidaten auf genau den 10
Fenstern optimiert, die auch die Erwartungswerte in Abschnitt 5 liefern).
Ein bezahltes Datenupgrade würde daran nichts ändern. Die Empfehlung
an dieser Stelle: vor Schritt 3 und einem Live-Test mit dem Nutzer klären,
ob die Strategie in dieser eingefrorenen Parametrisierung überhaupt noch
weiterverfolgt werden soll, ein sauberer Out-of-Sample-/Walk-Forward-Test
wäre die eigentlich nötige Nachbesserung, aber das ist eine neue
Backtest-Aufgabe, kein Bot-Bau-Schritt, und geht über den Rahmen dieser
Spec hinaus.

---

## 10. Kriterium für den Umstieg auf echtes Geld

**Entscheidung (24.08.2026):** Umstieg auf echtes Geld, wenn die Methode
(Opening Range Breakout, Abschnitt 1) im Papierhandel bis März 2027
positiv ist, also kumulierter Gewinn (Gesamt-PnL, per `/status` oder
[scripts/monthly_evaluation.py](scripts/monthly_evaluation.py) abrufbar)
über den gesamten Zeitraum seit Live-Schaltung des Bots.

Das verlängert den in Zeile 3 genannten Mindestzeitraum von einem Monat auf
rund sieben Monate. Nach der Erfahrung mit der Original-Strategie
(Abschnitt 9: ein einzelner Monat oder sogar ein einzelnes Jahr hätte ein
falsches Bild geben können, siehe die Jahr-für-Jahr-Tabelle dort) ist das
methodisch die richtige Richtung, ein längerer Zeitraum ist weniger anfällig
für Zufallsschwankungen als ein kurzer.

Vor dem eigentlichen Umstieg zusätzlich zu klären, unabhängig vom
PnL-Ergebnis: reales Geldkonto bei Alpaca einrichten (Abschnitt 7 schließt
das für den Bot in seiner jetzigen Form ausdrücklich aus, "nur
Paper-API-Endpunkte"), Positionsgrößen-Hebel-Frage aus Abschnitt 1 bewusst
entscheiden, und Kill-Switch/Sicherheitsschalter aus Abschnitt 3 nochmal
gegen echtes Geld statt Papierkapital durchdenken.

---

## 11. Betriebsvorfälle (laufend ergänzt)

**25./26.08.2026: Position ungeplant über Nacht offen.** Der
Tagesende-Zwangsschluss schlug fehl, weil Alpaca eine zusätzliche
Schließ-Order ablehnte, solange die Bracket-Order (Stop/Ziel) die
Stückzahl noch für ihre offenen Legs reserviert hatte ("insufficient qty
available"). Der Fehler war im Bot-Code unbehandelt, das Skript stürzte
ab, bevor `state.json` gespeichert wurde. Die Position blieb dadurch
ungeschützt (Stop und Ziel liefen zum Handelsschluss reguär per
Tages-Order ab) bis zum nächsten Handelsstart offen, wurde dort
automatisch geschlossen. Realer Verlust überschaubar (−113,18 $, kleine
Kurslücke über Nacht), aber ein echtes, unkontrolliertes Risiko, keine
Frage von Glück gewesen zu sein reicht als Absicherung nicht.

Behoben (Commit `9666e29`): `force_close_open_position` storniert jetzt
zuerst alle noch offenen Bracket-Legs, bevor die Position geschlossen
wird. `run_bot.py` fängt außerdem jeden unerwarteten Fehler zentral ab
und sichert `state.json` in jedem Fall, bevor der Fehler erneut
ausgelöst wird (der GitHub-Actions-Lauf zeigt weiterhin rot, aber der
nächste Lauf verliert den Überblick nicht mehr). Der schon geloggte
Trade wurde nachträglich auf den echten Füllpreis korrigiert (Alpacas
Order-Historie), `exit_preis`/`pnl` in
[trades.csv](trades.csv) und `total_pnl` in `state.json` angepasst.

**25.08.2026: GitHub Actions taktet deutlich langsamer als geplant.**
Siehe Abschnitt 6/9 weiter oben, hier nur der Verweis: gemessener
Abstand zwischen Läufen 18-50 Minuten statt der geplanten 5, betrifft
Signal-Timing und Tagesende-Schluss gleichermaßen. Lösung in Arbeit:
externer Cron-Trigger statt GitHubs eigenem `schedule`-Event.

---

## 12. Telegram-Signal-Ausführung (neues Feature, 26.08.2026)

Zweiter, komplett getrennter Bot neben dem ORB-Bot: liest Handelssignale aus
einem öffentlichen Telegram-Kanal ("KaraokeAndi", Live-Day-Trading, Signale
für NASDAQ INDEX, DOW JONES INDEX, GERMAN DAX INDEX und FTSE 100 INDEX),
lässt sie per LLM auswerten und führt sie automatisch aus - ursprünglich auf
demselben Alpaca-Paper-Konto wie der ORB-Bot (QQQ/DIA-ETF-Proxys), seit
28.08.2026 auf einem eigenen Pepperstone-Demokonto über die cTrader Open API
mit echten Index-CFDs (siehe Nachtrag unten, "Broker-Umstieg").

**Warum getrennt vom ORB-Bot:** eigener Zustand
([signal_state.json](signal_state.json)), eigenes Protokoll
([signal_trades.csv](signal_trades.csv)), eigener Workflow
([.github/workflows/signal-bot.yml](.github/workflows/signal-bot.yml)).
Geteilt wird nur die Kill-Switch-Datei (`STOP`) - seit dem Broker-Umstieg
nicht mehr das Konto (der ORB-Bot bleibt auf Alpaca) - ein
Sicherheitsschalter-Stopp im einen Bot stoppt nicht automatisch den anderen,
außer über die gemeinsame Kill-Switch-Datei.

**Kanalzugang:** ein Bot kann über die normale Telegram-Bot-API keinem
Kanal selbstständig beitreten. Lösung: Pyrogram (MTProto) mit dem
vorhandenen Bot-Token plus `api_id`/`api_hash` (von
[my.telegram.org](https://my.telegram.org)) - damit kann sich der Bot per
`join_chat` selbst in den öffentlichen Kanal einklinken, ganz ohne
Nutzer-Account. Läuft `in_memory` (kein Session-File), weil jeder Lauf
ohnehin ein neuer Prozess ist (Abschnitt 2).

**Instrument-Mapping (`signalbot/mapping.py`):** Alpaca bietet die Indizes
selbst nicht zum Handel an. NASDAQ INDEX → QQQ, DOW JONES INDEX → DIA (die
liquidesten 1:1-Tracking-ETFs). Levels aus dem Signal stehen in
Index-Punkten, nicht im ETF-Kurs - eine direkte Übernahme wäre falsch.
Stattdessen: prozentualer Abstand zwischen Entry- und Stop-Level im Index
berechnen, auf den tatsächlichen ETF-Kurs beim Einstieg anwenden. Fehlt ein
Stop-Level in der Nachricht (häufig, der Kanal ist eher Freitext als
strukturierte Daten), greift ein fester Fallback von 0,5 %. Ziel folgt,
falls nicht selbst genannt, der gleichen 2:1-CRV-Konvention wie beim
ORB-Bot. Positionsgröße: gleiche 1-%-Risiko-Regel, gleicher Code
(`tradingbot/orders.py`, wiederverwendet statt dupliziert).

**LLM-Auswahl (`signalbot/parser.py`):** kostenlos gewünscht → Google
Gemini (Freikontingent ohne Kreditkarte). Modellwahl mehrfach angepasst,
weil ältere Modellnamen zur Laufzeit "no longer available to new users"
lieferten (`gemini-2.0-flash`, `gemini-2.5-flash-lite`, `gemini-2.5-flash`
alle mit 404 abgelehnt) - aktuell `gemini-3.6-flash`.

Wichtiger Befund beim Testen (26.08.2026): mit striktem `responseSchema`
(erzwungene JSON-Struktur) ließ das Modell wiederholt Felder wie
`direction` komplett weg, obwohl die Nachricht sie eindeutig enthielt
("NASDAQ INDEX long" → kein `direction`-Feld in der Antwort) - das hätte
echte Signale still unter den Tisch fallen lassen. Ohne Schema, nur mit
`responseMimeType=application/json` und dem Format als Text im
System-Prompt, lieferte dasselbe Modell in denselben Testfällen
vollständige und korrekte Antworten. Deshalb bewusst kein `responseSchema`
im Code. Zusätzlich im Prompt verschärft: Zahlen (Kurslevel) nur
übernehmen, wenn sie wörtlich in der Nachricht stehen, niemals raten oder
einen plausibel klingenden Marktwert erfinden - ein früherer Test hatte
sonst frei erfundene Indexstände geliefert.

**Ausführung: vollautomatisch, keine Bestätigung.** Ausdrücklicher Wunsch
(26.08.2026), abweichend von meiner ursprünglichen Empfehlung
(Bestätigung per Telegram vor Ausführung, wegen der zusätzlichen
Fehlerquelle LLM-Auswertung + Instrument-Mapping). Vertretbar, weil
weiterhin reines Paper-Trading (Abschnitt 7/10 gelten unverändert auch für
dieses Feature).

**Sicherheitsschalter (`scripts/run_signal_bot.py`):** eigene, bewusst
schlankere Fassung als Abschnitt 3 (kein Tages-Trade-Limit, keine
Datenlücken-Prüfung - beides ergibt bei signalgetriebener statt
kerzengetriebener Auslösung wenig Sinn). Vorhanden: gemeinsamer
Kill-Switch, Gesamtverlust-Schalter (−15 %, wie Abschnitt 3, eigene
`initial_equity`-Referenz), API-Fehler-Zähler, Tagesende-Zwangsschluss
(15:55 ET, gleiche Bracket-Leg-Cancel-Reihenfolge wie der Fix aus
Abschnitt 11 vom 25./26.08.2026 - hier von Anfang an eingebaut statt erst
nach einem Vorfall). Je Instrument höchstens eine offene Position
gleichzeitig (neues Signal für ein Instrument mit bereits offener Position
wird übersprungen, nicht in eine Warteschlange gestellt).

**Nachtrag 26.08.2026: Kanal nur per Einladungslink erreichbar, kein
Bot-Zugriff möglich.** Der Zielkanal hat keinen öffentlichen Nutzernamen,
nur einen Einladungslink (`t.me/+...`). Live getestet: Bots dürfen
Einladungslinks grundsätzlich nicht verwenden, weder zum Beitreten
(`join_chat` → `BOT_METHOD_INVALID`) noch zum bloßen Auflösen
(`messages.CheckChatInvite` → derselbe Fehler) - eine harte
Telegram-Einschränkung, unabhängig von Pyrogram. Die ursprüngliche Prämisse
("Bot tritt öffentlichen Kanälen selbstständig bei, ganz ohne den privaten
Account") gilt also nur für Kanäle mit öffentlichem Nutzernamen, nicht für
diesen.

Entscheidung (26.08.2026, ausdrücklich vom Nutzer): stattdessen der eigene
Telegram-Account statt des Bots, da bereits Mitglied des Kanals. Dafür
[signalbot/generate_session.py](signalbot/generate_session.py) - ein
separates, manuell und einmalig auszuführendes Skript (nicht Teil des
automatisierten Bots), das über Pyrograms eigenen interaktiven Login-Ablauf
eine Session-Zeichenkette erzeugt. Telefonnummer, Login-Code und ein
eventuelles Cloud-Passwort werden dabei ausschließlich lokal im eigenen
Terminal des Nutzers eingegeben, nie von Claude - nur die resultierende
Session-Zeichenkette (`TELEGRAM_USER_SESSION`) wird weitergereicht und als
Secret hinterlegt. `signalbot/telegram_signals.py` unterstützt jetzt beide
Modi (Nutzer-Session, falls gesetzt, sonst Bot-Token) - Letzteres bleibt
für eventuelle künftige Kanäle mit öffentlichem Namen nutzbar.

Sicherheitsunterschied zum Bot-Token: eine Session-Zeichenkette hat vollen
Zugriff auf den gesamten privaten Account (nicht nur den einen Kanal) -
bei einem Leck entsprechend größerer Schaden als bei einem kompromittierten
Bot-Token. Bewusst in Kauf genommen (Nutzerentscheidung), da GitHub Secrets
verschlüsselt und nicht in Logs sichtbar sind und der Zugriff rein lesend
ist (kein `send_message` über diese Session).

**Nachtrag 26.08.2026: Kanal-Zugriff live verifiziert, ein weiterer
Pyrogram-Stolperstein gelöst.** `TELEGRAM_USER_SESSION` liegt vor
(Kanal heißt tatsächlich "TraderTom Live Day Trading"). Beim ersten
End-to-End-Test zeigte sich: `get_chat_history` kann mit dem
Einladungslink selbst nichts anfangen (versucht ihn wie einen
Nutzernamen aufzulösen, `USERNAME_INVALID`), und `join_chat()` liefert
ab dem zweiten Lauf nur noch `UserAlreadyParticipant` statt des
Chat-Objekts - erwartbar, weil jeder Lauf ein neuer, leerer Prozess ohne
Pyrograms sonst üblichen Peer-Cache aus Vorläufen ist (Abschnitt 2).
Gelöst über `messages.CheckChatInvite` (klappt mit dem eigenen Account,
anders als beim Bot-Token-Test oben): liefert unabhängig vom
Mitgliedsstatus das volle Kanal-Objekt inklusive `access_hash`, der wird
in `signalbot/telegram_signals.py::_resolve_invite_link` manuell in
Pyrograms Peer-Speicher eingetragen. Damit im Live-Test erfolgreich
echte Nachrichten aus dem Kanal geholt und durch die volle Kette
(Parser → Mapping → Signal) geschickt - Format passt zur Erwartung
("NASDAQ INDEX", "BOUGHT LONG", "ENTRY = ...", "STOP = ..."), korrekt
als Long-Signal mit prozentual übersetztem Stop erkannt. Der Kanal
schickt auch "CLOSE TRADE ALERT"-Nachrichten (Hinweis, dass der
Kanal-Urheber seinen eigenen Trade schließt) - die erkennt der Parser
bewusst NICHT als eigenes Signal (Abschnitt 12 oben, "Kommentare/
Ergebnis-Updates zählen nicht"); der Signal-Bot reagiert darauf nicht,
sondern verwaltet offene Positionen ausschließlich über die eigene
Bracket-Order (Stop/Ziel/EOD). Das ist eine bewusste Vereinfachung, kein
Versehen - abweichend vom Vorbild-Kanal, aber konsistent mit der
eigenen 1-%-Risiko/2:1-Konvention.

**Nachtrag 26.08.2026: Taktung eingerichtet, mit einer wichtigen
Klarstellung.** Nutzerwunsch war "sofort reagieren, wenn eine Nachricht
kommt", zuletzt sogar "alle 5 Sekunden". Technisch nicht erreichbar mit
diesem Aufbau: cron-job.org unterstützt keine Intervalle unter 1 Minute,
und ein GitHub-Actions-Lauf braucht selbst schon 45-90 Sekunden
(Checkout, Python-Setup, Ausführung) - ein 5-Sekunden-Trigger würde nur
zu einem Rückstau wartender Läufe führen (`concurrency: cancel-in-progress:
false`), nicht zu echter 5-Sekunden-Reaktion. Echtes Sofort-Reagieren
bräuchte einen dauerhaft laufenden Prozess statt einzelner
GitHub-Actions-Läufe - ein größerer architektonischer Umbau, der hier
bewusst nicht gemacht wurde (nicht angefragt).

Stattdessen umgesetzt, als beste Annäherung an die Nutzerbeschreibung:

- **Zweiter cron-job.org-Job** (`signalbot`, Job-ID 8333098, per API
  angelegt - gleicher GitHub-PAT wie beim ersten Job, siehe dort), löst
  `signal-bot.yml` per `workflow_dispatch` aus, **jede Minute** (die
  technische Untergrenze), 06:00-21:00 UTC, Mo-Fr. Das Fenster ist
  bewusst breiter als noetig (deckt EU- und US-Handelszeiten inklusive
  Sommer-/Winterzeit-Unschärfe ab) - gleiches Prinzip wie beim
  ORB-Bot-Workflow ("Workflow bewusst weiter getaktet als die Session,
  Skript prüft selbst", Abschnitt 9).
- **Feingranulare Steuerung im Skript selbst** (`scripts/run_signal_bot.py`),
  weil cron-job.org das nicht kann:
  - `_is_eu_hours`/`_is_us_market_open`: nur wenn EU- ODER US-Markt
    offen ist (US über Alpacas Marktkalender wie beim ORB-Bot, EU grob
    per festem UTC-Fenster 06:00-17:00, da es dafür keine
    Alpaca-äquivalente Quelle gibt) wird der Kanal überhaupt abgefragt.
  - `_should_poll_channel`: die vom Nutzer gewünschte Drosselung - 30
    Minuten ohne neue Kanal-Nachricht → nur noch alle 5 Minuten
    tatsächlich abfragen (statt bei jedem minütlichen Lauf), bis wieder
    eine neue Nachricht kommt oder der Markt schließt. Neue Zustandsfelder
    dafür in `signalbot/state.py`: `last_channel_message_at` (Telegrams
    eigener Sendezeitpunkt, nicht der lokale Abrufzeitpunkt),
    `last_poll_at`.

Live verifiziert: der neue Cron-Job löst zuverlässig minütlich aus, ein
dadurch ausgelöster `signal-bot.yml`-Lauf ist erfolgreich durchgelaufen.

**Nachtrag 26.08.2026: `gemini-3.6-flash` durch `gemini-flash-lite-latest`
ersetzt - Freikontingent-Falle gefunden, plus Beispiele in den Prompt.**
Nutzerfrage "läuft die LLM auch, können wir ihr alte Nachrichten geben,
damit sie lernt" - dazu zwei Dinge:

1. Ein API-Aufruf ist zustandslos, das Modell "lernt" nichts zwischen
   getrennten Aufrufen (kein Fine-Tuning im Einsatz). Was tatsächlich
   wirkt: gute Beispiele direkt im System-Prompt, der bei jedem Aufruf
   erneut mitgeschickt wird. `signalbot/parser.py::SYSTEM_PROMPT` enthält
   jetzt fünf echte, beim Testen aus dem Kanal geholte Beispiele (Signal
   mit Levels, "CLOSE TRADE ALERT" als Nicht-Signal, Status-/PnL-Updates
   als Nicht-Signal) - im Test 11/11 Fälle korrekt erkannt (vorher nicht
   systematisch geprüft).
2. Beim Prüfen dabei entdeckt: `gemini-3.6-flash` hat im kostenlosen
   Freikontingent nur **20 Anfragen pro Tag** (`quotaId:
   GenerateRequestsPerDayPerProjectPerModel-FreeTier`, live per 429-Fehler
   aufgedeckt) - durch das eigene Testen am 26.08. mehrfach ausgeschöpft,
   für einen Live-Bot völlig unzureichend. `gemini-3.5-flash-lite` (das
   Modell, das beim allerersten Test mit striktem `responseSchema`
   fehlerhafte Antworten lieferte, siehe oben) funktioniert ohne Schema
   (aktueller Code) korrekt und fehlerfrei - die Ursache war also
   tatsächlich das Schema, nicht das Modell. Jetzt fest auf
   `gemini-flash-lite-latest` (Alias, zeigt auf das jeweils aktuelle
   Lite-Modell - schützt vor der "not available to new users"-Falle bei
   fest benannten Modellversionen, die schon zweimal aufgetreten ist).

Zusätzlich: `_try_new_signals` protokolliert jetzt auch den Fall "Kanal
abgefragt, keine neuen Nachrichten" (vorher stumm, was den ersten
Live-Läufen einen leeren, missverständlichen Log gab).

**Nachtrag 28.08.2026: Broker-Umstieg von Alpaca (QQQ/DIA-ETF-Proxys) auf
die cTrader Open API (Pepperstone-Demokonto, echte Index-CFDs für
NASDAQ/DOW/UK100/DAX).** Wunsch des Nutzers: ein Kurs, der 1:1 dieselben
Werte wie der Kanal zeigt (z. B. "US Tech 100" bei ~29.000 Punkten), statt
eines ETF-Proxys mit abweichenden Zahlen. Drei vorherige Broker-Anläufe
scheiterten:
- **OANDA**: "Manage API Access" (Personal Access Token) war auf dem für
  EU-Kunden erreichten Rechtsträger (`oanda.com/eu-en`) nicht auffindbar.
- **IG**: verlangt ein KYC-verifiziertes Live-Konto, um überhaupt einen
  API-Key zu erzeugen - auch wenn nur das Demo-Konto gehandelt werden
  soll.
- **MetaApi.cloud** (Bridge zu einem beliebigen MT4/5-Broker, hier
  Pepperstone-MT5-Demo): der Token selbst ist kostenlos, aber das
  eigentliche Trading-Account-Hosting kostet laufend (~9 $/Monat + 2,10 $
  einmalig, live im MetaApi-Dashboard bestätigt) - dem Nutzer zu teuer für
  ein Demo-Setup.

Gefunden: die **cTrader Open API** (Pepperstone bietet neben MT4/5 auch
die Plattform cTrader an) ist eine offizielle, komplett kostenlose API -
weder Hosting-Gebühr wie MetaApi noch KYC-Pflicht wie IG. Voraussetzung:
ein separates kostenloses cTrader-Demokonto bei Pepperstone (zusätzlich
zum vorhandenen MT5-Demokonto, da andere Plattform).

**Architekturbruch:** anders als die bisherigen REST-basierten Module
(`tradingbot/oanda.py`, `tradingbot/ig.py`, `tradingbot/metaapi.py` - alle
reines `requests`, kein SDK) ist die cTrader Open API kein REST/JSON-API,
sondern ein **Protobuf-Protokoll über eine dauerhafte TCP-Verbindung**.
Neues Modul `tradingbot/ctrader.py` nutzt deshalb ausnahmsweise die
offizielle Bibliothek `ctrader_open_api` (Twisted-basiert). Um trotzdem
normalen `async`/`await`-Code schreiben zu können (und denselben
Event-Loop wie den bestehenden Telegram-Abruf zu nutzen statt zwei
parallele Event-Loops zu betreiben), wird Twisteds `asyncioreactor`
installiert - Twisted-Deferreds werden per `.asFuture(loop)` awaitet.
`scripts/run_signal_bot.py` wurde komplett auf `async def main()`
umgebaut (vorher rein synchron).

**Konto-Ermittlung automatisch statt manuell gesucht** - Lehre aus dem
MetaApi-Vorfall, wo eine falsch/nicht auffindbare Account-ID zu
stundenlangem Debugging führte (`404 Not Found`, dann `list_accounts()`
als Workaround nachgerüstet): `tradingbot/ctrader.py::ctrader_session()`
ruft nach der App-/Access-Token-Authentifizierung automatisch
`ProtoOAGetAccountListByAccessTokenReq` auf und wählt das erste Konto mit
`isLive == False` - kein manuell zu suchendes `CTRADER_ACCOUNT_ID`-Secret
nötig, und ein Sicherheitsnetz gegen versehentlichen Live-Handel (bricht
mit klarer Fehlermeldung ab, falls kein Demo-Konto gefunden wird).

**UNVERIFIZIERT** (`help.ctrader.com` war in dieser Umgebung per
Netzwerk-Policy nicht abrufbar, nur `pypi.org/project/ctrader-open-api`
lieferte Basis-Infos zu Host/Port/Bibliotheksname) - vor dem ersten
Live-Lauf zwingend zu prüfen:
- Symbolnamen (`signalbot/mapping.py::INDEX_TO_SYMBOL`: `NAS100`, `US30`,
  `UK100`, `GER40`) über `scripts/find_ctrader_symbols.py`.
- Order-Platzierung, Volumen-Konvention (Lots vs. symbol-spezifische
  kleinste Einheit) und SL/TP-Setzung über
  `scripts/place_test_ctrader_order.py`.
- Exakte Feldnamen der Protobuf-Requests in `tradingbot/ctrader.py`
  (`ProtoOANewOrderReq`, `ProtoOAAmendPositionSLTPReq` etc.) - Bestwissen,
  nicht live geprüft.
- `_check_filled_trades` in `scripts/run_signal_bot.py` verwendet aktuell
  den Marktkurs als Näherung für den Ausstiegspreis geschlossener
  Positionen (cTrader bietet, anders als IG/MetaApi, keinen einfachen
  "Schlusskurs der zuletzt geschlossenen Position"-Aufruf ohne die noch
  ungeprüfte Deal-Historie) - führt zu einer leicht ungenauen PnL-Zahl bis
  zur Verifikation, aber keinem Datenverlust.

Neue Skripte: `scripts/ctrader_authorize.py` (einmaliger interaktiver
OAuth-Login, liefert den dauerhaften Refresh-Token - App-Registrierung bei
`connect.spotware.com/apps` ist ebenfalls kostenlos),
`scripts/find_ctrader_symbols.py`, `scripts/place_test_ctrader_order.py`.
Secrets: `CTRADER_CLIENT_ID`/`CTRADER_CLIENT_SECRET`/
`CTRADER_REFRESH_TOKEN` neu in `.github/workflows/signal-bot.yml`,
ersetzen `ALPACA_API_KEY`/`ALPACA_SECRET_KEY` (der ORB-Bot-Workflow
braucht sie weiterhin). Der Parser (`signalbot/parser.py`) erkennt jetzt
wieder alle vier Indizes (NASDAQ/DOW/DAX/FTSE) mit echten
Kanal-Beispielen - vorher (nur Alpaca/QQQ-DIA) auf NASDAQ/DOW beschränkt,
da UK100/DAX bei Alpaca keine Entsprechung hatten.

Wie bei jedem vorigen Broker-Wechsel gilt die Regel: **kein Merge nach
`master`, solange die drei Secrets nicht vom Nutzer hinterlegt sind** -
sonst läuft der Workflow automatisiert und dauerhaft fehlschlagend. Der
alte OANDA/IG/MetaApi-Feature-Branch (`claude/signal-bot-datetime-offset-diyxej`)
sollte auf Nutzerwunsch gelöscht werden (nie gemergt, Broker-Vergleich
abgeschlossen) - das direkte Löschen aus dieser Umgebung heraus wurde vom
Git-Proxy mit 403 abgelehnt, der Nutzer muss ihn manuell im GitHub-Web-UI
entfernen.

**Nachtrag 28.08.2026, direkt danach: Bot reagierte auf 1 von über 5
gesendeten Signalen nicht, und ignorierte eine Kanal-Schliess-Anweisung
für zwei offene Trades.** Nutzer-Feedback (noch ohne exakte
Nachrichtentexte, die folgen bei Gelegenheit): der Bot hatte reagiert,
als der Kanal seine eigenen zwei Dow-Jones-Trades per Nachricht schloss -
der Bot selbst tat nichts und wartete stattdessen passiv auf seinen
eigenen Stop, statt der Anweisung zu folgen. Zusätzlich wurde nur 1 von
über 5 in der Zwischenzeit gesendeten Einstiegssignalen tatsächlich
gehandelt.

Zwei strukturelle Fixes, die ohne die noch ausstehenden echten
Nachrichtentexte möglich waren:

1. **Schliess-Anweisungen werden jetzt befolgt.** Bisher wurde eine
   Nachricht wie "CLOSE TRADE ALERT... CLOSING DAX INDEX trade now"
   bewusst als Nicht-Signal behandelt (ursprüngliche Design-Entscheidung:
   der Bot verwaltet seine Position ausschließlich über den eigenen
   Stop/Ziel) - das war offenbar der falsche Kompromiss. `signalbot/parser.py`
   hat jetzt ein neues `"action": "open" | "close" | null`-Feld im
   JSON-Schema; das bereits vorhandene reale Beispiel ("CLOSE TRADE
   ALERT...") liefert jetzt `action: "close"`, `scripts/run_signal_bot.py`
   schließt daraufhin sofort die betroffene Position (`_close_one_open`),
   statt nichts zu tun. Nennt eine Schliess-Nachricht KEIN konkretes
   Instrument (z. B. "closing both trades now" ohne Namen - vermutlich der
   Fall aus dem Nutzer-Feedback), wird sie bewusst NICHT gehandelt (kein
   Raten, welches Instrument gemeint ist) - das braucht ein echtes
   Beispiel aus dem Kanal, um sauber ins Prompt aufgenommen zu werden.
2. **Verschluckte Gemini-Fehler von echten Nicht-Signalen unterschieden.**
   `parse_signal_message()` gab bisher bei jedem Fehler (Netzwerk,
   Ratenlimit, unparsebare Antwort) `None` zurück - identisch zu einer
   echten "kein Signal"-Klassifizierung. Das könnte die 1-von-5-Quote
   erklären, war aber nicht nachprüfbar. Jetzt löst ein echter
   Gemini-Fehler `GeminiError` aus, `scripts/run_signal_bot.py` fängt das
   pro Nachricht ab, zählt es als API-Fehler und protokolliert es explizit
   als `gemini_fehler` statt als `kein_signal`.

**Neu: `signalbot/channel_log.py` + `signal_channel_log.csv`.** Auf
Nutzerwunsch protokolliert der Bot jetzt JEDE ausgewertete Kanal-Nachricht
der letzten sieben Tage (nicht nur die, die zu einem Trade führten) -
inklusive Originaltext, geparstem JSON und einem kurzen Grund-Code
(`trade_eroeffnet`, `kein_signal`, `bereits_offen`,
`index_nicht_unterstuetzt`, `gemini_fehler`, `kurs_nicht_ladbar`,
`kein_gueltiges_signal`, `volumen_zu_klein`, `order_fehlgeschlagen`,
`trade_geschlossen`, `schliessung_ohne_position`). Ältere Zeilen werden
beim Schreiben automatisch verworfen (sieben Tage Aufbewahrung), damit die
Datei nicht unbegrenzt wächst. Zweck: Ursachen für ausgelassene/verzögerte
Signale sind jetzt direkt in der Datei nachvollziehbar, ohne extra
`scripts/dump_channel_history.py` per GitHub-Actions-Lauf zu triggern -
liefert außerdem die Rohdaten für echte Beispielnachrichten (z. B. die
noch ausstehende "closing both trades"-Formulierung), sobald sie im Kanal
auftauchen. Die Datei wird wie `signal_state.json`/`signal_trades.csv` vom
Workflow zurückcommittet.

**Nachtrag 28.08.2026, direkt danach: Gemini bekommt jetzt Gesprächsverlauf
als Kontext mit.** Nutzer-Feedback: `parse_signal_message()` wertete bisher
jede Nachricht komplett isoliert aus - eine Schließ-Anweisung ohne erneut
genanntes Instrument (z. B. "closing the trade now"/"closing both trades
now", genauer Wortlaut vom Nutzer noch ausstehend) konnte deshalb nicht
aufgelöst werden, selbst wenn aus dem Gesprächsverlauf offensichtlich
gewesen wäre, welche zuvor eröffnete Position gemeint ist.

- **`signalbot/channel_log.py::recent_message_texts()`** (neu): liefert die
  letzten `limit` (Standard 15) protokollierten Nachrichtentexte vor einem
  Zeitpunkt, chronologisch - nutzt die ohnehin durch `signal_channel_log.csv`
  gespeicherte Sieben-Tage-Historie (siehe oben), kein zusätzlicher
  Telegram-Abruf nötig.
- **`signalbot/parser.py::parse_signal_message()`** akzeptiert jetzt einen
  optionalen `history`-Parameter - wird der Gemini-Anfrage als klar
  abgegrenzter Kontextblock vorangestellt ("BISHERIGE NACHRICHTEN... ===
  NEUE NACHRICHT ==="), mit der expliziten Anweisung, NUR die neue
  Nachricht zu klassifizieren, den Kontext aber zur Auflösung von
  Schließ-Anweisungen ohne erneut genanntes Instrument zu nutzen (das
  jüngste noch offene "BOUGHT LONG"/"SOLD SHORT" je Instrument in der
  Historie identifizieren). Bleibt die Zuordnung nach Kontext uneindeutig,
  bleibt die bisherige Regel bestehen: kein Raten, `is_signal: false`.
- `scripts/run_signal_bot.py` holt vor jedem `parse_signal_message()`-Aufruf
  die Historie über `recent_message_texts()` (Zeitpunkt der jeweiligen
  Nachricht als Grenze, keine zukünftigen Nachrichten als Kontext).
- Nebeneffekt: da `signal_channel_log.csv` ALLE ausgewerteten Nachrichten
  enthält (nicht nur Signale), lernt Gemini nebenbei auch den allgemeinen
  Nachrichtenaufbau des Kanals kennen (Nutzerwunsch: "die anderen
  Nachrichten sind dafür da, damit er weiß, wie andere Nachrichten
  aufgebaut sind").

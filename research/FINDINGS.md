# Strategiesuche auf QQQ 5-Min, 2021–2026

Ausgangslage: Die in [trading-bot-spec.md](../trading-bot-spec.md), Abschnitt 1,
spezifizierte "Wickless Candle Retest"-Strategie hält einer Nachprüfung über
den vollen, durchgehenden Zeitraum 2021–2026 nicht stand (Abschnitt 9 der
Spec, vermutlich In-Sample-Overfitting bei der Parameterwahl). Auftrag: eine
eigene Strategie finden, die auf den kompletten sechs Jahren funktioniert,
nicht nur auf einzelnen Stichproben.

## Methodik

Um denselben Fehler nicht zu wiederholen, strikte Trennung:

- **Training:** 2021-01-04 bis 2024-12-31 (1005 Handelstage). Hier werden
  Kandidaten entwickelt und beurteilt.
- **Test:** 2025-01-02 bis 2026-08-18 (407 Handelstage). Wird während der
  Entwicklung nicht angeschaut, erst ein einziges Mal am Ende zur Kontrolle.

Parameter der Kandidaten sind vorab fixiert (Standardwerte, keine Suche über
viele Kombinationen). Datenquelle: Twelve Data, `time_series`, 5-Min-Bars,
reguläre Session (09:30–15:55 ET), lokal gecacht in
[data/qqq_5min_2021_2026.csv](../data/qqq_5min_2021_2026.csv) (nicht im
Repository, siehe `.gitignore` — reproduzierbar über
[scripts/fetch_and_cache_data.py](../scripts/fetch_and_cache_data.py)).

Exit-Modell identisch zum Original-Backtest
([tradingbot/backtest.py](../tradingbot/backtest.py)): Stop/Ziel ab der
Kerze nach dem Einstieg geprüft, keine Übernachtpositionen, bei
gleichzeitiger Berührung von Stop und Ziel in derselben Kerze zählt
konservativ der Stop, Zwangsschluss zum Handelsende ohne Berührung.

Code: [research/engine.py](engine.py) (Backtest-Engine, getrennt von
`tradingbot/`, das bleibt die eingefrorene Original-Strategie),
[research/strategies.py](strategies.py) (Kandidaten),
[scripts/research_strategies.py](../scripts/research_strategies.py) (Lauf).

## Kandidaten

### Opening Range Breakout (ORB)

Erste 30 Minuten des Handelstags (09:30–10:00, 6 Kerzen) definieren eine
Handelsspanne. Erster Ausbruch darüber/darunter danach löst einen Trade aus,
höchstens einer pro Tag. Einstieg am Open der Kerze nach der
Ausbruchskerze. Stop auf der Gegenseite der Spanne, Ziel bei 2:1 CRV
(Breakeven-Trefferquote 33,3 %).

### MA-Reversion

Einstieg gegen eine Abweichung von mehr als dem 1,5-fachen der
durchschnittlichen Kerzenrange (20-Kerzen-Fenster) vom 20-Kerzen-Durchschnitt,
Richtung Durchschnitt. Ein Signal pro Ausschlag (kein erneutes Signal, bis
der Kurs wieder innerhalb der normalen Bandbreite war). Stop beim 1-fachen
der Durchschnitts-Range, Ziel bei 1,5:1 CRV (Breakeven-Trefferquote 40 %).

### Donchian-Trendfolge (dritter Kandidat, mehrtägig)

20-Tage-Ausbruchskanal für den Einstieg, 10-Tage-Gegenkanal oder 2×ATR-Stop
für den Ausstieg, klassische "Turtle Trading"-Standardwerte, nicht
getunt. Andere Mechanik als die beiden Intraday-Kandidaten: Positionen
laufen über mehrere Tage (Ø 26-31 Tage Haltedauer), kein festes CRV-Ziel,
kein Tagesende-Zwang. Code: [research/trend_following.py](trend_following.py),
Lauf: [scripts/research_trend_following.py](../scripts/research_trend_following.py).

| Zeitraum | Trades | Trefferquote | Ø R/Trade | Gesamt R |
|---|---|---|---|---|
| Training 2021–2024 | 33 | 45,5 % | +0,251 | +8,29 |
| Test 2025–2026 | 13 | 30,8 % | +0,050 | +0,65 |

**Nicht belastbar, in beide Richtungen.** Bleibt im Test positiv, kein
Einbruch, aber bei nur 13 Testtrades ist der Rückgang der Trefferquote
(45,5 % → 30,8 %) genauso gut Stichprobenrauschen wie ein echtes
Nachlassen. Trendfolge-Systeme handeln naturgemäß selten (nur bei echten
Ausbrüchen), 5,6 Jahre reichen dafür nicht für eine verlässliche Aussage,
das bräuchte eher Jahrzehnte. Weder als validiert noch als widerlegt zu
behandeln, einfach unentscheidbar mit diesen Daten.

## Ergebnis

| Strategie | Zeitraum | Trades | Trades/Tag | Trefferquote | Ø R/Trade | Gesamt R |
|---|---|---|---|---|---|---|
| Opening Range Breakout | Training 2021–2024 | 988 | 0,98 | 50,8 % | +0,078 | +76,69 |
| Opening Range Breakout | Test 2025–2026 | 402 | 0,99 | **51,7 %** | +0,048 | +19,22 |
| MA-Reversion | Training 2021–2024 | 6372 | 6,34 | 41,9 % | +0,037 | +234,08 |
| MA-Reversion | Test 2025–2026 | 2540 | 6,24 | **41,3 %** | +0,020 | +50,72 |

Beide halten sich im Test, kein Einbruch wie bei der Original-Strategie.
ORB verbessert die Trefferquote sogar leicht (50,8 % → 51,7 %) bei stabiler
Handelsfrequenz, klarer Abstand zur Gewinnschwelle (33,3 %). MA-Reversion
hält sich auch, aber mit deutlich dünnerer Marge zur eigenen
Gewinnschwelle (40 %) und sinkendem Ø R/Trade im Test.

### Kostenrobustheit (alle 6 Jahre, 0,02/0,05 Punkte Kosten pro Trade)

| Strategie | Trades | Ø Risiko | Ø Punkte/Trade | Brutto | Netto (0,02) | Netto (0,05) |
|---|---|---|---|---|---|---|
| Opening Range Breakout | 1390 | 3,257 Pkt. | 0,184 | +256,43 | +228,63 | +186,93 |
| MA-Reversion | 8914 | 0,641 Pkt. | 0,017 | +148,12 | **−30,16** | **−297,58** |

Das entscheidet die Sache: ORBs Vorteil ist in absoluten Punkten groß
genug (durchschnittliches Risiko 3,26 Punkte, viel weiter gestreute
Stops), dass 0,02–0,05 Punkte Kosten kaum ins Gewicht fallen. Bei
MA-Reversion ist der Vorteil pro Trade so dünn (0,017 Punkte, ähnliche
Größenordnung wie beim Risiko der Original-Strategie), dass schon die
kleinere Kostenannahme das Ergebnis ins Minus dreht. Nominell positive
Trefferquote und R/Trade reichen also nicht, wenn der Vorteil in Punkten
kleiner ist als plausible Slippage, dasselbe Problem, vor dem die Spec
bei der Original-Strategie selbst gewarnt hatte (Abschnitt 4). MA-Reversion
scheidet damit aus.

## Lohnt sich ein Bot gegenüber Buy-and-Hold?

Positionsgröße wie im Original-Spec: 1 % Risiko des Kontostands pro Trade,
compoundiert, Start 100.000 $.

| | Gesamt (5,6 Jahre) | Pro Jahr | Max. Drawdown |
|---|---|---|---|
| QQQ Buy-and-Hold | +128,3 % | 15,8 % | 37,6 % (Nov. 2021–Okt. 2022) |
| ORB-Bot, ohne Hebel (eigenes Kapital) | +79,9 % | 11,0 % | 8,2 % |
| ORB-Bot, mit Hebel (wie die 1-%-Regel es verlangt) | +145,0 % | 17,3 % | 12,5 % |

Wichtiger Fund nebenbei: Die reine 1-%-Risiko-Positionsgrößenformel
verlangt bei dieser Strategie im Schnitt das 1,6-fache des Kapitals als
Positionsgröße (Maximum 5,9-fach, 77 % der Trades über 1x), das ist
stillschweigend Margin, keine konservative, vollständig gedeckte
Positionsgröße, wie man bei "1 % Risiko" vielleicht annehmen würde.

Ohne Hebel bleibt der Bot in absoluter Rendite hinter Buy-and-Hold zurück,
weil er nur einen Bruchteil der Zeit investiert ist, während Buy-and-Hold
durchgehend voll im Markt steckt, und 2021–2026 ein außergewöhnlich
starker Zeitraum für QQQ war. Der Bot hätte dabei aber nur 8,2 % maximalen
Rückgang gehabt statt 37,6 % (der QQQ-Einbruch 2022), ein Risikoprofil,
kein Renditevorteil. Mit Hebel schlägt der Bot Buy-and-Hold auch bei der
Rendite, bei weiterhin viel kleinerem Drawdown, allerdings ohne
Berücksichtigung von Margin-Zinsen und Nachschuss-Risiko.

## Einordnung

Von drei Kandidaten bleibt einer klar übrig: **Opening Range Breakout**,
positiv auf Training und Test, Trefferquote im Test sogar leicht über dem
Trainingswert, deutlicher Sicherheitsabstand zur Gewinnschwelle (51,1 %
gegen 33,3 % über alle 6 Jahre), und der Vorteil ist in absoluten Punkten
groß genug, um realistische Handelskosten zu überstehen. **MA-Reversion**
scheidet trotz nominell positivem Ergebnis aus, der Vorteil ist zu dünn
für reale Kosten. **Donchian-Trendfolge** ist weder bestätigt noch
widerlegt, die Stichprobe ist mit 46 Trades insgesamt zu klein für eine
verlässliche Aussage.

Das ist kein Beweis für eine dauerhafte Edge, nur ein einziger
Train/Test-Split, kein Walk-Forward über mehrere Fenster, und ohne
Berücksichtigung von Slippage/Lauf-Verspätung, wie sie ein echter
Live-Test nach Abschnitt 4/6 der Spec zeigen würde. Aber: ORB hat genau
die Nachprüfung bestanden, an der die Original-Strategie gescheitert ist,
mit größerem Sicherheitsabstand und ohne erkennbares Zeichen von
Überanpassung.

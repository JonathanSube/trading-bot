"""Kandidatenstrategien fuer die Suche nach einer robusten Edge.

Parameter sind bewusst vorab fixiert (Standardwerte aus der
Trading-Literatur), nicht auf den Daten optimiert - genau das war der
Fehler bei der Original-Strategie (siehe trading-bot-spec.md Abschnitt 9,
Overfitting durch Parametersuche auf begrenzter Stichprobe). Wer hier
nachtraeglich Parameter durchsucht, um die Zahlen zu verbessern, macht
denselben Fehler nochmal.
"""

from collections import defaultdict

from research.daily_bars import to_daily_bars
from research.engine import Signal
from tradingbot.setup_detection import Bar, Direction

# Opening Range Breakout
OR_BARS = 6  # erste 30 Minuten (09:30-10:00)
ORB_TARGET_R = 2.0
TIGHT_STOP_FRACTION = 0.5  # Testvariante: Stop auf halbem Weg zwischen Einstieg und der Original-Marke

# Testvariante: Ziel an der ueblichen Tagesbewegung ausgerichtet statt an
# der Eroeffnungsspanne (siehe Diskussion zum offenen Trade vom 25.08.2026)
ADAPTIVE_TARGET_DAYS = 10  # rollierender Schnitt der letzten 10 Handelstage
ADAPTIVE_TARGET_MULT = 0.5  # Ziel = halbe durchschnittliche Tagesspanne vom Einstieg entfernt

# Gleitender-Durchschnitt-Reversion
MA_WINDOW = 20
STRETCH_MULT = 1.5  # Einstieg, wenn Abstand zum SMA > 1.5x Durchschnitts-Range
STOP_MULT = 1.0
REVERSION_TARGET_R = 1.5


def _by_day(bars: list[Bar]) -> dict:
    days = defaultdict(list)
    for i, b in enumerate(bars):
        days[b.timestamp.date()].append(i)
    return days


def opening_range_breakout(bars: list[Bar]) -> list[Signal]:
    """Erster Ausbruch aus der Handelsspanne der ersten OR_BARS Kerzen des
    Tages, hoechstens ein Trade pro Tag. Einstieg am Open der Kerze NACH
    der Ausbruchskerze (kein Blick in die Zukunft: das Signal steht erst
    fest, wenn die Ausbruchskerze abgeschlossen ist)."""
    signals = []
    for day, idxs in _by_day(bars).items():
        if len(idxs) <= OR_BARS + 1:
            continue

        or_bars = [bars[i] for i in idxs[:OR_BARS]]
        or_high = max(b.high for b in or_bars)
        or_low = min(b.low for b in or_bars)

        for pos in range(OR_BARS, len(idxs) - 1):
            i = idxs[pos]
            bar = bars[i]
            entry_bar = bars[i + 1]
            if entry_bar.timestamp.date() != day:
                break

            if bar.close > or_high:
                entry = entry_bar.open
                stop = or_low
                risk = entry - stop
                if risk <= 0:
                    break
                signals.append(Signal(Direction.LONG, i + 1, entry, stop,
                                       entry + ORB_TARGET_R * risk, entry_bar.timestamp))
                break
            if bar.close < or_low:
                entry = entry_bar.open
                stop = or_high
                risk = stop - entry
                if risk <= 0:
                    break
                signals.append(Signal(Direction.SHORT, i + 1, entry, stop,
                                       entry - ORB_TARGET_R * risk, entry_bar.timestamp))
                break

    return signals


def opening_range_breakout_tight_stop(bars: list[Bar]) -> list[Signal]:
    """Testvariante von opening_range_breakout: derselbe Ausbruch, aber
    Stop bei TIGHT_STOP_FRACTION des Abstands zur urspruenglichen
    Gegenseiten-Marke statt der vollen Distanz - damit ruecken Stop UND
    (bei gleichbleibendem 2:1-CRV) auch das Ziel naeher an den Einstieg.
    Einziger Unterschied zum Original, um die Frage "hilft ein engerer
    Stop/Ziel" isoliert zu pruefen, keine weitere Parametersuche."""
    signals = []
    for day, idxs in _by_day(bars).items():
        if len(idxs) <= OR_BARS + 1:
            continue

        or_bars = [bars[i] for i in idxs[:OR_BARS]]
        or_high = max(b.high for b in or_bars)
        or_low = min(b.low for b in or_bars)

        for pos in range(OR_BARS, len(idxs) - 1):
            i = idxs[pos]
            bar = bars[i]
            entry_bar = bars[i + 1]
            if entry_bar.timestamp.date() != day:
                break

            if bar.close > or_high:
                entry = entry_bar.open
                full_risk = entry - or_low
                if full_risk <= 0:
                    break
                risk = full_risk * TIGHT_STOP_FRACTION
                stop = entry - risk
                signals.append(Signal(Direction.LONG, i + 1, entry, stop,
                                       entry + ORB_TARGET_R * risk, entry_bar.timestamp))
                break
            if bar.close < or_low:
                entry = entry_bar.open
                full_risk = or_high - entry
                if full_risk <= 0:
                    break
                risk = full_risk * TIGHT_STOP_FRACTION
                stop = entry + risk
                signals.append(Signal(Direction.SHORT, i + 1, entry, stop,
                                       entry - ORB_TARGET_R * risk, entry_bar.timestamp))
                break

    return signals


def ma_reversion(bars: list[Bar]) -> list[Signal]:
    """Einstieg gegen eine ueberdehnte Abweichung vom gleitenden
    MA_WINDOW-Kerzen-Durchschnitt, Richtung Durchschnitt. Einstieg am Open
    der Folgekerze.

    "armed" verhindert, dass eine anhaltende Streckung (z. B. ein echter
    Trend) auf jeder einzelnen Kerze ein neues Signal ausloest: Nach einem
    Signal muss der Kurs erst wieder innerhalb der normalen Bandbreite
    gewesen sein, bevor das naechste Signal scharf ist."""
    signals = []
    armed = True

    for i in range(MA_WINDOW, len(bars) - 1):
        bar = bars[i]
        entry_bar = bars[i + 1]
        if entry_bar.timestamp.date() != bar.timestamp.date():
            armed = True  # neuer Handelstag, neu bewaffnen
            continue

        window = bars[i - MA_WINDOW:i]
        sma = sum(b.close for b in window) / MA_WINDOW
        avg_range = sum(b.high - b.low for b in window) / MA_WINDOW
        if avg_range <= 0:
            continue

        deviation = bar.close - sma
        stretched = abs(deviation) > STRETCH_MULT * avg_range

        if not stretched:
            armed = True
            continue
        if not armed:
            continue

        armed = False
        if deviation < 0:
            entry = entry_bar.open
            stop = entry - STOP_MULT * avg_range
            risk = entry - stop
            signals.append(Signal(Direction.LONG, i + 1, entry, stop,
                                   entry + REVERSION_TARGET_R * risk, entry_bar.timestamp))
        else:
            entry = entry_bar.open
            stop = entry + STOP_MULT * avg_range
            risk = stop - entry
            signals.append(Signal(Direction.SHORT, i + 1, entry, stop,
                                   entry - REVERSION_TARGET_R * risk, entry_bar.timestamp))

    return signals


def opening_range_breakout_adaptive_target(bars: list[Bar]) -> list[Signal]:
    """Testvariante von opening_range_breakout: Stop bleibt auf der
    Gegenseite der Eroeffnungsspanne wie im Original, aber das Ziel
    orientiert sich an der ueblichen Tagesbewegung (rollierender
    ADAPTIVE_TARGET_DAYS-Tage-Schnitt der taeglichen High-Low-Spanne aus
    den VORHERIGEN Tagen, kein Blick in die Zukunft) statt an der oft viel
    schmaleren/breiteren Eroeffnungsspanne selbst. CRV ist dadurch nicht
    mehr fix 2:1, sondern haengt vom Verhaeltnis der beiden Groessen ab."""
    daily = to_daily_bars(bars)
    avg_range_by_day = {}
    for idx in range(ADAPTIVE_TARGET_DAYS, len(daily)):
        window = daily[idx - ADAPTIVE_TARGET_DAYS:idx]
        avg_range_by_day[daily[idx].date] = sum(d.high - d.low for d in window) / ADAPTIVE_TARGET_DAYS

    signals = []
    for day, idxs in _by_day(bars).items():
        if len(idxs) <= OR_BARS + 1 or day not in avg_range_by_day:
            continue

        or_bars = [bars[i] for i in idxs[:OR_BARS]]
        or_high = max(b.high for b in or_bars)
        or_low = min(b.low for b in or_bars)
        target_distance = ADAPTIVE_TARGET_MULT * avg_range_by_day[day]

        for pos in range(OR_BARS, len(idxs) - 1):
            i = idxs[pos]
            bar = bars[i]
            entry_bar = bars[i + 1]
            if entry_bar.timestamp.date() != day:
                break

            if bar.close > or_high:
                entry = entry_bar.open
                stop = or_low
                risk = entry - stop
                if risk <= 0:
                    break
                signals.append(Signal(Direction.LONG, i + 1, entry, stop,
                                       entry + target_distance, entry_bar.timestamp))
                break
            if bar.close < or_low:
                entry = entry_bar.open
                stop = or_high
                risk = stop - entry
                if risk <= 0:
                    break
                signals.append(Signal(Direction.SHORT, i + 1, entry, stop,
                                       entry - target_distance, entry_bar.timestamp))
                break

    return signals

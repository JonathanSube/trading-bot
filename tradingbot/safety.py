"""Sicherheitsschalter und Kill-Switch, siehe trading-bot-spec.md Abschnitt 3.

Reine Entscheidungslogik: liest tradingbot.state.BotState und aktuelle
Kontodaten, entscheidet ob und warum gestoppt werden soll. Aendert selbst
keinen Zustand.

Interpretation zweier Unklarheiten aus der Spec-Tabelle, dokumentiert statt
stillschweigend entschieden:
- "Trades pro Tag" und "Datenluecke" reagieren laut Tabelle nur mit
  "keine neuen Einstiege", nicht mit Positionsschluss - anders als die
  vier als "einstellen"/"stoppen" bezeichneten Ausloeser, fuer die
  "bei jedem Stopp: offene Positionen schliessen" gilt.
- "API-Fehler -> Bot stoppen" wird hier NICHT als dauerhafter Stopp (wie
  Gesamtverlust) behandelt, weil die Spec dafuer kein "dauerhaft"/
  "manuelles Reset" nennt. Der Zaehler setzt sich stattdessen automatisch
  zurueck, sobald ein API-Aufruf wieder gelingt (state.record_api_success).
"""

from dataclasses import dataclass
from pathlib import Path

from tradingbot.state import BotState

DAILY_LOSS_LIMIT = -0.03
TOTAL_LOSS_LIMIT = -0.15
LOSS_STREAK_LIMIT = 8
MAX_TRADES_PER_DAY = 15
API_ERROR_LIMIT = 5
DATA_GAP_LIMIT_MINUTES = 10.0


@dataclass(frozen=True)
class SafetyCheck:
    block_new_entries: bool
    close_open_positions: bool
    permanent: bool
    reason: str


def check_kill_switch(kill_switch_path: Path) -> SafetyCheck | None:
    """Abschnitt 3: Datei oder Repository-Secret STOP beendet den Handel,
    noch bevor irgendeine Order angefasst wird."""
    if kill_switch_path.exists():
        return SafetyCheck(True, True, True, f"Kill-Switch-Datei {kill_switch_path} vorhanden")
    return None


def check_safety_switches(
    state: BotState, current_equity: float, data_gap_minutes: float = 0.0
) -> SafetyCheck | None:
    """Prueft in Reihenfolge fallender Schwere, liefert die erste
    zutreffende SafetyCheck oder None, wenn nichts ausgeloest ist."""

    if state.stopped_permanently:
        return SafetyCheck(True, True, True,
                            "Bot bereits dauerhaft gestoppt, manuelles Reset noetig")

    if state.initial_equity:
        total_loss = (current_equity - state.initial_equity) / state.initial_equity
        if total_loss <= TOTAL_LOSS_LIMIT:
            return SafetyCheck(True, True, True,
                                f"Gesamtverlust {total_loss*100:.1f}% "
                                f"(Grenze {TOTAL_LOSS_LIMIT*100:.0f}%)")

    if state.start_of_day_equity:
        daily_loss = (current_equity - state.start_of_day_equity) / state.start_of_day_equity
        if daily_loss <= DAILY_LOSS_LIMIT:
            return SafetyCheck(True, True, False,
                                f"Tagesverlust {daily_loss*100:.1f}% "
                                f"(Grenze {DAILY_LOSS_LIMIT*100:.0f}%)")

    if state.counters.consecutive_losses >= LOSS_STREAK_LIMIT:
        return SafetyCheck(True, True, False,
                            f"{state.counters.consecutive_losses} Verlusttrades in Folge")

    if state.counters.consecutive_api_errors >= API_ERROR_LIMIT:
        return SafetyCheck(True, True, False,
                            f"{state.counters.consecutive_api_errors} API-Fehler in Folge")

    if state.counters.trades_today >= MAX_TRADES_PER_DAY:
        return SafetyCheck(True, False, False,
                            f"{state.counters.trades_today} Trades heute erreicht")

    if data_gap_minutes > DATA_GAP_LIMIT_MINUTES:
        return SafetyCheck(True, False, False,
                            f"Datenluecke {data_gap_minutes:.0f} Minuten")

    return None

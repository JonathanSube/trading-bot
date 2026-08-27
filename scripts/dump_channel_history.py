"""Einmaliges Diagnose-Skript, nicht Teil des automatisierten Workflows -
gleiche Kategorie wie scripts/place_test_order.py. Laedt die
Kanal-Nachrichten der letzten LOOKBACK_DAYS Tage und schreibt sie als JSON,
um echte Beispielnachrichten fuer den Gemini-Prompt zu sammeln (siehe
signalbot/parser.py) statt sie zu erfinden. Ausgefuehrt ueber
.github/workflows/dump-channel-history.yml (workflow_dispatch), Ergebnis
als Build-Artefakt hochgeladen.
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

from signalbot.telegram_signals import fetch_new_messages

LOOKBACK_DAYS = 7
OUTPUT_PATH = ROOT / "channel_history.json"


async def main() -> None:
    channel = os.environ["SIGNAL_CHANNEL"]
    messages = await fetch_new_messages(channel, since_message_id=None, limit=1000)

    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    recent = [
        {"message_id": message_id, "date": date.isoformat(), "text": text}
        for message_id, text, date in messages
        if date >= cutoff
    ]

    OUTPUT_PATH.write_text(json.dumps(recent, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"{len(recent)} Nachrichten der letzten {LOOKBACK_DAYS} Tage nach {OUTPUT_PATH} geschrieben "
          f"(von insgesamt {len(messages)} abgerufenen).")

    # Zusaetzlich direkt ins Job-Log drucken: das per Artefakt hochgeladene
    # channel_history.json liegt in Azure Blob Storage, das aus manchen
    # Umgebungen (z. B. Netzwerk-Policy-Restriktionen) nicht herunterladbar
    # ist - die Log-Ausgabe hier ist der zuverlaessigere Zugriffsweg.
    print("\n--- BEGIN CHANNEL HISTORY JSON ---")
    print(json.dumps(recent, ensure_ascii=False))
    print("--- END CHANNEL HISTORY JSON ---")


if __name__ == "__main__":
    asyncio.run(main())

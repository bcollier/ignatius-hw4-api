"""Print the errors people's browsers reported while opening the app (see
app/routes/client_errors.py). Run: .venv/bin/python tools/client_errors.py [YYYY-MM-DD]"""

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.storage import StorageError, store  # noqa: E402


async def main(day: str) -> None:
    try:
        entries = json.loads(await store.get_file(f"_client_errors/{day}.json"))
    except StorageError:
        return print(f"No errors reported on {day}.")
    for e in entries:
        where = f"{e['where']} " if e["where"] else ""
        home = " (Home Screen app)" if e.get("standalone") else ""
        print(f"{e['at'][11:19]} v{e['version']} {where}{e['message']}{home}\n    {e['agent']}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else f"{datetime.now(timezone.utc):%Y-%m-%d}"))

from __future__ import annotations

import asyncio

from bodrye_bot.config import get_settings
from bodrye_bot.digest.runtime import pulse_digest
from bodrye_bot.main_bot import create_application


async def main() -> None:
    settings = get_settings()
    bot, _dispatcher, worker = create_application(settings)
    await pulse_digest(worker)
    await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())

from __future__ import annotations

from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import CallbackQuery, Message

from bodrye_bot.bootstrap import build_telegram_shell
from bodrye_bot.config import Settings, get_settings
from bodrye_bot.telegram.router import IncomingCallback, IncomingMessage, TelegramShell


def create_router(shell: TelegramShell) -> Router:
    router = Router(name="owner_shell")

    @router.message()
    async def receive_message(message: Message) -> None:
        sender_id = message.from_user.id if message.from_user is not None else 0
        response = await shell.handle(IncomingMessage(sender_id=sender_id, text=message.text or ""))
        await message.answer(response.text)

    @router.callback_query()
    async def receive_callback(callback: CallbackQuery) -> None:
        sender_id = callback.from_user.id
        response = await shell.handle_callback(
            IncomingCallback(sender_id=sender_id, data=callback.data or "")
        )
        await callback.answer()
        if callback.message is not None:
            await callback.message.answer(response.text)

    return router


def create_application(settings: Settings) -> tuple[Bot, Dispatcher]:
    bot = Bot(
        token=settings.telegram_bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher()
    dispatcher.include_router(create_router(build_telegram_shell(settings)))
    return bot, dispatcher


async def main() -> None:
    bot, dispatcher = create_application(get_settings())
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())

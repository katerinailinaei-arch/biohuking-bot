from __future__ import annotations

from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bodrye_bot.bootstrap import build_telegram_shell
from bodrye_bot.config import Settings, get_settings
from bodrye_bot.telegram.channel import AiogramChannelPublisher
from bodrye_bot.telegram.router import (
    IncomingCallback,
    IncomingMessage,
    TelegramResponse,
    TelegramShell,
)


def _markup(response: TelegramResponse) -> InlineKeyboardMarkup | None:
    if not response.buttons:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=button.text, callback_data=button.callback_data)]
            for button in response.buttons
        ]
    )


def create_router(shell: TelegramShell) -> Router:
    router = Router(name="owner_shell")

    @router.message()
    async def receive_message(message: Message) -> None:
        sender_id = message.from_user.id if message.from_user is not None else 0
        response = await shell.handle(IncomingMessage(sender_id=sender_id, text=message.text or ""))
        await message.answer(response.text, reply_markup=_markup(response))

    @router.callback_query()
    async def receive_callback(callback: CallbackQuery) -> None:
        sender_id = callback.from_user.id
        response = await shell.handle_callback(
            IncomingCallback(sender_id=sender_id, data=callback.data or "")
        )
        await callback.answer()
        if callback.message is not None:
            await callback.message.answer(response.text, reply_markup=_markup(response))

    return router


def create_application(settings: Settings) -> tuple[Bot, Dispatcher]:
    bot = Bot(
        token=settings.telegram_bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher()
    shell = build_telegram_shell(
        settings, channel_publisher=AiogramChannelPublisher(bot, settings.telegram_channel_id)
    )
    dispatcher.include_router(create_router(shell))
    return bot, dispatcher


async def main() -> None:
    bot, dispatcher = create_application(get_settings())
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())

from __future__ import annotations

import asyncio
from typing import Any

from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import (
    BotCommand,
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    MessageOriginChannel,
    MessageOriginChat,
    ReplyKeyboardMarkup,
)

from bodrye_bot.bootstrap import build_telegram_shell, build_usage_ledger
from bodrye_bot.config import Settings, get_settings
from bodrye_bot.digest.runtime import build_digest_worker, pulse_digest
from bodrye_bot.digest.worker import DigestWorker
from bodrye_bot.domain.errors import SafeError
from bodrye_bot.ports.transcription import AudioTranscriber
from bodrye_bot.providers.deepgram import DeepgramTranscriber
from bodrye_bot.telegram.channel import AiogramChannelPublisher
from bodrye_bot.telegram.media import (
    audio_mime_type,
    read_clip_bytes,
    read_image_bytes,
    telegram_audio_clip,
    telegram_image_file_id,
)
from bodrye_bot.telegram.router import (
    IncomingCallback,
    IncomingMessage,
    TelegramButton,
    TelegramResponse,
    TelegramShell,
)
from bodrye_bot.telegram.views import (
    MENU_BUDGET,
    MENU_HELP,
    MENU_POST,
    MENU_PUBLISH,
    MENU_REVIEWED,
    MENU_TOPICS,
    MISSING_DEEPGRAM,
    NEUTRAL_DENIAL,
    TOPICS_WAIT_TEXT,
    render_safe_error,
    render_transcript,
)

_MAIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=MENU_TOPICS), KeyboardButton(text=MENU_POST)],
        [KeyboardButton(text=MENU_REVIEWED), KeyboardButton(text=MENU_PUBLISH)],
        [KeyboardButton(text=MENU_BUDGET), KeyboardButton(text=MENU_HELP)],
    ],
    resize_keyboard=True,
    is_persistent=True,
)
_DIGEST_STARTERS = frozenset({"/digest", MENU_TOPICS, "Темы"})
_BOT_COMMANDS = (
    BotCommand(command="start", description="Меню. Для новых — инструкция из 3 сообщений"),
    BotCommand(command="help", description="Помощь: как пользоваться ботом"),
    BotCommand(command="costs", description="Сколько токенов потратил бот"),
    BotCommand(command="settov", description="Загрузить свой тон (примеры постов)"),
    BotCommand(command="digest", description="Прислать темы сейчас"),
    BotCommand(command="draft", description="Черновик: /draft тема"),
    BotCommand(command="reviewed", description="Я проверила факты черновика"),
    BotCommand(command="publish", description="Отправить проверенный пост в канал"),
    BotCommand(command="cover", description="Обложка: пришлите фото, текст — в подписи"),
)


def _inline_button(button: TelegramButton) -> InlineKeyboardButton:
    if button.url:
        return InlineKeyboardButton(text=button.text, url=button.url)
    return InlineKeyboardButton(text=button.text, callback_data=button.callback_data)


def _markup(response: TelegramResponse) -> InlineKeyboardMarkup | ReplyKeyboardMarkup | None:
    if response.show_main_keyboard:
        return _MAIN_KEYBOARD
    if response.button_rows:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [_inline_button(button) for button in row] for row in response.button_rows
            ]
        )
    if not response.buttons:
        return None
    buttons = [_inline_button(button) for button in response.buttons]
    if len(buttons) >= 3:
        rows = [buttons[:2], buttons[2:]]
    else:
        rows = [[button] for button in buttons]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _send_response(message: Message, response: TelegramResponse) -> None:
    markup = _markup(response)
    if response.photo_jpeg:
        try:
            await message.answer_photo(
                photo=BufferedInputFile(response.photo_jpeg, filename="cover.jpg"),
                caption=response.text,
                reply_markup=markup,
            )
            for extra in response.extra_messages:
                await message.answer(extra, reply_markup=markup)
            return
        except Exception:
            pass
    if response.photo_url:
        try:
            await message.answer_photo(photo=response.photo_url)
        except Exception:
            pass
    await message.answer(response.text, reply_markup=markup)
    for extra in response.extra_messages:
        await message.answer(extra, reply_markup=markup)


def create_router(
    shell: TelegramShell,
    *,
    bot: Bot,
    owner_id: int,
    transcriber: AudioTranscriber | None,
) -> Router:
    router = Router(name="owner_shell")

    @router.message()
    async def receive_message(message: Message) -> None:
        sender_id = message.from_user.id if message.from_user is not None else 0
        clip = telegram_audio_clip(message)
        if clip is not None:
            await _handle_audio(message, sender_id, clip)
            return
        text = message.text or message.caption or ""
        command = text.strip().split(" ", 1)[0].split("@", 1)[0]
        if command in _DIGEST_STARTERS or text.strip() in _DIGEST_STARTERS:
            await message.answer(TOPICS_WAIT_TEXT)
        photo: bytes | None = None
        image_id = telegram_image_file_id(message)
        if image_id is not None:
            try:
                photo = await read_image_bytes(bot, image_id)
            except SafeError as error:
                await message.answer(render_safe_error(error))
                return
        response = await shell.handle(
            IncomingMessage(
                sender_id=sender_id,
                text=text,
                forward_from=_forward_handle(message),
                photo=photo,
            )
        )
        await _send_response(message, response)

    async def _handle_audio(message: Message, sender_id: int, clip: Any) -> None:
        if sender_id != owner_id:
            await message.answer(NEUTRAL_DENIAL)
            return
        if transcriber is None:
            await message.answer(MISSING_DEEPGRAM)
            return
        await message.answer("Расшифровываю аудио…")
        try:
            payload = await read_clip_bytes(bot, clip)
            text = await transcriber.transcribe(payload, mime_type=audio_mime_type(clip))
        except SafeError as error:
            await message.answer(render_safe_error(error))
            return
        await message.answer(render_transcript(text))
        response = await shell.handle(
            IncomingMessage(sender_id=sender_id, text=text, transcribed=True)
        )
        await _send_response(message, response)

    @router.callback_query()
    async def receive_callback(callback: CallbackQuery) -> None:
        sender_id = callback.from_user.id
        response = await shell.handle_callback(
            IncomingCallback(sender_id=sender_id, data=callback.data or "")
        )
        if response.toast:
            await callback.answer(response.toast, show_alert=False)
        else:
            await callback.answer()
        if response.skip_message or not isinstance(callback.message, Message):
            return
        await _send_response(callback.message, response)

    return router


def _forward_handle(message: Message) -> str | None:
    origin = message.forward_origin
    chat = None
    if isinstance(origin, MessageOriginChannel):
        chat = origin.chat
    elif isinstance(origin, MessageOriginChat):
        chat = origin.sender_chat
    elif message.forward_from_chat is not None:
        chat = message.forward_from_chat
    if chat is not None:
        username = getattr(chat, "username", None)
        return str(username) if username else ""
    if origin is not None or message.forward_date is not None:
        return ""
    return None


def create_application(settings: Settings) -> tuple[Bot, Dispatcher, DigestWorker]:
    bot = Bot(
        token=settings.telegram_bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher()
    usage_ledger = build_usage_ledger(settings)
    digest_worker = build_digest_worker(settings, bot, usage_ledger=usage_ledger)
    transcriber = None
    if settings.deepgram_api_key is not None:
        transcriber = DeepgramTranscriber(settings.deepgram_api_key)
    shell = build_telegram_shell(
        settings,
        channel_publisher=AiogramChannelPublisher(bot, settings.telegram_channel_id),
        digest_worker=digest_worker,
        usage_ledger=usage_ledger,
    )
    dispatcher.include_router(
        create_router(
            shell,
            bot=bot,
            owner_id=settings.telegram_owner_id,
            transcriber=transcriber,
        )
    )
    return bot, dispatcher, digest_worker


async def main() -> None:
    bot, dispatcher, digest_worker = create_application(get_settings())
    await bot.set_my_commands(list(_BOT_COMMANDS))
    asyncio.create_task(pulse_digest(digest_worker))
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

from __future__ import annotations

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from bodrye_bot.domain.errors import SafeError, SafeErrorCode


class AiogramChannelPublisher:
    def __init__(self, bot: Bot, channel_id: int | str) -> None:
        self._bot = bot
        self._channel_id = channel_id

    async def publish(self, *, owner_id: int, text: str) -> str:
        del owner_id
        try:
            message = await self._bot.send_message(self._channel_id, text)
        except (TelegramForbiddenError, TelegramBadRequest) as error:
            raise SafeError(
                code=SafeErrorCode.PUBLICATION_FAILED,
                message_ru="Бот не смог написать в канал.",
                preserved_ru="Черновик и твоя проверка фактов сохранены.",
                next_action_ru=(
                    "Открой канал «Бодрые люди» → управление → администраторы → "
                    "добавь этого бота с правом публиковать сообщения. Потом снова "
                    "нажми «Опубликовать в канал»."
                ),
                developer_detail=str(error),
            ) from error
        except Exception as error:
            raise SafeError.for_code(
                SafeErrorCode.PUBLICATION_FAILED, developer_detail=type(error).__name__
            ) from error
        return str(message.message_id)

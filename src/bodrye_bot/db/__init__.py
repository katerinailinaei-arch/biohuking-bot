"""PostgreSQL persistence adapters for the application."""

from bodrye_bot.db.base import Base, async_session_factory

__all__ = ["Base", "async_session_factory"]

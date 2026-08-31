from __future__ import annotations

from collections.abc import Callable
from typing import Any, NoReturn

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bodrye_bot.db.models import Source
from bodrye_bot.domain.errors import SafeError, SafeErrorCode
from bodrye_bot.domain.sources import SourceRole
from bodrye_bot.sources.catalog import (
    AccessMethod,
    SourceCatalog,
    SourceDefinition,
    SourceKind,
    SourceStatus,
)


class SqlAlchemySourceCatalogRepository:
    def __init__(self, session: AsyncSession, *, ensure_active: Callable[[], None]) -> None:
        self._session = session
        self._ensure_active = ensure_active

    async def save(self, owner_id: int, catalog: SourceCatalog) -> None:
        self._ensure_active()
        records = list(
            (
                await self._session.execute(
                    select(Source).where(Source.owner_id == owner_id)
                )
            ).scalars()
        )
        stored_by_url = {record.canonical_url: record for record in records}
        canonical_urls = {definition.canonical_url for definition in catalog.sources}
        for record in records:
            if record.canonical_url not in canonical_urls and _is_current(record):
                record.status = SourceStatus.RETIRED.value
                record.config_json = {
                    **record.config_json,
                    "catalog_current": False,
                    "superseded_by_registry_version": catalog.version,
                }
        for definition in catalog.sources:
            stored = stored_by_url.get(definition.canonical_url)
            values = _values(definition, catalog.version)
            if stored is None:
                self._session.add(Source(owner_id=owner_id, **values))
            else:
                for key, value in values.items():
                    setattr(stored, key, value)
        await self._session.flush()

    async def get(self, owner_id: int) -> SourceCatalog:
        self._ensure_active()
        records = list(
            (
                await self._session.execute(
                    select(Source)
                    .where(Source.owner_id == owner_id)
                    .order_by(Source.canonical_url, Source.id)
                )
            ).scalars()
        )
        records = [record for record in records if _is_current(record)]
        if not records:
            raise SafeError.for_code(SafeErrorCode.OWNER_FORBIDDEN)
        versions = {str(record.config_json.get("registry_version", "")) for record in records}
        if len(versions) != 1 or not next(iter(versions)):
            raise SafeError.for_code(SafeErrorCode.INVALID_TRANSITION)
        return SourceCatalog(
            version=next(iter(versions)),
            sources=tuple(_definition(record) for record in records),
        )


def _values(definition: SourceDefinition, registry_version: str) -> dict[str, object]:
    config: dict[str, Any] = dict(definition.config)
    config["registry_version"] = registry_version
    config["source_version"] = definition.version
    config["allowed_hosts"] = list(definition.allowed_hosts)
    config["catalog_current"] = True
    config.pop("superseded_by_registry_version", None)
    return {
        "name": definition.name,
        "canonical_url": definition.canonical_url,
        "source_type": definition.kind.value,
        "roles": [role.value for role in definition.roles],
        "access_method": definition.access_method.value,
        "status": definition.status.value,
        "checked_at": definition.checked_at,
        "license_note": definition.license_note,
        "config_json": config,
    }


def _definition(record: Source) -> SourceDefinition:
    config = dict(record.config_json)
    config.pop("registry_version")
    config.pop("catalog_current", None)
    config.pop("superseded_by_registry_version", None)
    source_version = str(config.pop("source_version"))
    allowed_hosts = tuple(str(host) for host in config.pop("allowed_hosts", []))
    return SourceDefinition(
        name=record.name,
        canonical_url=record.canonical_url,
        kind=SourceKind(record.source_type),
        roles=tuple(SourceRole(role) for role in record.roles),
        access_method=AccessMethod(record.access_method),
        status=SourceStatus(record.status),
        version=source_version,
        license_note=record.license_note or "",
        checked_at=record.checked_at or _missing_checked_at(),
        allowed_hosts=allowed_hosts,
        config={str(key): str(value) for key, value in config.items()},
    )


def _is_current(record: Source) -> bool:
    return record.config_json.get("catalog_current", True) is True


__all__ = ["SqlAlchemySourceCatalogRepository"]


def _missing_checked_at() -> NoReturn:
    raise SafeError.for_code(SafeErrorCode.INVALID_TRANSITION)

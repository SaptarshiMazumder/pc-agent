"""GPU renting, in one module — everything about Vast lives under here and nowhere else.

THIS FILE IS THE ENTIRE CONTRACT. A host wires the module in three calls and knows nothing else
about it: not the marketplace, not the table, not the routes, not the key.

    import vast

    vast.create_sqlite_schema(conn)          # or create_postgres_schema(conn)
    _APP_SECRET_FIELDS += vast.SECRET_FIELDS
    app.include_router(vast.build_router(db=_db, now=_now, require_internal=_require_internal))

WHY THAT MATTERS HERE MORE THAN USUAL. This module spends money. The narrower the surface, the
fewer places a second GPU can be rented from, and the easier it is to answer "what could
possibly have started that instance" with a short list. The module also owns its own schema
version, so installing, upgrading or removing it never touches the host's migration state.

REMOVING IT is deleting this directory and those three lines. Nothing else in the codebase
imports anything under `vast.`
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from vast.infrastructure.postgres_schema import create_schema as create_postgres_schema
from vast.infrastructure.sqlite_schema import create_schema as create_sqlite_schema
from vast.main.vast_factory import (
    SECRET_FIELDS,
    build_reaper,
    build_service,
    settings_from_env,
)
from vast.presentation.vast_router import build_vast_router

__all__ = [
    "SECRET_FIELDS",
    "build_reaper",
    "build_router",
    "build_service",
    "create_postgres_schema",
    "create_sqlite_schema",
    "settings_from_env",
]


def build_router(
    *,
    db: Callable[[], AbstractContextManager[Any]],
    now: Callable[[], float],
    require_internal: Callable[[str | None], bool],
):
    """The /vast/* routes, wired and ready to mount.

    `require_internal` is the host's own answer to "is this caller trusted" — passed in rather
    than re-implemented, so this module cannot disagree with the service it is mounted in about
    who is allowed to spend money.
    """
    return build_vast_router(
        service=build_service(db=db, now=now),
        reaper=build_reaper(db=db, now=now),
        require_internal=require_internal,
    )

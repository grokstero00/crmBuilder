"""FastAPI application. ``crmbuilder-web`` migrates the database and starts the server."""

from __future__ import annotations

import os

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from crmbuilder import __version__
from crmbuilder.bitrix import BitrixClient
from crmbuilder_web.config import Settings
from crmbuilder_web.db import make_engine, make_sessionmaker
from crmbuilder_web.deps import BitrixFactory
from crmbuilder_web.routes import auth, projects
from crmbuilder_web.security import Cipher


def _read_only_client(webhook: str) -> BitrixClient:
    # The web API only reads portals for now; writing comes with apply.
    return BitrixClient(webhook, read_only=True)


_FIELD_NAMES = {
    "email": "Email",
    "password": "Пароль",
    "name": "Название",
    "webhook": "Вебхук",
    "yaml": "Спецификация",
}
_ERROR_TEXTS = {
    "missing": "обязательное поле",
    "string_too_short": "слишком короткое значение",
    "string_too_long": "слишком длинное значение",
    "string_pattern_mismatch": "неверный формат",
}


async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Readable Russian messages for form errors instead of pydantic's English list."""
    problems = []
    for err in exc.errors():
        field = str(err["loc"][-1]) if err.get("loc") else ""
        text = _ERROR_TEXTS.get(err.get("type", ""), err.get("msg", ""))
        problems.append(f"{_FIELD_NAMES.get(field, field)}: {text}" if field else text)
    return JSONResponse({"detail": "; ".join(problems)}, status_code=422)


def create_app(
    settings: Settings | None = None, bitrix_factory: BitrixFactory | None = None
) -> FastAPI:
    settings = settings or Settings.from_env()
    app = FastAPI(title="crmbuilder", version=__version__, docs_url="/api/docs",
                  openapi_url="/api/openapi.json", redoc_url=None)  # fmt: skip
    app.state.settings = settings
    app.state.engine = make_engine(settings.database_url)
    app.state.sessionmaker = make_sessionmaker(app.state.engine)
    app.state.cipher = Cipher(settings.secret_key)
    app.state.bitrix_factory = bitrix_factory or _read_only_client

    app.add_exception_handler(RequestValidationError, _validation_error)
    app.include_router(auth.router, prefix="/api")
    app.include_router(projects.router, prefix="/api")

    @app.get("/api/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return app


def run() -> None:
    import uvicorn

    from crmbuilder_web.migrations import upgrade

    settings = Settings.from_env()
    upgrade(settings.database_url)
    uvicorn.run(
        create_app(settings),
        host=os.environ.get("CRMBUILDER_HOST", "0.0.0.0"),
        port=int(os.environ.get("CRMBUILDER_PORT", "8000")),
        proxy_headers=True,
    )

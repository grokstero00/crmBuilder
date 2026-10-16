"""FastAPI application. ``crmbuilder-web`` migrates the database and starts the server."""

from __future__ import annotations

import os
import threading

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from crmbuilder import __version__, design
from crmbuilder.bitrix import BitrixClient
from crmbuilder_web.config import Settings
from crmbuilder_web.db import make_engine, make_sessionmaker
from crmbuilder_web.deps import BitrixFactory
from crmbuilder_web.jobs import AnthropicFactory, JobRunner, mark_jobs_interrupted
from crmbuilder_web.routes import auth, jobs, projects, runs
from crmbuilder_web.runs import Executor, Runner, mark_interrupted
from crmbuilder_web.security import Cipher


def _client(webhook: str, *, writable: bool = False) -> BitrixClient:
    # Read-only unless a run asks for a writer: the client then refuses every write method.
    return BitrixClient(webhook, read_only=not writable)


def _in_thread(job) -> None:
    threading.Thread(target=job, daemon=True).start()


_FIELD_NAMES = {
    "email": "Email",
    "password": "Пароль",
    "name": "Название",
    "webhook": "Вебхук",
    "yaml": "Спецификация",
    "brief": "ТЗ",
    "context": "О бизнесе клиента",
    "title": "Заголовок",
    "answers": "Ответы",
    "model": "Модель",
}
_ERROR_TEXTS = {
    "missing": "обязательное поле",
    "string_too_short": "слишком короткое значение",
    "string_too_long": "слишком длинное значение",
    "string_pattern_mismatch": "неверный формат",
    "too_long": "слишком много значений",
}


async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Readable Russian messages for form errors instead of pydantic's English list."""
    problems = []
    for err in exc.errors():
        # The last name in the path: ("body", "answers", 0) is about "answers".
        field = next((str(p) for p in reversed(err.get("loc") or ()) if isinstance(p, str)), "")
        text = _ERROR_TEXTS.get(err.get("type", ""), err.get("msg", ""))
        problems.append(f"{_FIELD_NAMES.get(field, field)}: {text}" if field else text)
    return JSONResponse({"detail": "; ".join(problems)}, status_code=422)


def create_app(
    settings: Settings | None = None,
    bitrix_factory: BitrixFactory | None = None,
    run_executor: Executor | None = None,
    anthropic_factory: AnthropicFactory | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    app = FastAPI(title="crmbuilder", version=__version__, docs_url="/api/docs",
                  openapi_url="/api/openapi.json", redoc_url=None)  # fmt: skip
    app.state.settings = settings
    app.state.engine = make_engine(settings.database_url)
    app.state.sessionmaker = make_sessionmaker(app.state.engine)
    app.state.cipher = Cipher(settings.secret_key)
    app.state.bitrix_factory = bitrix_factory or _client
    app.state.runner = Runner(app.state.sessionmaker, app.state.cipher, app.state.bitrix_factory)
    app.state.run_executor = run_executor or _in_thread
    # Design, audit and report jobs run through the same executor as apply runs.
    app.state.anthropic_factory = anthropic_factory or design.make_client
    app.state.job_runner = JobRunner(app.state.sessionmaker, app.state.anthropic_factory)

    app.add_exception_handler(RequestValidationError, _validation_error)
    app.include_router(auth.router, prefix="/api")
    app.include_router(projects.router, prefix="/api")
    app.include_router(runs.router, prefix="/api")
    app.include_router(jobs.router, prefix="/api")
    app.include_router(jobs.ai_router, prefix="/api")

    @app.get("/api/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return app


def run() -> None:
    import uvicorn

    from crmbuilder_web.migrations import upgrade

    settings = Settings.from_env()
    upgrade(settings.database_url)
    app = create_app(settings)
    mark_interrupted(app.state.sessionmaker)
    mark_jobs_interrupted(app.state.sessionmaker)
    uvicorn.run(
        app,
        host=os.environ.get("CRMBUILDER_HOST", "0.0.0.0"),
        port=int(os.environ.get("CRMBUILDER_PORT", "8000")),
        proxy_headers=True,
    )

# syntax=docker/dockerfile:1
# Web API. Build context: the repository root (it needs the engine in src/ too).

FROM python:3.12-slim AS build
WORKDIR /src
RUN pip install --no-cache-dir build
COPY pyproject.toml README.md ./
COPY src ./src
COPY backend ./backend
RUN python -m build --wheel --outdir /dist . \
    && python -m build --wheel --outdir /dist backend

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
COPY --from=build /dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl \
    && useradd --create-home --uid 1000 app
USER app
WORKDIR /home/app
EXPOSE 8000
# Applies database migrations, then starts the server.
CMD ["crmbuilder-web"]

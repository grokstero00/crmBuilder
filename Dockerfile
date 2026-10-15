# syntax=docker/dockerfile:1

FROM python:3.12-slim AS build
# Extra trusted roots from certs/ (antivirus or proxy that inspects HTTPS); usually empty.
COPY --from=certs . /tmp/extra-ca/
RUN cat /etc/ssl/certs/ca-certificates.crt /tmp/extra-ca/*.pem /tmp/extra-ca/*.crt \
    > /etc/ssl/build-ca.pem 2>/dev/null; true
ENV PIP_CERT=/etc/ssl/build-ca.pem
WORKDIR /src
RUN pip install --no-cache-dir build
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m build --wheel --outdir /dist

FROM python:3.12-slim
COPY --from=build /etc/ssl/build-ca.pem /etc/ssl/build-ca.pem
ENV PIP_CERT=/etc/ssl/build-ca.pem
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
COPY --from=build /dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl \
    && useradd --create-home --uid 1000 app \
    && mkdir /workspace && chown app:app /workspace
USER app
WORKDIR /workspace
ENTRYPOINT ["crmbuilder"]
CMD ["--help"]

# Runtime image for the API and the worker. Alpine keeps it small; every dependency
# (temporalio, pydantic-core, ...) ships musllinux wheels, so nothing is compiled.
FROM python:3.12-alpine AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /srv
RUN adduser -D -u 1000 app
COPY pyproject.toml ./
COPY app ./app
RUN pip install .
USER app
EXPOSE 8000
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]

# Test image: Debian slim, because Temporal's time-skipping test server is a glibc binary and
# does not run on Alpine. It is downloaded at build time, so running the tests needs no network.
FROM python:3.12-slim AS test
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /srv
RUN useradd --create-home --uid 1000 app
COPY pyproject.toml ./
COPY app ./app
RUN pip install ".[dev]"
COPY tests ./tests
COPY scripts/fetch_test_server.py ./scripts/
USER app
RUN python scripts/fetch_test_server.py
CMD ["pytest", "-q", "-p", "no:cacheprovider"]

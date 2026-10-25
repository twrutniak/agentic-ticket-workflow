FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /srv
RUN useradd --create-home --uid 1000 app
COPY pyproject.toml ./
COPY app ./app
RUN pip install .
USER app
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]

# Test image: dev dependencies, tests, and Temporal's test server binary downloaded at build
# time, so running the tests needs no network.
FROM base AS test
USER root
RUN pip install ".[dev]"
COPY tests ./tests
COPY scripts/fetch_test_server.py ./scripts/
USER app
RUN python scripts/fetch_test_server.py
CMD ["pytest", "-q", "-p", "no:cacheprovider"]

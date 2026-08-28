# syntax=docker/dockerfile:1.7

ARG PYTHON_IMAGE=python:3.12.13-slim-bookworm
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.5

FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE} AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/eve-rag
WORKDIR /build

COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src

RUN uv sync --frozen --no-dev --extra demo --no-editable

FROM ${PYTHON_IMAGE} AS runtime

ARG APP_UID=10001
ARG APP_GID=10001

ENV PATH=/opt/eve-rag/bin:${PATH} \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

RUN groupadd --gid "${APP_GID}" eve-rag \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" --no-create-home --shell /usr/sbin/nologin eve-rag

COPY --from=builder --chown=${APP_UID}:${APP_GID} /opt/eve-rag /opt/eve-rag
COPY --chown=${APP_UID}:${APP_GID} alembic.ini ./
COPY --chown=${APP_UID}:${APP_GID} migrations ./migrations
COPY --chown=${APP_UID}:${APP_GID} app ./app
COPY --chown=${APP_UID}:${APP_GID} .streamlit ./.streamlit

USER ${APP_UID}:${APP_GID}

EXPOSE 8000 8501

CMD ["uvicorn", "eve_relation_rag.api.app:app", "--host", "0.0.0.0", "--port", "8000"]

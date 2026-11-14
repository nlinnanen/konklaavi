FROM python:3.12-slim
RUN pip install --no-cache-dir uv==0.13.8
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY app ./app
COPY migrations ./migrations
ARG APP_VERSION=dev
ENV APP_VERSION=$APP_VERSION
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM python:3.12-slim
RUN pip install --no-cache-dir uv==0.13.8
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY *.py ./
# The "eval" build context is backend/app/eval/results (compose.yaml, CI). Dev compose mounts the live folder over it.
COPY --from=eval latest.json /eval/latest.json
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true", "--browser.gatherUsageStats=false"]

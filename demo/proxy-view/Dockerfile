# The proxy view with Chromium for rendering. Dependencies come from viewer.py.lock (uv lock --script).
FROM python:3.12-slim
RUN pip install --no-cache-dir uv==0.13.8
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
COPY viewer.py viewer.py.lock ./
RUN uv export --script viewer.py --frozen --no-hashes -o requirements.txt \
 && uv pip install --system --no-cache -r requirements.txt \
 && apt-get update && apt-get install -y --no-install-recommends libatomic1 \
 && playwright install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/*
COPY *.py *.js *.css ./
# Playwright starts Chromium without its own sandbox; the unprivileged container is the boundary.
RUN useradd --create-home --uid 10001 viewer
USER viewer
EXPOSE 8787
CMD ["python", "viewer.py"]

# The proxy view with Chromium for rendering. Build context: the repository root, since the overlay
# bundles the extension's modules from client/. Python dependencies come from viewer.py.lock.

# The overlay: src/overlay.ts with client/entrypoints/content and client/lib, bundled by esbuild.
FROM node:22-bookworm-slim AS overlay
WORKDIR /src
RUN npm install --no-save --no-audit --no-fund --loglevel=error esbuild@0.28.2
COPY client/lib client/lib
COPY client/entrypoints/content client/entrypoints/content
COPY demo/proxy-view/src demo/proxy-view/src
COPY demo/proxy-view/build.mjs demo/proxy-view/
RUN node demo/proxy-view/build.mjs

FROM python:3.12-slim
RUN pip install --no-cache-dir uv==0.13.8
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
COPY demo/proxy-view/viewer.py demo/proxy-view/viewer.py.lock ./
RUN uv export --script viewer.py --frozen --no-hashes -o requirements.txt \
 && uv pip install --system --no-cache -r requirements.txt \
 && apt-get update && apt-get install -y --no-install-recommends libatomic1 \
 && playwright install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/*
COPY demo/proxy-view/*.py demo/proxy-view/*.css demo/proxy-view/sites.toml ./
COPY --from=overlay /src/demo/proxy-view/overlay.bundle.js ./
# Playwright starts Chromium without its own sandbox; the unprivileged container is the boundary.
RUN useradd --create-home --uid 10001 viewer
USER viewer
EXPOSE 8787
CMD ["python", "viewer.py"]

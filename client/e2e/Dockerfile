# Chromium and its system libraries, for running the e2e test where they aren't installed:
#   docker build -t trust-verifier-e2e -f e2e/Dockerfile e2e
#   docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -v "$PWD:/work" -w /work trust-verifier-e2e npm run test:e2e
FROM node:24-bookworm-slim
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN npx -y playwright@1.65.1 install --with-deps chromium && chmod -R a+rX /ms-playwright

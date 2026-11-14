# Build context: the repository root.
FROM node:24-slim
WORKDIR /app/fingerprint-service
COPY fingerprint-service/package.json fingerprint-service/package-lock.json ./
# The packages ship their native binaries; their install scripts only download optional extras.
RUN npm ci --omit=dev --ignore-scripts
COPY client/lib /app/client/lib
COPY fingerprint-service/src ./src
ENV PORT=8100
ENV MODEL_CACHE_DIR=/models
EXPOSE 8100
CMD ["node", "--import", "tsx", "src/server.ts"]

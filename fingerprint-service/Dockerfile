# Build context: the repository root.
FROM node:24-slim
WORKDIR /app/fingerprint-service
COPY fingerprint-service/package.json fingerprint-service/package-lock.json ./
RUN npm ci --omit=dev
COPY client/lib /app/client/lib
COPY fingerprint-service/src ./src
ENV PORT=8100
EXPOSE 8100
CMD ["node", "--import", "tsx", "src/server.ts"]

# KnowledgeAI web (Next.js BFF) image (blueprint §15 M7), built with Next's standalone output:
# only the traced server files ship, no node_modules install at runtime.
# Build from the repo root: docker compose --profile app build web
# Runtime settings are read on the server at request time (API_BASE_URL, GUEST_ASKS_PER_IP_PER_MINUTE), so one
# image runs in every environment. NEXT_PUBLIC_* values are inlined at build time: pass them as build args.

FROM node:24.16.0-bookworm-slim AS deps
WORKDIR /srv/web
COPY package.json package-lock.json .npmrc ./
RUN npm ci --no-audit --no-fund

FROM deps AS build
# Defaults match the code's (lib/support.ts, lib/documents.ts): an empty value would read as "" and 0 MB.
ARG NEXT_PUBLIC_SUPPORT_URL=/support
ARG NEXT_PUBLIC_MAX_UPLOAD_MB=20
ENV NEXT_TELEMETRY_DISABLED=1 \
    NEXT_OUTPUT_STANDALONE=1 \
    NEXT_PUBLIC_SUPPORT_URL=$NEXT_PUBLIC_SUPPORT_URL \
    NEXT_PUBLIC_MAX_UPLOAD_MB=$NEXT_PUBLIC_MAX_UPLOAD_MB
COPY . .
RUN npm run build

FROM node:24.16.0-bookworm-slim AS runtime
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
WORKDIR /srv/web
COPY --from=build --chown=node:node /srv/web/.next/standalone ./
COPY --from=build --chown=node:node /srv/web/.next/static ./.next/static
COPY --from=build --chown=node:node /srv/web/public ./public
USER node
EXPOSE 3000
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:3000/login').then((r) => process.exit(r.ok ? 0 : 1), () => process.exit(1))"]
CMD ["node", "server.js"]

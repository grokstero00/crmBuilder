# syntax=docker/dockerfile:1

FROM node:22-slim AS build
# Extra trusted roots from certs/ (antivirus or proxy that inspects HTTPS); usually empty.
COPY --from=certs . /tmp/extra-ca/
RUN cat /tmp/extra-ca/*.pem /tmp/extra-ca/*.crt > /tmp/extra-ca.pem 2>/dev/null; true
ENV NODE_EXTRA_CA_CERTS=/tmp/extra-ca.pem
RUN corepack enable
WORKDIR /app
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY . .
RUN pnpm build

FROM node:22-slim
WORKDIR /app
COPY --from=build /app/.output ./.output
ENV HOST=0.0.0.0 \
    PORT=3000 \
    NODE_ENV=production
USER node
EXPOSE 3000
CMD ["node", ".output/server/index.mjs"]

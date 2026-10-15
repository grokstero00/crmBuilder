# syntax=docker/dockerfile:1

FROM node:22-slim AS build
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

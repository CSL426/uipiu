FROM node:22-alpine AS builder

WORKDIR /extension
RUN corepack enable && corepack prepare pnpm@10.33.0 --activate
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY . .
RUN pnpm build

FROM builder AS tester
CMD ["pnpm", "test"]

FROM nginx:1.31-alpine AS runner
COPY --from=builder /extension /usr/share/nginx/html

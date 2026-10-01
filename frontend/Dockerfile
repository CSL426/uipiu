FROM node:22-alpine AS builder

WORKDIR /frontend
RUN corepack enable && corepack prepare pnpm@12.4.2 --activate
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY . .
RUN pnpm build

FROM builder AS tester
CMD ["pnpm", "exec", "vitest", "run"]

FROM nginx:1.31-alpine AS runner
COPY --from=builder /frontend/dist /usr/share/nginx/html
COPY docker/entrypoint.sh /usr/local/bin/frontend-entrypoint
RUN chmod +x /usr/local/bin/frontend-entrypoint \
    && printf 'server { listen 80; server_name _; root /usr/share/nginx/html; index index.html; location / { try_files $uri $uri/ /index.html; } }\n' > /etc/nginx/conf.d/default.conf
EXPOSE 80
ENTRYPOINT ["frontend-entrypoint"]
CMD ["nginx", "-g", "daemon off;"]

# Web UI: static build served by nginx, which also proxies REST + WS to the
# orchestrator so the browser only ever talks same-origin.
FROM node:22-bookworm-slim AS build
WORKDIR /web
COPY package.json package-lock.json* ./
RUN npm install
COPY tsconfig.json vite.config.ts index.html ./
COPY src ./src
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /web/dist /usr/share/nginx/html

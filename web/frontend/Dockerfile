# Stage 1: Build
FROM node:20-alpine AS builder
WORKDIR /app
COPY web/frontend/package.json web/frontend/package-lock.json ./
RUN npm ci
COPY web/frontend/ ./
RUN npm run build

# Stage 2: Serve via nginx
FROM nginx:alpine
COPY web/frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY web/frontend/htpasswd /etc/nginx/htpasswd
COPY --from=builder /app/dist /usr/share/nginx/html
EXPOSE 80

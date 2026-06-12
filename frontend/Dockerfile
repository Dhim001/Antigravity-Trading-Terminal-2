FROM node:20-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

ARG VITE_HTTP_BASE_URL=
ARG VITE_WS_URL=ws://localhost:8765
ARG VITE_HTTP_API_KEY=
ENV VITE_HTTP_BASE_URL=$VITE_HTTP_BASE_URL
ENV VITE_WS_URL=$VITE_WS_URL
ENV VITE_HTTP_API_KEY=$VITE_HTTP_API_KEY

RUN npm run build

FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

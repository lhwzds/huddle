FROM node:22-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
# The path the app is served under ('/' or '/huddle/'); the proxy in front of
# it strips the prefix, the browser's URLs keep it.
ARG HUDDLE_BASE_PATH=/
ENV HUDDLE_BASE_PATH=$HUDDLE_BASE_PATH
RUN npm run build

FROM nginx:alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80

# Stage 1: Build
FROM dart:stable AS build

WORKDIR /app
COPY pubspec.yaml pubspec.lock ./
RUN dart pub get

COPY . .
RUN dart compile exe bin/main.dart -o bin/main

# Stage 2: Run
FROM debian:stable-slim

WORKDIR /app
COPY --from=build /app/bin/main /app/bin/main

# Environment variables
ENV BOT_TOKEN=""
ENV GEMINI_API_KEY=""

EXPOSE 8080

CMD ["/app/bin/main"]

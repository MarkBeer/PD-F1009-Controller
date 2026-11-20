# syntax=docker/dockerfile:1

FROM node:22-alpine AS web-build
WORKDIR /src/web
COPY app/web/package.json app/web/package-lock.json ./
RUN npm ci
COPY app/web/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /srv

RUN apt-get update \
    && apt-get install -y --no-install-recommends libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY --from=web-build /src/web/dist ./app/web/dist

RUN mkdir -p /data

ENV PYTHONUNBUFFERED=1 \
    DISC_DETECT_HOST=0.0.0.0 \
    DISC_DETECT_PORT=8000 \
    DISC_DETECT_RELOAD=0 \
    DISC_DETECT_DB=/data/catalog.sqlite \
    DISC_DETECT_WEB_DIST=/srv/app/web/dist

EXPOSE 8000
VOLUME ["/data"]

CMD ["python", "-m", "uvicorn", "app.server.main:app", "--host", "0.0.0.0", "--port", "8000"]

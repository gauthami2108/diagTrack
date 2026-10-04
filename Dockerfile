# DiagTrack application image (used by the "web" and "worker" containers).
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STATIC_ROOT=/app/staticfiles \
    MEDIA_ROOT=/data/media \
    STATUS_DIR=/data/status

WORKDIR /app
COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ .
RUN SECRET_KEY=build-only python manage.py collectstatic --noinput \
    && useradd --uid 1000 --create-home diagtrack \
    && mkdir -p /data/media /data/status

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
CMD ["web"]

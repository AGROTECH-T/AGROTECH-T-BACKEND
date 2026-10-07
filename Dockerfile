# Construye pruebas y producción con Python 3.13.
FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app

RUN groupadd --system app && useradd --system --gid app app
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements.txt

FROM base AS test
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY . .
RUN chown -R app:app /app
USER app
CMD ["pytest"]

FROM base AS production
COPY . .
RUN chown -R app:app /app
USER app
EXPOSE 8080
CMD ["sh", "-c", "python manage.py migrate && gunicorn config.wsgi:application --bind 0.0.0.0:${PORT:-8080} --workers 3"]

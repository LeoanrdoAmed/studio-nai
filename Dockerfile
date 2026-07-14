# Dockerfile
FROM mcr.microsoft.com/playwright/python:v1.52.0-jammy

ENV PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER pwuser
WORKDIR /app

COPY --chown=pwuser:pwuser requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/requirements.txt

# Copiar o app
COPY --chown=pwuser:pwuser . /app

# Pasta de sessão (se usar Flask-Session filesystem)
RUN mkdir -p /app/flask_session

EXPOSE 8080

# Use wsgi:app (arquivo certo!) com 1 worker + threads
CMD ["python", "-m", "gunicorn", "wsgi:app", "-b", "0.0.0.0:8080", "--workers", "1", "--threads", "8", "--timeout", "180"]
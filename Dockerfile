FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DEFAULT_TIMEOUT=300 \
    PIP_RETRIES=8

WORKDIR /app
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

COPY backend/ /app/backend/
COPY frontend/dist/ /app/frontend/dist/

RUN mkdir -p /app/backend/data
WORKDIR /app/backend

EXPOSE 3009
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3009"]

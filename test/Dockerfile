FROM python:3.11-slim

WORKDIR /app

RUN pip install fastapi uvicorn

COPY api.py /app/api.py

EXPOSE 8080

CMD ["sh", "-c", "uvicorn api:app --host 0.0.0.0 --port ${PORT:-8080}"]

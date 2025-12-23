FROM python:3.11-slim

# --- system deps (LibreOffice + fonts) ---
RUN apt-get update && \
    apt-get install -y \
        libreoffice \
        fonts-dejavu-core \
        fonts-dejavu-extra \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# --- app directory ---
WORKDIR /app

# --- python deps ---
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- copy app ---
COPY app/ /app/

# --- ensure folders exist ---
RUN mkdir -p /app/outputs /app/tmp

# --- run ---
CMD ["python"]


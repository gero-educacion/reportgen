FROM python:3.11-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y \
    libreoffice \
    libreoffice-writer \
    fonts-dejavu \
    fonts-liberation \
    fontconfig \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Custom fonts (Formular + TT Squares Black)
COPY assets/fonts/ /usr/share/fonts/custom/
RUN fc-cache -fv

# Matplotlib cache location (must be writable)
ENV MPLCONFIGDIR=/app/.mplconfig
RUN mkdir -p /app/.mplconfig
RUN mkdir -p /app/tmp/jobs

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ✅ Pre-build Matplotlib font cache at build time (kills the cold-start delay)
RUN python -c "import matplotlib; import matplotlib.pyplot as plt; plt.figure(); plt.text(0.5,0.5,'warm'); plt.savefig('/tmp/mpl_warm.png'); print('mpl warm ok')"

# App code
COPY app /app/app
COPY assets /app/assets

EXPOSE 8080

CMD ["sh", "-c", "uvicorn app.api:app --host 0.0.0.0 --port ${PORT:-8080}"]

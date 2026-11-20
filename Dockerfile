FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 LAUNDRY_DB=/data/laundry.db
WORKDIR /app
RUN groupadd --system loop && useradd --system --gid loop loop && mkdir /data && chown loop:loop /data
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py index.html app.js style.css manifest.webmanifest sw.js icon-192.png icon-512.png ./
USER loop
EXPOSE 8000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]

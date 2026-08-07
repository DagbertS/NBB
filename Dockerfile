FROM python:3.12-slim

# Logs direct doorsturen (anders blijven ze in de buffer hangen op Railway)
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# 1 worker: de scheduler (dagelijkse taken) moet maar één keer draaien.
# Railway/Render geven de poort door via $PORT; lokaal valt hij terug op 8000.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}

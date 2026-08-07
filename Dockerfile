FROM python:3.12-slim

# Logs direct doorsturen (anders blijven ze in de buffer hangen op Railway)
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# De screening-pipeline (fases 1-6) als bibliotheek voor de webapp
RUN pip install --no-cache-dir ./screen

EXPOSE 8000

# 1 worker: de scheduler (dagelijkse taken) moet maar één keer draaien.
# Vaste poort 8000 — stel het publieke domein in Railway/Render in op poort 8000.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

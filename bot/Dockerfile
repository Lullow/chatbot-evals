FROM python:3.12-slim

# Kopplar paketet på GHCR till repot, så att det syns på repots sida.
LABEL org.opencontainers.image.source=https://github.com/Lullow/docker-chatbot

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/

# Kör som en vanlig användare. UID:t anges som siffra så att k8s kan kontrollera
# runAsNonRoot utan att slå upp namnet.
USER 10001
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

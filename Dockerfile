FROM python:3.13-slim

WORKDIR /app

COPY src/requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY src/ /app/

# Create data directory for players.json
RUN mkdir -p /app/data

CMD ["python", "main.py"]

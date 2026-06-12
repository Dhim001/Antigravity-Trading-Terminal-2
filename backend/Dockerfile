FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV TERMINAL_MODE=SIMULATED
ENV TERMINAL_ROLE=all
ENV HTTP_ENABLED=true
ENV HTTP_HOST=0.0.0.0
ENV WS_HOST=0.0.0.0
ENV WS_PORT=8765
ENV HTTP_PORT=8766

EXPOSE 8765 8766

CMD ["python", "main.py"]

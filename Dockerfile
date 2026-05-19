FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends openssl curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt pyproject.toml ./
COPY src ./src
COPY app ./app
RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 8443

CMD ["python", "-m", "overmind.main", "--host", "0.0.0.0", "--port", "8000"]
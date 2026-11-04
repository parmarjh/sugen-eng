FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy dataset and engine code into container image
COPY data/ ./data/
COPY coder.py .
COPY score.py .

ENTRYPOINT ["python", "coder.py"]


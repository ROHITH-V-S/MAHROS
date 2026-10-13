FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt pyproject.toml README.md ./
RUN pip install --no-cache-dir -r requirements.txt "psycopg[binary]>=3.1" "cryptography>=42"

COPY mahros ./mahros
COPY results ./results
RUN pip install --no-cache-dir --no-deps -e .

EXPOSE 8000
CMD ["python", "-m", "mahros.server"]

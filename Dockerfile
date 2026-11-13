# One image for the demo service and the test runner.
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml ./
COPY config ./config
COPY xray ./xray
COPY demo_service ./demo_service
COPY apitest ./apitest
COPY tests ./tests

RUN useradd --create-home app && mkdir -p /app/reports && chown app /app/reports
USER app
EXPOSE 8000
CMD ["python", "-m", "demo_service"]

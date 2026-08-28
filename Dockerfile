# Base pulled from AWS public ECR (mirror of the official image) —
# Docker Hub anonymous pulls proved unreachable from Railway builders on
# 2026-08-28 (two consecutive i/o timeouts on registry-1.docker.io).
# Same image lineage; avoids Docker Hub throttling/reachability as a
# build dependency.
FROM public.ecr.aws/docker/library/python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8080
EXPOSE 8080

# --timeout 300: metadata refresh hits the Salesforce REST API across up to
# 6 categories serially; for large orgs the happy path can take 3-4 min.
# The "right" fix is to enqueue the refresh as a background job on the
# Railway worker service (see TODO in primeqa/metadata/service.py); until
# then, 5-minute web workers keep the inline flow from timing out.
# --graceful-timeout: let in-flight work drain on deploy instead of SIGKILL.
CMD gunicorn primeqa.app:app --bind 0.0.0.0:${PORT} --workers 2 --timeout 300 --graceful-timeout 30

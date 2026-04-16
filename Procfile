web: gunicorn primeqa.app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120
worker: python -m primeqa.worker
scheduler: python -m primeqa.scheduler

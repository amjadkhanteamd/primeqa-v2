"""Background worker entrypoint.

Polls pipeline_runs for queued jobs, executes pipeline stages,
sends heartbeats, and checks cancellation tokens between steps.
"""

import os
import time
import uuid

from dotenv import load_dotenv

load_dotenv()


def run_worker():
    """Main worker loop — poll for queued runs and execute them."""
    worker_id = f"worker-{uuid.uuid4().hex[:8]}"
    print(f"Worker {worker_id} starting...")
    pass


if __name__ == "__main__":
    run_worker()

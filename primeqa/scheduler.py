"""Reaper/scheduler entrypoint.

Runs on a timer (APScheduler), not HTTP. Handles:
- Dead job reaper (every 60s)
- Slot reaper (release stuck slots)
- Jira sync (requirement staleness check)
- Metadata refresh scheduling
- Failure pattern decay (nightly)
- Metadata version archival (nightly)
"""

import os

from dotenv import load_dotenv

load_dotenv()


def run_scheduler():
    """Start the scheduler with all periodic jobs."""
    print("Scheduler starting...")
    pass


if __name__ == "__main__":
    run_scheduler()

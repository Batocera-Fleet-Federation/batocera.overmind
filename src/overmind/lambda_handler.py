"""AWS Lambda entrypoints for Overmind.

The same FastAPI application is used for local Uvicorn, Docker/EC2, and Lambda.
API Gateway invokes ``handler``; EventBridge scheduled rules invoke
``scheduled_handler`` with a ``job`` value.
"""

import os
from typing import Any

os.environ.setdefault("OVERMIND_RUNTIME", "lambda")

from mangum import Mangum  # type: ignore

from overmind.main import app, initialize_runtime, run_scheduled_job


handler = Mangum(app, lifespan="auto")


def scheduled_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Run one scheduled Overmind maintenance job."""
    initialize_runtime(start_pollers=False, prepare_tls=False)
    job_name = (
        event.get("job")
        or event.get("detail", {}).get("job")
        or event.get("resources", [""])[0].split("/")[-1]
    )
    return run_scheduled_job(str(job_name or ""))

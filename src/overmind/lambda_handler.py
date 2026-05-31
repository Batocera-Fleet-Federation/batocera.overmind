"""AWS Lambda entrypoints for Overmind.

The same FastAPI application is used for local Uvicorn, Docker/EC2, and Lambda.
API Gateway invokes ``handler``; EventBridge scheduled rules invoke
``scheduled_handler`` with a ``job`` value.
"""

import json
import logging
import os
from typing import Any

os.environ.setdefault("OVERMIND_RUNTIME", "lambda")

from mangum import Mangum  # type: ignore

from overmind.main import app, initialize_runtime, run_scheduled_job

logger = logging.getLogger("overmind.lambda")


_adapter = Mangum(app, lifespan="off")


def _service_unavailable_response(error: Exception) -> dict[str, Any]:
    return {
        "statusCode": 503,
        "headers": {"content-type": "application/json"},
        "isBase64Encoded": False,
        "body": json.dumps(
            {
                "message": "Overmind startup failed",
                "error_type": error.__class__.__name__,
                "detail": str(error),
            }
        ),
    }


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Initialize runtime explicitly so API Gateway receives app diagnostics."""
    try:
        initialize_runtime(start_pollers=False, prepare_tls=False)
    except Exception as error:
        return _service_unavailable_response(error)
    return _adapter(event, context)


def scheduled_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Run one scheduled Overmind maintenance job."""
    initialize_runtime(start_pollers=False, prepare_tls=False)
    job_name = (
        event.get("job")
        or event.get("detail", {}).get("job")
        or event.get("resources", [""])[0].split("/")[-1]
    )
    logger.info("Running scheduled Overmind job job=%s", job_name)
    return run_scheduled_job(str(job_name or ""))

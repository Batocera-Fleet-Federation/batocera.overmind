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


def _event_path(event: dict[str, Any]) -> str:
    return (
        event.get("rawPath")
        or event.get("path")
        or event.get("requestContext", {}).get("http", {}).get("path")
        or "/"
    )


def _event_method(event: dict[str, Any]) -> str:
    return (
        event.get("requestContext", {}).get("http", {}).get("method")
        or event.get("httpMethod")
        or "GET"
    ).upper()


def _can_skip_startup(event: dict[str, Any]) -> bool:
    if _event_method(event) not in {"GET", "HEAD", "OPTIONS"}:
        return False
    path = _event_path(event)
    if path in {"/", "/health", "/docs", "/openapi.json"}:
        return True
    return path.startswith(("/static/", "/content/", "/favicon"))


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
    if not _can_skip_startup(event):
        try:
            initialize_runtime(start_pollers=False, prepare_tls=False)
        except Exception as error:
            return _service_unavailable_response(error)
    return _adapter(event, context)


def scheduled_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Run one scheduled Overmind maintenance job."""
    job_name = (
        event.get("job")
        or event.get("detail", {}).get("job")
        or event.get("resources", [""])[0].split("/")[-1]
    )
    job_name = str(job_name or "")
    logger.info("Running scheduled Overmind job job=%s", job_name)
    try:
        initialize_runtime(start_pollers=False, prepare_tls=False)
        return run_scheduled_job(job_name)
    except Exception as error:
        logger.exception("Scheduled Overmind job skipped job=%s", job_name)
        return {
            "job": job_name,
            "status": "skipped",
            "error_type": error.__class__.__name__,
            "detail": str(error),
        }

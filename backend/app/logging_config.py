"""Sensitive-data-safe structured logging and request correlation.

Log records are JSON with a correlation id and never contain material text,
prompt bodies, model response bodies, or identity information (design 10.2
and 11.3). The API middleware assigns one correlation id per request and
logs only method, path, status, and duration.
"""

import json
import logging
import time
import uuid

from fastapi import Request


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        correlation_id = getattr(record, "correlation_id", None)
        if correlation_id:
            payload["correlation_id"] = correlation_id
        for key in ("job_id", "application_id", "document_id", "step", "error_code"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_json_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True


class CorrelationMiddleware:
    """Assign a correlation id per request, log a summary, and echo the id."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        correlation_id = request.headers.get("X-Correlation-ID") or str(uuid.uuid4())
        request.state.correlation_id = correlation_id
        started = time.monotonic()
        status_code = 500

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = message.get("headers") or []
                headers.append(
                    (b"x-correlation-id", correlation_id.encode("ascii"))
                )
                message["headers"] = headers
            await send(message)

        logger = logging.getLogger("icrm.request")
        logger.info(
            "request started",
            extra={"correlation_id": correlation_id},
        )
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = int((time.monotonic() - started) * 1000)
            logger.info(
                "request finished",
                extra={
                    "correlation_id": correlation_id,
                    "method": scope.get("method"),
                    "path": scope.get("path"),
                    "status": status_code,
                    "duration_ms": duration_ms,
                },
            )

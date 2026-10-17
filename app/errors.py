from __future__ import annotations

import json
import logging
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from starlette.responses import JSONResponse


class ErrorCode(IntEnum):
    MISSING_REQUIRED_FIELD = 40001
    INVALID_FORMAT = 40002
    AUTHENTICATION_FAILED = 40101
    ACCESS_DENIED = 40301
    RESOURCE_NOT_FOUND = 40401
    REQUEST_CONFLICT = 40901
    IDEMPOTENCY_CONFLICT = 40902
    RATE_LIMIT_EXCEEDED = 42901
    SERVICE_UNAVAILABLE = 50001
    INTERNAL_SERVER_ERROR = 50002


@dataclass(frozen=True)
class ErrorDefinition:
    http_status: int
    message: str
    detail: str


ERROR_DEFINITIONS = {
    ErrorCode.MISSING_REQUIRED_FIELD: ErrorDefinition(
        400,
        "Invalid input provided.",
        "A required field is missing.",
    ),
    ErrorCode.INVALID_FORMAT: ErrorDefinition(
        400,
        "Invalid input provided.",
        "One or more fields have an invalid format.",
    ),
    ErrorCode.AUTHENTICATION_FAILED: ErrorDefinition(
        401,
        "Authentication required.",
        "Your session is invalid or expired. Please sign in again.",
    ),
    ErrorCode.ACCESS_DENIED: ErrorDefinition(
        403,
        "Access denied.",
        "You do not have permission to perform this action.",
    ),
    ErrorCode.RESOURCE_NOT_FOUND: ErrorDefinition(
        404,
        "Not found.",
        "The requested item could not be found.",
    ),
    ErrorCode.REQUEST_CONFLICT: ErrorDefinition(
        409,
        "Request conflict.",
        "The request could not be completed in the current state.",
    ),
    ErrorCode.IDEMPOTENCY_CONFLICT: ErrorDefinition(
        409,
        "Request conflict.",
        "This action identifier was already used for a different action.",
    ),
    ErrorCode.RATE_LIMIT_EXCEEDED: ErrorDefinition(
        429,
        "Too many requests.",
        "Please wait before trying again.",
    ),
    ErrorCode.SERVICE_UNAVAILABLE: ErrorDefinition(
        503,
        "Service temporarily unavailable.",
        "Please try again later.",
    ),
    ErrorCode.INTERNAL_SERVER_ERROR: ErrorDefinition(
        500,
        "Internal server error.",
        "Something went wrong. Please try again.",
    ),
}


HTTP_STATUS_CODES = {
    400: ErrorCode.INVALID_FORMAT,
    401: ErrorCode.AUTHENTICATION_FAILED,
    403: ErrorCode.ACCESS_DENIED,
    404: ErrorCode.RESOURCE_NOT_FOUND,
    409: ErrorCode.REQUEST_CONFLICT,
    422: ErrorCode.INVALID_FORMAT,
    429: ErrorCode.RATE_LIMIT_EXCEEDED,
    500: ErrorCode.INTERNAL_SERVER_ERROR,
    503: ErrorCode.SERVICE_UNAVAILABLE,
}


def new_trace_id() -> str:
    return f"err_{uuid4().hex}"


def error_body(
    code: ErrorCode,
    trace_id: str,
    field_errors: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    definition = ERROR_DEFINITIONS[code]
    return {
        "status": "fail" if field_errors else "error",
        "code": int(code),
        "message": definition.message,
        "errors": field_errors or [{"message": definition.detail}],
        "trace_id": trace_id,
    }


def validation_errors(errors: list[dict[str, Any]]) -> list[dict[str, str]]:
    public_errors: list[dict[str, str]] = []
    for error in errors:
        location = error.get("loc", ())
        field = ".".join(str(part) for part in location if part not in {"body", "query", "path", "header"})
        error_type = str(error.get("type", ""))
        if error_type == "missing":
            message = ERROR_DEFINITIONS[ErrorCode.MISSING_REQUIRED_FIELD].detail
        elif error_type in {"greater_than", "greater_than_equal", "less_than", "less_than_equal"}:
            message = "The value is outside the allowed range."
        else:
            message = ERROR_DEFINITIONS[ErrorCode.INVALID_FORMAT].detail
        item = {"message": message}
        if field:
            item["field"] = field
        public_errors.append(item)
    return public_errors or [{"message": ERROR_DEFINITIONS[ErrorCode.INVALID_FORMAT].detail}]


def log_error(
    logger: logging.Logger,
    code: ErrorCode,
    trace_id: str,
    error: BaseException | str,
    request: Request | None = None,
    level: int = logging.ERROR,
) -> None:
    if isinstance(error, BaseException):
        error_type = type(error).__name__
        technical_message = str(error)
        stack_trace = "".join(traceback.format_exception(error))
    else:
        error_type = "RequestError"
        technical_message = error
        stack_trace = ""

    record: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "level": logging.getLevelName(level),
        "trace_id": trace_id,
        "service": "cowboys-ph-poker-api",
        "code": int(code),
        "error": {
            "type": error_type,
            "technical_message": technical_message,
            "stack_trace": stack_trace,
        },
    }
    if request is not None:
        record["http"] = {
            "method": request.method,
            "url": request.url.path,
            "status_code": ERROR_DEFINITIONS[code].http_status,
            "client_ip": request.client.host if request.client else None,
        }
    logger.log(level, json.dumps(record, separators=(",", ":")))


def install_error_handlers(app: FastAPI, logger_name: str = "cowboys.errors") -> None:
    error_logger = logging.getLogger(logger_name)

    @app.exception_handler(HTTPException)
    async def handle_http_error(request: Request, error: HTTPException) -> JSONResponse:
        code = HTTP_STATUS_CODES.get(error.status_code, ErrorCode.INTERNAL_SERVER_ERROR)
        trace_id = new_trace_id()
        level = logging.WARNING if error.status_code < 500 else logging.ERROR
        log_error(error_logger, code, trace_id, str(error.detail), request, level)
        definition = ERROR_DEFINITIONS[code]
        return JSONResponse(
            status_code=definition.http_status,
            content=error_body(code, trace_id),
            headers=error.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation_error(
        request: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        validation_items = error.errors()
        code = (
            ErrorCode.MISSING_REQUIRED_FIELD
            if validation_items and all(item.get("type") == "missing" for item in validation_items)
            else ErrorCode.INVALID_FORMAT
        )
        trace_id = new_trace_id()
        log_error(
            error_logger,
            code,
            trace_id,
            "Request validation failed: "
            + str([
                {"type": item.get("type"), "loc": item.get("loc")}
                for item in validation_items
            ]),
            request,
            logging.WARNING,
        )
        return JSONResponse(
            status_code=400,
            content=error_body(code, trace_id, validation_errors(validation_items)),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, error: Exception) -> JSONResponse:
        trace_id = new_trace_id()
        log_error(
            error_logger,
            ErrorCode.INTERNAL_SERVER_ERROR,
            trace_id,
            error,
            request,
        )
        return JSONResponse(
            status_code=500,
            content=error_body(ErrorCode.INTERNAL_SERVER_ERROR, trace_id),
        )
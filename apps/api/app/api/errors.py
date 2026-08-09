import logging
from dataclasses import dataclass, field

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


@dataclass
class ApiError(Exception):
    status: int
    code: str
    title: str
    detail: str
    headers: dict[str, str] = field(default_factory=dict)


def _problem(
    request: Request,
    *,
    status: int,
    code: str,
    title: str,
    detail: str,
    errors: list[dict[str, str]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body: dict[str, object] = {
        "type": f"https://nigeria-forest-monitor.local/problems/{code.replace('_', '-')}",
        "title": title,
        "status": status,
        "detail": detail,
        "instance": request.url.path,
        "code": code,
        "request_id": getattr(request.state, "request_id", None),
    }
    if errors:
        body["errors"] = errors
    return JSONResponse(
        body,
        status_code=status,
        media_type="application/problem+json",
        headers=headers,
    )


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, error: ApiError) -> JSONResponse:
        return _problem(
            request,
            status=error.status,
            code=error.code,
            title=error.title,
            detail=error.detail,
            headers=error.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        fields: list[dict[str, str]] = []
        for item in error.errors():
            location = ".".join(str(part) for part in item["loc"] if part != "body")
            fields.append(
                {
                    "field": location,
                    "code": str(item["type"]),
                    "message": str(item["msg"]),
                }
            )
        return _problem(
            request,
            status=422,
            code="validation_error",
            title="Request validation failed",
            detail="One or more fields are invalid.",
            errors=fields,
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(
        request: Request,
        error: StarletteHTTPException,
    ) -> JSONResponse:
        codes = {
            404: ("resource_not_found", "Resource not found"),
            405: ("method_not_allowed", "Method not allowed"),
            503: ("service_unavailable", "Service unavailable"),
        }
        code, title = codes.get(error.status_code, ("http_error", "Request failed"))
        return _problem(
            request,
            status=error.status_code,
            code=code,
            title=title,
            detail=str(error.detail),
            headers=error.headers,
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(
        request: Request,
        error: Exception,
    ) -> JSONResponse:
        logging.getLogger("nfm.api").exception(
            "unhandled request error",
            exc_info=error,
            extra={"request_id": getattr(request.state, "request_id", None)},
        )
        return _problem(
            request,
            status=500,
            code="internal_error",
            title="Internal server error",
            detail="The request could not be completed.",
        )

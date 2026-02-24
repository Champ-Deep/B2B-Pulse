import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import api_router
from app.config import settings
from app.logging_config import setup_logging

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    # Configure logging first
    setup_logging(app_env=settings.app_env, log_level=settings.log_level)

    # Initialize Sentry if DSN is configured
    if settings.sentry_dsn:
        try:
            import sentry_sdk
            from sentry_sdk.integrations.celery import CeleryIntegration
            from sentry_sdk.integrations.fastapi import FastApiIntegration

            sentry_sdk.init(
                dsn=settings.sentry_dsn,
                environment=settings.app_env,
                integrations=[FastApiIntegration(), CeleryIntegration()],
                traces_sample_rate=0.1 if settings.is_production else 1.0,
                send_default_pii=False,
            )
            logger.info("Sentry initialized")
        except ImportError:
            logger.warning("sentry-sdk not installed, skipping Sentry initialization")

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None if settings.is_production else "/redoc",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router)

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        logger.error(
            f"Unhandled exception on {request.method} {request.url.path}: {exc}", exc_info=True
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={"detail": exc.errors()},
        )

    @app.get("/health")
    async def health_check():
        return {"status": "healthy", "app": settings.app_name, "version": "0.1.0"}

    logger.info(f"AutoEngage started (env={settings.app_env})")
    return app


app = create_app()

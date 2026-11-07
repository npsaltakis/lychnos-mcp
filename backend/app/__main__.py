"""Entry point: ``python -m app`` (run from the backend folder)."""

import uvicorn

from app.core.config import get_settings
from app.main import create_app


def run() -> None:
    settings = get_settings()
    settings.validate_exposure()
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_config=None,  # our own JSON logging is configured in the app lifespan
        access_log=False,
    )


if __name__ == "__main__":
    run()

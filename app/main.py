from __future__ import annotations

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.services.runner import PipelineRunner


def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    runner = PipelineRunner(settings)
    runner.start()
    runner.join()


if __name__ == "__main__":
    main()
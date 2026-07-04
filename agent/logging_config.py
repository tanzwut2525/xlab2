import logging

from agent.config import config


def configure_logging() -> None:
    logging.basicConfig(
        level=config.log_level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

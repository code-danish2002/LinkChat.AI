"""Logging setup: one concise format for the app and quieter third-party loggers."""

from __future__ import annotations

import logging


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    for noisy in ("httpx", "httpcore", "urllib3", "trafilatura", "fastembed", "huggingface_hub"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

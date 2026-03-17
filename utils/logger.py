"""
Configuration du logger — format structuré avec niveau configurable.
"""

import logging
import sys
import os


def setup_logger(name: str, level: str = None) -> logging.Logger:
    level   = level or os.getenv("LOG_LEVEL", "INFO")
    logger  = logging.getLogger(f"apex.{name}")
    if logger.handlers:
        return logger

    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        fmt   = "%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt = "%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(handler)
    logger.propagate = False
    return logger

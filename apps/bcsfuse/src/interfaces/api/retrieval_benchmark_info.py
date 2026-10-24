"""Small on-demand runtime snapshot; no secrets, environment dump or hot-path I/O."""

import logging
import os
import platform
import resource
from importlib.metadata import PackageNotFoundError, version


def runtime_info() -> dict:
    loggers = []
    for name in ("", "src", "src.infra.public.vectorstores.qdrant_local_vector_store", "uvicorn.access"):
        logger = logging.getLogger(name)
        handlers = []
        for handler in logger.handlers:
            item = {"type": type(handler).__name__, "module": type(handler).__module__,
                    "level": logging.getLevelName(handler.level)}
            filename = getattr(handler, "baseFilename", None)
            if filename:
                item["file"] = filename
                try:
                    item["file_bytes"] = os.stat(filename).st_size
                except OSError:
                    item["file_bytes"] = None
            for attribute in ("maxBytes", "backupCount"):
                if hasattr(handler, attribute):
                    item[attribute] = getattr(handler, attribute)
            handlers.append(item)
        loggers.append({"name": name or "root", "effective_level": logging.getLevelName(logger.getEffectiveLevel()),
                        "propagate": logger.propagate, "handlers": handlers})
    packages = {}
    for name in ("qdrant-client", "numpy", "fastapi", "anyio"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = "unavailable"
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {"pid": os.getpid(), "python": platform.python_version(), "packages": packages,
            "peak_rss_bytes": rss if platform.system() == "Darwin" else rss * 1024,
            "logging": loggers}

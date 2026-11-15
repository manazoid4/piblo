"""Piblo Core entry point: python -m mazhost (works under pythonw: logs go to logs/core.log)."""
import logging
import sys
from pathlib import Path


class _Quiet(logging.Filter):
    """Drop the device's constant polling from the access log."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return "/beam/pull" not in msg and "/buddy/summary" not in msg


def main() -> None:
    import uvicorn

    from .config import Settings

    log = Path.cwd() / "logs" / "core.log"
    log.parent.mkdir(exist_ok=True)
    if log.exists() and log.stat().st_size > 5_000_000:
        log.replace(log.with_suffix(".log.old"))
    sys.stdout = sys.stderr = open(log, "a", encoding="utf-8", buffering=1)
    logging.getLogger("uvicorn.access").addFilter(_Quiet())
    s = Settings()
    if s.kokoro_pcm_port:
        from .kokoro_pcm import serve_in_thread
        serve_in_thread(s)
    uvicorn.run("mazhost.app:app", host=s.bind, port=s.port)


if __name__ == "__main__":
    main()

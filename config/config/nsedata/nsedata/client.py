"""HTTP access to NSE's public archive files.

NSE rejects requests without browser-like headers and rate-limits hard.
Everything here is end-of-day archive data. No live quotes.
"""

from __future__ import annotations

import io
import time
import zipfile
import logging

import requests

log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/csv,application/csv,application/zip,*/*",
    "Accept-Language": "en-GB,en;q=0.9",
    "Referer": "https://www.nseindia.com/all-reports",
    "Connection": "keep-alive",
}

ARCHIVE_HOSTS = [
    "https://nsearchives.nseindia.com",
    "https://archives.nseindia.com",
]


class NSEClient:
    def __init__(self, timeout: int = 30, retries: int = 3, pause: float = 1.0):
        self.timeout = timeout
        self.retries = retries
        self.pause = pause
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self._warmed = False

    def _warm(self):
        """Pick up cookies. Failure is not fatal -- archive files often work without."""
        if self._warmed:
            return
        try:
            self.session.get("https://www.nseindia.com", timeout=self.timeout)
        except requests.RequestException as exc:
            log.debug("Cookie warm-up failed (continuing): %s", exc)
        self._warmed = True

    def get(self, path: str) -> bytes | None:
        """Fetch `path` (leading slash) from the archive hosts. None on 404 everywhere."""
        self._warm()
        last_status = None
        for host in ARCHIVE_HOSTS:
            url = host + path
            for attempt in range(self.retries):
                try:
                    r = self.session.get(url, timeout=self.timeout)
                except requests.RequestException as exc:
                    log.debug("%s attempt %d error: %s", url, attempt + 1, exc)
                    time.sleep(self.pause * (attempt + 1))
                    continue
                last_status = r.status_code
                if r.status_code == 200 and r.content:
                    return r.content
                if r.status_code == 404:
                    break  # genuinely absent on this host; try the next host
                time.sleep(self.pause * (attempt + 1))
        log.debug("Not retrieved: %s (last status %s)", path, last_status)
        return None

    @staticmethod
    def unzip_single(payload: bytes) -> bytes:
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            name = zf.namelist()[0]
            return zf.read(name)

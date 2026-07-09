"""_net.py — shared HTTP session for all pull modules.

Two jobs:
  * truststore.inject_into_ssl() — this machine sits behind an iboss
    TLS-intercepting proxy whose CA lives in the Windows cert store, not in
    certifi. Without this, sec.gov (and any other decrypted category) fails
    SSL verification. Discovered + fixed 2026-07-08.
  * one retry/backoff Session with the house User-Agent.
"""
from __future__ import annotations

import truststore

truststore.inject_into_ssl()  # must run before any TLS connection

import requests  # noqa: E402
from requests.adapters import HTTPAdapter  # noqa: E402
from urllib3.util.retry import Retry  # noqa: E402

USER_AGENT = "Avos Capital Management market-conditions-dashboard (gavin@avos.co)"


def session(total_retries: int = 4, backoff: float = 1.0) -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    retry = Retry(total=total_retries, backoff_factor=backoff,
                  status_forcelist=[429, 500, 502, 503, 504],
                  allowed_methods=["GET", "HEAD"])
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s

"""quiet.py — silence known-benign third-party warnings so the daily run's
stderr stays readable. Imported for its side effect by the entry points
(run.py, refresh_backfill.py).

Deliberately TARGETED, not a blanket mute: each filter names a specific,
understood message/category. Anything unexpected — a new deprecation, a real
cert failure on another host, our own warnings — still surfaces.
"""
from __future__ import annotations

import warnings

# xbbg 0.x maintenance-mode notice + the 1.0 defaults-changing notice. We pin
# 0.x on purpose; these are cosmetic and fire on every Bloomberg import/call.
warnings.filterwarnings("ignore", message=r".*xbbg 0\.x.*")
warnings.filterwarnings("ignore", message=r".*defaults are changing.*")

# pandas: dateutil fallback on the FINRA margin-page date column. The dates
# parse correctly ("%b-%y" plus a mixed fallback); this is just the notice.
warnings.filterwarnings("ignore", message=r".*Could not infer format.*")

# urllib3: unverified HTTPS to the Massive flat-file host (files.polygon.io).
# TLS verification is disabled for that host because the corporate proxy (iboss)
# intercepts it; the proper fix is truststore (see pull/_net.py) once the S3
# path routes through it. Until then, quiet the per-request spam. Cert failures
# on OTHER hosts still raise, so this does not mask a genuine MITM elsewhere.
try:
    from urllib3.exceptions import InsecureRequestWarning
    warnings.filterwarnings("ignore", category=InsecureRequestWarning)
except Exception:  # pragma: no cover — urllib3 internal layout drift
    pass

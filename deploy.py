#!/usr/bin/env python3
"""deploy.py — push the built HTML to S3 + invalidate CloudFront (§2).

Mirrors avos-country-dashboard/deploy.py. The lens.avos.co distribution +
Cognito→Entra auth auto-cover any new /<slug>, so this just uploads the file.

  python deploy.py market-conditions      # push outputs/dashboard/pulse_latest.html
  aws sso login                           # if the AWS session has expired

Bucket + distribution come from infra/config.env (safe to commit).
"""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).parent.resolve()
CONFIG = REPO / "infra" / "config.env"

APPS = {
    "market-conditions": {"html": "outputs/dashboard/pulse_latest.html"},
}


def load_config():
    if not CONFIG.exists():
        sys.exit(f"Missing {CONFIG}; copy infra/config.env from avos-country-dashboard or run infra setup.")
    cfg = {}
    for line in CONFIG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        cfg[k.strip()] = v.strip().strip("'\"")
    return cfg


def run(cmd):
    print(f"  $ {' '.join(cmd)}")
    return subprocess.run(cmd, check=True)


def deploy_app(slug, cfg):
    bucket = cfg["S3_BUCKET"]
    distro = cfg["CLOUDFRONT_DISTRIBUTION_ID"]
    region = cfg.get("S3_BUCKET_REGION", "us-east-2")
    html = REPO / APPS[slug]["html"]
    if not html.exists():
        sys.exit(f"[{slug}] missing artifact {html}. Run `python -m src.run` first.")
    print(f"\n=== Deploying '{slug}' ({html.stat().st_size:,} bytes) ===")
    run(["aws", "s3", "cp", str(html), f"s3://{bucket}/{slug}/index.html",
         "--content-type", "text/html; charset=utf-8", "--cache-control", "max-age=60",
         "--region", region])
    run(["aws", "cloudfront", "create-invalidation", "--distribution-id", distro,
         "--paths", f"/{slug}/index.html"])
    print(f"Done. Live in ~30s at https://{cfg.get('DOMAIN','lens.avos.co')}/{slug}")


def main():
    args = sys.argv[1:]
    if not args:
        sys.exit(f"Usage: python deploy.py <app|--all>\n  apps: {', '.join(APPS)}")
    cfg = load_config()
    for k in ("S3_BUCKET", "CLOUDFRONT_DISTRIBUTION_ID"):
        if not cfg.get(k):
            sys.exit(f"Missing {k} in infra/config.env.")
    targets = list(APPS) if args == ["--all"] else args
    for slug in targets:
        if slug not in APPS:
            sys.exit(f"Unknown app '{slug}'. Known: {', '.join(APPS)}")
        deploy_app(slug, cfg)


if __name__ == "__main__":
    main()

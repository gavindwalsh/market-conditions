# EC2 Spot backfill box

Runs the Massive flat-file backfill on a throwaway EC2 Spot instance —
datacenter bandwidth, no iboss proxy, ~$3-4/day (Spot) — and syncs the
aggregate lake back through a private S3 bucket. Same resumable `src/pull`
code as the workstation; the box is fully disposable.

## Runbook

```
python infra/backfill/backfill_ctl.py launch      # seed lake to S3 + launch box + lanes start
python infra/backfill/backfill_ctl.py status      # instance + lane progress (10-min granularity)
python infra/backfill/backfill_ctl.py pull        # merge S3 lake into local data/dashboard
python refresh_backfill.py                        # fold into charts (main checkout)
python infra/backfill/backfill_ctl.py terminate   # once verified
```

Stop the local backfill terminals after `launch` — the box covers those
ranges; local lanes would just duplicate downloads.

## Methodology reprocess (not a gap-fill)

When the classifier changes, bump `config.RETAIL_METHOD_VERSION`. Every stored
day carries that stamp, and `massive._have_days` — the ONE shared "is this day
done" rule used by both `backfill_tape` and the lane runner — treats any day
below the current version as work. Holiday markers stay done forever.

Before 2026-08 there was no stamp and the rule was "any midpoint-signed day is
done", so a methodology change left the lanes with nothing to do while they
logged success: a box that ran, synced, and recomputed nothing. If a reprocess
finishes suspiciously fast, check `status` for `tape_days_remaining` **before**
believing it.

Reprocess procedure:

1. **Back up the lake first** — `data/dashboard/massive_retail_daily/` (~215MB).
   `pull` overwrites it and there is no other way back.
2. Merge to `main` and launch from there. `upload_bootstrap` tars whatever
   checkout you run it from, so launching from a worktree ships worktree code.
3. `launch` normally the first time (seeding is harmless — nothing has been
   recomputed yet, and it gives the box the grouped/OPRA history).
4. **On any RE-launch after a Spot reclaim, use `--no-seed`.** `seed_lake`
   pushes the LOCAL lake up to S3, and `aws s3 sync` copies whenever file sizes
   differ regardless of which side is newer — so re-seeding mid-reprocess shoves
   stale local days over ones the box already recomputed.
5. `pull` uses `--exact-timestamps`. Without it plain `sync` skips a remote file
   when the local copy is newer, which on a reprocess is exactly the case, and
   the recomputed days silently never land.

While the lake holds mixed versions the retail compute refuses to build and the
panel renders nothing — deliberate, so a chart can never splice two
methodologies. It clears itself once every day is on the current version.

## What runs on the box (lane_runner.py constants)

| lane      | what                                | range                  |
|-----------|-------------------------------------|------------------------|
| tape1/2   | trades+quotes, midpoint signing     | 2026-01-02 → yesterday (missing + trades-only days) |
| opra      | OPRA trades                         | 2024-01-01 → yesterday |
| grouped   | grouped-daily REST bars             | 2016-01-04 → yesterday |

Lake syncs to `s3://mcd-lake-<acct>-us-east-2/lake/` every 10 min; raw tape
never leaves the box (deleted after aggregation, per §2). Progress at
`status/status.json`, full log at `status/lane-runner.log`.

## Spot reclaim

2-minute warning → lane_runner does a final sync (at most the in-flight day
is lost). Recovery: `launch` again — infra is reused, the seed sync is
incremental, lanes recompute what's missing. `launch --on-demand` if Spot
capacity is ever tight.

## Debug access

No SSH — SSM only: `aws ssm start-session --target <instance-id> --region
us-east-2` (needs session-manager-plugin) or EC2 console → Connect → Session
Manager. Logs on-box: `/var/log/mcd-bootstrap.log`, `/var/log/mcd-lane-runner.log`.

## Kept resources (all ~free)

Bucket `mcd-lake-*` (lake backup + bootstrap code/creds), IAM role/profile
`mcd-backfill-ec2`, SG `mcd-backfill-sg`, launch template `mcd-backfill`.
To scrub the Massive credentials from S3 after retiring the workflow:
`aws s3 rm s3://<bucket>/bootstrap/ --recursive`.

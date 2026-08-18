#!/usr/bin/env python3
"""backfill_ctl.py — one-command EC2 Spot box for the Massive backfill.

The box pulls Massive flat files at datacenter speed (no iboss proxy),
reduces them with the SAME src/pull code, and syncs the aggregate lake to a
private S3 bucket every 10 minutes. The workstation pulls the lake down and
folds it into the charts with refresh_backfill.py. Everything is idempotent
and Spot-interruption-safe; relaunch after a reclaim is just `launch` again.

  python infra/backfill/backfill_ctl.py launch      # create infra (if missing) + seed lake + launch box
  python infra/backfill/backfill_ctl.py status      # instance state + lane progress
  python infra/backfill/backfill_ctl.py pull        # S3 lake -> local data/dashboard (merge; never deletes)
  python infra/backfill/backfill_ctl.py terminate   # kill the box once verified (pull first!)

  launch --on-demand    # skip Spot (pay ~3x, never reclaimed)
  terminate --yes       # skip the confirm prompt

Needs an aws CLI session (same as deploy.py). Bucket, IAM role, security
group and launch template are created once and kept — all free to retain.
"""
import argparse
import base64
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import time

REGION = "us-east-2"
NAME = "mcd-backfill"                 # launch template / instance / SG / tag name
ROLE = PROFILE = "mcd-backfill-ec2"   # IAM role + instance profile
INSTANCE_TYPE = "m6i.2xlarge"         # 8 vCPU / 32 GB — two 10GB DuckDB lanes + OPRA
VOLUME_GB = 300                       # 2 tape lanes x ~40GB transient + spill headroom
AMI_SSM = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
EXCLUDES = ["_tape_scratch*", "*.duckdb*", "run_log.jsonl", "*.tmp"]

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))


# ---- plumbing -----------------------------------------------------------------
def aws_json(*args, check=True):
    r = subprocess.run(["aws", *args, "--output", "json", "--region", REGION],
                       capture_output=True, text=True)
    if r.returncode != 0:
        if check:
            sys.exit(f"aws {' '.join(args[:3])} failed:\n{r.stderr.strip()}")
        return None
    return json.loads(r.stdout) if r.stdout.strip() else {}


def sh(cmd):
    print(f"  $ {' '.join(cmd[:6])}{' ...' if len(cmd) > 6 else ''}")
    subprocess.run(cmd, check=True)


def main_root() -> str:
    """Lake + credential files live in the MAIN checkout even when this script
    runs from a git worktree under <main>/.claude/worktrees/<name>."""
    marker = f"{os.sep}.claude{os.sep}worktrees{os.sep}"
    return REPO.split(marker)[0] if marker in REPO else REPO


def local_lake() -> str:
    for root in (os.environ.get("WORKSPACE"), main_root(), REPO):
        if root and os.path.isdir(os.path.join(root, "data", "dashboard")):
            return os.path.join(root, "data", "dashboard")
    sys.exit("no local data/dashboard lake found (checked WORKSPACE, main checkout, repo)")


def creds_files() -> tuple[str, str]:
    for root in (main_root(), REPO):
        api = os.path.join(root, ".massive_api_key")
        s3k = next((p for p in (os.path.join(root, ".massive_s3_key"),
                                os.path.join(root, ".massive_s3_keys"))
                    if os.path.exists(p)), None)
        if os.path.exists(api) and s3k:
            return api, s3k
    sys.exit("missing .massive_api_key / .massive_s3_key at the repo root")


def bucket_name() -> str:
    account = aws_json("sts", "get-caller-identity")["Account"]
    return f"mcd-lake-{account}-{REGION}"


def find_instance(states="pending,running"):
    js = aws_json("ec2", "describe-instances", "--filters",
                  f"Name=tag:Name,Values={NAME}",
                  f"Name=instance-state-name,Values={states}")
    for res in js.get("Reservations", []):
        for inst in res["Instances"]:
            return inst
    return None


def s3_cat(uri: str) -> str | None:
    r = subprocess.run(["aws", "s3", "cp", uri, "-", "--region", REGION],
                       capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


# ---- create-if-missing infra ----------------------------------------------------
def ensure_bucket(bucket: str):
    if aws_json("s3api", "head-bucket", "--bucket", bucket, check=False) is not None:
        return
    print(f"creating private bucket {bucket}")
    aws_json("s3api", "create-bucket", "--bucket", bucket,
             "--create-bucket-configuration", f"LocationConstraint={REGION}")
    aws_json("s3api", "put-public-access-block", "--bucket", bucket,
             "--public-access-block-configuration",
             "BlockPublicAcls=true,IgnorePublicAcls=true,"
             "BlockPublicPolicy=true,RestrictPublicBuckets=true")


def ensure_role(bucket: str):
    created = False
    if aws_json("iam", "get-role", "--role-name", ROLE, check=False) is None:
        print(f"creating IAM role {ROLE} (SSM access + lake bucket rw)")
        trust = json.dumps({"Version": "2012-10-17", "Statement": [
            {"Effect": "Allow", "Principal": {"Service": "ec2.amazonaws.com"},
             "Action": "sts:AssumeRole"}]})
        aws_json("iam", "create-role", "--role-name", ROLE,
                 "--assume-role-policy-document", trust)
        aws_json("iam", "attach-role-policy", "--role-name", ROLE,
                 "--policy-arn", "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore")
        created = True
    policy = json.dumps({"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Action": ["s3:ListBucket"],
         "Resource": f"arn:aws:s3:::{bucket}"},
        {"Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject"],
         "Resource": f"arn:aws:s3:::{bucket}/*"}]})
    aws_json("iam", "put-role-policy", "--role-name", ROLE,
             "--policy-name", "mcd-lake-rw", "--policy-document", policy)
    if aws_json("iam", "get-instance-profile", "--instance-profile-name", PROFILE,
                check=False) is None:
        aws_json("iam", "create-instance-profile", "--instance-profile-name", PROFILE)
        aws_json("iam", "add-role-to-instance-profile", "--instance-profile-name",
                 PROFILE, "--role-name", ROLE, check=False)
        created = True
    if created:
        print("  (waiting 12s for IAM propagation)")
        time.sleep(12)


def ensure_sg() -> str:
    vpc = aws_json("ec2", "describe-vpcs", "--filters",
                   "Name=is-default,Values=true")["Vpcs"][0]["VpcId"]
    sgs = aws_json("ec2", "describe-security-groups", "--filters",
                   f"Name=group-name,Values={NAME}-sg",
                   f"Name=vpc-id,Values={vpc}")["SecurityGroups"]
    if sgs:
        return sgs[0]["GroupId"]
    print(f"creating security group {NAME}-sg (no ingress; SSM only)")
    return aws_json("ec2", "create-security-group", "--group-name", f"{NAME}-sg",
                    "--description", "mcd backfill box - no ingress, SSM access only",
                    "--vpc-id", vpc)["GroupId"]


def userdata_b64(bucket: str) -> str:
    with open(os.path.join(HERE, "bootstrap.sh"), newline="") as f:
        ud = f.read().replace("\r\n", "\n")
    ud = ud.replace("__BUCKET__", bucket).replace("__REGION__", REGION)
    return base64.b64encode(ud.encode()).decode()


def ensure_launch_template(ami: str, sg_id: str, ud_b64: str):
    data = json.dumps({
        "ImageId": ami,
        "InstanceType": INSTANCE_TYPE,
        "IamInstanceProfile": {"Name": PROFILE},
        "SecurityGroupIds": [sg_id],
        "BlockDeviceMappings": [{"DeviceName": "/dev/xvda", "Ebs": {
            "VolumeSize": VOLUME_GB, "VolumeType": "gp3",
            "Iops": 6000, "Throughput": 500, "DeleteOnTermination": True}}],
        "MetadataOptions": {"HttpTokens": "required"},
        "TagSpecifications": [{"ResourceType": "instance", "Tags": [
            {"Key": "Name", "Value": NAME}, {"Key": "Project", "Value": NAME}]}],
        "UserData": ud_b64,
    })
    if aws_json("ec2", "describe-launch-templates", "--launch-template-names", NAME,
                check=False) is None:
        print(f"creating launch template {NAME}")
        aws_json("ec2", "create-launch-template", "--launch-template-name", NAME,
                 "--launch-template-data", data)
    else:
        v = aws_json("ec2", "create-launch-template-version",
                     "--launch-template-name", NAME, "--launch-template-data", data
                     )["LaunchTemplateVersion"]["VersionNumber"]
        aws_json("ec2", "modify-launch-template", "--launch-template-name", NAME,
                 "--default-version", str(v))
        print(f"launch template {NAME} updated to v{v}")


def upload_bootstrap(bucket: str):
    tmp = os.path.join(tempfile.gettempdir(), "mcd_repo.tar.gz")
    with tarfile.open(tmp, "w:gz") as tar:
        skip = lambda ti: None if "__pycache__" in ti.name else ti  # noqa: E731
        tar.add(os.path.join(REPO, "src"), arcname="src", filter=skip)
        tar.add(os.path.join(REPO, "requirements.txt"), arcname="requirements.txt")
        tar.add(HERE, arcname="infra/backfill", filter=skip)
    api, s3k = creds_files()
    sh(["aws", "s3", "cp", tmp, f"s3://{bucket}/bootstrap/repo.tar.gz",
        "--only-show-errors", "--region", REGION])
    sh(["aws", "s3", "cp", api, f"s3://{bucket}/bootstrap/massive_api_key",
        "--only-show-errors", "--region", REGION])
    sh(["aws", "s3", "cp", s3k, f"s3://{bucket}/bootstrap/massive_s3_key",
        "--only-show-errors", "--region", REGION])


def seed_lake(bucket: str):
    lake = local_lake()
    print(f"seeding s3://{bucket}/lake/ from {lake} (aggregates only)")
    cmd = ["aws", "s3", "sync", lake, f"s3://{bucket}/lake/",
           "--only-show-errors", "--region", REGION]
    for e in EXCLUDES:
        cmd += ["--exclude", e]
    sh(cmd)


# ---- launch helpers -------------------------------------------------------------
def _run_instances(spot: bool) -> tuple[str | None, str]:
    """Launch one instance from the template. Returns (instance_id, "") on
    success or (None, stderr) on failure. Retries only the IAM instance-profile
    propagation lag — capacity/quota errors won't self-heal, so they return
    immediately for the caller to handle (e.g. Spot->on-demand fallback)."""
    market = json.dumps({"MarketType": "spot", "SpotOptions": {
        "SpotInstanceType": "one-time", "InstanceInterruptionBehavior": "terminate"}})
    cmd = ["aws", "ec2", "run-instances", "--launch-template",
           f"LaunchTemplateName={NAME}", "--count", "1",
           "--region", REGION, "--output", "json"]
    if spot:
        cmd += ["--instance-market-options", market]
    last = ""
    for attempt in range(4):
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0:
            return json.loads(r.stdout)["Instances"][0]["InstanceId"], ""
        last = r.stderr.strip()
        if "iam instance profile" in last.lower() or "invalid iam" in last.lower():
            print(f"  waiting for IAM instance-profile propagation ({attempt + 1}/4)...")
            time.sleep(10)
            continue
        break  # any other error is surfaced immediately
    return None, last


# ---- subcommands ----------------------------------------------------------------
def cmd_launch(args):
    inst = find_instance()
    if inst:
        sys.exit(f"box already up: {inst['InstanceId']} ({inst['State']['Name']}) — "
                 "use `status`, or `terminate` first")
    bucket = bucket_name()
    ensure_bucket(bucket)
    ensure_role(bucket)
    sg_id = ensure_sg()
    ami = aws_json("ssm", "get-parameters", "--names", AMI_SSM)["Parameters"][0]["Value"]
    ensure_launch_template(ami, sg_id, userdata_b64(bucket))
    upload_bootstrap(bucket)
    if args.no_seed:
        # REPROCESS mode. seed_lake pushes the LOCAL lake up to S3; on a
        # methodology change the local copy is the OLD schema, so seeding would
        # shove stale days over ones the box has already recomputed (aws s3 sync
        # copies whenever sizes differ, regardless of which side is newer).
        # Harmless when gap-filling, wrong when reprocessing.
        print("  --no-seed: skipping lake seed (reprocess mode)")
    else:
        seed_lake(bucket)

    spot = not args.on_demand
    iid, err = _run_instances(spot)
    if not iid and spot and "InsufficientInstanceCapacity" in err:
        print(f"  Spot capacity for {INSTANCE_TYPE} unavailable in {REGION} right now "
              "— falling back to on-demand\n"
              "  (~$0.38/hr; a ~2-4h backfill is well under $2, and no reclaim risk).")
        spot = False
        iid, err = _run_instances(spot)
    if not iid:
        sys.exit(f"run-instances failed:\n{err}")
    print(f"\nlaunched {iid} ({INSTANCE_TYPE}, "
          f"{'Spot' if spot else 'ON-DEMAND'}) — waiting for running state")
    sh(["aws", "ec2", "wait", "instance-running", "--instance-ids", iid,
        "--region", REGION])
    print(f"""
box is up. Bootstrap takes ~5-10 min (pip install + lake seed), then lanes start.
  progress:   python infra/backfill/backfill_ctl.py status
  fold in:    python infra/backfill/backfill_ctl.py pull   (then refresh_backfill.py)
  finish:     python infra/backfill/backfill_ctl.py terminate
If Spot reclaims the box (status goes stale / instance gone): just `launch` again —
it reuses everything and resumes where it stopped.
NOTE: stop the local backfill terminals now — the box covers those ranges.""")


def cmd_status(args):
    inst = find_instance("pending,running,shutting-down,stopped,terminated")
    if inst:
        print(f"instance: {inst['InstanceId']}  {inst['State']['Name']}  "
              f"({inst.get('InstanceType', '?')}, "
              f"{inst.get('InstanceLifecycle', 'on-demand')})")
    else:
        print("instance: none found (never launched, or terminated >1h ago)")
    raw = s3_cat(f"s3://{bucket_name()}/status/status.json")
    if not raw:
        print("status: no status.json yet (bootstrap still running?)")
        return
    st = json.loads(raw)
    print(f"box status: {st['state']}  as of {st['ts']} UTC  "
          f"(disk free {st.get('disk_free_gb', '?')} GB)")
    print(f"signed days: {st.get('signed_days', '?')}   "
          f"tape days remaining: {st.get('tape_days_remaining', '?')}")
    for t, v in st.get("tables", {}).items():
        print(f"  {t}: {v['days']} days  [{v['first']} .. {v['last']}]")
    for name, ln in sorted(st.get("lanes", {}).items()):
        extra = f" range={ln['range']}" if "range" in ln else ""
        err = f" ERR={ln['error']}" if ln.get("error") else ""
        print(f"  lane {name}: {ln.get('state', '?')} "
              f"done={ln.get('done_total', 0)}{extra}{err}")


def cmd_pull(args):
    lake = local_lake()
    print(f"merging s3 lake into {lake} (add/update only — never deletes)")
    # --exact-timestamps: plain `sync` skips a remote file when the LOCAL copy
    # is newer, which on a reprocess is exactly the case (the old day was
    # written locally after the box wrote the new one). Without this the
    # reprocessed days never land and everything looks fine.
    sh(["aws", "s3", "sync", f"s3://{bucket_name()}/lake/", lake,
        "--exact-timestamps", "--only-show-errors", "--region", REGION])
    print("done. now (from the main checkout):  python refresh_backfill.py")


def cmd_terminate(args):
    inst = find_instance()
    if not inst:
        sys.exit("no running instance found — nothing to terminate")
    iid = inst["InstanceId"]
    raw = s3_cat(f"s3://{bucket_name()}/status/status.json")
    if raw:
        st = json.loads(raw)
        print(f"last box status: {st['state']} as of {st['ts']} UTC "
              f"(signed={st.get('signed_days')}, "
              f"tape remaining={st.get('tape_days_remaining')})")
        if st["state"] != "done":
            print("WARNING: lanes not reported done — run `pull` first if you "
                  "haven't; unfinished days resume on a future `launch`.")
    if not args.yes:
        if input(f"terminate {iid}? [y/N] ").strip().lower() != "y":
            sys.exit("aborted")
    aws_json("ec2", "terminate-instances", "--instance-ids", iid)
    print(f"{iid} terminating. Bucket/role/template kept (free) — "
          "`launch` recreates the box any time; lake stays in S3 as a backup.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["launch", "status", "pull", "terminate"])
    ap.add_argument("--on-demand", action="store_true",
                    help="launch on-demand instead of Spot")
    ap.add_argument("--yes", action="store_true", help="skip confirm prompts")
    ap.add_argument("--no-seed", action="store_true",
                    help="launch: skip seeding the local lake to S3 — REQUIRED "
                         "for a methodology reprocess, see cmd_launch")
    args = ap.parse_args()
    {"launch": cmd_launch, "status": cmd_status,
     "pull": cmd_pull, "terminate": cmd_terminate}[args.cmd](args)


if __name__ == "__main__":
    main()

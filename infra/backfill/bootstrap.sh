#!/bin/bash
# bootstrap.sh — EC2 user-data for the Massive backfill box (AL2023).
# __BUCKET__/__REGION__ are substituted by backfill_ctl.py before upload.
# Log: /var/log/mcd-bootstrap.log   Lanes log: /var/log/mcd-lane-runner.log
set -uo pipefail
exec > /var/log/mcd-bootstrap.log 2>&1
set -x

BUCKET=__BUCKET__
REGION=__REGION__
export AWS_DEFAULT_REGION=$REGION

dnf install -y -q python3.11 python3.11-pip

mkdir -p /opt/backfill
cd /opt/backfill
aws s3 cp "s3://$BUCKET/bootstrap/repo.tar.gz" /tmp/repo.tar.gz
tar xzf /tmp/repo.tar.gz
aws s3 cp "s3://$BUCKET/bootstrap/massive_api_key" .massive_api_key
aws s3 cp "s3://$BUCKET/bootstrap/massive_s3_key" .massive_s3_key

python3.11 -m pip install -q -r requirements.txt boto3

# seed the lake with everything already pulled (workstation + prior boxes)
mkdir -p data/dashboard
aws s3 sync "s3://$BUCKET/lake/" data/dashboard/ --only-show-errors

MCD_BUCKET=$BUCKET MCD_REGION=$REGION \
  nohup python3.11 -u infra/backfill/lane_runner.py \
  >> /var/log/mcd-lane-runner.log 2>&1 &
echo "bootstrap complete — lane_runner started"

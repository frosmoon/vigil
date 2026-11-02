# Vigil production runbook

## Runtime

Vigil runs as a non-root Python container behind the existing Docker network. The container uses Gunicorn, not Flask's development server. Dependencies are installed while building the image, not while starting the service.

```bash
cd /home/bisha/docker-containers/configs/vigil
docker compose build --pull
docker compose up -d --force-recreate
docker compose ps
docker compose logs --tail=100 fleet-dash
```

The service is healthy when `/healthz` returns HTTP 200. A `503` means the first AWS poll failed and there is no usable state yet.

## Secrets

Keep AWS credentials in `.env`, which is loaded by Compose and must remain untracked and mode `600`:

```env
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
# AWS_SESSION_TOKEN=... only for temporary credentials
```

Never put credentials in `app.py`, `config.yml`, the image, or this document. Prefer an instance or workload IAM role when the host supports one.

## IAM policy

The `vigil-monitor` identity needs these actions:

- `ec2:DescribeInstances`
- `ec2:DescribeInstanceStatus`
- `ec2:StartInstances`
- `ec2:StopInstances`
- `ec2:RebootInstances`
- `cloudwatch:GetMetricData`

`cloudwatch:GetMetricData` requires `Resource: "*"`. Limit EC2 actions by resource or tag conditions when the account allows it. Check permissions boundaries, SCPs, and explicit denies if AWS returns `AccessDenied` after the policy is attached.

## Configuration

Edit `app/config.yml` to set the AWS regions and machine-specific TCP checks. The API reports the configured regions and last successful poll. CloudWatch failures are degraded gracefully: machine state remains visible while metric charts show no data.

## Operations

- `GET /healthz`: container and polling health.
- `GET /api/machines`: live EC2 inventory, metrics, tags, interfaces, and recent events.
- `POST /api/machines/<region>/<instance-id>/<action>`: start, stop, or reboot an instance.
- `POST /api/test-alert`: send the configured alert test.

Review logs after deployment and after IAM changes. Do not use `docker compose exec` to print environment variables because that can expose secrets in terminal history.

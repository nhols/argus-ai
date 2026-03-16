# Infrastructure

This directory contains a minimal DigitalOcean deployment layout for one single-tenant ArgusAI instance.

Current scope:

- one DigitalOcean droplet (`s-1vcpu-1gb`)
- one firewall
- public IPv4
- SSH access restricted by CIDR
- local video storage on the droplet filesystem
- no object storage
- no DNS

## Layout

- `modules/droplet`: DigitalOcean droplet and firewall rules
- `environments/example`: example environment wiring the modules together
- `scripts/bootstrap.sh.tftpl`: instance bootstrap script used by Terraform
- `scripts/deploy.sh`: local deploy helper to sync the repo, copy `.env`, and start Compose

## Typical flow

1. Copy `infra/environments/example/terraform.tfvars.example` to `terraform.tfvars` and fill in values.
2. Run `terraform init` and `terraform apply` from the environment directory.
3. Use `infra/scripts/deploy.sh` to:
   - sync the repo to the instance
   - copy the `.env`
   - run `docker compose up -d --build`

# Infrastructure

This directory contains a minimal AWS deployment layout for one instance of the
stack.

Current scope:

- one EC2 instance
- one S3 bucket
- one IAM role / instance profile
- private EC2 instance in an existing VPC / subnet
- AWS Systems Manager (SSM) access instead of SSH / public IPv4
- no DNS
- no Terraform-managed secrets

## Layout

- `modules/storage`: S3 bucket and lifecycle configuration
- `modules/iam`: EC2 IAM role, instance profile, and S3 access policy
- `modules/instance`: EC2 instance, security group, and bootstrap user data
- `environments/example`: example environment wiring the modules together
- `scripts/bootstrap.sh.tftpl`: instance bootstrap script used by Terraform
- `scripts/deploy.sh`: local deploy helper to upload config to S3 and deploy over SSM

## Typical flow

1. Copy `infra/environments/example/terraform.tfvars.example` to `terraform.tfvars` and fill in values.
2. Run `terraform init` and `terraform apply` from the environment directory.
3. Use `infra/scripts/deploy.sh` to:
   - clone or update the repo on the instance
   - write the `.env` on the instance
   - upload the analyser config JSON to S3
   - run `docker compose up -d --build` via SSM

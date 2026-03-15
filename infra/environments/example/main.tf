module "storage" {
  source = "../../modules/storage"

  bucket_name           = var.bucket_name
  video_prefix          = var.video_prefix
  video_expiration_days = var.video_expiration_days
  tags                  = var.tags
}

module "iam" {
  source = "../../modules/iam"

  name         = var.name
  bucket_arn   = module.storage.bucket_arn
  config_key   = var.config_key
  video_prefix = module.storage.video_prefix
  tags         = var.tags
}

module "instance" {
  source = "../../modules/instance"

  name                  = var.name
  vpc_id                = var.vpc_id
  subnet_id             = var.subnet_id
  instance_type         = var.instance_type
  instance_profile_name = module.iam.instance_profile_name
  root_volume_size_gb   = var.root_volume_size_gb
  app_dir               = var.app_dir
  user_data_template    = "${path.module}/../../scripts/bootstrap.sh.tftpl"
  tags                  = var.tags
}

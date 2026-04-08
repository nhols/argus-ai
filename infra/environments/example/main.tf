module "droplet" {
  source = "../../modules/droplet"

  name                = var.name
  region              = var.do_region
  size                = var.droplet_size
  image               = var.image
  ssh_key_fingerprint = var.ssh_key_fingerprint
  ssh_cidr            = var.ssh_cidr
  app_port            = var.app_port
  app_dir             = var.app_dir
  project_name        = var.project_name
  swap_size_mb        = var.swap_size_mb
  user_data_template  = "${path.module}/../../scripts/bootstrap.sh.tftpl"
  tags                = var.tags
}

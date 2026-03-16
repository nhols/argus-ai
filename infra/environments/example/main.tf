module "droplet" {
  source = "../../modules/droplet"

  name                = var.name
  region              = var.do_region
  size                = var.droplet_size
  image               = var.image
  ssh_key_fingerprint = var.ssh_key_fingerprint
  ssh_cidr            = var.ssh_cidr
  app_dir             = var.app_dir
  user_data_template  = "${path.module}/../../scripts/bootstrap.sh.tftpl"
  tags                = var.tags
}

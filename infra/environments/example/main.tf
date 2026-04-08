module "droplet" {
  source = "../../modules/droplet"

  name                = var.name
  region              = var.do_region
  size                = var.droplet_size
  image               = var.image
  ssh_key_fingerprint = var.ssh_key_fingerprint
  ssh_cidr            = var.ssh_cidr
  public_http_ports   = var.public_http_ports
  app_dir             = var.app_dir
  swap_size_mb        = var.swap_size_mb
  user_data_template  = "${path.module}/../../scripts/bootstrap.sh.tftpl"
  tags                = var.tags
}

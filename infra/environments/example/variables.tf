variable "do_token" {
  type      = string
  sensitive = true
}

variable "do_region" {
  type    = string
  default = "lon1"
}

variable "name" {
  type = string
}

variable "droplet_size" {
  type    = string
  default = "s-1vcpu-1gb"
}

variable "image" {
  type    = string
  default = "ubuntu-24-04-x64"
}

variable "ssh_key_fingerprint" {
  type = string
}

variable "ssh_cidr" {
  type    = string
  default = "0.0.0.0/0"
}

variable "public_http_ports" {
  type    = list(number)
  default = [80, 443]
}

variable "app_dir" {
  type    = string
  default = "/opt/argusai"
}

variable "swap_size_mb" {
  type    = number
  default = 1024
}

variable "tags" {
  type    = map(string)
  default = {}
}

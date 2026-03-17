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

variable "app_port" {
  type    = number
  default = 8000
}

variable "app_dir" {
  type    = string
  default = "/opt/argusai"
}

variable "tags" {
  type    = map(string)
  default = {}
}

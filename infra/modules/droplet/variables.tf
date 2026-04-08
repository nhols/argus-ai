variable "name" {
  type = string
}

variable "region" {
  type = string
}

variable "size" {
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
  type = string
}

variable "app_port" {
  type    = number
  default = 8000
}

variable "app_dir" {
  type    = string
  default = "/opt/argusai"
}

variable "swap_size_mb" {
  type    = number
  default = 1024
}

variable "user_data_template" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}

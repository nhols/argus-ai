variable "aws_region" {
  type = string
}

variable "name" {
  type = string
}

variable "bucket_name" {
  type = string
}

variable "config_key" {
  type    = string
  default = "config/run_config.json"
}

variable "video_prefix" {
  type    = string
  default = "videos"
}

variable "video_expiration_days" {
  type    = number
  default = null
}

variable "instance_type" {
  type    = string
  default = "t3.small"
}

variable "subnet_id" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "root_volume_size_gb" {
  type    = number
  default = 20
}

variable "app_dir" {
  type    = string
  default = "/opt/eufy-client"
}

variable "tags" {
  type    = map(string)
  default = {}
}

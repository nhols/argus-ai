variable "name" {
  type = string
}

variable "bucket_arn" {
  type = string
}

variable "config_key" {
  type = string
}

variable "video_prefix" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}

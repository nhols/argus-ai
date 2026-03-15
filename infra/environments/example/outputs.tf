output "instance_id" {
  value = module.instance.instance_id
}

output "instance_private_ip" {
  value = module.instance.private_ip
}

output "bucket_name" {
  value = module.storage.bucket_name
}

output "config_key" {
  value = var.config_key
}

output "video_prefix" {
  value = module.storage.video_prefix
}

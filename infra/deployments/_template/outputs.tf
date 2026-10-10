output "url" {
  value = module.app.url
}

output "cluster_name" {
  value = module.app.cluster_name
}

output "service_name" {
  value = module.app.service_name
}

output "target_group_arn" {
  value = module.app.target_group_arn
}

output "log_group_name" {
  value = module.app.log_group_name
}

output "image" {
  value = module.app.image
}

output "task_security_group_id" {
  value = module.app.task_security_group_id
}

output "task_definition_arn" {
  value = module.app.task_definition_arn
}

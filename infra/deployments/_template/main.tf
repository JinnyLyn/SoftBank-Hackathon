module "app" {
  source = "../../modules/ecs-web-app"

  # 플랫폼 입력
  deploy_id        = var.platform.deploy_id
  image            = var.platform.image
  cpu_architecture = var.platform.cpu_architecture
  listener_port    = var.platform.listener_port
  foundation       = var.platform.foundation

  database_url_parameter_arn = var.platform.database_url_parameter_arn
  health_check_grace_seconds = var.platform.health_check_grace_seconds

  # LLM 입력 (승인된 값)
  container_port    = var.app.container_port
  health_check_path = var.app.health_check_path
  task_size         = var.app.task_size
  min_tasks         = var.app.min_tasks
  max_tasks         = var.app.max_tasks
  use_database      = var.app.use_database
  environment       = var.app.environment
}

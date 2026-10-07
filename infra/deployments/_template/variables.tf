# 입력은 두 파일로 나눈다.
#   platform.auto.tfvars.json : 플랫폼 코드가 쓴다 (배포 ID, 이미지, 포트, foundation 출력)
#   app.auto.tfvars.json      : 스키마 검증을 통과하고 사용자가 승인한 LLM 출력을 그대로 쓴다
# 승인 화면에는 app 파일 내용과 plan 결과를 같이 보여준다.

variable "platform" {
  description = "플랫폼이 결정하는 값"
  type = object({
    region           = string
    deploy_id        = string
    image            = string
    cpu_architecture = optional(string, "X86_64")
    listener_port    = number
    foundation = object({
      alb_arn      = string
      alb_dns_name = string
      allowed_listener_ports = object({
        from = number
        to   = number
      })
      assign_public_ip           = bool
      cluster_name               = string
      database_url_parameter_arn = string
      ecr_repository_url         = string
      execution_role_arn         = string
      task_security_group_id     = string
      task_subnet_ids            = list(string)
      vpc_id                     = string
    })
  })
}

variable "app" {
  description = "LLM이 제안하고 사용자가 승인한 값. 필드 규칙은 modules/ecs-web-app/app-config.schema.json"
  type = object({
    container_port    = number
    health_check_path = optional(string, "/")
    task_size         = optional(string, "xsmall")
    min_tasks         = optional(number, 1)
    max_tasks         = optional(number, 1)
    use_database      = optional(bool, false)
    environment       = optional(map(string), {})
  })
}

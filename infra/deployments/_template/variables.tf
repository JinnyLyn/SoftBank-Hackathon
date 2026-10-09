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
    # 이 배포 전용 DB 접속 정보 파라미터. 비우면 공유 DB URL을 쓴다 (deploy.sh가 앱별 DB를 만들면 채운다)
    database_url_parameter_arn = optional(string, "")
    # 실패한 배포를 얼마나 빨리 확정할지 조절한다. 느리게 뜨는 앱은 늘린다
    health_check_grace_seconds = optional(number, 90)
    # 교체되는 태스크가 처리 중인 요청을 끝내도록 기다리는 시간(초)
    deregistration_delay_seconds = optional(number, 30)
    foundation = object({
      alb_arn      = string
      alb_dns_name = string
      allowed_listener_ports = object({
        from = number
        to   = number
      })
      alb_security_group_id      = string
      assign_public_ip           = bool
      certificate_arn            = optional(string, "")
      cluster_name               = string
      database_url_parameter_arn = string
      db_security_group_id       = string
      ecr_repository_url         = string
      execution_role_arn         = string
      listener_protocol          = optional(string, "HTTP")
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

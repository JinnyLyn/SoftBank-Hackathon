# 입력은 두 파일로 나눈다.
#   platform.auto.tfvars.json : 플랫폼 코드가 쓴다 (배포 ID, 이미지, 포트 또는 도메인, foundation 출력)
#   app.auto.tfvars.json      : 스키마 검증을 통과하고 사용자가 승인한 LLM 출력을 그대로 쓴다
# 승인 화면에는 app 파일 내용과 plan 결과를 같이 보여준다.

variable "platform" {
  description = "플랫폼이 결정하는 값"
  type = object({
    region           = string
    deploy_id        = string
    image            = string
    cpu_architecture = optional(string, "X86_64")
    # 포트 방식의 리스너 포트. 도메인 방식(domain_name)이면 쓰지 않는다(0)
    listener_port = optional(number, 0)
    # 도메인 방식: <deploy_id>.<foundation.app_domain>과 공유 443 리스너의 규칙 우선순위. deploy.sh up --domain이 채운다
    domain_name            = optional(string, "")
    listener_rule_priority = optional(number, 0)
    # 이 배포 전용 DB 접속 정보 파라미터. 비우면 공유 DB URL을 쓴다 (deploy.sh가 앱별 DB를 만들면 채운다)
    database_url_parameter_arn = optional(string, "")
    # 실패한 배포를 얼마나 빨리 확정할지 조절한다. 느리게 뜨는 앱은 늘린다
    health_check_grace_seconds = optional(number, 90)
    # 교체되는 태스크가 처리 중인 요청을 끝내도록 기다리는 시간(초)
    deregistration_delay_seconds = optional(number, 30)
    # 앱에 비밀로 주입할 SSM 파라미터 {환경 변수 이름 = ARN}. deploy.sh up --secret 이 채운다. 플랫폼이 정하는 값이다(LLM 입력 아님)
    secret_parameter_arns = optional(map(string), {})
    foundation = object({
      alb_arn      = string
      alb_dns_name = string
      allowed_listener_ports = object({
        from = number
        to   = number
      })
      alb_security_group_id      = string
      app_domain                 = optional(string, "")
      assign_public_ip           = bool
      certificate_arn            = optional(string, "")
      cluster_name               = string
      database_url_parameter_arn = string
      db_security_group_id       = string
      ecr_repository_url         = string
      execution_role_arn         = string
      https_listener_arn         = optional(string, "")
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
    # 앱 이미지로 1회 실행할 초기화 명령(테이블 생성 등). Terraform은 쓰지 않고 deploy.sh가 apply 뒤에 실행한다
    init_command = optional(list(string), [])
  })
}

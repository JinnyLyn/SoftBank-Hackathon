###############################################################################
# 플랫폼이 정하는 값 (코드가 결정, LLM 관여 없음)
###############################################################################

variable "project" {
  description = "프로젝트 이름. 리소스 이름과 로그 그룹 경로에 쓴다"
  type        = string
  default     = "paved-clouds"
}

variable "short_prefix" {
  description = "32자 이름 제한이 있는 리소스(대상 그룹)에 쓰는 짧은 접두사"
  type        = string
  default     = "pc"

  validation {
    condition     = can(regex("^[a-z]{2,6}$", var.short_prefix))
    error_message = "short_prefix는 소문자 2~6자여야 합니다."
  }
}

variable "deploy_id" {
  description = "배포 건 식별자. 리소스 이름과 태그에 들어간다"
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9]{4,8}$", var.deploy_id))
    error_message = "deploy_id는 소문자·숫자 4~8자여야 합니다."
  }
}

variable "image" {
  description = "배포할 이미지. 프로젝트 ECR의 고정 태그 또는 다이제스트만 허용한다"
  type        = string

  validation {
    condition = (
      can(regex("^[0-9]{12}\\.dkr\\.ecr\\.[a-z0-9-]+\\.amazonaws\\.com/[a-z0-9._/-]+(:[A-Za-z0-9._-]+|@sha256:[a-f0-9]{64})$", var.image)) &&
      !endswith(var.image, ":latest")
    )
    error_message = "image는 ECR 이미지여야 하고, 태그나 다이제스트를 명시해야 하며, latest 태그는 쓸 수 없습니다."
  }
}

variable "cpu_architecture" {
  description = "이미지를 빌드한 CPU 아키텍처. Apple Silicon에서 기본 빌드한 이미지는 ARM64다"
  type        = string
  default     = "X86_64"

  validation {
    condition     = contains(["X86_64", "ARM64"], var.cpu_architecture)
    error_message = "cpu_architecture는 X86_64 또는 ARM64여야 합니다."
  }
}

variable "listener_port" {
  description = "이 배포에 할당한 공유 ALB 리스너 포트"
  type        = number
}

variable "log_retention_days" {
  description = "CloudWatch Logs 보관 기간(일)"
  type        = number
  default     = 7

  validation {
    condition     = contains([1, 3, 5, 7, 14, 30], var.log_retention_days)
    error_message = "log_retention_days는 1, 3, 5, 7, 14, 30 중 하나여야 합니다."
  }
}

# foundation 출력값 (terraform output -json deploy_inputs)
variable "foundation" {
  description = "사전 생성 리소스 정보. foundation 스택의 deploy_inputs 출력을 그대로 받는다"
  type = object({
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

  validation {
    condition     = contains(["HTTP", "HTTPS"], var.foundation.listener_protocol)
    error_message = "foundation.listener_protocol은 HTTP 또는 HTTPS여야 합니다."
  }
}

variable "database_url_parameter_arn" {
  description = "이 배포 전용 DB 접속 정보가 든 SSM 파라미터 ARN. 비우면 foundation의 공유 DB URL(관리자 계정)을 쓴다. deploy.sh가 앱별 DB와 계정을 만들고 이 값을 채운다"
  type        = string
  default     = ""

  validation {
    condition     = var.database_url_parameter_arn == "" || can(regex("^arn:aws[a-z-]*:ssm:[a-z0-9-]+:[0-9]{12}:parameter/.+$", var.database_url_parameter_arn))
    error_message = "database_url_parameter_arn은 SSM 파라미터 ARN이어야 합니다."
  }
}

variable "deregistration_delay_seconds" {
  description = "교체되는 태스크가 대상 그룹에서 빠질 때 처리 중인 요청을 끝내도록 기다리는 시간(초). 짧으면 배포가 빨리 끝나지만 오래 걸리는 요청(업로드 등)이 끊긴다. ALB 기본값은 300"
  type        = number
  default     = 30

  validation {
    condition     = var.deregistration_delay_seconds >= 0 && var.deregistration_delay_seconds <= 300
    error_message = "deregistration_delay_seconds는 0~300이어야 합니다."
  }
}

variable "health_check_grace_seconds" {
  description = "새 태스크가 뜬 뒤 헬스체크 실패를 무시하는 시간(초). 느리게 기동하는 앱(JVM 등)은 늘려서 오탐 실패를 막는 용도다. 줄여도 실패한 배포가 빨리 확정되지는 않는다. 서킷 브레이커는 태스크가 3번 실패해야 배포를 FAILED로 만드는데, 실측으로 유예 90초에서 527초, 30초에서 500초가 걸렸다(태스크 기동, 대상 등록, 헬스체크 실패, 교체 시간이 지배적)"
  type        = number
  default     = 90

  validation {
    condition     = var.health_check_grace_seconds >= 0 && var.health_check_grace_seconds <= 600
    error_message = "health_check_grace_seconds는 0~600이어야 합니다."
  }
}

###############################################################################
# LLM이 제안하고 사용자가 승인하는 값 (3단계 분석, 5단계 구성 추천의 결과)
# 여기 있는 검증은 LLM 출력 JSON 스키마 검증 다음의 두 번째 방어선이다.
###############################################################################

variable "container_port" {
  description = "앱 컨테이너가 수신하는 포트 (3단계 분석 결과)"
  type        = number

  validation {
    condition     = var.container_port >= 1 && var.container_port <= 65535 && floor(var.container_port) == var.container_port
    error_message = "container_port는 1~65535 사이의 정수여야 합니다."
  }
}

variable "health_check_path" {
  description = "ALB 헬스체크 경로 (3단계 분석 결과)"
  type        = string
  default     = "/"

  validation {
    condition     = can(regex("^/[A-Za-z0-9._~/-]*$", var.health_check_path)) && length(var.health_check_path) <= 128
    error_message = "health_check_path는 /로 시작하고 공백·특수문자 없이 128자 이하여야 합니다."
  }
}

variable "task_size" {
  description = "태스크 크기 프리셋 (5단계 추천 결과). 비용 계산 코드는 같은 프리셋 표를 쓴다"
  type        = string
  default     = "xsmall"

  validation {
    condition     = contains(["xsmall", "small", "medium"], var.task_size)
    error_message = "task_size는 xsmall, small, medium 중 하나여야 합니다."
  }
}

variable "min_tasks" {
  description = "최소 태스크 수 (5단계 추천 결과)"
  type        = number
  default     = 1

  validation {
    condition     = contains([1, 2], var.min_tasks)
    error_message = "min_tasks는 1 또는 2여야 합니다."
  }
}

variable "max_tasks" {
  description = "최대 태스크 수. min_tasks보다 크면 CPU 기준 오토스케일링을 켠다 (5단계 추천 결과)"
  type        = number
  default     = 1

  validation {
    condition     = contains([1, 2, 3, 4], var.max_tasks)
    error_message = "max_tasks는 1~4여야 합니다. 지원금 범위를 넘지 않도록 상한을 둔다."
  }
}

variable "use_database" {
  description = "앱이 DB를 쓰는지 여부 (3단계 분석 결과). true면 DATABASE_URL을 주입한다"
  type        = bool
  default     = false
}

variable "environment" {
  description = "비밀이 아닌 앱 환경 변수 (3단계 분석 결과)"
  type        = map(string)
  default     = {}

  validation {
    condition     = length(var.environment) <= 20
    error_message = "environment는 20개 이하여야 합니다."
  }

  validation {
    condition     = alltrue([for k in keys(var.environment) : can(regex("^[A-Z_][A-Z0-9_]{0,63}$", k))])
    error_message = "environment 키는 대문자, 숫자, 밑줄로 된 64자 이하 이름이어야 합니다."
  }

  validation {
    condition     = !contains(keys(var.environment), "DATABASE_URL")
    error_message = "DATABASE_URL은 environment로 넘길 수 없습니다. use_database=true로 secrets 주입을 사용하세요."
  }

  # 비밀로 보이는 이름은 평문 환경 변수로 넣지 못하게 막는다 (CLAUDE.md 필수 규칙 4)
  validation {
    condition = alltrue([
      for k in keys(var.environment) :
      !can(regex("(SECRET|PASSWORD|PASSWD|TOKEN|PRIVATE|CREDENTIAL|API_?KEY|ACCESS_?KEY)", k))
    ])
    error_message = "비밀로 보이는 이름(SECRET, PASSWORD, TOKEN, API_KEY 등)은 environment에 넣을 수 없습니다."
  }
}

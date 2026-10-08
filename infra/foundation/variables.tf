variable "project" {
  description = "리소스 이름과 태그에 쓰는 프로젝트 이름"
  type        = string
  default     = "paved-clouds"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,20}$", var.project))
    error_message = "project는 소문자로 시작하는 3~21자의 소문자·숫자·하이픈이어야 합니다."
  }
}

variable "region" {
  description = "배포 리전"
  type        = string
  default     = "ap-northeast-2"
}

variable "vpc_cidr" {
  description = "VPC CIDR. /16 기준으로 서브넷을 /24로 나눈다"
  type        = string
  default     = "10.20.0.0/16"

  validation {
    condition     = can(cidrhost(var.vpc_cidr, 0)) && endswith(var.vpc_cidr, "/16")
    error_message = "vpc_cidr는 /16 CIDR이어야 합니다."
  }
}

variable "enable_nat_instance" {
  description = "true면 앱 태스크를 프라이빗 서브넷 + NAT 인스턴스로 실행. false면 퍼블릭 서브넷 + 퍼블릭 IP로 실행(비용 절감)"
  type        = bool
  default     = false
}

variable "nat_instance_type" {
  description = "NAT 인스턴스 타입. AMI가 arm64이므로 Graviton 계열(t4g 등)이어야 한다"
  type        = string
  default     = "t4g.small"

  validation {
    condition     = can(regex("^[a-z]+[0-9]+g[a-z]*\\.[a-z0-9]+$", var.nat_instance_type))
    error_message = "nat_instance_type은 Graviton(arm64) 인스턴스 타입이어야 합니다. 예: t4g.small"
  }
}

variable "listener_port_range" {
  description = "배포별 ALB 리스너에 할당할 포트 범위. ALB당 리스너 기본 할당량 50개 중 80번 기본 리스너가 1개를 쓰므로 최대 49개"
  type = object({
    from = number
    to   = number
  })
  default = {
    from = 8001
    to   = 8049
  }

  validation {
    condition = (
      var.listener_port_range.from >= 1024 &&
      var.listener_port_range.to <= 65535 &&
      var.listener_port_range.to >= var.listener_port_range.from &&
      var.listener_port_range.to - var.listener_port_range.from < 49
    )
    error_message = "listener_port_range는 1024~65535 사이, 최대 49개 포트여야 합니다."
  }
}

variable "db_engine_version" {
  description = "RDS MySQL 메이저 버전. 8.0은 2026-07-31 표준 지원이 끝나 유료 Extended Support 대상이므로 쓰지 않는다"
  type        = string
  default     = "8.4"

  validation {
    condition     = var.db_engine_version != "8.0" && !startswith(var.db_engine_version, "8.0.")
    error_message = "MySQL 8.0은 표준 지원이 종료되어 사용할 수 없습니다."
  }
}

variable "db_instance_class" {
  description = "RDS 인스턴스 클래스"
  type        = string
  default     = "db.t4g.micro"
}

variable "db_allocated_storage" {
  description = "RDS 스토리지(GiB)"
  type        = number
  default     = 20
}

variable "db_name" {
  description = "기본 데이터베이스 이름"
  type        = string
  default     = "app"
}

variable "db_username" {
  description = "RDS 마스터 사용자 이름"
  type        = string
  default     = "paved_admin"
}

variable "ecr_keep_images" {
  description = "ECR에 보관할 최근 이미지 수. 롤백 대상 이미지가 지워지지 않도록 넉넉하게 둔다"
  type        = number
  default     = 30
}

variable "protect_from_destroy" {
  description = "true면 RDS 삭제 보호를 켜고 ECR 강제 삭제를 막는다. 행사 종료 후 정리할 때만 false로 바꾼다"
  type        = bool
  default     = true
}

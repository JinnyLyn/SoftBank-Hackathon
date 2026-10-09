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
  description = "배포 리전. 계정에 지정된 리전을 반드시 직접 지정한다. 기본값을 두면 변수를 빼먹었을 때 엉뚱한 리전에 만들어진다"
  type        = string

  validation {
    condition     = can(regex("^[a-z]{2}(-[a-z]+)+-[0-9]$", var.region))
    error_message = "region은 sa-east-1 같은 AWS 리전 이름이어야 합니다."
  }
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

variable "enable_db_lambda" {
  description = "true(기본)면 앱별 DB 준비·확인을 VPC 안의 Lambda로 한다(몇 초). false면 이 Lambda를 만들지 않고 deploy.sh가 Fargate 작업으로 대신한다(약 70초 더 걸림). 끄면 deploy.sh의 db_provisioner_lambda_name 출력이 비어 자동으로 Fargate 경로를 쓴다"
  type        = bool
  default     = true
}

variable "enable_nat_instance" {
  description = "true(기본)면 앱 태스크를 프라이빗 서브넷 + NAT 인스턴스(NAT Gateway 대신)로 실행. false면 퍼블릭 서브넷 + 퍼블릭 IP로 실행(비용 절감, 시험용)"
  type        = bool
  default     = true
}

variable "nat_instance_type" {
  description = "NAT 인스턴스 타입. AMI가 arm64이므로 Graviton 계열(t4g 등)이어야 한다"
  type        = string
  default     = "t4g.small"

  validation {
    condition     = can(regex("^[a-z]+[0-9]+g[a-z]*\\.[a-z0-9]+$", var.nat_instance_type))
    error_message = "nat_instance_type은 Graviton(arm64) 인스턴스 타입이어야 합니다. 예: t4g.small"
  }

  # nano(0.5GB)와 micro(1GB)는 부팅할 때 iptables-services 설치가 메모리 부족으로 종료되어 NAT가 동작하지 않는다.
  # 이때 인스턴스 상태 검사는 정상으로 나와서 겉으로는 알 수 없다. small(2GB) 이상만 허용한다
  validation {
    condition     = !can(regex("[.](nano|micro)$", var.nat_instance_type))
    error_message = "nat_instance_type은 small(메모리 2GB) 이상이어야 합니다. nano·micro는 NAT 초기화가 메모리 부족으로 실패합니다."
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
  description = "ECR에 보관할 최근 앱 이미지 수. 저장소 하나를 모든 앱이 같이 쓰므로 앱 수 x 롤백으로 되돌릴 버전 수보다 커야 한다(앱 49개 x 4버전 = 196). 부족하면 오래된 앱의 롤백 대상 이미지가 지워진다"
  type        = number
  default     = 200

  validation {
    condition     = var.ecr_keep_images >= 10 && var.ecr_keep_images <= 1000
    error_message = "ecr_keep_images는 10~1000이어야 합니다."
  }
}

variable "protect_from_destroy" {
  description = "true면 RDS 삭제 보호를 켜고 ECR 강제 삭제를 막는다. 행사 종료 후 정리할 때만 false로 바꾼다"
  type        = bool
  default     = true
}

variable "certificate_arn" {
  description = "ALB HTTPS 리스너에 쓸 ACM 인증서 ARN. 비우면 HTTP만 연다(개발·시험용). 실제 서비스는 반드시 지정한다. 지정하면 80번은 443으로 넘기고 배포별 리스너도 HTTPS가 된다"
  type        = string
  default     = ""

  validation {
    condition     = var.certificate_arn == "" || can(regex("^arn:aws[a-z-]*:acm:[a-z0-9-]+:[0-9]{12}:certificate/[a-f0-9-]+$", var.certificate_arn))
    error_message = "certificate_arn은 ACM 인증서 ARN(arn:aws:acm:리전:계정:certificate/...)이어야 합니다."
  }
}

variable "az_count" {
  description = "사용할 가용 영역 수. 서브넷과 NAT 인스턴스(고가용성일 때)가 이 수만큼 만들어진다. ALB와 RDS 서브넷 그룹은 최소 2개가 필요하다"
  type        = number
  default     = 3

  validation {
    condition     = contains([2, 3], var.az_count)
    error_message = "az_count는 2 또는 3이어야 합니다."
  }
}

variable "nat_high_availability" {
  description = "true면 가용 영역마다 NAT 인스턴스를 하나씩(AZ 하나가 멈춰도 다른 AZ는 외부 통신 유지). false면 NAT 인스턴스 1대로 모든 AZ가 같이 쓴다(비용 절감, 그 AZ가 멈추면 전체 외부 통신 중단)"
  type        = bool
  default     = true
}

variable "nat_ami_id" {
  description = "NAT 인스턴스 AMI를 고정하려면 지정한다(예: ami-0123456789abcdef0). 비우면 Amazon Linux 2023 arm64 최신 AMI를 쓴다. 어느 쪽이든 만든 뒤 AMI가 바뀌어도 인스턴스를 교체하지 않는다"
  type        = string
  default     = ""

  validation {
    condition     = var.nat_ami_id == "" || can(regex("^ami-[0-9a-f]{8,17}$", var.nat_ami_id))
    error_message = "nat_ami_id는 ami-로 시작하는 AMI ID이어야 합니다."
  }
}

variable "db_backup_retention_days" {
  description = "RDS 자동 백업 보관 기간(일). 장애나 실수로 지웠을 때 이 기간 안의 시점으로 복구할 수 있다"
  type        = number
  default     = 7

  validation {
    condition     = var.db_backup_retention_days >= 1 && var.db_backup_retention_days <= 35
    error_message = "db_backup_retention_days는 1~35여야 합니다."
  }
}

variable "db_multi_az" {
  description = "true면 RDS를 다른 가용 영역에 대기 복제본을 두는 다중 AZ로 만든다(비용 약 2배, 장애 시 자동 전환)"
  type        = bool
  default     = false
}

variable "final_snapshot" {
  description = "true면 RDS를 지울 때 최종 스냅샷을 남긴다. 시험용으로 지우고 다시 만들 때만 false로 한다"
  type        = bool
  default     = true
}

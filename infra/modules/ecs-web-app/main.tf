# ecs-web-app: 웹 앱 컨테이너 1개를 ECS Fargate로 배포하는 검증된 모듈이다. 배포 1건이 이 모듈 1회 호출이다.
# VPC, ALB, ECS 클러스터, ECR, RDS는 infra/foundation이 미리 만들어 두었고, 이 모듈은 그 위에 앱만 올린다.
#
# 요청이 앱에 닿는 길: 인터넷 → 공유 ALB의 전용 포트 리스너 → 대상 그룹 → ECS 태스크(컨테이너)
#
# 파일 구성 (AWS 서비스별)
#   cloudwatch.tf   로그 그룹: 컨테이너 로그 저장
#   ecs.tf          태스크 정의(컨테이너 실행 명세)와 서비스(실행 유지 관리)
#   alb.tf          대상 그룹과 전용 포트 리스너: ALB가 요청을 앱으로 전달
#   autoscaling.tf  태스크 수 범위와 CPU 기준 자동 증감
#   variables.tf    입력 변수. outputs.tf: 플랫폼이 쓰는 출력값. app-config.schema.json: LLM 출력 스키마

data "aws_region" "current" {}

locals {
  name           = "${var.project}-${var.deploy_id}"
  short_name     = "${var.short_prefix}-${var.deploy_id}"
  container_name = "app"

  # 태스크 크기 프리셋. 비용 계산 코드(6단계)는 이 표와 같은 값을 써야 한다
  task_sizes = {
    xsmall = { cpu = 256, memory = 512 }
    small  = { cpu = 512, memory = 1024 }
    medium = { cpu = 1024, memory = 2048 }
  }
  size = local.task_sizes[var.task_size]

  environment = [
    for k in sort(keys(var.environment)) : {
      name  = k
      value = var.environment[k]
    }
  ]

  # DB 접속 정보는 평문 environment가 아니라 secrets로만 주입한다 (CLAUDE.md 3.2)
  secrets = var.use_database ? [{
    name      = "DATABASE_URL"
    valueFrom = var.foundation.database_url_parameter_arn
  }] : []

  autoscaling_enabled = var.max_tasks > var.min_tasks
}

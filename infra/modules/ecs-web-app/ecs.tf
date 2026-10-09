# ECS Fargate: 서버 없이 컨테이너를 실행하는 서비스다.
#   태스크 정의: 어떤 이미지를, CPU·메모리를 얼마나 주고, 어떤 환경 변수로 실행할지 적은 명세서
#   서비스: 그 명세대로 태스크를 정해진 개수만큼 계속 실행하고, 죽으면 다시 띄우며, 대상 그룹에 등록한다
# 이미지를 바꿔 다시 apply하면 새 태스크 정의로 교체된다. 롤백도 이전 이미지로 이 과정을 반복하는 것이다.
#
# 관련 파일: cloudwatch.tf(로그), alb.tf(요청 연결), autoscaling.tf(태스크 수 조절)

resource "aws_ecs_task_definition" "app" {
  family                   = local.name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(local.size.cpu)
  memory                   = tostring(local.size.memory)
  execution_role_arn       = var.foundation.execution_role_arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }

  container_definitions = jsonencode([{
    name         = local.container_name
    image        = var.image
    essential    = true
    portMappings = [{ containerPort = var.container_port, protocol = "tcp" }]
    environment  = local.environment
    secrets      = local.secrets
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.app.name
        awslogs-region        = data.aws_region.current.region
        awslogs-stream-prefix = local.container_name
      }
    }
  }])

  lifecycle {
    precondition {
      condition     = startswith(var.image, "${var.foundation.ecr_repository_url}:") || startswith(var.image, "${var.foundation.ecr_repository_url}@")
      error_message = "image는 foundation이 만든 ECR 저장소의 이미지여야 합니다."
    }
  }
}

resource "aws_ecs_service" "app" {
  name             = local.name
  cluster          = var.foundation.cluster_name
  task_definition  = aws_ecs_task_definition.app.arn
  desired_count    = var.min_tasks
  launch_type      = "FARGATE"
  platform_version = "LATEST"

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  # 새 태스크가 계속 뜨자마자 죽으면 ECS는 기본적으로 끝없이 재시도하고 배포가 IN_PROGRESS로 남는다.
  # 서킷 브레이커를 켜면 반복 실패 시 배포가 FAILED가 된다. 이전 정상 버전으로의 자동 복구(rollback)는
  # 허용 범위가 팀에서 확정되기 전이라 켜지 않는다 (AGENTS.md 6장). 복구는 deploy.sh rollback으로 사람이 한다
  deployment_circuit_breaker {
    enable   = true
    rollback = false
  }
  # JVM 앱은 0.25 vCPU에서 기동에 30초 이상 걸릴 수 있어 기본 유예를 넉넉히(90초) 둔다. 변수로 조절한다.
  # 실패 판정 시간은 플랫폼 10단계 타임아웃이 정한다
  health_check_grace_period_seconds = var.health_check_grace_seconds

  enable_ecs_managed_tags = true
  propagate_tags          = "SERVICE"

  # 안정화 대기는 플랫폼 10단계 헬스체크가 타임아웃을 두고 맡는다 (CLAUDE.md 4장)
  wait_for_steady_state = false

  network_configuration {
    subnets          = var.foundation.task_subnet_ids
    security_groups  = [aws_security_group.task.id]
    assign_public_ip = var.foundation.assign_public_ip
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.app.arn
    container_name   = local.container_name
    container_port   = var.container_port
  }

  lifecycle {
    # 실행 중 태스크 수는 오토스케일링이 관리한다
    ignore_changes = [desired_count]

    precondition {
      condition     = !var.use_database || local.database_param_arn != ""
      error_message = "use_database=true인데 DATABASE_URL 파라미터가 없습니다."
    }
  }

  # 리스너에 연결되지 않은 대상 그룹으로는 서비스를 만들 수 없다
  depends_on = [aws_lb_listener.app]
}

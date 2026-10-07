# Application Auto Scaling: ECS 서비스의 태스크 개수를 자동으로 늘리고 줄이는 서비스다.
# `min_tasks`와 `max_tasks`가 같으면 개수가 고정되고, max가 더 크면 CPU 사용률 60%를 기준으로 증감한다.
#
# 관련 파일: ecs.tf(조절 대상 서비스)

# 태스크 수 범위. min과 max가 같으면 고정 개수로 동작한다
resource "aws_appautoscaling_target" "app" {
  service_namespace  = "ecs"
  scalable_dimension = "ecs:service:DesiredCount"
  resource_id        = "service/${var.foundation.cluster_name}/${aws_ecs_service.app.name}"
  min_capacity       = var.min_tasks
  max_capacity       = var.max_tasks

  lifecycle {
    precondition {
      condition     = var.max_tasks >= var.min_tasks
      error_message = "max_tasks는 min_tasks보다 작을 수 없습니다."
    }
  }
}

resource "aws_appautoscaling_policy" "cpu" {
  count = local.autoscaling_enabled ? 1 : 0

  name               = "${local.name}-cpu"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.app.service_namespace
  scalable_dimension = aws_appautoscaling_target.app.scalable_dimension
  resource_id        = aws_appautoscaling_target.app.resource_id

  target_tracking_scaling_policy_configuration {
    target_value       = 60
    scale_in_cooldown  = 60
    scale_out_cooldown = 60

    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
  }
}

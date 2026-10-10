# 10단계 헬스체크와 status·rollback 구현이 쓰는 값

output "url" {
  description = "외부 접속 URL"
  value       = "${lower(var.foundation.listener_protocol)}://${var.foundation.alb_dns_name}:${var.listener_port}"
}

output "cluster_name" {
  description = "ECS 클러스터 이름"
  value       = var.foundation.cluster_name
}

output "service_name" {
  description = "ECS 서비스 이름. status 조회에 쓴다"
  value       = aws_ecs_service.app.name
}

output "target_group_arn" {
  description = "대상 그룹 ARN. 대상 헬스 상태 조회에 쓴다"
  value       = aws_lb_target_group.app.arn
}

output "log_group_name" {
  description = "이 배포의 로그 그룹. 실패 분석 LLM에 넘길 로그를 여기서 읽는다"
  value       = aws_cloudwatch_log_group.app.name
}

output "task_definition_arn" {
  description = "현재 태스크 정의 ARN"
  value       = aws_ecs_task_definition.app.arn
}

output "image" {
  description = "현재 배포된 이미지. 다음 배포의 롤백 대상으로 배포 이력에 기록한다"
  value       = var.image
}

output "task_security_group_id" {
  description = "이 배포 전용 앱 태스크 보안 그룹"
  value       = aws_security_group.task.id
}

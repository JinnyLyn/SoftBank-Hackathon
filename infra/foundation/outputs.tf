# 플랫폼은 배포마다 이 값을 platform.auto.tfvars.json의 platform.foundation에 그대로 넣는다.
#   terraform output -json deploy_inputs > foundation.json
output "deploy_inputs" {
  description = "배포 루트 모듈에 넘길 사전 생성 리소스 정보 (비밀 값 없음)"
  value = {
    alb_arn                    = aws_lb.this.arn
    alb_dns_name               = aws_lb.this.dns_name
    allowed_listener_ports     = var.listener_port_range
    assign_public_ip           = local.assign_public_ip
    cluster_name               = aws_ecs_cluster.this.name
    database_url_parameter_arn = aws_ssm_parameter.database_url.arn
    ecr_repository_url         = aws_ecr_repository.apps.repository_url
    execution_role_arn         = aws_iam_role.task_execution.arn
    task_security_group_id     = aws_security_group.tasks.id
    task_subnet_ids            = local.task_subnet_ids
    vpc_id                     = aws_vpc.this.id
  }
}

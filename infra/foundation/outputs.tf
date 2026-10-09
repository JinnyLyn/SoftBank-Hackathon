# 플랫폼은 배포마다 이 값을 platform.auto.tfvars.json의 platform.foundation에 그대로 넣는다.
#   terraform output -json deploy_inputs > foundation.json
output "deploy_inputs" {
  description = "배포 루트 모듈에 넘길 사전 생성 리소스 정보 (비밀 값 없음)"
  value = {
    alb_arn                           = aws_lb.this.arn
    alb_dns_name                      = aws_lb.this.dns_name
    alb_security_group_id             = aws_security_group.alb.id
    allowed_listener_ports            = var.listener_port_range
    assign_public_ip                  = local.assign_public_ip
    certificate_arn                   = var.certificate_arn
    cluster_name                      = aws_ecs_cluster.this.name
    database_url_parameter_arn        = aws_ssm_parameter.database_url.arn
    db_admin_password_parameter_arn   = aws_ssm_parameter.db_admin_password.arn
    db_admin_username                 = var.db_username
    db_name                           = var.db_name
    db_host                           = aws_db_instance.this.address
    db_port                           = aws_db_instance.this.port
    db_provisioner_execution_role_arn = aws_iam_role.db_provisioner_execution.arn
    db_provisioner_lambda_name        = try(aws_lambda_function.db_provisioner[0].function_name, "")
    db_provisioner_log_group          = aws_cloudwatch_log_group.db_provisioner.name
    db_provisioner_security_group     = aws_security_group.db_provisioner.id
    db_security_group_id              = aws_security_group.db.id
    ecr_repository_url                = aws_ecr_repository.apps.repository_url
    execution_role_arn                = aws_iam_role.task_execution.arn
    listener_protocol                 = local.https_enabled ? "HTTPS" : "HTTP"
    task_subnet_ids                   = local.task_subnet_ids
    vpc_id                            = aws_vpc.this.id
  }
}

# 앱 전용 보안 그룹: 이 배포의 태스크에만 붙는 방화벽이다.
# foundation에는 앱 태스크용 공용 보안 그룹이 없다. 배포마다 이 파일이 아래를 만든다.
#   1. 태스크 보안 그룹: ALB가 보내는 컨테이너 포트 하나만 받는다
#   2. ALB 보안 그룹에 "이 앱의 컨테이너 포트로 나가기" 규칙 하나
#   3. DB를 쓰는 앱이면 DB 보안 그룹에 "이 앱에서 3306으로 들어오기" 규칙 하나
# 그래서 앱끼리 서로 접근할 수 없고, DB를 쓰지 않는 앱은 DB에 닿을 수 없다.
#
# 관련 파일: ecs.tf(서비스가 이 보안 그룹을 쓴다)

resource "aws_security_group" "task" {
  name        = "${local.name}-task"
  description = "ECS tasks of ${local.name}"
  vpc_id      = var.foundation.vpc_id

  tags = {
    Name = "${local.name}-task"
  }
}

resource "aws_vpc_security_group_ingress_rule" "task_from_alb" {
  security_group_id            = aws_security_group.task.id
  description                  = "From ALB to the container port only"
  referenced_security_group_id = var.foundation.alb_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = var.container_port
  to_port                      = var.container_port
}

# ECR 이미지 pull, CloudWatch Logs, SSM 조회, 앱이 부르는 외부 API에 필요하다
resource "aws_vpc_security_group_egress_rule" "task_all" {
  security_group_id = aws_security_group.task.id
  description       = "Outbound for ECR, logs, SSM and the app itself"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_vpc_security_group_egress_rule" "alb_to_task" {
  security_group_id            = var.foundation.alb_security_group_id
  description                  = "To ${local.name} container port"
  referenced_security_group_id = aws_security_group.task.id
  ip_protocol                  = "tcp"
  from_port                    = var.container_port
  to_port                      = var.container_port
}

resource "aws_vpc_security_group_ingress_rule" "db_from_task" {
  count = var.use_database ? 1 : 0

  security_group_id            = var.foundation.db_security_group_id
  description                  = "MySQL from ${local.name}"
  referenced_security_group_id = aws_security_group.task.id
  ip_protocol                  = "tcp"
  from_port                    = 3306
  to_port                      = 3306
}

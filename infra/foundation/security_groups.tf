# 보안 그룹: 리소스마다 붙이는 방화벽이다. 어디서 오는 어떤 포트의 통신을 허용할지 정한다.
# 이 프로젝트는 3계층으로 연결한다: 인터넷 → ALB → 앱 태스크 → RDS
# 각 계층은 바로 앞 계층의 보안 그룹에서 오는 통신만 허용하므로, DB는 인터넷에서도 ALB에서도 직접 접근할 수 없다.
#
# 관련 파일: alb.tf, ec2_nat.tf, rds.tf (각 리소스가 이 보안 그룹을 붙인다)

# --- ALB: 인터넷에서 오는 요청을 받는다 ---
resource "aws_security_group" "alb" {
  name        = "${var.project}-alb"
  description = "Public ALB"
  vpc_id      = aws_vpc.this.id

  tags = {
    Name = "${var.project}-alb"
  }
}

resource "aws_vpc_security_group_ingress_rule" "alb_http" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTP default listener"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
}

resource "aws_vpc_security_group_ingress_rule" "alb_app_ports" {
  security_group_id = aws_security_group.alb.id
  description       = "Per-deployment listener ports"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = var.listener_port_range.from
  to_port           = var.listener_port_range.to
}

resource "aws_vpc_security_group_egress_rule" "alb_to_tasks" {
  security_group_id            = aws_security_group.alb.id
  description                  = "Forward to app tasks"
  referenced_security_group_id = aws_security_group.tasks.id
  ip_protocol                  = "tcp"
  from_port                    = 1
  to_port                      = 65535
}

# --- 앱 태스크: ALB가 보낸 요청만 받는다 ---
resource "aws_security_group" "tasks" {
  name        = "${var.project}-tasks"
  description = "ECS app tasks"
  vpc_id      = aws_vpc.this.id

  tags = {
    Name = "${var.project}-tasks"
  }
}

# 앱 포트는 배포마다 다르므로 ALB 보안 그룹에서 오는 TCP만 전부 허용한다
resource "aws_vpc_security_group_ingress_rule" "tasks_from_alb" {
  security_group_id            = aws_security_group.tasks.id
  description                  = "From ALB only"
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = 1
  to_port                      = 65535
}

# ECR 이미지 pull, CloudWatch Logs, SSM 조회에 필요
resource "aws_vpc_security_group_egress_rule" "tasks_all" {
  security_group_id = aws_security_group.tasks.id
  description       = "Outbound for ECR, logs, SSM"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

# --- NAT 인스턴스: 앱 태스크에서 오는 트래픽만 받아 인터넷으로 중계한다 ---
resource "aws_security_group" "nat" {
  count = var.enable_nat_instance ? 1 : 0

  name        = "${var.project}-nat"
  description = "NAT instance"
  vpc_id      = aws_vpc.this.id

  tags = {
    Name = "${var.project}-nat"
  }
}

resource "aws_vpc_security_group_ingress_rule" "nat_from_tasks" {
  count = var.enable_nat_instance ? 1 : 0

  security_group_id            = aws_security_group.nat[0].id
  description                  = "From app tasks"
  referenced_security_group_id = aws_security_group.tasks.id
  ip_protocol                  = "-1"
}

resource "aws_vpc_security_group_egress_rule" "nat_all" {
  count = var.enable_nat_instance ? 1 : 0

  security_group_id = aws_security_group.nat[0].id
  description       = "Outbound to internet"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

# --- RDS: 앱 태스크에서 오는 MySQL(3306) 접속만 허용한다 ---
resource "aws_security_group" "db" {
  name        = "${var.project}-db"
  description = "RDS MySQL"
  vpc_id      = aws_vpc.this.id

  tags = {
    Name = "${var.project}-db"
  }
}

resource "aws_vpc_security_group_ingress_rule" "db_from_tasks" {
  security_group_id            = aws_security_group.db.id
  description                  = "MySQL from app tasks"
  referenced_security_group_id = aws_security_group.tasks.id
  ip_protocol                  = "tcp"
  from_port                    = 3306
  to_port                      = 3306
}

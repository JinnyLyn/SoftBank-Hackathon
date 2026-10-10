# 보안 그룹: 리소스마다 붙이는 방화벽이다. 어디서 오는 어떤 포트의 통신을 허용할지 정한다.
# 이 프로젝트는 3계층으로 연결한다: 인터넷 → ALB → 앱 태스크 → RDS
#
# 앱 태스크의 보안 그룹은 여기 없다. 배포마다 modules/ecs-web-app이 앱 전용 보안 그룹을 만들고,
# ALB와 DB 보안 그룹에는 그 앱의 포트·접속만 허용하는 규칙을 하나씩 추가한다.
# 그래서 앱끼리 서로 접근할 수 없고, DB를 쓰지 않는 앱은 DB에 닿을 수 없다.
#
# 관련 파일: alb.tf, ec2_nat.tf, rds.tf, db_provisioner.tf

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

resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  count = local.https_enabled ? 1 : 0

  security_group_id = aws_security_group.alb.id
  description       = "HTTPS default listener"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

resource "aws_vpc_security_group_ingress_rule" "alb_app_ports" {
  security_group_id = aws_security_group.alb.id
  description       = "Per-deployment listener ports"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = var.listener_port_range.from
  to_port           = var.listener_port_range.to
}

# --- NAT 인스턴스: 프라이빗 서브넷에서 오는 트래픽만 받아 인터넷으로 중계한다 ---
resource "aws_security_group" "nat" {
  count = var.enable_nat_instance ? 1 : 0

  name        = "${var.project}-nat"
  description = "NAT instance"
  vpc_id      = aws_vpc.this.id

  tags = {
    Name = "${var.project}-nat"
  }
}

# 앱 태스크 보안 그룹이 배포마다 생기므로 보안 그룹 참조 대신 프라이빗 서브넷 대역으로 허용한다
resource "aws_vpc_security_group_ingress_rule" "nat_from_private" {
  count = var.enable_nat_instance ? length(aws_subnet.private) : 0

  security_group_id = aws_security_group.nat[0].id
  description       = "From private subnet ${count.index}"
  cidr_ipv4         = aws_subnet.private[count.index].cidr_block
  ip_protocol       = "-1"
}

resource "aws_vpc_security_group_egress_rule" "nat_all" {
  count = var.enable_nat_instance ? 1 : 0

  security_group_id = aws_security_group.nat[0].id
  description       = "Outbound to internet"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

# --- RDS: 접속 허용은 배포 모듈이 앱 보안 그룹 단위로 추가한다 ---
resource "aws_security_group" "db" {
  name        = "${var.project}-db"
  description = "RDS MySQL"
  vpc_id      = aws_vpc.this.id

  tags = {
    Name = "${var.project}-db"
  }
}

# 앱별 DB와 계정을 만드는 1회성 작업(db_provisioner.tf)이 쓰는 보안 그룹
resource "aws_security_group" "db_provisioner" {
  name        = "${var.project}-db-provisioner"
  description = "One-off task that creates per-app databases"
  vpc_id      = aws_vpc.this.id

  tags = {
    Name = "${var.project}-db-provisioner"
  }
}

resource "aws_vpc_security_group_egress_rule" "db_provisioner_all" {
  security_group_id = aws_security_group.db_provisioner.id
  description       = "Outbound for ECR image pull and DB access"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_vpc_security_group_ingress_rule" "db_from_provisioner" {
  security_group_id            = aws_security_group.db.id
  description                  = "MySQL from DB provisioner task"
  referenced_security_group_id = aws_security_group.db_provisioner.id
  ip_protocol                  = "tcp"
  from_port                    = 3306
  to_port                      = 3306
}

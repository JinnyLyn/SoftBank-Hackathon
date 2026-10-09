# EC2 NAT 인스턴스: 프라이빗 서브넷의 앱이 인터넷으로 나갈 때 거치는 중계 서버다.
# 프라이빗 서브넷은 인터넷에서 들어오는 길이 없으므로, 앱이 ECR에서 이미지를 받고 로그를 보내려면 이 중계가 필요하다.
# `enable_nat_instance = false`(기본)면 이 파일의 NAT 리소스는 만들지 않는다. 이때 앱은 퍼블릭 서브넷에서 실행된다.
#
# NAT Gateway(AWS 관리형) 대신 EC2를 직접 쓰는 이유는 시간당 비용이 훨씬 낮기 때문이다.
# 대신 OS 설정(iptables)과 장애 복구를 우리가 챙긴다.
# 고가용성(기본)이면 AZ마다 NAT를 하나씩 두고 라우트 테이블도 AZ별로 나눠서, AZ 하나가 멈춰도 다른 AZ의 외부 통신은 유지된다.
# nat_high_availability=false면 NAT 1대를 모든 AZ가 같이 쓴다(비용 절감).
#
# 관련 파일: vpc.tf(서브넷), security_groups.tf(NAT 방화벽)

locals {
  # NAT 인스턴스 수: 고가용성이면 AZ마다 1대, 아니면 1대
  nat_count = var.enable_nat_instance ? (var.nat_high_availability ? length(local.azs) : 1) : 0
}

data "aws_ssm_parameter" "nat_ami" {
  # AMI를 직접 고정하면 조회하지 않는다
  count = var.enable_nat_instance && var.nat_ami_id == "" ? 1 : 0

  # Amazon Linux 2023 (arm64). nat_instance_type은 Graviton(t4g 등) 계열이어야 한다
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

resource "aws_instance" "nat" {
  count = local.nat_count

  ami                    = var.nat_ami_id != "" ? var.nat_ami_id : one(data.aws_ssm_parameter.nat_ami[*].value)
  instance_type          = var.nat_instance_type
  subnet_id              = aws_subnet.public[count.index].id
  vpc_security_group_ids = [aws_security_group.nat[0].id]

  # 다른 호스트의 패킷을 중계하려면 출발지·목적지 검사를 꺼야 한다
  source_dest_check = false

  user_data = <<-EOT
    #!/bin/bash
    set -euo pipefail
    dnf install -y iptables-services
    echo "net.ipv4.ip_forward = 1" > /etc/sysctl.d/99-nat.conf
    sysctl --system
    IFACE=$(ip -o -4 route show to default | awk '{print $5}')
    iptables -t nat -A POSTROUTING -o "$IFACE" -s ${var.vpc_cidr} -j MASQUERADE
    service iptables save
    systemctl enable --now iptables
  EOT
  # 스크립트가 바뀌면 인스턴스를 새로 만든다
  user_data_replace_on_change = true

  lifecycle {
    # AMI는 SSM 파라미터가 항상 최신 값을 돌려준다(현재까지 190번 넘게 갱신). 무시하지 않으면 새 AMI가 나올 때마다
    # plan이 NAT 인스턴스 교체를 제안하고, 교체 중에는 프라이빗 서브넷의 외부 통신이 끊긴다.
    # 의도해서 최신 AMI로 바꿀 때는 terraform apply -replace='aws_instance.nat[0]' 처럼 하나씩 교체한다
    ignore_changes = [ami]
  }

  metadata_options {
    http_tokens = "required"
  }

  root_block_device {
    encrypted = true
  }

  tags = {
    Name = "${var.project}-nat-${local.azs[count.index]}"
  }

  depends_on = [aws_internet_gateway.this]
}

# 인스턴스를 다시 만들어도 외부로 나가는 IP가 바뀌지 않도록 고정 IP를 붙인다
resource "aws_eip" "nat" {
  count = local.nat_count

  domain   = "vpc"
  instance = aws_instance.nat[count.index].id

  tags = {
    Name = "${var.project}-nat-${local.azs[count.index]}"
  }

  depends_on = [aws_internet_gateway.this]
}

# 호스트 하드웨어 문제로 시스템 상태 검사가 실패하면 EC2가 같은 설정(ENI, EIP 유지)으로 인스턴스를 복구한다
resource "aws_cloudwatch_metric_alarm" "nat_recover" {
  count = local.nat_count

  alarm_name          = "${var.project}-nat-${local.azs[count.index]}-recover"
  namespace           = "AWS/EC2"
  metric_name         = "StatusCheckFailed_System"
  statistic           = "Maximum"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  period              = 60
  evaluation_periods  = 2
  treat_missing_data  = "missing"

  dimensions = {
    InstanceId = aws_instance.nat[count.index].id
  }

  alarm_actions = ["arn:aws:automate:${var.region}:ec2:recover"]
}

# 프라이빗 라우트 테이블은 AZ마다 하나씩 두어 같은 AZ의 NAT로만 나가게 한다
resource "aws_route_table" "private" {
  count = length(aws_subnet.private)

  vpc_id = aws_vpc.this.id

  tags = {
    Name = "${var.project}-private-${local.azs[count.index]}"
  }
}

# 프라이빗 서브넷의 외부 통신(0.0.0.0/0)을 NAT 인스턴스로 보낸다.
# 고가용성이면 같은 AZ의 NAT로, 아니면 하나뿐인 NAT로 보낸다
resource "aws_route" "private_nat" {
  count = var.enable_nat_instance ? length(aws_subnet.private) : 0

  route_table_id         = aws_route_table.private[count.index].id
  destination_cidr_block = "0.0.0.0/0"
  network_interface_id   = aws_instance.nat[local.nat_count == 1 ? 0 : count.index].primary_network_interface_id
}

resource "aws_route_table_association" "private" {
  count = length(aws_subnet.private)

  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

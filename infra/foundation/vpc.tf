# VPC: AWS 안에 만드는 나만의 가상 네트워크다. 이 안에 ALB, 앱 태스크, DB가 모두 들어간다.
#
# 구성 (가용 영역(AZ) 3개 기준)
#   인터넷 ── 인터넷 게이트웨이 ── 퍼블릭 서브넷 3개 (ALB, NAT 인스턴스가 위치)
#                                  프라이빗 서브넷 3개 (RDS, NAT를 켠 경우 앱 태스크가 위치)
# 서브넷은 VPC를 잘게 나눈 IP 대역이고, AZ는 서로 다른 데이터센터다. AZ를 여러 개 쓰면 하나가 멈춰도 서비스가 유지된다.
#
# 관련 파일: ec2_nat.tf(프라이빗 서브넷의 인터넷 경로), security_groups.tf(방화벽)

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  # ALB와 RDS 서브넷 그룹은 최소 2개 AZ가 필요하다. 3개 AZ에 퍼블릭·프라이빗 서브넷을 각 1개씩 둔다
  azs = slice(data.aws_availability_zones.available.names, 0, 3)

  # 앱 태스크 위치: NAT 인스턴스를 켜면 프라이빗, 끄면 퍼블릭 + 퍼블릭 IP
  task_subnet_ids  = var.enable_nat_instance ? aws_subnet.private[*].id : aws_subnet.public[*].id
  assign_public_ip = !var.enable_nat_instance
}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = "${var.project}-vpc"
  }
}

# VPC와 인터넷을 잇는 출입문
resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id

  tags = {
    Name = "${var.project}-igw"
  }
}

# 퍼블릭 서브넷: 인터넷 게이트웨이로 직접 통신할 수 있는 서브넷
resource "aws_subnet" "public" {
  count = length(local.azs)

  vpc_id            = aws_vpc.this.id
  availability_zone = local.azs[count.index]
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index)

  tags = {
    Name = "${var.project}-public-${local.azs[count.index]}"
    Tier = "public"
  }
}

# 프라이빗 서브넷: 인터넷에서 직접 접근할 수 없는 서브넷. 나가는 통신은 NAT를 거친다 (ec2_nat.tf)
resource "aws_subnet" "private" {
  count = length(local.azs)

  vpc_id            = aws_vpc.this.id
  availability_zone = local.azs[count.index]
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index + 10)

  tags = {
    Name = "${var.project}-private-${local.azs[count.index]}"
    Tier = "private"
  }
}

# 라우트 테이블: 서브넷의 패킷이 어디로 가야 하는지 적은 표. 퍼블릭은 모든 외부 통신을 인터넷 게이트웨이로 보낸다
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }

  tags = {
    Name = "${var.project}-public"
  }
}

resource "aws_route_table_association" "public" {
  count = length(aws_subnet.public)

  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

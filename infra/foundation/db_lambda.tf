# 앱별 DB·계정을 만들고 지우고 확인하는 Lambda (db_provisioner.tf의 Fargate 작업과 같은 일).
# Fargate 작업은 컨테이너를 띄우는 데만 약 70초가 걸리고, 이 Lambda는 몇 초면 끝난다. 배포 시간(3분 시연)을 줄이려고 둔다.
#
# VPC 안(프라이빗 서브넷)에서 RDS에 접속한다. DB 관리자 비밀번호와 앱 접속 정보는 실행 때 SSM에서 읽는다.
# 프라이빗 서브넷의 Lambda는 인터넷으로 나갈 수 없으므로, NAT 인스턴스를 끈 구성(기본값)에서는 SSM용 VPC 엔드포인트를 둔다.
# (NAT 인스턴스를 켠 구성은 NAT를 거쳐 SSM에 닿으므로 엔드포인트를 만들지 않는다.)
# 비밀번호를 환경 변수에 넣는 방법도 있지만, 관리자 비밀번호를 읽을 수 있는 대상을 이 Lambda 역할과 DB 작업용 실행 역할(iam.tf)로
# 한정하려고 SSM에서 읽는다.
# 이 Lambda가 없는 foundation(옛 버전)에서는 deploy.sh가 출력의 db_provisioner_lambda_name이 비어 있다고 보고 Fargate 작업으로 대신한다.
#
# 코드: lambda/db_provisioner/ (handler.py + 순수 파이썬 PyMySQL 1.1.2를 그대로 넣었다)
# 관련 파일: security_groups.tf(db_provisioner 보안 그룹을 같이 쓴다: RDS 3306 허용, 외부 아웃바운드), iam.tf, ssm.tf

locals {
  db_lambda_enabled = var.enable_db_lambda
}

data "archive_file" "db_provisioner" {
  count       = local.db_lambda_enabled ? 1 : 0
  type        = "zip"
  source_dir  = "${path.module}/lambda/db_provisioner"
  output_path = "${path.module}/.terraform/db_provisioner.zip"
  excludes    = ["__pycache__"]
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "db_provisioner_lambda" {
  count              = local.db_lambda_enabled ? 1 : 0
  name               = "${var.project}-db-provisioner-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

# 로그와 VPC 연결(ENI 생성)
resource "aws_iam_role_policy_attachment" "db_provisioner_lambda_vpc" {
  count      = local.db_lambda_enabled ? 1 : 0
  role       = aws_iam_role.db_provisioner_lambda[0].name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

data "aws_iam_policy_document" "db_provisioner_lambda_read" {
  statement {
    actions = ["ssm:GetParameter"]
    resources = [
      aws_ssm_parameter.db_admin_password.arn,
      local.apps_param_arn,
    ]
  }
}

resource "aws_iam_role_policy" "db_provisioner_lambda_read" {
  count  = local.db_lambda_enabled ? 1 : 0
  name   = "read-admin-and-app-secrets"
  role   = aws_iam_role.db_provisioner_lambda[0].id
  policy = data.aws_iam_policy_document.db_provisioner_lambda_read.json
}

resource "aws_cloudwatch_log_group" "db_provisioner_lambda" {
  count             = local.db_lambda_enabled ? 1 : 0
  name              = "/aws/lambda/${var.project}-db-provisioner"
  retention_in_days = 7
}

resource "aws_lambda_function" "db_provisioner" {
  count            = local.db_lambda_enabled ? 1 : 0
  function_name    = "${var.project}-db-provisioner"
  description      = "Creates, verifies and drops per-app databases and accounts"
  role             = aws_iam_role.db_provisioner_lambda[0].arn
  runtime          = "python3.12"
  handler          = "handler.handler"
  filename         = data.archive_file.db_provisioner[0].output_path
  source_code_hash = data.archive_file.db_provisioner[0].output_base64sha256
  timeout          = 60
  memory_size      = 256

  vpc_config {
    subnet_ids         = aws_subnet.private[*].id
    security_group_ids = [aws_security_group.db_provisioner.id]
  }

  environment {
    variables = {
      DB_HOST           = aws_db_instance.this.address
      DB_PORT           = tostring(aws_db_instance.this.port)
      DB_ADMIN_USER     = var.db_username
      ADMIN_PW_PARAM    = aws_ssm_parameter.db_admin_password.name
      APPS_PARAM_PREFIX = "/${var.project}/apps"
      OTHER_DB          = var.db_name
    }
  }

  depends_on = [
    aws_vpc_endpoint.ssm,
    aws_cloudwatch_log_group.db_provisioner_lambda,
    aws_iam_role_policy_attachment.db_provisioner_lambda_vpc,
    aws_iam_role_policy.db_provisioner_lambda_read,
  ]
}

# --- SSM VPC 엔드포인트 (NAT 인스턴스를 끈 구성에서만) ---
# 프라이빗 서브넷의 Lambda가 인터넷 없이 SSM(비밀번호 파라미터)에 닿는 경로. 인터페이스 엔드포인트는 시간당 요금이 있어
# 비용을 줄이려고 가용 영역 하나에만 둔다(다른 영역의 Lambda도 같은 VPC 안에서 이 주소로 접근한다)
resource "aws_security_group" "ssm_endpoint" {
  count       = local.db_lambda_enabled && !var.enable_nat_instance ? 1 : 0
  name        = "${var.project}-ssm-endpoint"
  description = "SSM VPC endpoint for the DB provisioner Lambda"
  vpc_id      = aws_vpc.this.id

  tags = {
    Name = "${var.project}-ssm-endpoint"
  }
}

resource "aws_vpc_security_group_ingress_rule" "ssm_endpoint_from_provisioner" {
  count                        = local.db_lambda_enabled && !var.enable_nat_instance ? 1 : 0
  security_group_id            = aws_security_group.ssm_endpoint[0].id
  description                  = "HTTPS from the DB provisioner Lambda"
  referenced_security_group_id = aws_security_group.db_provisioner.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
}

resource "aws_vpc_endpoint" "ssm" {
  count               = local.db_lambda_enabled && !var.enable_nat_instance ? 1 : 0
  vpc_id              = aws_vpc.this.id
  service_name        = "com.amazonaws.${var.region}.ssm"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = [aws_subnet.private[0].id]
  security_group_ids  = [aws_security_group.ssm_endpoint[0].id]
  private_dns_enabled = true

  tags = {
    Name = "${var.project}-ssm"
  }
}

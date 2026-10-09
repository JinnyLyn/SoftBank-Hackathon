# Terraform state를 보관하는 S3 버킷이다.
# 로컬 state 파일은 잃어버리면 AWS 리소스를 지울 수 없고, DB 비밀번호가 평문으로 들어 있다.
# 이 버킷은 버전 관리(실수로 덮어써도 되돌림), 암호화, 공개 차단, 잠금(동시 실행 방지)을 쓴다.
#
# 이 스택 자체의 state는 로컬 파일이다(버킷을 만들기 전에는 원격 state를 쓸 수 없다).
# 한 번 만들고 나면 거의 바꾸지 않으니 terraform.tfstate는 담당자가 보관한다.
# 버킷은 foundation과 배포보다 오래 살아야 하므로 foundation과 분리했다.
#
# 사용
#   terraform -chdir=infra/bootstrap init
#   terraform -chdir=infra/bootstrap apply -var=region=sa-east-1
#   export PAVED_STATE_BUCKET=$(terraform -chdir=infra/bootstrap output -raw bucket)
# 그 뒤 deploy.sh가 배포 state를 이 버킷에 둔다. foundation은 README의 "원격 state" 절을 따른다.

data "aws_caller_identity" "current" {}

locals {
  bucket = "${var.project}-tfstate-${data.aws_caller_identity.current.account_id}-${var.region}"
}

resource "aws_s3_bucket" "state" {
  bucket        = local.bucket
  force_destroy = var.force_destroy
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket = aws_s3_bucket.state.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# 암호화되지 않은 연결(HTTP)로는 접근할 수 없게 한다
data "aws_iam_policy_document" "tls_only" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.state.arn, "${aws_s3_bucket.state.arn}/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "state" {
  bucket = aws_s3_bucket.state.id
  policy = data.aws_iam_policy_document.tls_only.json

  # 공개 차단 설정이 먼저 적용되어야 정책 적용이 충돌하지 않는다
  depends_on = [aws_s3_bucket_public_access_block.state]
}

# 덮어써서 생긴 옛 버전은 90일 뒤 지워 용량이 계속 늘지 않게 한다. 현재 버전은 지우지 않는다
resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    id     = "expire-old-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 90
    }
  }

  depends_on = [aws_s3_bucket_versioning.state]
}

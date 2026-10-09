# IAM: AWS 리소스끼리 서로 무엇을 할 수 있는지 정하는 권한 설정이다.
# 여기서는 ECS 태스크 "실행 역할"을 만든다. 컨테이너가 시작될 때 ECS가 이 역할로 ECR에서 이미지를 받고,
# CloudWatch에 로그를 쓰고, SSM에서 DATABASE_URL을 읽는다. DB 관리자 비밀번호를 읽는 역할은 따로 둔다(아래).
# 태스크 실행 역할을 미리 만든다. 배포 때 IAM 역할을 새로 만들면 전파 지연으로 태스크 시작이 실패할 수 있다.
#
# 관련 파일: ecs.tf, ecr.tf, ssm.tf(읽을 파라미터)

data "aws_partition" "current" {}
data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "task_execution" {
  name               = "${var.project}-task-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

# ECR 이미지 pull과 CloudWatch Logs 쓰기 권한 (AWS 기본 제공 정책)
resource "aws_iam_role_policy_attachment" "task_execution_managed" {
  role       = aws_iam_role.task_execution.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

locals {
  apps_param_arn = "arn:${data.aws_partition.current.partition}:ssm:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:parameter/${var.project}/apps/*"
}

# 앱 태스크가 쓰는 공유 실행 역할이 읽을 수 있는 SSM 파라미터는 아래 둘뿐이다.
#   - 공유 DB URL(--shared-db로 만든 배포용)
#   - /<프로젝트>/apps/ 아래의 앱별 DB 접속 정보
# DB 관리자 비밀번호는 읽을 수 없다. 모든 앱이 이 역할을 같이 쓰므로, 여기서 읽을 수 있으면 앱 하나의 task definition만
# 바뀌어도 관리자 비밀번호가 새어 나간다. 앱 파라미터를 다른 앱의 것으로 지정하는 실수는 배포 모듈의 사전 조건이 막는다
data "aws_iam_policy_document" "read_secrets" {
  statement {
    actions = ["ssm:GetParameters"]
    resources = [
      aws_ssm_parameter.database_url.arn,
      local.apps_param_arn,
    ]
  }
}

resource "aws_iam_role_policy" "task_execution_read_secrets" {
  name   = "read-secrets"
  role   = aws_iam_role.task_execution.id
  policy = data.aws_iam_policy_document.read_secrets.json
}

# 앱별 DB와 계정을 만드는 1회성 작업(deploy.sh)만 쓰는 실행 역할. 관리자 비밀번호를 읽을 수 있는 것은 이 역할뿐이다
resource "aws_iam_role" "db_provisioner_execution" {
  name               = "${var.project}-db-provisioner-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy_attachment" "db_provisioner_execution_managed" {
  role       = aws_iam_role.db_provisioner_execution.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "db_provisioner_read" {
  statement {
    actions = ["ssm:GetParameters"]
    resources = [
      aws_ssm_parameter.db_admin_password.arn,
      local.apps_param_arn,
    ]
  }
}

resource "aws_iam_role_policy" "db_provisioner_read" {
  name   = "read-admin-and-app-secrets"
  role   = aws_iam_role.db_provisioner_execution.id
  policy = data.aws_iam_policy_document.db_provisioner_read.json
}

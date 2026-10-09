# IAM: AWS 리소스끼리 서로 무엇을 할 수 있는지 정하는 권한 설정이다.
# 여기서는 ECS 태스크 "실행 역할"을 만든다. 컨테이너가 시작될 때 ECS가 이 역할로 ECR에서 이미지를 받고,
# CloudWatch에 로그를 쓰고, SSM에서 DATABASE_URL을 읽는다.
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

# 읽을 수 있는 SSM 파라미터를 좁게 제한한다.
#   - 공유 DB URL
#   - 관리자 비밀번호(앱별 DB를 만드는 1회성 작업만 쓴다)
#   - /<프로젝트>/apps/ 아래의 앱별 DB 접속 정보
data "aws_iam_policy_document" "read_secrets" {
  statement {
    actions = ["ssm:GetParameters"]
    resources = [
      aws_ssm_parameter.database_url.arn,
      aws_ssm_parameter.db_admin_password.arn,
      "arn:${data.aws_partition.current.partition}:ssm:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:parameter/${var.project}/apps/*",
    ]
  }
}

resource "aws_iam_role_policy" "task_execution_read_secrets" {
  name   = "read-secrets"
  role   = aws_iam_role.task_execution.id
  policy = data.aws_iam_policy_document.read_secrets.json
}

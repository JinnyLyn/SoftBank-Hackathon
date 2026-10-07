# IAM: AWS 리소스끼리 서로 무엇을 할 수 있는지 정하는 권한 설정이다.
# 여기서는 ECS 태스크 "실행 역할"을 만든다. 컨테이너가 시작될 때 ECS가 이 역할로 ECR에서 이미지를 받고,
# CloudWatch에 로그를 쓰고, SSM에서 DATABASE_URL을 읽는다.
# 태스크 실행 역할을 미리 만든다. 배포 때 IAM 역할을 새로 만들면 전파 지연으로 태스크 시작이 실패할 수 있다.
#
# 관련 파일: ecs.tf, ecr.tf, ssm.tf(읽을 파라미터)

data "aws_partition" "current" {}

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

# DATABASE_URL 파라미터 하나만 읽을 수 있게 제한한다
data "aws_iam_policy_document" "read_database_url" {
  statement {
    actions   = ["ssm:GetParameters"]
    resources = [aws_ssm_parameter.database_url.arn]
  }
}

resource "aws_iam_role_policy" "task_execution_read_database_url" {
  name   = "read-database-url"
  role   = aws_iam_role.task_execution.id
  policy = data.aws_iam_policy_document.read_database_url.json
}

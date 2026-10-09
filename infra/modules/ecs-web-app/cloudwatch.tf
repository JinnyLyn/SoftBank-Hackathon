# CloudWatch Logs: 컨테이너가 출력하는 로그를 모아 보관하는 서비스다.
# 배포가 실패했을 때 원인을 분석하는 LLM이 이 로그를 읽는다.
#
# 관련 파일: ecs.tf(컨테이너가 이 로그 그룹으로 로그를 보낸다)

# 배포 건별 로그 그룹. 실패 분석 LLM에는 이 그룹의 로그만 넘긴다 (CLAUDE.md 4장)
resource "aws_cloudwatch_log_group" "app" {
  name              = "/${var.project}/${var.deploy_id}"
  retention_in_days = var.log_retention_days
}

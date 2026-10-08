# ECS 클러스터: 컨테이너를 실행하는 서비스(ECS Fargate)의 작업 공간이다.
# Fargate는 서버를 직접 관리하지 않고 컨테이너만 올리는 방식이다. 배포 1건마다 이 클러스터 안에 서비스가 하나 생긴다.
#
# 관련 파일: iam.tf(태스크 실행 역할), ecr.tf(이미지 저장소), alb.tf(요청 입구)

resource "aws_ecs_cluster" "this" {
  name = var.project

  setting {
    name  = "containerInsights"
    value = "disabled"
  }
}

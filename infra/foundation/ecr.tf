# ECR(Elastic Container Registry): 빌드한 Docker 이미지를 보관하는 저장소다.
# ECS는 여기서 이미지를 받아 컨테이너를 실행한다. 롤백은 이전에 올려 둔 이미지로 되돌리는 방식이다.
#
# 관련 파일: ecs.tf, iam.tf(이미지를 받을 권한)

# 태그를 덮어쓸 수 없게 해서 "이전 이미지"가 항상 같은 이미지를 가리키게 한다 (롤백 신뢰성)
resource "aws_ecr_repository" "apps" {
  name                 = "${var.project}/apps"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = !var.protect_from_destroy

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }
}

# 보관 개수는 저장소 전체(모든 앱) 기준이다. 앱마다 롤백 대상 이미지가 남아 있어야 하므로 넉넉하게 둔다.
# DB 작업용 mysql 클라이언트 이미지(tools- 태그)는 앱 이미지가 밀어내지 못하도록 별도 규칙으로 둔다
# (우선순위가 낮은 숫자가 먼저 적용되고, 태그가 있는 모든 이미지를 잡는 규칙은 맨 뒤에 둔다)
resource "aws_ecr_lifecycle_policy" "apps" {
  repository = aws_ecr_repository.apps.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep tool images"
        selection = {
          tagStatus     = "tagged"
          tagPrefixList = ["tools-"]
          countType     = "imageCountMoreThan"
          countNumber   = 10
        }
        action = {
          type = "expire"
        }
      },
      {
        rulePriority = 2
        description  = "Keep the most recent app images only"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = var.ecr_keep_images
        }
        action = {
          type = "expire"
        }
      },
    ]
  })
}

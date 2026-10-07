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

resource "aws_ecr_lifecycle_policy" "apps" {
  repository = aws_ecr_repository.apps.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the most recent images only"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = var.ecr_keep_images
      }
      action = {
        type = "expire"
      }
    }]
  })
}

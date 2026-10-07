terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # 배포 건마다 디렉터리를 복사해 state를 분리한다. 원격 state는 쓰지 않는다
  backend "local" {}
}

provider "aws" {
  region = var.platform.region

  default_tags {
    tags = {
      Project   = "paved-clouds"
      DeployId  = var.platform.deploy_id
      ManagedBy = "paved-clouds-platform"
    }
  }
}

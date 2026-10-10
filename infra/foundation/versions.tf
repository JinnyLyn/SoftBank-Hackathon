terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }
}

provider "aws" {
  region = var.region

  # 모든 리소스에 공통 태그를 일괄 적용 (CLAUDE.md 4장 리소스 식별)
  default_tags {
    tags = {
      Project   = var.project
      Stack     = "foundation"
      ManagedBy = "terraform"
    }
  }
}

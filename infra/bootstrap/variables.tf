variable "project" {
  description = "버킷 이름과 태그에 쓰는 프로젝트 이름"
  type        = string
  default     = "paved-clouds"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,20}$", var.project))
    error_message = "project는 소문자로 시작하는 3~21자의 소문자·숫자·하이픈이어야 합니다."
  }
}

variable "region" {
  description = "state 버킷을 만들 리전. 계정에 지정된 리전을 직접 지정한다"
  type        = string

  validation {
    condition     = can(regex("^[a-z]{2}(-[a-z]+)+-[0-9]$", var.region))
    error_message = "region은 sa-east-1 같은 AWS 리전 이름이어야 합니다."
  }
}

variable "force_destroy" {
  description = "true면 버킷에 state가 남아 있어도 지운다. 시험용으로 정리할 때만 true로 바꾼다"
  type        = bool
  default     = false
}

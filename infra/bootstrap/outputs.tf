output "bucket" {
  description = "state 버킷 이름. PAVED_STATE_BUCKET 환경 변수에 넣는다"
  value       = aws_s3_bucket.state.bucket
}

output "region" {
  description = "버킷 리전. 필요하면 PAVED_STATE_REGION 환경 변수에 넣는다"
  value       = var.region
}

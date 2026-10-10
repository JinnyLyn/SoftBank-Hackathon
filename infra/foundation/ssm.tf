# SSM Parameter Store: 비밀 값을 암호화해서 보관하는 AWS 서비스다.
# DB 접속 정보를 코드나 환경 변수에 평문으로 두지 않고, 컨테이너가 시작될 때 ECS가 이 값을 꺼내 DATABASE_URL로 주입한다.
#
# 관련 파일: rds.tf(접속할 DB), iam.tf(이 파라미터를 읽을 권한)

# 공유 DB 접속 URL. DB를 앱별로 나누지 않는 배포(--shared-db)나 기존 앱이 쓴다.
# 앱은 DATABASE_URL 하나로만 접속 정보를 받는다 (CLAUDE.md 3.2)
# 형식은 mysql://user:password@host:port/db. 프레임워크별 형식 변환은 3단계 LLM 수정안이 맡는다.
# 이 URL은 DB 관리자 계정이다. 기본 배포는 이것 대신 앱별 계정(/<프로젝트>/apps/<배포ID>/database-url)을 쓴다
resource "aws_ssm_parameter" "database_url" {
  name        = "/${var.project}/database-url"
  description = "Shared admin DATABASE_URL (use per-app credentials instead when possible)"
  type        = "SecureString"
  value       = "mysql://${var.db_username}:${random_password.db.result}@${aws_db_instance.this.address}:${aws_db_instance.this.port}/${var.db_name}"
}

# 앱별 DB와 계정을 만드는 1회성 작업이 쓰는 관리자 비밀번호
resource "aws_ssm_parameter" "db_admin_password" {
  name        = "/${var.project}/db-admin-password"
  description = "RDS admin password, read only by the one-off DB provisioner task"
  type        = "SecureString"
  value       = random_password.db.result
}

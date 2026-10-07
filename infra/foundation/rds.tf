# RDS: AWS가 관리해 주는 MySQL 데이터베이스다. 백업, 패치 같은 운영을 AWS가 맡는다.
# 프라이빗 서브넷에 두고 앱 태스크 보안 그룹에서 오는 접속만 허용한다. 모든 앱이 `app` 데이터베이스 하나를 같이 쓴다.
#
# 관련 파일: vpc.tf(프라이빗 서브넷), security_groups.tf(db 보안 그룹), ssm.tf(접속 정보를 앱에 전달)

# URL에 그대로 넣을 수 있도록 특수문자 없이 생성한다.
# 이 값은 foundation state에 남는다. foundation state는 Git에 올리지 않고 담당자만 보관한다.
resource "random_password" "db" {
  length  = 32
  special = false
}

# RDS가 사용할 서브넷 묶음. 최소 2개 AZ가 필요하다
resource "aws_db_subnet_group" "this" {
  name       = "${var.project}-db"
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_db_instance" "this" {
  identifier = "${var.project}-mysql"

  engine         = "mysql"
  engine_version = var.db_engine_version
  # 표준 지원이 끝난 버전으로 생성·복원되어 유료 Extended Support에 자동 가입되는 것을 막는다
  engine_lifecycle_support = "open-source-rds-extended-support-disabled"

  instance_class    = var.db_instance_class
  allocated_storage = var.db_allocated_storage
  storage_type      = "gp3"
  storage_encrypted = true

  db_name  = var.db_name
  username = var.db_username
  password = random_password.db.result

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.db.id]
  publicly_accessible    = false
  multi_az               = false

  backup_retention_period    = 1
  auto_minor_version_upgrade = true
  apply_immediately          = true
  skip_final_snapshot        = true
  deletion_protection        = var.protect_from_destroy
}

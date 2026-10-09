# 앱별 DB·계정 생성용 1회성 작업(deploy.sh)이 쓰는 로그 그룹.
# 모든 앱이 DB 관리자 계정으로 같은 DB를 쓰면 앱 하나가 뚫렸을 때 다른 앱의 데이터까지 읽고 지울 수 있다.
# 그래서 배포마다 전용 DB와 그 DB에만 권한이 있는 계정을 만들어 앱에 준다.
# DB는 프라이빗 서브넷에 있어서 PC에서 직접 접속할 수 없으므로, VPC 안에서 mysql 클라이언트를 한 번 실행하는
# Fargate 작업으로 만든다. 작업 정의는 deploy.sh가 그때그때 등록한다(비밀번호 파라미터가 배포마다 다르기 때문).
#
# 관련 파일: security_groups.tf(db_provisioner 보안 그룹), iam.tf(읽을 파라미터), ssm.tf(관리자 비밀번호)

resource "aws_cloudwatch_log_group" "db_provisioner" {
  name              = "/${var.project}/db-provisioner"
  retention_in_days = 7
}

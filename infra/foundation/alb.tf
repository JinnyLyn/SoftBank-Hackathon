# ALB(Application Load Balancer): 인터넷에서 들어오는 HTTP 요청을 받아 앱 컨테이너로 나눠 보내는 입구다.
# 배포마다 ALB를 만들면 생성에 수 분이 걸려 3분 데모를 넘기므로 미리 하나만 만든다.
# 배포별 앱은 이 ALB에 전용 포트 리스너를 하나씩 추가한다 (modules/ecs-web-app).
#
# 관련 파일: vpc.tf(퍼블릭 서브넷에 위치), security_groups.tf(alb 보안 그룹)

resource "aws_lb" "this" {
  name                       = "${var.project}-alb"
  load_balancer_type         = "application"
  internal                   = false
  security_groups            = [aws_security_group.alb.id]
  subnets                    = aws_subnet.public[*].id
  drop_invalid_header_fields = true
}

# 80번 포트는 앱 없이 404만 돌려준다
resource "aws_lb_listener" "default" {
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "fixed-response"

    fixed_response {
      content_type = "text/plain"
      message_body = "Paved Clouds: no application on this port"
      status_code  = "404"
    }
  }
}

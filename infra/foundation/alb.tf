# ALB(Application Load Balancer): 인터넷에서 들어오는 요청을 받아 앱 컨테이너로 나눠 보내는 입구다.
# 배포마다 ALB를 만들면 생성에 수 분이 걸려 3분 데모를 넘기므로 미리 하나만 만든다.
# 배포별 앱은 이 ALB에 전용 포트 리스너를 하나씩 추가한다 (modules/ecs-web-app).
#
# HTTPS: certificate_arn을 지정하면 443 리스너를 만들고 80번은 443으로 넘긴다. 배포별 리스너도 HTTPS가 된다.
# 지정하지 않으면 HTTP만 열린다. 비밀번호와 로그인 쿠키가 평문으로 오가므로 시험용으로만 쓴다.
#
# 관련 파일: vpc.tf(퍼블릭 서브넷에 위치), security_groups.tf(alb 보안 그룹)

locals {
  https_enabled = var.certificate_arn != ""
  # 인증서에 맞는 최신 TLS 정책(TLS 1.2 이상)
  ssl_policy = "ELBSecurityPolicy-TLS13-1-2-2021-06"
}

resource "aws_lb" "this" {
  name                       = "${var.project}-alb"
  load_balancer_type         = "application"
  internal                   = false
  security_groups            = [aws_security_group.alb.id]
  subnets                    = aws_subnet.public[*].id
  drop_invalid_header_fields = true
}

# 80번 포트: HTTPS를 켜면 443으로 넘기고, 아니면 앱 없이 404만 돌려준다
resource "aws_lb_listener" "default" {
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = local.https_enabled ? "redirect" : "fixed-response"

    dynamic "redirect" {
      for_each = local.https_enabled ? [1] : []

      content {
        port        = "443"
        protocol    = "HTTPS"
        status_code = "HTTP_301"
      }
    }

    dynamic "fixed_response" {
      for_each = local.https_enabled ? [] : [1]

      content {
        content_type = "text/plain"
        message_body = "Paved Clouds: no application on this port"
        status_code  = "404"
      }
    }
  }
}

resource "aws_lb_listener" "https_default" {
  count = local.https_enabled ? 1 : 0

  load_balancer_arn = aws_lb.this.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = local.ssl_policy
  certificate_arn   = var.certificate_arn

  default_action {
    type = "fixed-response"

    fixed_response {
      content_type = "text/plain"
      message_body = "Paved Clouds: no application on this port"
      status_code  = "404"
    }
  }
}

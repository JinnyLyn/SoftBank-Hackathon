# ALB 연결: 미리 만들어 둔 공유 ALB(foundation/alb.tf)에 이 배포 전용 입구를 추가한다. 두 방식 중 하나다.
#   포트 방식(기본): 전용 포트 리스너. ALB가 배포마다 다른 포트(예: 8001)에서 요청을 기다린다.
#                    접속 URL은 <http 또는 https>://<ALB 주소>:<리스너 포트> 이다. 리스너 수 때문에 앱은 최대 49개다
#   도메인 방식(domain_name 지정): foundation의 공유 443 리스너에 "Host가 <deploy_id>.<app_domain>이면 이 앱으로" 규칙 하나.
#                    접속 URL은 https://<deploy_id>.<app_domain> 이다. DNS는 foundation의 와일드카드 레코드, 인증서는 foundation의 것을 쓴다.
#                    이 배포가 소유하는 것은 규칙 하나뿐이라 destroy해도 공유 리스너·인증서·DNS는 남는다
#   대상 그룹: 요청을 보낼 컨테이너 목록이다. ECS 서비스가 태스크를 여기에 자동 등록하고, 헬스체크로 정상 여부를 확인한다
#
# 관련 파일: ecs.tf(서비스가 대상 그룹에 태스크를 등록)

# 대상 그룹 이름은 32자 제한이 있어 짧은 접두사를 쓴다 (예: pc-a1b2c3d4)
# 헬스체크 간격은 10초로 짧게 잡아 실패를 빨리 안다. 등록 해제 지연(deregistration_delay_seconds)은 교체되는 태스크가 처리 중인 요청을 끝낼 시간이다
resource "aws_lb_target_group" "app" {
  name                 = local.short_name
  port                 = var.container_port
  protocol             = "HTTP"
  target_type          = "ip"
  vpc_id               = var.foundation.vpc_id
  deregistration_delay = var.deregistration_delay_seconds

  health_check {
    path                = var.health_check_path
    matcher             = "200-399"
    interval            = 10
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}

# 포트 방식: 공유 ALB에 이 배포 전용 포트 리스너를 추가한다
resource "aws_lb_listener" "app" {
  count = local.use_domain ? 0 : 1

  load_balancer_arn = var.foundation.alb_arn
  port              = var.listener_port
  protocol          = var.foundation.listener_protocol
  # HTTPS면 TLS는 ALB에서 끝낸다. 컨테이너까지는 HTTP로 간다
  ssl_policy      = var.foundation.listener_protocol == "HTTPS" ? "ELBSecurityPolicy-TLS13-1-2-2021-06" : null
  certificate_arn = var.foundation.listener_protocol == "HTTPS" ? var.foundation.certificate_arn : null

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }

  lifecycle {
    precondition {
      condition = (
        floor(var.listener_port) == var.listener_port &&
        var.listener_port >= var.foundation.allowed_listener_ports.from &&
        var.listener_port <= var.foundation.allowed_listener_ports.to
      )
      error_message = "listener_port가 foundation에서 허용한 포트 범위 밖입니다. 범위 밖 포트는 보안 그룹에 막혀 외부에서 접속할 수 없습니다."
    }

    precondition {
      condition     = var.foundation.listener_protocol != "HTTPS" || var.foundation.certificate_arn != ""
      error_message = "listener_protocol이 HTTPS인데 certificate_arn이 비어 있습니다."
    }
  }
}

# count를 붙이기 전에 만든 배포의 리스너를 지우고 다시 만들지 않도록 주소만 옮긴다
moved {
  from = aws_lb_listener.app
  to   = aws_lb_listener.app[0]
}

# 도메인 방식: foundation의 공유 443 리스너에 이 배포의 호스트 규칙을 붙인다
resource "aws_lb_listener_rule" "app" {
  count = local.use_domain ? 1 : 0

  listener_arn = var.foundation.https_listener_arn
  priority     = var.listener_rule_priority

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }

  condition {
    host_header {
      values = [var.domain_name]
    }
  }

  lifecycle {
    precondition {
      condition     = var.foundation.https_listener_arn != "" && var.foundation.listener_protocol == "HTTPS"
      error_message = "domain_name을 쓰려면 foundation에 HTTPS 공유 리스너(https_listener_arn)가 있어야 합니다. foundation을 certificate_arn·app_domain과 함께 apply하세요."
    }

    # 와일드카드 DNS·인증서가 덮는 한 단계 이름만 허용한다. 배포 ID는 겹치지 않으므로 다른 앱의 주소를 가로챌 수 없다.
    # 사용자가 가진 도메인을 연결하게 되면 이 조건을 넓힌다(인증서·소유권 검증이 먼저 필요하다)
    precondition {
      condition     = var.foundation.app_domain != "" && var.domain_name == "${var.deploy_id}.${var.foundation.app_domain}"
      error_message = "domain_name은 <deploy_id>.<foundation.app_domain>이어야 합니다."
    }

    precondition {
      condition     = var.listener_rule_priority >= 1
      error_message = "도메인 방식에는 listener_rule_priority(1~50000)가 필요합니다."
    }
  }
}

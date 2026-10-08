# ALB 연결: 미리 만들어 둔 공유 ALB(foundation/alb.tf)에 이 배포 전용 입구를 추가한다.
#   리스너: ALB가 특정 포트에서 요청을 기다린다. 배포마다 포트가 다르다 (예: 8001)
#   대상 그룹: 요청을 보낼 컨테이너 목록이다. ECS 서비스가 태스크를 여기에 자동 등록하고, 헬스체크로 정상 여부를 확인한다
# 접속 URL은 http://<ALB 주소>:<리스너 포트> 이다 (outputs.tf)
#
# 관련 파일: ecs.tf(서비스가 대상 그룹에 태스크를 등록)

# 대상 그룹 이름은 32자 제한이 있어 짧은 접두사를 쓴다 (예: pc-a1b2c3d4)
# 데모 시간을 줄이려고 헬스체크 간격과 등록 해제 지연을 짧게 잡았다
resource "aws_lb_target_group" "app" {
  name                 = local.short_name
  port                 = var.container_port
  protocol             = "HTTP"
  target_type          = "ip"
  vpc_id               = var.foundation.vpc_id
  deregistration_delay = 10

  health_check {
    path                = var.health_check_path
    matcher             = "200-399"
    interval            = 10
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}

# 공유 ALB에 이 배포 전용 포트 리스너를 추가한다
resource "aws_lb_listener" "app" {
  load_balancer_arn = var.foundation.alb_arn
  port              = var.listener_port
  protocol          = "HTTP"

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
  }
}

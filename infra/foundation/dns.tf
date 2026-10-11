# 앱 자동 주소(<deploy_id>.<app_domain>)를 위한 DNS. app_domain을 지정했을 때만 만든다.
#   와일드카드 A 별칭: *.<app_domain> → 공유 ALB. 앱마다 레코드를 만들지 않는다. 앱 구분은 443 리스너의 호스트 규칙이 한다(modules/ecs-web-app/alb.tf)
#   인증서 검증 CNAME: ACM이 인증서를 자동 갱신할 때 다시 확인한다. 지워지면 만료일에 갱신이 실패한다
# 호스팅 영역 자체와 apex(<app_domain>) 레코드는 만들거나 바꾸지 않는다(도메인 등록과 플랫폼 주소는 수명 주기가 다르다).
# allow_overwrite: 콘솔에서 먼저 만든 같은 이름·유형의 레코드가 있으면 덮어써서 이 state로 가져온다.
# 이 레코드들은 foundation 소유다. 앱 destroy로 지워지지 않고, foundation을 destroy하면 함께 지워진다
#
# 관련 파일: alb.tf(443 리스너), outputs.tf(app_domain·https_listener_arn을 배포에 넘김)

locals {
  app_domain_enabled = var.app_domain != ""
  app_zone_id        = !local.app_domain_enabled ? "" : (var.app_domain_zone_id != "" ? var.app_domain_zone_id : data.aws_route53_zone.app[0].zone_id)
}

data "aws_route53_zone" "app" {
  count = local.app_domain_enabled && var.app_domain_zone_id == "" ? 1 : 0

  name         = var.app_domain
  private_zone = false
}

resource "aws_route53_record" "apps_wildcard" {
  count = local.app_domain_enabled ? 1 : 0

  zone_id         = local.app_zone_id
  name            = "*.${var.app_domain}"
  type            = "A"
  allow_overwrite = true

  alias {
    name                   = aws_lb.this.dns_name
    zone_id                = aws_lb.this.zone_id
    evaluate_target_health = false
  }

  lifecycle {
    precondition {
      condition     = local.https_enabled
      error_message = "app_domain을 쓰려면 certificate_arn(*.<app_domain>을 포함한 인증서)이 필요합니다. 앱 주소는 HTTPS 443 리스너의 호스트 규칙으로만 연결합니다."
    }
  }
}

resource "aws_route53_record" "certificate_validation" {
  for_each = local.app_domain_enabled ? { for r in var.certificate_validation_records : trimsuffix(r.name, ".") => r } : {}

  zone_id         = local.app_zone_id
  name            = each.key
  type            = "CNAME"
  ttl             = 300
  records         = [each.value.value]
  allow_overwrite = true

  lifecycle {
    precondition {
      condition     = endswith(each.key, ".${var.app_domain}")
      error_message = "certificate_validation_records의 이름은 app_domain의 호스팅 영역 안(…<app_domain>)이어야 합니다."
    }
  }
}

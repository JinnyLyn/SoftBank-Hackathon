# AWS Budgets: 계정의 월 비용이 정한 금액에 가까워지거나 넘을 것으로 예상되면 이메일로 알린다.
# 알림만 보낸다. 리소스를 막거나 멈추거나 지우지 않는다(차단 장치가 아니다).
# 사용자가 입력한 앱별 월 예산(비용 계산 코드가 구성을 고르는 기준)과는 별개인 운영자용 안전망이다.
# 태스크 수·크기 상한을 코드에서 없앴으므로, 계산이 틀리거나 제외 항목(NAT 인스턴스, 데이터 전송 등)이 커졌을 때 이 알림으로 알아챈다.
#
# budget_monthly_usd 와 budget_alert_emails 를 둘 다 지정해야 만들어진다(기본은 만들지 않는다).
# 대상은 이 계정 전체 비용이다(필터 없음). 만들려면 배포 역할에 budgets 권한(budgets:ViewBudget, budgets:ModifyBudget 등)이 필요하다.
#
# 관련 파일: variables.tf(budget_monthly_usd, budget_alert_emails)

locals {
  budget_enabled = var.budget_monthly_usd > 0 && length(var.budget_alert_emails) > 0
}

# 금액만 정하고 받는 사람을 빼먹으면 알림이 조용히 꺼진다. 막지는 않고 경고로 알린다
check "budget_alert_target" {
  assert {
    condition     = var.budget_monthly_usd == 0 || length(var.budget_alert_emails) > 0
    error_message = "budget_monthly_usd를 지정했지만 budget_alert_emails가 비어 있어 AWS Budgets 알림을 만들지 않습니다."
  }
}

resource "aws_budgets_budget" "monthly" {
  count = local.budget_enabled ? 1 : 0

  name         = "${var.project}-monthly"
  budget_type  = "COST"
  limit_amount = format("%.2f", var.budget_monthly_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  # 이미 쓴 금액이 80%, 100%를 넘을 때
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = var.budget_alert_emails
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = var.budget_alert_emails
  }

  # 이번 달 말까지 100%를 넘을 것으로 예측될 때(미리 알아챌 수 있다)
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = var.budget_alert_emails
  }
}

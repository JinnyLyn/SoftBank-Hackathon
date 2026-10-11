-- 배포된 앱의 운영 알림(배포·앱별 종류마다 최신 1건). 지금은 capacity: 자동 확장이 최대 태스크 수까지 늘었는데도 CPU가 높음.
-- worker가 CloudWatch를 읽어 기록하고, 화면은 SSE(/api/alerts/stream)로 받는다. 알림일 뿐 구성을 바꾸지 않는다(바꾸려면 다시 계획·승인).
CREATE TABLE IF NOT EXISTS deployment_alerts (
    deployment_id CHAR(36) NOT NULL,
    kind ENUM('capacity') NOT NULL,
    active BOOLEAN NOT NULL,
    message VARCHAR(1000) NOT NULL,
    metrics JSON NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (deployment_id, kind),
    INDEX idx_deployment_alerts_updated (updated_at),
    CONSTRAINT fk_deployment_alerts_deployment FOREIGN KEY (deployment_id) REFERENCES deployments (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

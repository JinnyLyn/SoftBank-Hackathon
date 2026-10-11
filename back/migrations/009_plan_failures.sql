-- worker가 계획을 만들지 못한 이유(대상별 최신 1건). 계획이 등록되면 지운다.
-- 예: 월 예산이 가장 작은 구성보다 낮음, 분석 결과에 포트가 없음. 화면이 5분을 기다리지 않고 이유를 바로 보여 주는 데 쓴다.
CREATE TABLE IF NOT EXISTS plan_failures (
    project_id CHAR(36) NOT NULL,
    target ENUM('local_docker', 'aws') NOT NULL,
    reason VARCHAR(1000) NOT NULL,
    retryable BOOLEAN NOT NULL,
    attempts INT UNSIGNED NOT NULL,
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (project_id, target),
    CONSTRAINT fk_plan_failures_project FOREIGN KEY (project_id) REFERENCES projects (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

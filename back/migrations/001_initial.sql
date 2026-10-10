CREATE TABLE IF NOT EXISTS projects (
    id CHAR(36) NOT NULL PRIMARY KEY,
    name VARCHAR(120) NOT NULL,
    source_filename VARCHAR(255) NOT NULL,
    source_path VARCHAR(1024) NOT NULL,
    source_sha256 CHAR(64) NOT NULL,
    source_size_bytes BIGINT UNSIGNED NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    INDEX idx_projects_created (created_at, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS analyses (
    id CHAR(36) NOT NULL PRIMARY KEY,
    project_id CHAR(36) NOT NULL,
    schema_version VARCHAR(32) NOT NULL,
    source_sha256 CHAR(64) NOT NULL,
    result JSON NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    INDEX idx_analyses_project_created (project_id, created_at),
    CONSTRAINT fk_analyses_project FOREIGN KEY (project_id) REFERENCES projects (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS deployment_plans (
    id CHAR(36) NOT NULL PRIMARY KEY,
    project_id CHAR(36) NOT NULL,
    analysis_id CHAR(36) NULL,
    target ENUM('local_docker', 'aws') NOT NULL,
    module_id VARCHAR(100) NOT NULL,
    variables JSON NOT NULL,
    summary TEXT NOT NULL,
    cost_estimate JSON NULL,
    terraform_plan_sha256 CHAR(64) NULL,
    terraform_plan_path VARCHAR(1024) NULL,
    fingerprint CHAR(64) NOT NULL,
    status ENUM('awaiting_approval', 'approved', 'consumed', 'superseded') NOT NULL DEFAULT 'awaiting_approval',
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    approved_at TIMESTAMP(6) NULL,
    approved_fingerprint CHAR(64) NULL,
    INDEX idx_plans_project_created (project_id, created_at),
    INDEX idx_plans_status (status, created_at),
    CONSTRAINT fk_plans_project FOREIGN KEY (project_id) REFERENCES projects (id),
    CONSTRAINT fk_plans_analysis FOREIGN KEY (analysis_id) REFERENCES analyses (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS deployments (
    id CHAR(36) NOT NULL PRIMARY KEY,
    plan_id CHAR(36) NOT NULL,
    project_id CHAR(36) NOT NULL,
    target ENUM('local_docker', 'aws') NOT NULL,
    status ENUM('queued', 'provisioning', 'deploying', 'healthy', 'failed', 'rolling_back', 'rolled_back') NOT NULL,
    url VARCHAR(2048) NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    INDEX idx_deployments_project_created (project_id, created_at),
    INDEX idx_deployments_status_created (status, created_at),
    CONSTRAINT fk_deployments_plan FOREIGN KEY (plan_id) REFERENCES deployment_plans (id),
    CONSTRAINT fk_deployments_project FOREIGN KEY (project_id) REFERENCES projects (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS deployment_events (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    deployment_id CHAR(36) NOT NULL,
    level ENUM('info', 'warning', 'error') NOT NULL,
    event_type VARCHAR(80) NOT NULL,
    message VARCHAR(4000) NOT NULL,
    details JSON NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    INDEX idx_deployment_events_created (deployment_id, created_at, id),
    CONSTRAINT fk_events_deployment FOREIGN KEY (deployment_id) REFERENCES deployments (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

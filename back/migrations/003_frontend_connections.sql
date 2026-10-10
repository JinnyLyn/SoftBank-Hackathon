CREATE TABLE IF NOT EXISTS connections (
    id CHAR(36) NOT NULL PRIMARY KEY,
    provider ENUM('aws') NOT NULL,
    name VARCHAR(120) NOT NULL,
    status ENUM('connected', 'pending', 'error') NOT NULL DEFAULT 'pending',
    detail VARCHAR(512) NOT NULL,
    error VARCHAR(2000) NULL,
    setup_url VARCHAR(2048) NULL,
    external_id CHAR(35) NOT NULL,
    fields JSON NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    INDEX idx_connections_status (status, updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

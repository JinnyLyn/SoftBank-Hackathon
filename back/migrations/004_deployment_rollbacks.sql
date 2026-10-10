ALTER TABLE deployments
    ADD COLUMN operation_type ENUM('deploy', 'rollback') NOT NULL DEFAULT 'deploy' AFTER target,
    ADD COLUMN rollback_from_deployment_id CHAR(36) NULL AFTER operation_type,
    ADD COLUMN rollback_to_deployment_id CHAR(36) NULL AFTER rollback_from_deployment_id,
    ADD INDEX idx_deployments_rollback_from (rollback_from_deployment_id, created_at),
    ADD CONSTRAINT fk_deployments_rollback_from
        FOREIGN KEY (rollback_from_deployment_id) REFERENCES deployments (id),
    ADD CONSTRAINT fk_deployments_rollback_to
        FOREIGN KEY (rollback_to_deployment_id) REFERENCES deployments (id);

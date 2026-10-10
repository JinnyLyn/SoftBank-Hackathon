ALTER TABLE projects
    ADD COLUMN expected_users VARCHAR(16) NULL AFTER source_ref,
    ADD COLUMN traffic_pattern ENUM('steady', 'peak', 'unknown') NULL AFTER expected_users,
    ADD COLUMN monthly_budget_usd DECIMAL(12, 4) NULL AFTER traffic_pattern,
    ADD COLUMN purpose VARCHAR(2000) NULL AFTER monthly_budget_usd;

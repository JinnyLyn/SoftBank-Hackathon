ALTER TABLE projects
    ADD COLUMN source_type ENUM('zip', 'github') NOT NULL DEFAULT 'zip' AFTER source_size_bytes,
    ADD COLUMN source_url VARCHAR(2048) NULL AFTER source_type,
    ADD COLUMN source_ref VARCHAR(255) NULL AFTER source_url;

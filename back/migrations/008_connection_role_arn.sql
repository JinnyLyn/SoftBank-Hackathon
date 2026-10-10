ALTER TABLE connections
    ADD COLUMN aws_account_id CHAR(12) NULL AFTER external_id,
    ADD COLUMN role_arn VARCHAR(2048) NULL AFTER aws_account_id;

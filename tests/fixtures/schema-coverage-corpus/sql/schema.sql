-- WI-luzuh fixture: SQL DDL constructs.
-- Triggers: table, view, index, trigger.

CREATE TABLE users (
    id BIGINT PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    name VARCHAR(100)
);

CREATE INDEX idx_users_email ON users(email);

CREATE VIEW active_users AS
SELECT id, email FROM users WHERE name IS NOT NULL;

CREATE TRIGGER update_users_timestamp
BEFORE UPDATE ON users
FOR EACH ROW
BEGIN
    SET NEW.email = LOWER(NEW.email);
END;

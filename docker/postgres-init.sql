-- The application role must NOT be a superuser and must NOT have BYPASSRLS,
-- otherwise Row-Level Security (client isolation) would be silently skipped.
CREATE ROLE agency LOGIN PASSWORD 'agency' NOSUPERUSER NOBYPASSRLS NOCREATEROLE;
CREATE DATABASE agency OWNER agency;

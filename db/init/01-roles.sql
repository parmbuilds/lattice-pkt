-- Database roles for Lattice (KTD8). Runs once as the PostgreSQL superuser,
-- outside Alembic, when the database is first created.
--
-- lattice_owner runs migrations and owns every table.
-- lattice_app is the runtime role the API and MCP server connect as; it never
-- owns anything, so it cannot disable triggers or regrant privileges.
--
-- The passwords below are local-development defaults. Change them for any
-- shared deployment.

CREATE ROLE lattice_owner LOGIN PASSWORD 'lattice_owner_dev';
CREATE ROLE lattice_app LOGIN PASSWORD 'lattice_app_dev' NOINHERIT;

ALTER DATABASE lattice OWNER TO lattice_owner;

\connect lattice

ALTER SCHEMA public OWNER TO lattice_owner;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO lattice_app;

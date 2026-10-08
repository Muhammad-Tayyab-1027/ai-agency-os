"""security: row-level security, append-only audit log, full-text search, seed data

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels = None
depends_on = None

# Tables holding client data. Rows with client_id IS NULL are agency-global.
CLIENT_SCOPED = [
    "projects",
    "tasks",
    "agent_messages",
    "approvals",
    "human_tasks",
    "kb_documents",
    "revenue_events",
]

BYPASS = "coalesce(current_setting('app.bypass_rls', true), 'off') = 'on'"
CURRENT_CLIENT = "nullif(current_setting('app.client_id', true), '')::uuid"


def upgrade() -> None:
    # --- Row-Level Security ---------------------------------------------------
    # FORCE makes the policies apply to the table owner too. The application role must
    # not be a superuser and must not have BYPASSRLS (see docker/postgres-init.sql).
    for table in CLIENT_SCOPED:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""CREATE POLICY client_isolation ON {table}
                USING ({BYPASS} OR client_id IS NULL OR client_id = {CURRENT_CLIENT})
                WITH CHECK ({BYPASS} OR client_id IS NULL OR client_id = {CURRENT_CLIENT})"""
        )
    op.execute("ALTER TABLE clients ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE clients FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""CREATE POLICY client_isolation ON clients
            USING ({BYPASS} OR id = {CURRENT_CLIENT})
            WITH CHECK ({BYPASS} OR id = {CURRENT_CLIENT})"""
    )

    # --- Append-only audit log --------------------------------------------------
    op.execute(
        """CREATE OR REPLACE FUNCTION audit_log_immutable() RETURNS trigger AS $$
           BEGIN
             RAISE EXCEPTION 'audit_log is append-only';
           END;
           $$ LANGUAGE plpgsql"""
    )
    op.execute(
        """CREATE TRIGGER audit_log_no_update BEFORE UPDATE OR DELETE ON audit_log
           FOR EACH ROW EXECUTE FUNCTION audit_log_immutable()"""
    )
    op.execute(
        """CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log
           FOR EACH STATEMENT EXECUTE FUNCTION audit_log_immutable()"""
    )

    # --- Knowledge-base full-text search ---------------------------------------
    op.execute(
        """CREATE INDEX ix_kb_documents_fts ON kb_documents
           USING gin (to_tsvector('english', title || ' ' || content))"""
    )

    # --- Seed: service catalog (prices are editable starting points) -----------
    op.execute(
        """INSERT INTO services (key, name, description, base_price, currency, active) VALUES
        ('website', 'Website design & build', 'Modern, fast, mobile-first small-business website (5-8 pages).', 1500, 'USD', true),
        ('website_refresh', 'Website refresh', 'Redesign and speed/SEO fixes for an existing site.', 800, 'USD', true),
        ('social_media', 'Social media management', 'Monthly content calendar, posting and community replies.', 600, 'USD', true),
        ('graphic_design', 'Graphic design', 'Logos, brand kits, flyers, social templates.', 300, 'USD', true),
        ('content', 'Content writing', 'Website copy, blog posts, newsletters.', 250, 'USD', true),
        ('marketing', 'Local marketing & SEO', 'Google Business Profile, local SEO, ad campaign setup.', 700, 'USD', true),
        ('research', 'Market research', 'Competitor and market research reports.', 400, 'USD', true),
        ('lead_generation', 'Lead generation', 'Researched, scored B2B lead lists.', 350, 'USD', true),
        ('ai_automation', 'AI automation', 'Chatbots, booking and workflow automation.', 1200, 'USD', true)
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM services")
    op.execute("DROP INDEX IF EXISTS ix_kb_documents_fts")
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_truncate ON audit_log")
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_update ON audit_log")
    op.execute("DROP FUNCTION IF EXISTS audit_log_immutable()")
    for table in [*CLIENT_SCOPED, "clients"]:
        op.execute(f"DROP POLICY IF EXISTS client_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

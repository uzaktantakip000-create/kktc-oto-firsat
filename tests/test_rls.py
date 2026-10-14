"""Supabase: public şemadaki her tablonun satır güvenliği (RLS) açık olmalı; kapalı tablo anon anahtarıyla REST'ten okunur/yazılır
(14.10.2026: blocked_sellers migration 010'da unutulmuştu, Supabase uyarı e-postası; 020 düzeltti). Yeni tablo ekleyen migration bunu unutursa bu test kırılır."""
import pytest


@pytest.mark.db
def test_every_public_table_has_row_level_security(db):
    open_tables = db.conn.execute(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND NOT c.relrowsecurity ORDER BY 1").fetchall()
    assert not open_tables, f"RLS kapalı tablo(lar): {open_tables}"


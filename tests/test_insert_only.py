"""`evaluations` EKLEME-YALNIZ (Adım 5b dilim 2): geçmiş kararlar sonradan değiştirilemez/silinemez; "bir kez gider" ve ölçüm raporları buna dayanır.
Bu bekçi yazılım kodunda (domain/application/infrastructure/entrypoints) ve migration dosyalarında evaluations üzerinde UPDATE / DELETE / TRUNCATE /
INSERT ... ON CONFLICT DO UPDATE yazılmasını yasaklar. Tek tarihî istisna: migration 009 (eski veriyi bir kez düzeltmişti); liste YALNIZ KÜÇÜLÜR."""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCAN_DIRS = ("domain", "application", "infrastructure", "entrypoints")
ALLOWED_MIGRATIONS = {"009_sade_seviye.sql"}

TABLE = r"(?:public\.)?\"?evaluations\"?"
BAD = re.compile(
    rf"\bupdate\s+(?:only\s+)?{TABLE}(?:\s+\w+)?\s+set\b"
    rf"|\bdelete\s+from\s+(?:only\s+)?{TABLE}\b"
    rf"|\btruncate\s+(?:table\s+)?(?:only\s+)?{TABLE}\b"
    rf"|\binsert\s+into\s+{TABLE}[^;]*?\bon\s+conflict\b[^;]*?\bdo\s+update\b",
    re.I | re.S)


def docstring_ids(tree: ast.AST) -> set[int]:
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                out.add(id(first.value))
    return out


def offenders_in_code() -> list[str]:
    found = []
    for d in SCAN_DIRS:
        for path in sorted((ROOT / d).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            skip = docstring_ids(tree)
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip and BAD.search(node.value):
                    found.append(f"{path.relative_to(ROOT).as_posix()}:{node.lineno}")
    return found


def test_guard_catches_known_bad_statements_and_ignores_good_ones():
    for bad in ("UPDATE evaluations SET tier='pazarlik' WHERE id=%s", "update public.evaluations e set tier = 'x'", 'UPDATE "evaluations" SET a=1',
                "DELETE FROM evaluations e USING listings l WHERE l.id = e.listing_id", "delete from public.evaluations",
                "TRUNCATE TABLE evaluations", "INSERT INTO evaluations (listing_id) VALUES (1) ON CONFLICT (id) DO UPDATE SET tier='x'"):
        assert BAD.search(bad), bad
    for good in ("INSERT INTO evaluations (listing_id, tier) VALUES (%s, %s)", "SELECT * FROM evaluations WHERE listing_id=%s",
                 "UPDATE listings SET is_active=FALSE", "DELETE FROM listings WHERE id=%s", "INSERT INTO evaluations SELECT * FROM evaluations e WHERE e.id=%s",
                 "CREATE INDEX IF NOT EXISTS idx_evaluations_listing ON evaluations (listing_id)", "ALTER TABLE evaluations ADD COLUMN IF NOT EXISTS x TEXT"):
        assert not BAD.search(good), good


def test_no_code_updates_or_deletes_evaluations():
    assert offenders_in_code() == [], "evaluations ekleme-yalnızdır (düşürme/yenileme yeni satır EKLER); şu yerler UPDATE/DELETE yazıyor"


def test_no_new_migration_rewrites_evaluations():
    found = []
    for path in sorted((ROOT / "infrastructure" / "db" / "migrations").glob("*.sql")):
        text = "\n".join(line.split("--", 1)[0] for line in path.read_text(encoding="utf-8").splitlines())
        if BAD.search(text):
            found.append(path.name)
    assert set(found) <= ALLOWED_MIGRATIONS, f"yeni migration evaluations'ı değiştiriyor/siliyor: {sorted(set(found) - ALLOWED_MIGRATIONS)}"
    stale = ALLOWED_MIGRATIONS - set(found)
    assert not stale, f"izin listesindeki satırlar artık gereksiz, silin (liste yalnız küçülür): {sorted(stale)}"

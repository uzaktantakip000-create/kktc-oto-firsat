"""Mimari bekçisi (CLAUDE.md "Clean Architecture"): kural değişikliği TEK yerde (domain/decision.py) yapılabilsin diye iki kural.

1. `domain/` dış dünyaya bağlanmaz: yalnız standart kütüphane, pydantic ve domain.* import eder (G/Ç, ağ, veritabanı, Telegram yok).
2. Karar ilkelleri (emsal seçimi, kâr hesabı, veri kapısı, gönderim kapısı, değer tablosu tahmini) yalnız domain/decision.py'den çağrılır.
   Henüz geçmemiş birkaç dosya aşağıdaki izin listesindedir; liste YALNIZCA KÜÇÜLÜR (bir dosya geçince satırı silinir, testler bunu ister)."""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCAN_DIRS = ("domain", "application", "infrastructure", "entrypoints")

# (tanımlandığı modül, ad) -> yalnız domain/decision.py kullanabilir
DECISION_PRIMITIVES = {
    ("domain.comparables", "find_market"),
    ("domain.profit", "evaluate_profit"),
    ("domain.alert_policy", "send_floor_ok"),
    ("domain.data_gate", "data_gaps"),
    ("domain.price_book", "estimate_from_book"),
}
# Henüz decide()'a geçmemiş çağıranlar: (dosya, ad). Geçince silinir; yeni satır EKLENEMEZ.
LEGACY_ALLOWED = {
    ("application/price_book_job.py", "estimate_from_book"),  # gece tablo işi: tabloyu kurar, karar vermez
    ("application/price_book_shadow.py", "find_market"),  # gölge karşılaştırma (yöntem A bağımsız kontrolü)
    ("application/backtest.py", "find_market"),  # geriye dönük test
    ("application/backtest.py", "evaluate_profit"),
    ("entrypoints/golden_snapshot.py", "find_market"),  # altın dosyayı yeniden üreten salt-okuma aracı
    ("entrypoints/golden_snapshot.py", "estimate_from_book"),
}
DEFINING_FILES = {"domain/comparables.py", "domain/profit.py", "domain/alert_policy.py", "domain/data_gate.py", "domain/price_book.py"}


def py_files(*dirs):
    for d in dirs:
        yield from sorted((ROOT / d).rglob("*.py"))


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def imports(path: Path):
    """(modül, ad|None) çiftleri: `import a.b` -> ('a.b', None); `from a.b import c` -> ('a.b', 'c')."""
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, None
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            for alias in node.names:
                yield node.module, alias.name


def test_domain_imports_only_standard_library_pydantic_and_domain():
    allowed_roots = set(sys.stdlib_module_names) | {"pydantic", "domain"}
    offenders = sorted({f"{rel(p)} -> {module}" for p in py_files("domain") for module, _ in imports(p)
                        if module.split(".")[0] not in allowed_roots})
    assert not offenders, "domain/ dış bağımlılık/G-Ç import etmemeli:\n" + "\n".join(offenders)


def test_decision_primitives_are_only_called_through_decision_module():
    seen = set()
    offenders = []
    for p in py_files(*SCAN_DIRS):
        f = rel(p)
        if f == "domain/decision.py" or f in DEFINING_FILES:
            continue
        for module, name in imports(p):
            if (module, name) in DECISION_PRIMITIVES:
                if (f, name) in LEGACY_ALLOWED:
                    seen.add((f, name))
                else:
                    offenders.append(f"{f}: from {module} import {name}")
    assert not offenders, ("Karar ilkelleri yalnız domain/decision.py'den çağrılabilir (decide() kullan):\n" + "\n".join(offenders))
    stale = sorted(LEGACY_ALLOWED - seen)
    assert not stale, f"İzin listesindeki satırlar artık gereksiz, silin (liste yalnız küçülür): {stale}"


def test_decision_module_exists_and_uses_every_primitive():
    used = {(m, n) for m, n in imports(ROOT / "domain" / "decision.py")}
    assert DECISION_PRIMITIVES <= used, f"decision.py şunları çağırmıyor: {sorted(DECISION_PRIMITIVES - used)}"

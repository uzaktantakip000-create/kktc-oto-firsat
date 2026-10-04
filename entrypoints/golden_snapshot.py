"""Karar altın dosyası (tests/fixtures/decision_golden.json) için canlı veriden anlık görüntü alır. SADECE OKUR.
Dosyada kimlik bilgisi YOK: telefon, bağlantı, satıcı adı, ilan metni saklanmaz (depo herkese açık). Satıcılar "s1, s2..." olarak,
emsal ilanlar "c1, c2..." olarak yeniden adlandırılır; tarihler "kaç gün önce" olarak saklanır.
Kullanım (kök dizinden): python -m entrypoints.golden_snapshot <seçim.json> <çıktı.json> [<etiketler.json>]
 seçim.json  : [{"id": "<ilan id öneki>", "origin": "...", "price_override": null | sayı, "note": "..."}]
 etiketler.json (isteğe bağlı): {"g01": {"expected": "yesil|kontrol|yok", "strict": true, "why": "..."}}
Dosyayı yeniden üretmek gerekirse: seçimi aynı tutup bu aracı çalıştır; yeni görüntü bugünkü piyasayı yansıtır (etiketleri gözden geçir)."""
import json
import sys
from datetime import datetime, timedelta, timezone

from application.evaluate import assess_listing, load_book
from domain.comparables import find_market, seller_key
from domain.normalize import is_car_brand
from domain.price_book import estimate_from_book
from domain.red_flags import blocking_flags, customs_stated, plate_flags, urgency_signals, warning_flags
from domain.settings import Settings

POOL_FIELDS = ("year", "km", "steering", "transmission", "fuel", "engine_l", "price_gbp", "currency_guess", "is_active")
LISTING_FIELDS = ("brand", "model", "brand_norm", "model_norm", "year", "km", "price_gbp", "currency", "currency_guess", "steering",
                  "transmission", "fuel", "engine_l", "extraction_by")


def _days_ago(ts, now: datetime) -> float | None:
    return round((now - ts).total_seconds() / 86400, 2) if ts else None


def snapshot_pool_row(r: dict, now: datetime, sellers: dict) -> dict:
    """Emsal satırı: kimlik yok, satıcı yeniden adlandırılır, tarih 'kaç gün önce'."""
    seller = seller_key(r)  # telefonsuz ilan kendi başına satıcı (tek tanım: comparables.seller_key)
    sellers.setdefault(seller, f"s{len(sellers) + 1}")
    out = {k: (round(float(r[k]), 2) if k in ("engine_l", "price_gbp") and r.get(k) is not None else r.get(k)) for k in POOL_FIELDS}
    out["ref_days_ago"] = _days_ago(r.get("ref_date") or r.get("first_seen_at"), now)
    out["seller"] = sellers[seller]
    out["sold"] = "satildi" in (r.get("urgency_signals") or [])
    return out


def build_snapshot(repo, selection: list[dict], now: datetime | None = None, labels: dict | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    s = Settings()
    pool_all = [r for r in repo.market_pool(days=s.comparable_window_days + 30) if is_car_brand(r.get("brand_norm"))]
    book = load_book(repo)
    pools: dict[str, list[dict]] = {}
    pool_ids: dict[str, list[str]] = {}  # emsal satırı -> özgün ilan id'si (yalnızca burada; dosyaya yazılmaz)
    sellers: dict[str, str] = {}
    cases = []
    for i, sel in enumerate(selection, 1):
        row = repo.conn.execute(
            """SELECT l.*, l.price_gbp::float8 AS price_gbp, l.engine_l::float8 AS engine_l, s.name AS source_name, s.platform
               FROM listings l JOIN sources s ON s.id = l.source_id WHERE l.id::text LIKE %s""", (sel["id"] + "%",)).fetchall()
        if len(row) != 1:
            raise SystemExit(f"{sel['id']}: {len(row)} ilan eşleşti")
        l = dict(row[0])
        if sel.get("price_override"):
            l["price_gbp"] = float(sel["price_override"])
        key = f"{l['brand_norm']}|{l['model_norm']}"
        if key not in pools:
            rows_k = [r for r in pool_all if (r["brand_norm"], r["model_norm"]) == (l["brand_norm"], l["model_norm"]) and not r.get("duplicate_of")]
            pools[key] = [snapshot_pool_row(r, now, sellers) for r in rows_k]
            pool_ids[key] = [str(r["id"]) for r in rows_k]
        if str(l["id"]) in pool_ids[key]:  # ilan kendi emsali olamaz: test, bu vakada o satırı atlar
            pools[key][pool_ids[key].index(str(l["id"]))].setdefault("cases", []).append(f"g{i:02d}")
        pool_rows = [r for r in pool_all if (r["brand_norm"], r["model_norm"]) == (l["brand_norm"], l["model_norm"])]
        text = (l.get("raw_text") or "") + " " + (l.get("model") or "")
        market = find_market({**l, "id": "target"}, [r for r in pool_rows if str(r["id"]) != str(l["id"])], s, now)
        a = assess_listing({**l}, pool_rows, s, book)
        brow = book.row(l.get("brand_norm"), l.get("model_norm"), l["year"], "") if book is not None and l.get("year") else None
        est = estimate_from_book(l, book, s) if book is not None else None
        cases.append({
            "id": f"g{i:02d}", "origin": sel["origin"], "note": sel.get("note", ""),
            "synthetic": bool(sel.get("price_override")),
            "listing": {k: (round(float(l[k]), 2) if k in ("price_gbp", "engine_l") and l.get(k) is not None else l.get(k)) for k in LISTING_FIELDS}
                       | {"platform": l["platform"], "site": l["source_name"] if l["platform"] == "web" else None,
                          "age_days": _days_ago(l.get("posted_at") or l.get("data_as_of") or l.get("first_seen_at"), now)},
            "text_flags": {"blocking": blocking_flags(text), "warning": warning_flags(text), "plate": plate_flags(text),
                           "customs_stated": customs_stated(text), "urgency": urgency_signals(text)},
            "pool_key": key,
            "book": {"row": ({k: (round(getattr(brow, k), 2) if k.endswith("_gbp") else getattr(brow, k))
                             for k in ("value_gbp", "low_gbp", "high_gbp", "n", "sellers", "method", "status")} if brow else None),
                     "estimate": ({"value_gbp": round(est.value_gbp, 2), "lower_gbp": round(est.lower_gbp, 2), "n": est.n, "sellers": est.sellers,
                                   "sigma": round(est.sigma, 3)} if est else None)},
            "stats": market_stats(market, float(l["price_gbp"])),
            "system_now": {"tier": a.profit.tier.value if a else None, "gaps": (a.gaps if a else []), "method": a.method if a else None},
            **(labels or {}).get(f"g{i:02d}", {}),
        })
    return {"as_of": now.isoformat(timespec="minutes"), "pools": pools, "cases": cases}


def market_stats(market, price: float) -> dict | None:
    if market is None:
        return None
    return {"n": market.n, "median_gbp": round(market.median_gbp, 2), "p25_gbp": round(market.p25_gbp, 2) if market.p25_gbp else None,
            "low_gbp": market.low_gbp, "high_gbp": market.high_gbp, "archived_share": round(market.archived_share, 3),
            "active_n": round(market.n * (1 - market.archived_share)), "year_span": market.year_span, "median_km": market.median_km,
            "price_vs_median_pct": round((price / market.median_gbp - 1) * 100, 1)}


def snapshot_now(snap: dict) -> datetime:
    return datetime.fromisoformat(snap["as_of"])


def case_pool(snap: dict, case: dict, now: datetime | None = None) -> list[dict]:
    """Vakanın emsal havuzu (find_market'in beklediği biçimde). İlanın kendi satırı atlanır.
    now verilirse tarihler ona göre kurulur ("kaç gün önce" korunur): testler zaman geçse de aynı sonucu versin."""
    now, (brand, model) = now or snapshot_now(snap), case["pool_key"].split("|")
    rows = []
    for i, r in enumerate(snap["pools"][case["pool_key"]]):
        if case["id"] in r.get("cases", []):
            continue
        ref = now - timedelta(days=r["ref_days_ago"] or 0)
        rows.append({"id": f"c{i}", "brand_norm": brand, "model_norm": model, "year": r["year"], "km": r["km"], "steering": r["steering"],
                     "transmission": r["transmission"], "fuel": r["fuel"], "engine_l": r["engine_l"], "price_gbp": r["price_gbp"],
                     "currency_guess": r["currency_guess"], "is_active": r["is_active"], "duplicate_of": None, "seller_phone": r["seller"],
                     "urgency_signals": ["satildi"] if r["sold"] else [], "ref_date": ref, "first_seen_at": ref})
    return rows


def case_target(case: dict) -> dict:
    return {**case["listing"], "id": case["id"]}


def main() -> None:
    from infrastructure.config import load_env, require
    from infrastructure.db.repository import Repository
    load_env()
    repo = Repository(require("DATABASE_URL"))
    selection = json.load(open(sys.argv[1]))
    labels = json.load(open(sys.argv[3])) if len(sys.argv) > 3 else None
    snap = build_snapshot(repo, selection, labels=labels)
    json.dump(snap, open(sys.argv[2], "w"), ensure_ascii=False, indent=1, sort_keys=False)
    print(f"{len(snap['cases'])} vaka, {sum(len(p) for p in snap['pools'].values())} emsal satırı yazıldı")


if __name__ == "__main__":
    main()

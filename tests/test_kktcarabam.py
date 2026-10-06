import re
from contextlib import nullcontext
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from application import collect_kktcarabam as kka
from application.notify import is_fresh
from domain.kktc_time import to_kktc
from infrastructure.collectors import kktcarabam as site
from infrastructure.collectors.kktcarabam import card_to_listing, parse_detail, parse_list

FIX = Path(__file__).parent / "fixtures"


def test_list_cards():
    cards = parse_list((FIX / "kktcarabam_list.html").read_text())
    assert len(cards) >= 10
    by_id = {c.item_id: c for c in cards}
    assert "263799" in by_id and by_id["263799"].price_text == "0 TL"
    assert any(c.price_text.endswith("GBP") for c in cards)


def test_detail_swift_without_price():
    cards = {c.item_id: c for c in parse_list((FIX / "kktcarabam_list.html").read_text())}
    d = parse_detail((FIX / "kktcarabam_detail_swift.html").read_text(), cards["263799"])
    assert (d["brand"], d["model"], d["year"], d["km"]) == ("Suzuki", "Swift", 2024, 69)
    assert d["fuel"] == "benzin" and d["transmission"] == "otomatik" and d["steering"] == "RHD"
    assert d["price_amount"] is None and "fiyatsiz" in d["urgency_signals"]
    assert d["posted_at"].month == 10 and d["posted_at"].day == 1
    assert d["seller_handle"] == "GÖKHAN TÜRK MOTORS" and d["seller_type"] == "galeri"
    assert d["location"] == "Lefkoşa / Küçük Kaymaklı"


def test_card_only_listing():
    cards = {c.item_id: c for c in parse_list((FIX / "kktcarabam_list.html").read_text())}
    d = card_to_listing(cards["263800"])
    assert (d["brand"], d["model"], d["year"], d["fuel"], d["location"]) == ("Mercedes-Benz", "E Serisi", 2013, "dizel", "girne")
    assert (d["price_amount"], d["currency"], d["transmission"]) == (14999, "GBP", "otomatik")
    assert "fiyatsiz" in card_to_listing(cards["263799"])["urgency_signals"]


# --- ilan sayfası okuma (yeni kartlar için): km, ilan tarihi, satıcı adı, konum karta eklenir ---
DETAIL = (FIX / "kktcarabam_detail_swift.html").read_text()  # 263799: 2024 Suzuki Swift, Lefkoşa, 69 km, 1 Ekim 2026
SELLER = "GÖKHAN TÜRK MOTORS"  # sayfadaki satıcı adı: kişisel veri sayılır, log'a ASLA girmemeli
SOURCE = {"id": "k1", "name": "KKTCarabam"}


def swift_card(i, price="7.999 GBP"):
    """Fixture sayfasıyla tutarlı sentetik kart (2024 Suzuki Swift, Lefkoşa, benzin, otomatik)."""
    return site.Card(str(i), f"https://www.kktcarabam.com/{i}-suzuki-swift-lefkosa-benzin-otomatik", "2024 Model Otomatik Suzuki Swift", price,
                     "Suzuki Swift")


def page(i, **replace):
    """Kartın kendi ilan sayfası: fixture'da ilan numarası kartınkine çevrilir (başka değişiklikler `replace` ile: eski -> yeni)."""
    html = DETAIL.replace("263799", str(i))
    for old, new in replace.items():
        html = html.replace(old, new)
    return html


class Repo:
    def __init__(self, known=()):
        self.known, self.upserts, self.alive, self.checked = set(known), [], [], 0

    def known_item_ids(self, source_id):
        return set(self.known)

    def mark_alive(self, source_id, item_ids):
        self.alive.append(sorted(item_ids))

    def upsert_listing(self, source_id, item_id, data):
        self.upserts.append((item_id, data))
        return True

    def mark_checked(self, *a, **k):
        self.checked += 1

    def count_recent(self, source_id):
        return 0


class Wire:
    """Sahte site: liste + ilan sayfaları (id -> html | Exception | None). Hangi oturumla kaç sayfa açıldığını ve bekleme sayısını tutar."""

    def __init__(self, monkeypatch, cards, pages):
        self.cards, self.pages, self.opened, self.sessions, self.sleeps = cards, pages, [], [], 0
        self.session = object()
        monkeypatch.setattr(site, "open_session", lambda: nullcontext(self.session))
        monkeypatch.setattr(site, "fetch_html", self.list_page)
        monkeypatch.setattr(site, "parse_list", lambda html: list(self.cards))
        monkeypatch.setattr(site, "fetch_detail_html", self.detail_page)
        monkeypatch.setattr(site, "polite_sleep", self.sleep)

    def list_page(self, session, url):
        self.sessions.append(session)
        return "<html>liste</html>"

    def detail_page(self, session, url, *a, **k):
        self.sessions.append(session)
        item_id = re.search(r"/(\d+)-", url).group(1)
        self.opened.append(item_id)
        result = self.pages.get(item_id)
        if isinstance(result, Exception):
            raise result
        return result

    def sleep(self):
        self.sleeps += 1


def saved(repo):
    return dict(repo.upserts)


def test_fetch_detail_html_bounds_the_wait_per_page_and_returns_none_unless_the_page_is_a_200_with_a_body():
    class Session:
        def __init__(self, status, body):
            self.status, self.body, self.calls = status, body, []

        def fetch(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return type("Page", (), {"status": self.status, "html_content": self.body})()

    ok = Session(200, "<html>ilan</html>")
    assert site.fetch_detail_html(ok, "https://www.kktcarabam.com/1-x") == "<html>ilan</html>"
    assert ok.calls == [("https://www.kktcarabam.com/1-x", {"timeout": site.DETAIL_TIMEOUT_MS})] and 0 < site.DETAIL_TIMEOUT_MS <= 30_000
    assert site.fetch_detail_html(Session(403, "<html>engel</html>"), "u") is None
    assert site.fetch_detail_html(Session(200, ""), "u") is None


def test_new_card_gets_km_posted_at_and_seller_from_its_own_ad_page_in_the_same_session(monkeypatch):
    card = swift_card(270001)
    net, repo = Wire(monkeypatch, [card], {"270001": page(270001)}), Repo()
    stats = kka.collect_kktcarabam(repo, SOURCE)
    d = saved(repo)["270001"]
    assert (d["km"], d["posted_at"], d["seller_handle"]) == (69, datetime(2026, 10, 1, tzinfo=timezone.utc), SELLER)
    assert d["location"] == "lefkosa"  # kartta şehir VAR: sayfadaki "Lefkoşa / Küçük Kaymaklı" kartın yerini almaz
    assert (d["brand"], d["model"], d["year"], d["price_amount"], d["currency"], d["price_gbp"]) == ("Suzuki", "Swift", 2024, 7999, "GBP", 7999.0)
    assert (d["transmission"], d["fuel"], d["extraction_by"], d["url"]) == ("otomatik", "benzin", "parser", card.url)
    assert net.opened == ["270001"] and net.sleeps == 1  # kendi sayfası açıldı, açılmadan önce nazik bekleme
    assert all(s is net.session for s in net.sessions) and len(net.sessions) == 2  # liste + ilan sayfası AYNI oturumda
    assert (stats.new, stats.detail_read, stats.detail_failed, stats.detail_skipped, stats.detail_conflicts) == (1, 1, 0, 0, 0)
    assert repo.checked == 1


def test_card_photo_address_is_stored_in_photo_urls_only_when_its_upload_date_is_readable(monkeypatch):
    """Kapak fotoğrafının adresi (yükleme tarihi içinde) `photo_urls`'e yazılır: `notify.is_fresh` yalnız tazeliği SIKILAŞTIRMAK için okur.
    Tarih okunamıyorsa liste boş kalır (eskisi gibi). Yeni sütun/migration yok: `photo_urls` ilk şemadan beri var."""
    url = "https://www.kktcarabam.com/uploads/images/2026/10/01/13/img-1-6abe346a6b863-270_200.jpg"
    dated = site.Card("270001", swift_card(270001).url, "2024 Model Otomatik Suzuki Swift", "7.999 GBP", "Suzuki Swift", url,
                      datetime(2026, 10, 1, 10, tzinfo=timezone.utc))
    undated = site.Card("270002", swift_card(270002).url, "2024 Model Otomatik Suzuki Swift", "7.999 GBP", "Suzuki Swift", "https://cdn.example.com/a.jpg", None)
    Wire(monkeypatch, [dated, undated, swift_card(270003)], {})
    repo = Repo()
    kka.collect_kktcarabam(repo, SOURCE)
    d = saved(repo)
    assert d["270001"]["photo_urls"] == [url] and d["270002"]["photo_urls"] == [] and d["270003"]["photo_urls"] == []
    assert all("posted_at" not in x for x in d.values())  # sayfa okunamadığı için ilan tarihi hâlâ yok: fotoğraf tarihi posted_at'e yazılmaz


def test_real_list_page_cards_are_saved_with_their_photo_address(monkeypatch):
    cards = parse_list((FIX / "kktcarabam_list.html").read_text())  # Wire parse_list'i değiştirmeden önce gerçek ayrıştırma
    Wire(monkeypatch, cards, {})
    repo = Repo()
    kka.collect_kktcarabam(repo, SOURCE)
    d = saved(repo)
    assert d["263802"]["photo_urls"] == ["https://www.kktcarabam.com/uploads/images/2026/10/01/13/4a8babb0-04f4-4317-8524-ecf662e5d701-6abe346a6b863-270_200.jpg"]
    assert d["259938"]["photo_urls"][0].endswith("/uploads/images/2026/09/09/21/img-8003-6aa1a49ebe8e0-270_200.jpg")
    assert d and all(len(x["photo_urls"]) == 1 for x in d.values())


def test_each_card_is_saved_right_after_its_own_page_so_a_cut_off_run_keeps_the_earlier_cards(monkeypatch):
    cards = [swift_card(270001), swift_card(270002)]
    net, repo = Wire(monkeypatch, cards, {"270001": page(270001), "270002": page(270002)}), Repo()
    events = []
    real_detail, real_upsert = net.detail_page, repo.upsert_listing
    monkeypatch.setattr(site, "fetch_detail_html", lambda s, url, *a, **k: events.append(("sayfa", url)) or real_detail(s, url))
    monkeypatch.setattr(repo, "upsert_listing", lambda sid, item, data: events.append(("kayit", item)) or real_upsert(sid, item, data))
    kka.collect_kktcarabam(repo, SOURCE)
    assert [e[0] for e in events] == ["sayfa", "kayit", "sayfa", "kayit"] and [e[1] for e in events if e[0] == "kayit"] == ["270001", "270002"]


def test_card_without_a_city_gets_the_page_location(monkeypatch):
    card = site.Card("270002", "https://www.kktcarabam.com/270002-suzuki-swift-other-benzin-otomatik", "2024 Model Otomatik Suzuki Swift", "7.999 GBP",
                     "Suzuki Swift")
    assert card_to_listing(card)["location"] == "other"  # sitenin "şehir yok" işareti
    Wire(monkeypatch, [card], {"270002": page(270002)})
    repo = Repo()
    kka.collect_kktcarabam(repo, SOURCE)
    assert saved(repo)["270002"]["location"] == "Lefkoşa / Küçük Kaymaklı"


def card_only_fields(card):
    """Eski davranış: ilan sayfası olmadan kaydedilen ilanın alanları."""
    return card_to_listing(card) | {"price_gbp": 7999.0, "extraction_by": "parser", "photo_urls": []}


@pytest.mark.parametrize("failure", [pytest.param(RuntimeError("engel"), id="hata"), pytest.param(TimeoutError("zaman aşımı"), id="zaman_asimi"),
                                     pytest.param(None, id="403_bos"), pytest.param("<html>Cloudflare engel sayfası</html>", id="sablon_yok"),
                                     pytest.param(page(999999), id="baska_ilanin_sayfasi")])  # ilan no kartınkiyle uyuşmuyor
def test_a_failed_ad_page_saves_the_card_exactly_as_before_and_never_fails_the_run(monkeypatch, capsys, failure):
    card = swift_card(270001)
    Wire(monkeypatch, [card], {"270001": failure})
    repo = Repo()
    stats = kka.collect_kktcarabam(repo, SOURCE)  # fırlatmaz
    assert saved(repo) == {"270001": card_only_fields(card)}  # km/tarih/satıcı YOK; kart kaybolmadı
    assert (stats.new, stats.detail_read, stats.detail_failed) == (1, 0, 1) and repo.checked == 1
    out = capsys.readouterr().out.splitlines()
    assert len([l for l in out if "270001" in l and "okunamadı" in l]) == 1  # ilan başına tek satır
    assert any("hiçbiri okunamadı" in l for l in out)  # hepsi başarısız: tek özet satırı
    assert SELLER not in "\n".join(out)


def test_one_failed_page_does_not_stop_the_other_cards_and_prints_no_summary(monkeypatch, capsys):
    cards = [swift_card(270001), swift_card(270002), swift_card(270003)]
    Wire(monkeypatch, cards, {"270001": page(270001), "270002": RuntimeError("zaman aşımı"), "270003": page(270003)})
    repo = Repo()
    stats = kka.collect_kktcarabam(repo, SOURCE)
    d = saved(repo)
    assert d["270001"]["km"] == 69 and d["270003"]["km"] == 69 and "km" not in d["270002"] and len(d) == 3
    assert (stats.new, stats.detail_read, stats.detail_failed) == (3, 2, 1)
    out = capsys.readouterr().out
    assert out.count("ilan sayfası okunamadı") == 1 and "hiçbiri" not in out


def test_known_card_does_not_fetch_its_page_and_is_only_marked_alive(monkeypatch):
    known, new = swift_card(270001), swift_card(270002)
    net, repo = Wire(monkeypatch, [known, new], {"270001": page(270001), "270002": page(270002)}), Repo(known={"270001"})
    kka.collect_kktcarabam(repo, SOURCE)
    assert net.opened == ["270002"] and set(saved(repo)) == {"270002"} and repo.alive == [["270001"]]


def test_a_card_that_cannot_become_a_listing_opens_no_page(monkeypatch):
    bad = site.Card("270009", "https://www.kktcarabam.com/270009-x", "başlık tanınmıyor", "5.000 GBP", "")
    net, repo = Wire(monkeypatch, [bad], {}), Repo()
    kka.collect_kktcarabam(repo, SOURCE)
    assert net.opened == [] and repo.upserts == []


def test_per_run_cap_of_18_ad_pages_still_saves_every_card(monkeypatch, capsys):
    cards = [swift_card(270000 + i) for i in range(20)]
    net, repo = Wire(monkeypatch, cards, {c.item_id: page(c.item_id) for c in cards}), Repo()
    stats = kka.collect_kktcarabam(repo, SOURCE)
    assert kka.MAX_DETAIL_PAGES == 18 and len(net.opened) == 18 and net.opened == [c.item_id for c in cards[:18]]
    assert len(repo.upserts) == 20 and stats.new == 20 and (stats.detail_read, stats.detail_skipped) == (18, 2)
    assert "km" in saved(repo)["270017"] and "km" not in saved(repo)["270018"]  # sınırdan sonrakiler eskisi gibi
    assert net.sleeps == 18 and capsys.readouterr().out.count("2 yeni ilanın sayfası bu tur açılmadı") == 1


def test_blocked_site_stops_after_three_failures_in_a_row_without_hammering_it(monkeypatch, capsys):
    cards = [swift_card(270000 + i) for i in range(6)]
    net, repo = Wire(monkeypatch, cards, {c.item_id: None for c in cards}), Repo()  # her sayfa 403
    stats = kka.collect_kktcarabam(repo, SOURCE)  # fırlatmaz
    assert len(net.opened) == kka.MAX_DETAIL_FAILS_IN_A_ROW == 3  # site zorlanmadı
    assert len(repo.upserts) == 6 and all("km" not in d for _, d in repo.upserts)  # 6 kartın hepsi eskisi gibi kaydedildi
    assert (stats.detail_failed, stats.detail_skipped, stats.new) == (3, 3, 6)
    assert capsys.readouterr().out.count("hiçbiri okunamadı") == 1  # tek özet satırı


def test_a_success_resets_the_failure_streak(monkeypatch):
    cards = [swift_card(270000 + i) for i in range(6)]
    pages = {"270000": None, "270001": None, "270002": page(270002), "270003": None, "270004": None, "270005": page(270005)}
    net, repo = Wire(monkeypatch, cards, pages), Repo()
    kka.collect_kktcarabam(repo, SOURCE)
    assert len(net.opened) == 6  # hiçbir zaman üst üste 3 hata olmadı


def test_ad_page_time_budget_stops_further_pages(monkeypatch):
    cards = [swift_card(270000 + i) for i in range(5)]
    net, repo = Wire(monkeypatch, cards, {c.item_id: page(c.item_id) for c in cards}), Repo()
    ticks = iter([0, 10, 20, 30, kka.DETAIL_BUDGET_SECONDS + 1, 9999])  # ilki bütçe başlangıcı; sonra 3 sayfa süre içinde, 4. sayfadan önce bütçe doldu
    stats = kka.collect_kktcarabam(repo, SOURCE, clock=lambda: next(ticks))
    assert len(net.opened) == 3 and len(repo.upserts) == 5 and (stats.detail_read, stats.detail_skipped) == (3, 2)


def test_page_that_conflicts_with_the_card_keeps_the_card_value_and_logs_one_line(monkeypatch, capsys):
    card = swift_card(270001)
    Wire(monkeypatch, [card], {"270001": page(270001, **{"<td>2024</td>": "<td>2019</td>", "Lefkoşa": "Girne"})})
    repo = Repo()
    stats = kka.collect_kktcarabam(repo, SOURCE)
    d = saved(repo)["270001"]
    assert (d["year"], d["location"]) == (2024, "lefkosa")  # kart değeri kaldı
    assert (d["km"], d["seller_handle"], d["posted_at"].day) == (69, SELLER, 1)  # çelişmeyen alanlar yine sayfadan geldi
    assert (d["price_amount"], d["brand"], d["model"]) == (7999, "Suzuki", "Swift")
    out = capsys.readouterr().out.splitlines()
    assert out == ["KKTCarabam ilan 270001: ilan sayfası kartla çelişiyor (year, location); kart değeri kaldı"]
    assert stats.detail_conflicts == 1 and stats.detail_read == 1


def test_log_lines_never_carry_seller_names_phones_or_exception_text(monkeypatch, capsys):
    cards = [swift_card(270001), swift_card(270002)]
    Wire(monkeypatch, cards, {"270001": page(270001, **{"<td>2024</td>": "<td>2019</td>"}), "270002": RuntimeError(f"{SELLER} 0533 123 45 67")})
    kka.collect_kktcarabam(Repo(), SOURCE)
    out = capsys.readouterr().out
    assert out and SELLER not in out and "0533" not in out and "GÖKHAN" not in out and "RuntimeError" in out


def test_merge_detail_fills_only_what_the_card_lacks():
    card = swift_card(270001)
    data = card_to_listing(card)
    detail = parse_detail(page(270001), card)
    merged, conflicts = site.merge_detail(data, detail)
    assert conflicts == [] and merged != data
    assert {k: v for k, v in merged.items() if k not in ("km", "posted_at", "seller_handle", "steering")} == data  # kartın hiçbir alanı değişmedi
    assert merged["steering"] == "RHD"  # kartta direksiyon yok: sayfadan gelir
    again, _ = site.merge_detail(merged, detail)
    assert again == merged  # tekrar birleştirmek bir şey değiştirmez
    assert site.merge_detail(data | {"km": 120_000}, detail)[0]["km"] == 120_000  # kartta olan değerin üstüne yazılmaz
    assert site.merge_detail(data, detail | {"steering": "LHD"})[0]["steering"] == "LHD"  # soldan direksiyon: sağ direksiyonlularla kıyaslanmaz


def test_merge_detail_conflicts_are_normalized_so_spelling_differences_are_not_conflicts():
    card = swift_card(270001)
    detail = parse_detail(page(270001), card)
    data = card_to_listing(card)
    mercedes = data | {"brand": "Mercedes", "model": "- Benz GLE"}  # etiket ilk boşluktan bölünmüş (bilinen KKTCarabam kusuru)
    assert site.merge_detail(mercedes, detail | {"brand": "Mercedes - Benz", "model": "GLE"})[1] == []
    assert site.merge_detail(data, detail | {"brand": "Toyota"})[1] == ["brand"]
    assert site.merge_detail(data, detail | {"model": "Yaris"})[1] == ["model"]
    assert site.merge_detail(data, detail | {"fuel": "dizel"})[1] == ["fuel"]
    assert site.merge_detail(data | {"transmission": "manuel"}, detail)[1] == ["transmission"]
    assert site.merge_detail(data | {"fuel": None, "location": None}, detail)[1] == []  # kartta olmayan değer çelişki değil
    assert site.merge_detail(data, detail | {"year": None, "km": None, "location": None})[1] == []  # sayfada olmayan da değil


def test_detail_date_has_no_time_and_is_stored_as_utc_midnight_on_the_same_kktc_calendar_day():
    card = swift_card(270001)
    for text, day in (("1 Ekim 2026", date(2026, 10, 1)), ("15 Aralık 2026", date(2026, 12, 15))):  # yaz saati (+3) ve kış saati (+2)
        posted = parse_detail(page(270001, **{"1 Ekim 2026": text}), card)["posted_at"]
        assert posted == datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        assert to_kktc(posted).date() == day and to_kktc(posted).hour in (2, 3)  # KKTC'de AYNI takvim günü: gösterilen gün ilandaki gün


def test_detail_for_another_ads_number_is_not_this_cards_page():
    card = swift_card(270001)
    assert parse_detail(page(270001), card) is not None
    assert parse_detail(page(270999), card) is None  # sayfa başka ilanın numarasını taşıyor: yönlendirme/yanlış sayfa
    assert parse_detail(page(270001, **{"<th>İlan No:</th>": "<th>Ilan:</th>"}), card) is not None  # numara okunamıyorsa engel olmaz


def test_seller_is_left_empty_rather_than_misread_when_equipment_follows_the_security_heading():
    card = swift_card(270001)
    with_features = re.sub(r'(<div class="baslik">Güvenlik</div>\s*<div class="row row-cols-lg-5">)',
                           r'\1<div class="col">ABS</div><div class="col">ESP</div>', page(270001))
    assert with_features != page(270001)
    d = parse_detail(with_features, card)
    assert d["seller_handle"] is None and d["seller_type"] == "bilinmiyor"  # "ABS" satıcı adı olarak yazılmaz
    assert d["km"] == 69  # diğer alanlar etkilenmez


def test_old_ad_resurfaced_on_the_latest_list_is_not_fresh_once_its_posting_date_is_known(monkeypatch):
    """Sitenin "en yeni" listesine geri itilmiş eski ilan: ilan sayfasındaki tarih eskiyse `is_fresh` (4 gün) anlık bildirimi keser."""
    Wire(monkeypatch, [swift_card(270001)], {"270001": page(270001)})  # ilan tarihi 1 Ekim 2026
    repo = Repo()
    kka.collect_kktcarabam(repo, SOURCE)
    posted = saved(repo)["270001"]["posted_at"]
    first_seen = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)  # bizim için "yeni": ilk kez şimdi görüldü
    assert not is_fresh(first_seen, posted, now=first_seen + timedelta(minutes=5))  # 19 günlük ilan: taze değil
    assert is_fresh(datetime(2026, 10, 3, 9, 0, tzinfo=timezone.utc), posted, now=datetime(2026, 10, 3, 9, 5, tzinfo=timezone.utc))  # 2 günlük: taze
    assert is_fresh(first_seen, None, now=first_seen + timedelta(minutes=5))  # tarih okunamadıysa kural bugünkü gibi (commit 2 bunu kapatır)

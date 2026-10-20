from datetime import date, datetime, timedelta, timezone

from application.dedupe import mark_duplicates
from domain.duplicates import (
    POST_TEXT_MIN_CHARS,
    cross_source_twin,
    district,
    post_text,
    post_text_fp,
    same_car,
    same_post_text,
)

NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def car(i, hours_ago=0, **kw):
    base = dict(id=i, brand_norm="Toyota", model_norm="vitz", year=2015, km=80_000, seller_phone="905330000009",
                price_gbp=6000.0, is_active=True, first_seen_at=NOW - timedelta(hours=hours_ago), duplicate_of=None)
    return base | kw


def test_repost_same_phone_km_price_is_duplicate():
    assert same_car(car(1), car(2, price_gbp=5800.0))


def test_same_dealer_two_different_cars_not_merged():
    assert not same_car(car(1), car(2, km=120_000))  # km çok farklı
    assert not same_car(car(1, km=None), car(2, km=None, price_gbp=9000.0))  # km yok, fiyat uzak


def test_cross_source_same_km_close_price_without_phone():
    assert same_car(car(1, km=80_250, seller_phone=None), car(2, km=80_250, seller_phone=None, price_gbp=6100.0))


def test_round_km_without_phone_needs_nearly_same_price():
    # İki farklı araç da "150.000 km" yazmış olabilir
    assert not same_car(car(1, km=150_000, seller_phone=None), car(2, km=150_000, seller_phone=None, price_gbp=6500.0))
    assert same_car(car(1, km=150_000, seller_phone=None), car(2, km=150_000, seller_phone=None, price_gbp=5950.0))


def test_same_km_but_far_price_and_no_phone_not_merged():
    assert not same_car(car(1, seller_phone=None), car(2, seller_phone=None, price_gbp=9000.0))


def test_no_km_requires_phone_and_price():
    assert same_car(car(1, km=None), car(2, km=None, price_gbp=6100.0))
    assert not same_car(car(1, km=None, seller_phone=None), car(2, km=None, seller_phone=None))


# --- 2.7: km, değerlendirmedeki gibi effective_km ile okunur (şüpheli km = bilinmiyor) ---
TODAY = date(2026, 10, 5)


def test_trusted_close_km_still_merges():
    assert same_car(car(1, km=80_000), car(2, km=80_300, price_gbp=5800.0), TODAY)  # aynı telefon + yakın km
    assert same_car(car(1, km=80_000, seller_phone=None), car(2, km=80_400, seller_phone=None, price_gbp=6500.0), TODAY)  # km + ±%15 fiyat
    assert not same_car(car(1, km=80_000), car(2, km=95_000), TODAY)  # güvenilir km uzak: farklı araç (telefon aynı olsa da)


def test_suspicious_km_on_both_is_not_evidence_of_same_car():
    # Eski kural: "1 km" ile "1 km" (ya da 107 ile 220 = 107.000 ile 220.000) yakın sayılıp telefonsuz iki farklı araç birleşiyordu
    assert not same_car(car(1, year=2010, km=1, seller_phone=None), car(2, year=2010, km=1, seller_phone=None), TODAY)
    assert not same_car(car(1, year=2013, km=107, seller_phone=None, price_gbp=6400.0),
                        car(2, year=2013, km=220, seller_phone=None, price_gbp=5790.0), TODAY)


def test_suspicious_km_falls_back_to_phone_and_price():
    # Canlıdaki örnek: aynı satıcı aynı Swift'i önce "1 km", sonra "218.000 km" yazarak yeniden ilana koydu
    first, fixed = car(1, year=2008, km=1, price_gbp=4100.0), car(2, year=2008, km=218_000, price_gbp=4100.0)
    assert same_car(first, fixed, TODAY)
    assert not same_car(first, {**fixed, "price_gbp": 5200.0}, TODAY)  # fiyat ±%15 dışında: birleşmez
    assert not same_car(first, {**fixed, "seller_phone": "905330000001"}, TODAY)  # farklı telefon: km kanıtı yok, birleşmez
    assert not same_car({**first, "seller_phone": None}, {**fixed, "seller_phone": None}, TODAY)  # telefonsuz: birleşmez


def test_one_suspicious_one_missing_km_needs_phone():
    a = car(1, year=2013, km=11_500, seller_phone=None)  # 13 yaşında 11.500 km: şüpheli (115.000 olabilir)
    assert not same_car(a, car(2, year=2013, km=115_000, seller_phone=None), TODAY)
    assert not same_car(a, car(2, year=2013, km=None, seller_phone=None), TODAY)
    assert same_car(car(1, year=2013, km=11_500), car(2, year=2013, km=None, price_gbp=6200.0), TODAY)  # aynı telefon + yakın fiyat


def test_equal_km_different_phone_depends_on_price():
    # Telefon farkı tek başına engel değil (aynı araç farklı sitede farklı numarayla olabilir); km + fiyat kuralı aynen geçerli
    a = car(1, km=80_250, seller_phone="905330000001")
    assert same_car(a, car(2, km=80_250, seller_phone="905330000002", price_gbp=6400.0), TODAY)
    assert not same_car(a, car(2, km=80_250, seller_phone="905330000002", price_gbp=7500.0), TODAY)
    r = car(1, km=150_000, seller_phone="905330000001")  # yuvarlak km: fiyat ±%3
    assert not same_car(r, car(2, km=150_000, seller_phone="905330000002", price_gbp=6300.0), TODAY)


def test_same_car_judges_km_on_the_given_day():
    a = car(1, year=2026, km=500, seller_phone=None, price_gbp=20_000.0)
    b = car(2, year=2026, km=500, seller_phone=None, price_gbp=20_500.0)
    assert same_car(a, b, date(2026, 10, 5))  # yeni araçta 500 km makul
    assert not same_car(a, b, date(2028, 6, 1))  # 2 yaşında 500 km: şüpheli → km kanıtı yok


def test_mark_duplicates_uses_the_round_time():
    def rows():
        return [car("a", 5, year=2026, km=500, seller_phone=None, price_gbp=20_000.0),
                car("b", 1, year=2026, km=500, seller_phone=None, price_gbp=20_500.0)]

    assert mark_duplicates(FakeRepo(rows()), now=datetime(2026, 10, 5, tzinfo=timezone.utc)) == 1
    assert mark_duplicates(FakeRepo(rows()), now=datetime(2028, 6, 1, tzinfo=timezone.utc)) == 0


class FakeRepo:
    def __init__(self, rows, twins=()):
        self.rows, self.twins, self.dups, self.twin_calls = rows, list(twins), {}, []

    def dedupe_candidates(self, days=120, new_hours=None):
        return self.rows

    def twin_candidates(self, window_hours, days=120, new_hours=None):
        self.twin_calls.append((window_hours, new_hours))
        return self.twins

    def set_duplicate(self, i, canon):
        self.dups[i] = canon


def test_oldest_is_canonical_and_chain_points_to_it():
    repo = FakeRepo([car("new", 1), car("old", 100), car("mid", 50, price_gbp=5900.0), car("other", 10, km=200_000)])
    assert mark_duplicates(repo) == 2
    assert repo.dups == {"mid": "old", "new": "old"}


def test_shadow_listing_seen_first_never_becomes_canonical_of_a_live_listing():
    """Facebook gölge haftası: araç önce gölge grupta, sonra sitede görüldü. Site ilanı kanonik kalır (bildirim ve emsal için görünür),
    gölge ilan onun kopyası olur; gölge ilanlar kendi aralarında ilk görülen kuralıyla bağlanır."""
    repo = FakeRepo([car("fb", 20, shadow=True), car("site", 1, shadow=False), car("fb2", 10, shadow=True, price_gbp=5900.0)])
    assert mark_duplicates(repo) == 2
    assert repo.dups == {"fb": "site", "fb2": "site"}
    alone = FakeRepo([car("fb", 20, shadow=True), car("fb2", 10, shadow=True, price_gbp=5900.0)])
    assert mark_duplicates(alone) == 1 and alone.dups == {"fb2": "fb"}


def test_relisted_car_is_not_duplicate_of_old_inactive_listing():
    repo = FakeRepo([car("old", 100, is_active=False), car("relisted", 1, price_gbp=5000.0)])
    repo.rows[1]["km"] = 80_000
    assert mark_duplicates(repo) == 0  # eski ilan satılmış/arşivde; yeni ilan yeni fırsat olabilir


def test_inactive_old_duplicates_still_collapse():
    repo = FakeRepo([car("a", 100, is_active=False), car("b", 50, is_active=False)])
    assert mark_duplicates(repo) == 1 and repo.dups == {"b": "a"}


def test_quick_round_asks_only_for_groups_with_new_listings_and_marks_the_same():
    class QuickRepo:
        def __init__(self, rows):
            self.rows, self.dups, self.new_hours = rows, {}, []

        def dedupe_candidates(self, days=120, new_hours=None):
            self.new_hours.append(new_hours)
            return list(self.rows)

        def twin_candidates(self, window_hours, days=120, new_hours=None):
            self.new_hours.append(("ikiz", new_hours))
            return []

        def set_duplicate(self, listing_id, canonical_id):
            self.dups[listing_id] = canonical_id

    repo = QuickRepo([car("a", 5), car("b", 1, price_gbp=5900.0)])
    assert mark_duplicates(repo, quick=True) == 1 and repo.dups == {"b": "a"}  # sonuç tam tarama ile aynı
    assert repo.new_hours == [3, ("ikiz", 3)]  # kaynaklar arası ikiz geçişi de aynı daraltmayla
    mark_duplicates(repo)  # varsayılan: tam tarama
    assert repo.new_hours == [3, ("ikiz", 3), None, ("ikiz", None)]  # tam taramada daraltma yok


def test_fast_lane_dedupe_reads_groups_new_since_its_import_and_skips_site_twins():
    """Facebook hızlı şeridi (entrypoints/fb_fast): aynı aracın başka gruptaki paylaşımı hemen kopya olur; KKTCarabam ↔ KibrisArabaAl ikiz
    geçişi turda kalır."""
    since = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)

    class SinceRepo(FakeRepo):
        def dedupe_candidates(self, days=120, new_hours=None, new_since=None):
            self.asked = (new_hours, new_since)
            return self.rows

    repo = SinceRepo([car("grup1", 5, shadow=False), car("grup2", 1, price_gbp=5900.0)])
    assert mark_duplicates(repo, since=since) == 1 and repo.dups == {"grup2": "grup1"}
    assert repo.asked == (None, since) and repo.twin_calls == []


# --- kaynaklar arası ikiz: KKTCarabam (km/telefon yok) ↔ KibrisArabaAl ---
def ad(i, site, minutes_ago=0, **kw):
    """twin_candidates satırı. Varsayılan: canlıdaki BMW vakası (KKTCarabam BMW 3 2007 £5.400 Girne ↔ KAA 3 Serisi 320i, konum boş, 16 dk önce)."""
    base = dict(id=i, site=site, brand_norm="BMW", model_norm="3", year=2007, price_amount=5400.0, currency="GBP",
                location="girne" if site == "kktcarabam" else None, transmission="otomatik", fuel="benzin",
                first_seen_at=NOW - timedelta(minutes=minutes_ago), duplicate_of=None, is_active=True)
    return base | kw


def test_cross_source_twin_needs_same_key_exact_price_and_close_time():
    a, b = ad("a", "kktcarabam"), ad("b", "kibrisarabaal", 16)
    assert cross_source_twin(a, b)
    assert cross_source_twin(a, {**b, "first_seen_at": NOW - timedelta(hours=3, minutes=1)})  # eski 3 saat sınırının ötesi: artık bağlanır
    assert cross_source_twin(a, {**b, "first_seen_at": NOW - timedelta(hours=23)})  # toplayıcı boşluğu / toplu yükleme: saatler sonra gelen ikiz
    assert cross_source_twin(a, {**b, "first_seen_at": NOW - timedelta(hours=24)})  # sınır dahil
    assert not cross_source_twin(a, {**b, "first_seen_at": NOW - timedelta(hours=24, minutes=1)})
    assert not cross_source_twin(a, {**b, "first_seen_at": NOW - timedelta(hours=25)})
    assert not cross_source_twin(a, {**b, "first_seen_at": NOW - timedelta(days=3)})  # 1-7 gün ölçümde güvenilmez çıktı: pencere 24 saati aşmaz
    assert cross_source_twin(a, {**b, "first_seen_at": NOW + timedelta(hours=23)})  # KAA sonra görülmüş: pencere iki yönde aynı
    assert not cross_source_twin(a, {**b, "first_seen_at": NOW + timedelta(hours=25)})
    assert cross_source_twin(a, {**b, "first_seen_at": NOW + timedelta(hours=2)})  # KAA sonra görülmüş: yön aynı
    assert not cross_source_twin(a, {**b, "price_amount": 5450.0})  # yakın fiyat yetmez: BİREBİR aynı tutar
    assert not cross_source_twin(a, {**b, "currency": "TRY"})
    assert not cross_source_twin(a, {**b, "price_amount": None})
    assert not cross_source_twin({**a, "price_amount": None}, b)
    assert not cross_source_twin(a, {**b, "year": 2008})
    assert not cross_source_twin(a, {**b, "model_norm": "5"})
    assert not cross_source_twin({**a, "model_norm": None}, {**b, "model_norm": None})  # "ikisi de bilinmiyor" aynı model değildir


def test_cross_source_twin_city_transmission_fuel_must_not_conflict():
    a, b = ad("a", "kktcarabam"), ad("b", "kibrisarabaal", 16)
    assert cross_source_twin(a, {**b, "location": "Girne"})
    assert cross_source_twin({**a, "location": "lefkosa"}, {**b, "location": "Lefkoşa"})
    assert cross_source_twin({**a, "location": "magusa"}, {**b, "location": "Gazimağusa"})
    assert not cross_source_twin(a, {**b, "location": "Lefkoşa"})  # şehir çelişiyor
    assert cross_source_twin(a, {**b, "location": "Benz C Serisi"})  # KAA konum alanında model parçası: bilinmiyor, çelişki değil
    assert cross_source_twin({**a, "location": None}, {**b, "location": "Lefke"})
    assert cross_source_twin({**a, "transmission": "düz"}, {**b, "transmission": "manuel"})  # yazım farkı çelişki değil
    assert not cross_source_twin(a, {**b, "transmission": "manuel"})
    assert cross_source_twin({**a, "fuel": "elektrik"}, {**b, "fuel": "elektrikli"})
    assert not cross_source_twin(a, {**b, "fuel": "dizel"})
    assert cross_source_twin(a, {**b, "fuel": None, "transmission": None})


def test_district_reads_the_six_districts_and_nothing_else():
    assert [district(v) for v in ("Lefkoşa", "lefkosa", "Gazimağusa", "magusa", "İskele", "Güzelyurt", "Lefke", "Girne", "lapta")] == \
        ["lefkosa", "lefkosa", "magusa", "magusa", "iskele", "guzelyurt", "lefke", "girne", "girne"]
    assert [district(v) for v in (None, "", "other", "Benz C Serisi", "5", "V Hybrid")] == [None] * 6


def test_twin_marks_kktcarabam_as_copy_of_kaa_even_when_kktcarabam_was_seen_first():
    # Canlıdaki BMW vakası: KKTCarabam 🟢 (km yok), KAA ikizi 145.000 km ile 'yok'. Bugünkü kural ilk görüleni kanonik yapardı.
    repo = FakeRepo([], twins=[ad("arabam", "kktcarabam", 0), ad("kaa", "kibrisarabaal", 16)])
    assert mark_duplicates(repo) == 1 and repo.dups == {"arabam": "kaa"}
    repo = FakeRepo([], twins=[ad("arabam", "kktcarabam", 120), ad("kaa", "kibrisarabaal", 0)])  # KKTCarabam ÖNCE görülmüş
    assert mark_duplicates(repo) == 1 and repo.dups == {"arabam": "kaa"}
    assert repo.twin_calls == [(24, None)]  # SQL'e giden pencere de 24 saat (python tarafındaki cross_source_twin ile aynı sabit)


def test_twin_requires_a_unique_match_in_both_directions():
    two_kaa = [ad("arabam", "kktcarabam"), ad("kaa1", "kibrisarabaal", 10), ad("kaa2", "kibrisarabaal", 20)]
    assert mark_duplicates(FakeRepo([], twins=two_kaa)) == 0  # hangi KAA ilanı? bilinmez
    two_arabam = [ad("arabam1", "kktcarabam"), ad("arabam2", "kktcarabam", 30), ad("kaa", "kibrisarabaal", 10)]
    assert mark_duplicates(FakeRepo([], twins=two_arabam)) == 0
    # zaten bağlı KKTCarabam ilanı da aday sayılır: sonradan gelen ikinci KKTCarabam ilanı aynı KAA ilanına bağlanmaz
    linked = [ad("arabam1", "kktcarabam", 60, duplicate_of="kaa"), ad("arabam2", "kktcarabam"), ad("kaa", "kibrisarabaal", 50)]
    repo = FakeRepo([], twins=linked)
    assert mark_duplicates(repo) == 0 and repo.dups == {}
    # rakip başka şehirde / farklı tutarda ise teklik bozulmaz
    other = [ad("arabam", "kktcarabam"), ad("kaa1", "kibrisarabaal", 10), ad("kaa2", "kibrisarabaal", 20, location="Lefkoşa"),
             ad("kaa3", "kibrisarabaal", 20, price_amount=5500.0)]
    repo = FakeRepo([], twins=other)
    assert mark_duplicates(repo) == 1 and repo.dups == {"arabam": "kaa1"}


def test_twin_window_links_a_late_twin_at_23_hours_but_not_at_25_hours():
    # Toplayıcı boşluğu/toplu yükleme: KKTCarabam ilanı KAA ikizinden saatler sonra (ya da önce) ilk kez görülebilir
    for hours, linked in ((5, 1), (23, 1), (25, 0)):
        repo = FakeRepo([], twins=[ad("arabam", "kktcarabam"), ad("kaa", "kibrisarabaal", hours * 60)])  # KAA saatler ÖNCE görülmüş
        assert mark_duplicates(repo) == linked and repo.dups == ({"arabam": "kaa"} if linked else {}), hours
        repo = FakeRepo([], twins=[ad("arabam", "kktcarabam", hours * 60), ad("kaa", "kibrisarabaal")])  # KKTCarabam saatler ÖNCE görülmüş
        assert mark_duplicates(repo) == linked and repo.dups == ({"arabam": "kaa"} if linked else {}), hours


def test_twin_ambiguity_still_blocks_inside_the_wider_window_and_not_outside_it():
    # geniş pencerede de iki yönde TEK eşleşme şart: 22 saat önceki ikinci KAA adayı da belirsizlik yaratır
    repo = FakeRepo([], twins=[ad("arabam", "kktcarabam"), ad("kaa1", "kibrisarabaal", 10), ad("kaa2", "kibrisarabaal", 22 * 60)])
    assert mark_duplicates(repo) == 0 and repo.dups == {}
    two_arabam = [ad("arabam1", "kktcarabam"), ad("arabam2", "kktcarabam", 22 * 60), ad("kaa", "kibrisarabaal", 10)]
    assert mark_duplicates(FakeRepo([], twins=two_arabam)) == 0
    # 25 saat önceki aynı tutarlı ilan pencerenin dışında: rakip sayılmaz, bağ kurulur
    repo = FakeRepo([], twins=[ad("arabam", "kktcarabam"), ad("kaa1", "kibrisarabaal", 10), ad("kaa2", "kibrisarabaal", 25 * 60)])
    assert mark_duplicates(repo) == 1 and repo.dups == {"arabam": "kaa1"}
    # geç gelen ikiz tek başına bağlanır; yanında şehir çelişkili bir rakip teklik bozmaz
    repo = FakeRepo([], twins=[ad("arabam", "kktcarabam"), ad("kaa1", "kibrisarabaal", 20 * 60),
                               ad("kaa2", "kibrisarabaal", 21 * 60, location="Lefkoşa")])
    assert mark_duplicates(repo) == 1 and repo.dups == {"arabam": "kaa1"}


def test_twin_respects_active_rule_and_never_chains_onto_a_kaa_copy():
    # aktif KKTCarabam ilanı pasif (satılmış/kalkmış) KAA ilanının kopyası olmaz (release_orphan_duplicates ile tutarlı)
    repo = FakeRepo([], twins=[ad("arabam", "kktcarabam"), ad("kaa", "kibrisarabaal", 10, is_active=False)])
    assert mark_duplicates(repo) == 0
    # ikisi de pasif: bağ kurulur (arşivde de tek araç sayılsın); pasif KKTCarabam aktif KAA'nın kopyası olabilir
    repo = FakeRepo([], twins=[ad("arabam", "kktcarabam", is_active=False), ad("kaa", "kibrisarabaal", 10, is_active=False)])
    assert mark_duplicates(repo) == 1
    # zaten bağlı KKTCarabam ilanı yeniden yazılmaz
    repo = FakeRepo([], twins=[ad("arabam", "kktcarabam", duplicate_of="kaa"), ad("kaa", "kibrisarabaal", 10)])
    assert mark_duplicates(repo) == 0 and repo.dups == {}


def test_twin_of_a_kaa_copy_links_to_its_canonical_without_a_chain():
    # Canlıdaki örnek: KAA'da aynı araç (aynı telefon/km/fiyat) iki kez ilanda; yeni KAA ilanı eskisinin kopyası, KKTCarabam ikizi yeni ilanla aynı saatte
    repo = FakeRepo([], twins=[ad("arabam", "kktcarabam"), ad("kaa_new", "kibrisarabaal", 16, duplicate_of="kaa_old")])
    assert mark_duplicates(repo) == 1 and repo.dups == {"arabam": "kaa_old"}
    # aynı aracın iki KAA ilanı (kanonik + kopyası) ikisi de eşleşirse "iki aday" sayılmaz
    repo = FakeRepo([], twins=[ad("arabam", "kktcarabam"), ad("kaa_new", "kibrisarabaal", 16, duplicate_of="kaa_old"),
                               ad("kaa_old", "kibrisarabaal", 100)])
    assert mark_duplicates(repo) == 1 and repo.dups == {"arabam": "kaa_old"}
    # ama FARKLI iki KAA aracı (biri kopya olsa da) hâlâ belirsizdir
    repo = FakeRepo([], twins=[ad("arabam", "kktcarabam"), ad("kaa_new", "kibrisarabaal", 16, duplicate_of="kaa_old"),
                               ad("kaa_other", "kibrisarabaal", 20)])
    assert mark_duplicates(repo) == 0
    # pasif KAA kopyası üzerinden aktif KKTCarabam ilanı bağlanmaz (kanoniğin aktifliği bilinmez)
    repo = FakeRepo([], twins=[ad("arabam", "kktcarabam"), ad("kaa_new", "kibrisarabaal", 16, duplicate_of="kaa_old", is_active=False)])
    assert mark_duplicates(repo) == 0


def test_twin_pass_never_links_a_listing_to_itself_when_kaa_copy_already_points_at_the_kktcarabam_listing():
    # KKTCarabam artık ilan sayfasından km alıyor: `same_car` (km + fiyat) onu önce görüldüğü için KAA ikizinin KANONİĞİ yapabilir (KAA kopya).
    # İkiz geçişi bu çifti yine bulur: kanonik = KKTCarabam ilanının kendisi. Eskiden set_duplicate(kendi, kendi) yazılır, ikisi de kaybolurdu.
    repo = FakeRepo([], twins=[ad("arabam", "kktcarabam", 120), ad("kaa", "kibrisarabaal", 0, duplicate_of="arabam")])
    assert mark_duplicates(repo) == 0 and repo.dups == {}


def test_twin_pass_never_pairs_two_listings_of_the_same_site():
    repo = FakeRepo([], twins=[ad("a1", "kktcarabam"), ad("a2", "kktcarabam", 10)])
    assert mark_duplicates(repo) == 0
    repo = FakeRepo([], twins=[ad("k1", "kibrisarabaal"), ad("k2", "kibrisarabaal", 10)])
    assert mark_duplicates(repo) == 0


# --- aynı gönderi metni (20.10.2026): sosyal medyada aynı metin + aynı fiyat = aynı araç; km ve telefon gerekmez ---
VITZ_TEXT = ("SATILIK 2015 model Toyota Vitz 1.3 otomatik 🚗 98.000 km, muayeneli, bakımları yeni yapıldı. Takas olmaz!! "
             "Fiyat: 5.500 STG ☎️ mesaj atın")


def post(i, hours_ago=0, text=VITZ_TEXT, platform="facebook", **kw):
    """dedupe_candidates satırı (Facebook gönderisi): km ve telefon YOK (canlıdaki kopyaların çoğu böyle), parmak izi metinden."""
    base = dict(id=i, brand_norm="Toyota", model_norm="vitz", year=2015, km=None, seller_phone=None, price_gbp=5500.0,
                price_amount=5500.0, currency="GBP", is_active=True, first_seen_at=NOW - timedelta(hours=hours_ago), duplicate_of=None)
    row = base | kw
    return row | {"text_fp": post_text_fp(text, row["year"], platform)}


def test_post_text_normalizes_case_turkish_letters_fancy_letters_punctuation_and_emoji_the_same_way():
    assert post_text("SATILIK İÇİN: Şık, ÇOK güzel ığdır ÖĞÜ âîû") == "satilik icin sik cok guzel igdir ogu aiu"
    assert post_text("𝐓𝐎𝐘𝐎𝐓𝐀 ＶＩＴＺ") == "toyota vitz"  # Facebook'un süslü kalın / tam genişlikli harfleri
    assert post_text("  🚗🚗 Vitz!!!\n\n2015 ---  model  ") == "vitz 2015 model"
    assert post_text("Ωραίο αυτοκίνητο Vitz Хорошая") == "vitz"  # Yunanca/Kiril düşer (SQL'de de)
    assert post_text(None) == "" and post_text("") == ""
    assert post_text("a" * 2000) == "a" * 300  # ilk 300 normalize karakter
    assert post_text("🚗" * 999 + "x" + "y" * 50) == "x"  # ham metnin yalnız ilk 1000 karakterine bakılır


def test_post_text_fp_same_post_reposted_with_other_emoji_and_punctuation_is_the_same_fingerprint():
    a = post_text_fp(VITZ_TEXT, 2015, "facebook")
    assert a and len(a) == 32
    assert post_text_fp("🔥🔥 " + VITZ_TEXT.upper().replace(",", " ;") + " 🔥", 2015, "facebook") == a
    assert post_text_fp(VITZ_TEXT, 2015, "instagram") == a
    assert post_text_fp(VITZ_TEXT.replace("98.000", "99.000"), 2015, "facebook") != a  # rakam farkı = farklı metin
    assert post_text_fp(VITZ_TEXT + " tek sahibinden", 2015, "facebook") != a  # 300 karakterden kısa metinde ek söz de fark
    long_post = VITZ_TEXT + " Lastikler yeni, klima buz gibi, iç temizliği yapıldı, sigara içilmedi." * 4
    assert len(post_text(long_post)) == 300
    long_fp = post_text_fp(long_post, 2015, "facebook")
    assert post_text_fp(long_post + "\nLefkoşa · £5,500 #satilik #vitz", 2015, "facebook") == long_fp  # 300'den sonrası (satış kutusu, etiketler) sayılmaz


def test_post_text_fp_only_for_social_long_texts_that_name_the_year():
    assert post_text_fp(VITZ_TEXT, 2015, "web") is None  # site metinleri kalıp (KKTCar "… ilan arşivi"): kural yalnız sosyal medyada
    assert post_text_fp(VITZ_TEXT, 2015, None) is None
    assert post_text_fp("2015 Vitz 5500£", 2015, "facebook") is None  # genel kısa gönderi asla birleşmez
    generic = "satilik 2015 model toyota vitz 1 3 otomatik benzin 5500 stg"  # 59 karakter: eşiğin hemen altında
    assert len(post_text(generic)) == POST_TEXT_MIN_CHARS - 1 and post_text_fp(generic, 2015, "facebook") is None
    assert post_text_fp(generic + " x", 2015, "facebook") is not None  # 61 karakter
    assert post_text_fp(VITZ_TEXT, None, "facebook") is None
    assert post_text_fp(VITZ_TEXT, 2016, "facebook") is None  # yıl önekte yok: parmak izi aracı anlatmıyor olabilir
    assert post_text_fp(VITZ_TEXT.replace("2015 model", "2015model"), 2015, "facebook") is None  # yıl ayrı sözcük olmalı
    intro = "Galerimizde kredi ve takas imkanı vardır, adresimiz sanayi bölgesi, her gün açığız, arayın bilgi verelim. " * 3
    assert post_text_fp(intro + VITZ_TEXT, 2015, "facebook") is None  # ilk 300 karakter yalnız galeri girişi: yıl yok


def test_same_post_text_links_reposts_without_km_or_phone():
    a, b = post(1, 30), post(2, 1)
    assert not same_car(a, b) and same_post_text(a, b)
    assert same_post_text(post(1, price_gbp=None, price_amount=None, currency=None), post(2, price_gbp=None, price_amount=None, currency=None))
    # TL ilan: asıl tutar karşılaştırılır (GBP karşılığı günün kuruyla değişebilir)
    assert same_post_text(post(1, price_amount=250_000.0, currency="TRY", price_gbp=5600.0),
                          post(2, price_amount=250_000.0, currency="TRY", price_gbp=5450.0))


def test_same_post_text_never_links_short_texts_different_prices_years_or_unknown_models():
    short = "2015 Vitz 5500£"
    assert not same_post_text(post(1, text=short), post(2, text=short))
    assert not same_post_text(post(1), post(2, price_amount=5000.0, price_gbp=5000.0))  # fiyat düşüşü yeni sinyal: kopya değil
    assert not same_post_text(post(1), post(2, currency="TRY"))
    assert not same_post_text(post(1), post(2, price_amount=None, currency=None, price_gbp=None))  # biri fiyatsız
    assert not same_post_text(post(1, price_amount=None, currency=None), post(2))
    assert not same_post_text(post(1, currency=None), post(2, currency=None))  # para birimi bilinmiyor: "aynı fiyat" denemez
    assert not same_post_text(post(1), post(2, year=2016, text=VITZ_TEXT.replace("2015", "2016")))
    assert not same_post_text(post(1), {**post(2), "year": 2016})  # parmak izi aynı olsa da yıl farklı
    assert not same_post_text({**post(1), "model_norm": None}, {**post(2), "model_norm": None})  # model bilinmiyor
    assert not same_post_text(post(1), {**post(2), "model_norm": "yaris"})
    assert not same_post_text(post(1), post(2, text=VITZ_TEXT.replace("muayeneli", "muayenesi var")))  # önekte metin farklı
    assert not same_post_text(post(1), post(2, platform="web"))  # web ilanının parmak izi yok


def test_same_post_text_is_vetoed_by_conflicting_km_or_phone():
    # aynı kalıpla iki ayrı araç (galeri): metnin 300'den sonrası farklı olabilir; km ya da telefon çelişirse birleşmez
    assert not same_post_text(post(1, km=98_000), post(2, km=145_000), TODAY)
    assert same_post_text(post(1, km=98_000), post(2, km=98_000), TODAY)
    assert same_post_text(post(1, km=98_000), post(2, km=None), TODAY)  # bir tarafta km yok: çelişki değil
    assert same_post_text(post(1, km=9), post(2, km=98_000), TODAY)  # şüpheli km (9) bilinmiyor sayılır
    assert not same_post_text(post(1, seller_phone="905330000001"), post(2, seller_phone="905330000002"))
    assert same_post_text(post(1, seller_phone="905330000001"), post(2, seller_phone="905330000001"))
    assert same_post_text(post(1, seller_phone="905330000001"), post(2))


def test_mark_duplicates_links_facebook_reposts_by_text_to_the_first_seen_post():
    repo = FakeRepo([post("g1", 50), post("g2", 30), post("g3", 1),
                     post("pahali", 20, price_amount=6500.0, price_gbp=6500.0, text=VITZ_TEXT.replace("5.500", "6.500")),
                     post("kisa1", 10, text="2015 Vitz 5500£"), post("kisa2", 5, text="2015 Vitz 5500£")])
    assert mark_duplicates(repo) == 2
    assert repo.dups == {"g2": "g1", "g3": "g1"}  # farklı fiyatlı ve kısa metinli gönderiler kendi başına kalır


def test_text_rule_keeps_the_shadow_and_active_ordering_rules():
    # gölge grupta önce görülen gönderi, sonra gölge olmayan grupta: gölge olmayan kanonik, gölge ilanlar onun kopyası
    repo = FakeRepo([post("golge", 20, shadow=True), post("canli", 1, shadow=False), post("golge2", 10, shadow=True)])
    assert mark_duplicates(repo) == 2 and repo.dups == {"golge": "canli", "golge2": "canli"}
    # aktif ilan pasif (kalkmış) ilanın kopyası olmaz: araç yeniden ilana çıkmış olabilir
    repo = FakeRepo([post("eski", 100, is_active=False), post("yeni", 1)])
    assert mark_duplicates(repo) == 0
    repo = FakeRepo([post("eski", 100, is_active=False), post("yeni", 50, is_active=False)])
    assert mark_duplicates(repo) == 1 and repo.dups == {"yeni": "eski"}
    repo = FakeRepo([post("eski", 100), post("yeni", 1, is_active=False)])  # pasif ilan aktif ilanın kopyası olabilir
    assert mark_duplicates(repo) == 1 and repo.dups == {"yeni": "eski"}
    # zaten kopya olan ilan kanonik olamaz ve yeniden yazılmaz
    repo = FakeRepo([post("a", 50, duplicate_of="z"), post("b", 1)])
    assert mark_duplicates(repo) == 0


def test_text_rule_gives_the_same_links_in_quick_full_and_fast_lane_rounds():
    rows = [post("g1", 50), post("g2", 2), post("baska", 1, text=VITZ_TEXT.replace("98.000", "120.000"))]

    class AnyRepo(FakeRepo):
        def dedupe_candidates(self, days=120, new_hours=None, new_since=None):
            return [dict(r) for r in self.rows]

    results = []
    for kw in ({}, {"quick": True}, {"since": NOW - timedelta(hours=3)}):
        repo = AnyRepo(rows)
        mark_duplicates(repo, **kw)
        results.append(repo.dups)
    assert results == [{"g2": "g1"}] * 3

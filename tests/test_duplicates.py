from datetime import date, datetime, timedelta, timezone

from application.dedupe import mark_duplicates
from domain.duplicates import cross_source_twin, district, same_car

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

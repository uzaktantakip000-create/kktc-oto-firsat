"""Haftalık rapor (plan v4 madde 2.2; sahte repo, ağ ve veritabanı yok): oy bekleyenler, bildirilmemiş fırsatlar (canlılık kontrolüyle),
yakın kaçanlar, tek satır sağlık + dürüst kaybolma özeti; Telegram sınırı ve gönderim zamanı."""
from datetime import datetime, timedelta, timezone

import pytest

from application import health, report

NOW = datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc)


def ago(**kw):
    return NOW - timedelta(**kw)


def vote_row(i, voted=False, tier="guclu", active=True, url=True):
    return dict(id=f"00000000-0000-0000-0000-{i:012d}", url=f"https://kibrisarabaal.com/ilan/{i}-bmw-3-serisi/" if url else None,
                year=2011, brand="BMW", model="3 Serisi 320i", price_gbp=7300.0, is_active=active, tier=tier,
                sent_at=ago(days=1, hours=i), voted=voted)


def strong_row(i, pct=30.0, n=12, method="A", first_seen=None, posted=None, url=None, platform="web", source="KibrisArabaAl", photo_urls=None):
    row = dict(id=f"S{i}", url=url or f"https://kibrisarabaal.com/ilan/{i}-honda-fit/", source_item_id=str(i), year=2013, brand="Honda",
                model="Fit", km=90_000, price_amount=5400.0, currency="GBP", price_gbp=5400.0, first_seen_at=first_seen or ago(days=5),
                posted_at=posted, source_name=source, platform=platform, evaluated_at=ago(hours=3), comparables_n=n,
                market_median_gbp=7900.0, profit_gbp=1500.0, profit_pct=pct, method=method, price_changed_at=None)
    if photo_urls is not None:  # yalnız verilince eklenir (eski satırlarda alan yok: rapor `.get` ile okur)
        row["photo_urls"] = photo_urls
    return row


def near_row(i, pct=18.0, nedenler=None):
    return dict(id=f"N{i}", url=f"https://kktcar.com/listing/2013-bmw-116i-{i}", year=2013, brand="BMW", model="116i", price_gbp=7750.0,
                source_name="KKTCar", comparables_n=10, profit_pct=pct, nedenler=nedenler)


class FakeRepo:
    def __init__(self, votes=(), strong=(), near=(), gone=(), sent=None, fb=None, counts=None, marks=None, names=(), state=None, due=True,
                 resurfaced=(), subs=(), sub_votes=None):
        self.votes, self.strong, self.near, self.gone = list(votes), list(strong), list(near), list(gone)
        self.subs, self.sub_votes = list(subs), sub_votes or {}  # onaylı aboneler (sahip satırı dahil) ve abone -> kendi oy listesi
        self.resurfaced = set(resurfaced)  # sorgunun "yeniden çıkmış eski KKTCarabam ilanı" diyeceği ilan kimlikleri
        self.sent, self.fb = sent or {}, fb or {}
        self.counts = counts or {"new_n": 1234, "active_n": 3133}
        self.marks, self.names, self.state, self.due = marks or {}, list(names), state or {}, due
        self.calls, self.marked = {}, []

    def alerted_votes(self, days=30, chat_id=None, owner=True):
        self.calls["votes_days"] = days
        self.calls.setdefault("votes_for", []).append((chat_id, owner))
        return self.votes if owner else self.sub_votes.get(chat_id, [])

    def approved_subscribers(self):
        return self.subs

    def week_feedback_counts(self, days=7):
        return self.fb

    def unnotified_strong(self, rules_version, days=14, limit=50):
        self.calls["strong"] = (rules_version, days)
        return self.strong

    def resurfaced_kktcarabam(self, listing_ids):
        ids = list(listing_ids)
        self.calls["resurfaced"] = ids  # yalnız tazelik süzgecinden geçemeyen (bayat) VE ilan tarihi bilinmeyen satırlar sorulur
        return {i for i in ids if i in self.resurfaced}

    def near_misses(self, rules_version, days=7, min_comparables=8, skip_reasons=(), limit=5):
        self.calls["near"] = (rules_version, days, min_comparables, tuple(skip_reasons), limit)
        return self.near[:limit]

    def week_alert_counts(self, days=7):
        return self.sent

    def listing_counts(self, days=7):
        return self.counts

    def disappeared_counts(self, days=7):
        return self.gone

    def alert_marks_since(self, prefix, days=7):
        return self.marks.get(prefix, [])

    def source_names(self, ids):
        return self.names

    def get_state(self, key, default=None):
        return self.state.get(key, default)

    def blocked_phones(self):
        return []

    def alert_recent(self, key, hours):
        return not self.due

    def mark_alerted(self, key):
        self.marked.append(key)


@pytest.fixture(autouse=True)
def no_late_sources(monkeypatch):
    monkeypatch.setattr(report, "late_sources", lambda repo, now=None: [])


def build(repo, recheck=None):
    return report.build_weekly_report(repo, recheck=recheck, now=NOW)


def keep_all(repo, cands):
    return cands


# --- bölüm sırası ve oy bekleyenler ---
def test_four_sections_in_priority_order_with_title_dates():
    text, _ = build(FakeRepo(votes=[vote_row(1), vote_row(2, voted=True)], strong=[strong_row(1)], near=[near_row(1)],
                             gone=[{"reason": "satildi", "n": 1, "alerted": 0}]))
    assert text.startswith("📊 Haftalık rapor · 08.10–15.10")
    order = [text.index(h) for h in ("🗳 OY BEKLEYENLER (1)", "🔎 BİLDİRMEDİĞİM FIRSATLAR (1 ilan)", "👀 YAKIN KAÇANLAR", "🩺 Sistem:", "🚪")]
    assert order == sorted(order)
    assert report.tg_len(text) <= report.MAX_UNITS


def test_unvoted_list_says_how_the_system_is_measured_and_has_numbered_buttons():
    repo = FakeRepo(votes=[vote_row(1), vote_row(2, tier="tahmini", active=False), vote_row(3, voted=True)])
    text, kb = build(repo)
    assert repo.calls["votes_days"] == 30
    assert "Son 30 günde 3 fırsat bildirimi gitti, 1 tanesine oy verdin." in text
    assert "Sistemi senin oylarınla ölçüyorum" in text and "👍 (işe yarar) ya da 👎 (yanlış)" in text
    assert "1) 🟢 2011 BMW 3 Serisi 320i · £7.300 · 14.10\nhttps://kibrisarabaal.com/ilan/1-bmw-3-serisi/" in text  # KKTC saati (UTC+3)
    assert "2) 🟠 2011 BMW 3 Serisi 320i · £7.300 · 14.10 · artık yayında değil" in text
    buttons = [b for row in kb["inline_keyboard"] for b in row]
    assert [b["text"] for b in buttons] == ["1 👍", "1 👎", "2 👍", "2 👎"] and len(kb["inline_keyboard"]) == 1
    assert buttons[0]["callback_data"] == "fb:ilgilendim:00000000-0000-0000-0000-000000000001"  # fırsat mesajındaki düğmeyle aynı eylem
    assert buttons[3]["callback_data"] == "fb:yanlis_fiyat:00000000-0000-0000-0000-000000000002"
    assert all(len(b["callback_data"].encode()) <= 64 for b in buttons)  # Telegram sınırı


def test_only_ten_unvoted_listed_and_the_rest_counted():
    text, kb = build(FakeRepo(votes=[vote_row(i) for i in range(1, 14)]))
    assert "10) 🟢" in text and "11) 🟢" not in text and "… ve 3 eski bildirim daha" in text
    assert sum(len(row) for row in kb["inline_keyboard"]) == 20


def test_all_voted_and_no_alerts_cases_have_no_buttons():
    text, kb = build(FakeRepo(votes=[vote_row(1, voted=True)]))
    assert "Hepsini oyladın" in text and kb is None
    text, kb = build(FakeRepo())
    assert "Son 30 günde fırsat bildirimi gitmedi" in text and kb is None


# --- bildirilmemiş fırsatlar ---
def test_unnotified_keeps_send_floor_and_freshness_rules():
    rows = [strong_row(1, pct=40, n=5),  # emsal < 8: anlık mesajda da gitmezdi
            strong_row(2, pct=35, method="B"),  # 🟢 yöntem A ister
            strong_row(3, pct=33, first_seen=ago(hours=10)),  # taze: normal yoldan gider, burada görünmez
            strong_row(4, pct=30, posted=ago(days=20)),
            strong_row(5, pct=25)]
    repo = FakeRepo(strong=rows)
    text, _ = build(repo)
    assert repo.calls["strong"] == (report.RULES_VERSION, 14)
    assert "(2 ilan)" in text and "ilan eski olduğu için" in text
    assert "/4-honda-fit/" in text and "/5-honda-fit/" in text
    assert not any(f"/{i}-honda-fit/" in text for i in (1, 2, 3))
    assert "~%30 kâr (piyasa ortası £7.900, 12 emsal)\n  KibrisArabaAl · ilan tarihi 25.09" in text
    assert "ilk görülme 10.10" in text  # ilan tarihi yoksa ilk görülme


def test_unnotified_never_lists_a_resurfaced_old_kktcarabam_ad(capsys):
    """Sitenin "en yeni" listesine geri ittiği eski KKTCarabam ilanı (tarihsiz, numarası daha önce görülenlerden küçük) bayat sayılır ama
    raporda LİSTELENMEZ: yeni fırsat değil, eski ve belki satılmış ilan. İlan tarihi belli olanlara kural uygulanmaz (sorgulanmaz bile)."""
    rows = [strong_row(1, pct=40, url="https://www.kktcarabam.com/264352-bmw-3-serisi"),  # yeniden çıkmış
            strong_row(2, pct=35),  # bayat, tarihsiz, yeniden çıkmış değil: listelenir
            strong_row(3, pct=30, posted=ago(days=20)),  # tarihi belli: tarih karar verir, sorgulanmaz, listelenir
            strong_row(4, pct=25, first_seen=ago(hours=10))]  # taze: zaten raporda değil, sorgulanmaz
    repo = FakeRepo(strong=rows, resurfaced={"S1"})
    text, _ = build(repo)
    assert repo.calls["resurfaced"] == ["S1", "S2"]
    assert "(2 ilan)" in text and "/264352-bmw-3-serisi" not in text
    assert "/2-honda-fit/" in text and "/3-honda-fit/" in text and "/4-honda-fit/" not in text
    # hepsi yeniden çıkmışsa bölüm "yok" der
    all_old = FakeRepo(strong=[strong_row(1, pct=40), strong_row(2, pct=35)], resurfaced={"S1", "S2"})
    text, _ = build(all_old)
    assert "🔎 Bildirmediğim fırsat yok (son 14 gün)." in text and "BİLDİRMEDİĞİM FIRSATLAR" not in text
    # aday yoksa sorgu da yok
    none = FakeRepo(strong=[strong_row(3, posted=ago(days=20))])
    build(none)
    assert "resurfaced" not in none.calls


def kka_photo(moment, host="www.kktcarabam.com"):
    """Kapak fotoğrafı `moment` (UTC) anında yüklenmiş KKTCarabam adresi (yol KKTC saatiyle, UTC+3)."""
    local = moment + timedelta(hours=3)
    return [f"https://{host}/uploads/images/{local:%Y/%m/%d/%H}/img-1-6abe346a6b863-270_200.jpg"]


def test_unnotified_never_lists_a_kktcarabam_ad_whose_photo_was_already_old_when_first_seen():
    """Kapak fotoğrafı bize İLK göründüğü anda zaten 36 saatten eskiyse (sitenin geri ittiği eski ilan) yeniden çıkmış ilan gibi LİSTELENMEZ ve numara
    sorgusuna konmaz. Fotoğraf ilk görülmeye yakınsa (gerçekten yeni ama gönderilememiş ilan) eskisi gibi listelenir; şimdiye göre bakılmaz."""
    seen = ago(days=5)
    rows = [strong_row(1, pct=40, first_seen=seen, photo_urls=kka_photo(seen - timedelta(days=40))),  # eski fotoğraf: listelenmez
            strong_row(2, pct=35, first_seen=seen, photo_urls=kka_photo(seen - timedelta(hours=2))),  # ilk görülmeye yakın: listelenir
            strong_row(3, pct=30, first_seen=seen, photo_urls=kka_photo(seen - timedelta(hours=40))),  # 36 saati aştı: listelenmez
            strong_row(4, pct=28, first_seen=seen, photo_urls=kka_photo(seen - timedelta(days=40), host="www.kktcar.com")),  # başka site: etkilenmez
            strong_row(5, pct=26, first_seen=seen, photo_urls=[]),  # fotoğraf bilgisi yok: eskisi gibi
            strong_row(6, pct=24, first_seen=seen),  # alan hiç yok: eskisi gibi
            strong_row(7, pct=22, first_seen=seen, photo_urls=kka_photo(seen - timedelta(hours=2)))]  # yeni fotoğraf ama numarası küçük
    repo = FakeRepo(strong=rows, resurfaced={"S7"})
    text, _ = build(repo)
    assert repo.calls["resurfaced"] == ["S2", "S4", "S5", "S6", "S7"]  # eski fotoğraflılar numara sorgusuna girmedi; yeni fotoğraflı S7 girdi
    assert "(4 ilan)" in text
    shown = [i for i in range(1, 8) if f"/{i}-honda-fit/" in text]
    assert shown == [2, 4, 5, 6]  # S1, S3 fotoğraf yüzünden; S7 numara kuralı yüzünden hiç listelenmedi
    # hepsi eski fotoğraflıysa bölüm "yok" der ve sorgu hiç yapılmaz
    only_old = FakeRepo(strong=[strong_row(1, first_seen=seen, photo_urls=kka_photo(seen - timedelta(days=9)))])
    text, _ = build(only_old)
    assert "🔎 Bildirmediğim fırsat yok (son 14 gün)." in text and "resurfaced" not in only_old.calls


def test_preview_does_no_liveness_check_and_says_so():
    text, _ = build(FakeRepo(strong=[strong_row(i, pct=40 - i) for i in range(7)]))
    assert "kontrol gönderim anında yapılır" in text
    assert text.count("-honda-fit/") == 5 and "… ve 2 tane daha." in text  # en çok 5


def test_liveness_drops_sold_ones_and_refills_from_the_next_best():
    calls = []

    def recheck(repo, cands):
        calls.append([c.listing["id"] for c in cands])
        return [c for c in cands if c.listing["id"] not in ("S0", "S2")]  # satılmış/fiyatı değişmiş

    text, _ = build(FakeRepo(strong=[strong_row(i, pct=40 - i) for i in range(9)]), recheck=recheck)
    assert calls == [["S0", "S1", "S2", "S3", "S4"], ["S5", "S6"]]  # yalnız eksik kalan kadar yeni sayfa açılır
    assert "az önce kontrol ettim; 2 tanesi satılmış, kalkmış ya da fiyatı değişmiş çıktı, göstermedim." in text
    shown = [i for i in range(9) if f"/{i}-honda-fit/" in text]
    assert shown == [1, 3, 4, 5, 6] and "… ve 2 tane daha." in text


def test_liveness_checks_are_capped():
    seen = []

    def drop_all(repo, cands):
        seen.extend(cands)
        return []

    text, _ = build(FakeRepo(strong=[strong_row(i) for i in range(30)]), recheck=drop_all)
    assert len(seen) == report.LIVENESS_MAX_CHECKS and "BİLDİRMEDİĞİM FIRSATLAR (30 ilan)" in text and "-honda-fit/" not in text


def test_liveness_failure_is_said_openly_and_report_still_goes():
    def boom(repo, cands):
        raise RuntimeError("site yok")

    text, _ = build(FakeRepo(strong=[strong_row(1), strong_row(2)]), recheck=boom)
    assert "kontrolü bu kez tamamlanamadı" in text and "/1-honda-fit/" in text and "/2-honda-fit/" in text


def test_source_without_liveness_is_marked():
    ig = strong_row(1, url="https://www.instagram.com/p/abc/", platform="instagram", source="ARABA KIBRIS", posted=ago(days=4))
    text, _ = build(FakeRepo(strong=[ig, strong_row(2)]), recheck=keep_all)
    assert "Instagram (satıldı mı izlenemiyor)" in text and "  KibrisArabaAl · " in text


# --- yakın kaçanlar ---
def test_near_misses_say_why_in_one_line_and_use_the_send_floor():
    repo = FakeRepo(near=[near_row(1, pct=33.9, nedenler=["km_yuksek"]), near_row(2, pct=24, nedenler=["emsal_yili_yeni", "tl_fiyat", "x"]),
                          near_row(3, pct=22), near_row(4, pct=18.6)])
    text, _ = build(repo)
    rv, days, min_n, skip, limit = repo.calls["near"]
    assert (rv, days, min_n, limit) == (report.RULES_VERSION, 7, 8, 5) and {"fiyat_asiri_dusuk", "model_belirsiz", "okuma_satildi"} <= set(skip)
    assert "• 2013 BMW 116i · £7.750 · ~%34 kâr ama km yüksek · https://kktcar.com/listing/2013-bmw-116i-1" in text
    assert "~%24 kâr ama emsaller daha yeni model, fiyat TL (TL ilanlar ucuz görünür) ·" in text
    assert "~%22 kâr ama kâr tutarı £750 altında" in text and "~%19 kâr (eşik %20)" in text
    near_part = text[text.index("👀"):text.index("🩺")]
    assert all("\n" not in line for line in near_part.strip().split("\n• ")[1:])  # tek satır


def test_near_miss_threshold_follows_owner_setting():
    text, _ = build(FakeRepo(near=[near_row(1, pct=22)], state={"cfg:strong_threshold": "0.25"}))
    assert "~%22 kâr (eşik %25)" in text


def test_empty_sections_are_one_line():
    text, _ = build(FakeRepo())
    assert "🔎 Bildirmediğim fırsat yok (son 14 gün)." in text and "👀 Bu hafta yakın kaçan yok." in text


# --- sağlık + kaybolan ilanlar ---
def test_gone_line_separates_really_sold_from_unclear():
    gone = [{"reason": "belirsiz", "n": 5, "alerted": 1}, {"reason": "kaldirildi", "n": 1, "alerted": 0},
            {"reason": "satildi", "n": 2, "alerted": 1}]
    text, _ = build(FakeRepo(gone=gone))
    assert ("🚪 Bu hafta yayından kalkan 8 ilan: 2 tanesi satıldı (site 'satıldı' yazdı ya da sen söyledin), 6 tanesinin satılıp "
            "satılmadığı belli değil (sayfası kalktı, listeden düştü ya da süresi doldu). Bunların 2 tanesi sana gönderdiğim fırsattı.") in text
    assert "hızlı satılmış" not in text  # eski karnede pasifleşen her 🟢 "satılmış" sayılıyordu
    assert "🚪 Bu hafta yayından kalkan ilan yok." in build(FakeRepo())[0]


def test_health_is_one_line_and_reports_late_sources(monkeypatch):
    repo = FakeRepo(sent={"guclu": 3}, counts={"new_n": 6282, "active_n": 3133})
    line = [x for x in build(repo)[0].split("\n") if x.startswith("🩺")][0]
    assert line == ("🩺 Sistem: 7 günde 6.282 yeni ilan tarandı, sana 3 🟢 fırsat gitti, şu an 3.133 aktif ilan var; "
                    "tüm kaynaklar zamanında taranıyor.")
    monkeypatch.setattr(report, "late_sources", lambda repo, now=None: [{"name": "A"}, {"name": "B"}])
    text = build(FakeRepo(sent={"guclu": 1, "tahmini": 2}))[0]
    assert "sana 1 🟢 ve 2 🟠 fırsat gitti" in text and "⚠️ 2 kaynakta gecikme var (ayrıntı: /durum)." in text


def test_self_made_changes_are_listed_only_when_there_are_any():
    assert "🔧" not in build(FakeRepo())[0]
    repo = FakeRepo(marks={"est_off:": ["fiat|egea"], "est_tighten": [""], "guard:": ["S1"]}, names=["KibrisCars"],
                    state={"cfg:est_min_discount_to_lower": "0.75"})
    text = build(repo)[0]
    assert "🔧 Bu hafta kendiliğinden değiştirdiklerim: 🟠 vermeyi bıraktığım modeller (çok 'yanlış' dedin): fiat egea" in text
    assert "%75'i ya da altında" in text and "bildirimden çıkardığım kaynaklar: KibrisCars" in text


# --- Telegram sınırı ---
def test_too_long_drops_lowest_priority_lines_first_says_how_many_and_never_cuts_a_link():
    long = "x" * 200
    votes = [vote_row(i) for i in range(1, 11)]
    for v in votes:
        v["url"] += long
    strong = [strong_row(i, url=f"https://kibrisarabaal.com/ilan/{i}-{long}/") for i in range(5)]
    near = [dict(near_row(i), url=f"https://kktcar.com/listing/{i}-{long}") for i in range(5)]
    text, kb = build(FakeRepo(votes=votes, strong=strong, near=near))
    assert report.tg_len(text) <= report.MAX_UNITS < 4096
    note = [x for x in text.split("\n") if x.startswith("✂️")][0]
    assert note.startswith("✂️ Mesaj sınırına sığsın diye çıkarılan satır: ") and "yakın kaçan 5" in note  # önce en az önemli bölüm
    assert "oy bekleyen" not in note and all(v["url"] in text for v in votes)  # en önemli bölüm yerinde
    kept_urls = [w for w in text.split() if w.startswith("https://")]
    every_url = {v["url"] for v in votes} | {r["url"] for r in strong} | {r["url"] for r in near}
    assert kept_urls and all(u in every_url for u in kept_urls)  # bağlantı ortadan kesilmedi
    kept_votes = sum(1 for v in votes if v["url"] in text)
    assert sum(len(row) for row in kb["inline_keyboard"]) == 2 * kept_votes  # yalnız mesajda kalan satırların düğmesi
    assert text.rstrip().split("\n")[-1].startswith("🚪")  # sağlık satırları hiç atılmaz


def test_when_even_votes_must_be_cut_buttons_follow_the_kept_lines():
    votes = [vote_row(i) for i in range(1, 11)]
    for v in votes:
        v["url"] += "y" * 450
    text, kb = build(FakeRepo(votes=votes, strong=[strong_row(1)], near=[near_row(1)]))
    assert report.tg_len(text) <= report.MAX_UNITS
    kept = [v for v in votes if v["url"] in text]
    assert 0 < len(kept) < 10 and kept == votes[:len(kept)]  # en yeniler kalır, sondan atılır
    assert f"oy bekleyen {10 - len(kept)}" in text and "bildirilmemiş 1" in text and "yakın kaçan 1" in text
    assert [b["text"] for row in kb["inline_keyboard"] for b in row][-1] == f"{len(kept)} 👎"


# --- gönderim ---
def test_report_is_not_even_built_before_it_is_due(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.setattr(report, "build_weekly_report", lambda *a, **k: (_ for _ in ()).throw(AssertionError("kurulmamalı")))
    assert report.send_weekly_report(FakeRepo(due=False)) is False


def test_send_uses_liveness_check_buttons_and_no_link_preview(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    sent, checked = [], []
    monkeypatch.setattr(health, "api", lambda *a, **kw: sent.append(kw))
    monkeypatch.setattr(report, "recheck_before_send", lambda repo, cands: checked.extend(cands) or cands)
    repo = FakeRepo(votes=[vote_row(1)], strong=[strong_row(1)])
    assert report.send_weekly_report(repo, now=NOW) is True
    (msg,) = sent
    assert msg["disable_web_page_preview"] is True and msg["reply_markup"]["inline_keyboard"][0][0]["text"] == "1 👍"
    assert "az önce kontrol ettim" in msg["text"] and [c.listing["id"] for c in checked] == ["S1"]
    assert repo.marked == ["weekly_report"]


def test_send_without_votes_sends_no_keyboard(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    sent = []
    monkeypatch.setattr(health, "api", lambda *a, **kw: sent.append(kw))
    assert report.send_weekly_report(FakeRepo(), recheck=keep_all) is True
    assert "reply_markup" not in sent[0]


def test_preview_text_is_the_built_text():
    repo = FakeRepo(votes=[vote_row(1)], strong=[strong_row(1)])
    assert report.weekly_report_text(repo, now=NOW) == build(repo)[0]


# --- herkese (sahibin kararı 08.10.2026): sahibe önce, sonra her aboneye kendi oy listesiyle ---
SUBS = [{"chat_id": "1", "is_owner": True}, {"chat_id": "22", "is_owner": False}, {"chat_id": "33", "is_owner": False}]


def sending(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    owner_sent, sub_sent, checked = [], [], []
    monkeypatch.setattr(health, "api", lambda *a, **kw: owner_sent.append(kw))
    monkeypatch.setattr(report, "api", lambda *a, **kw: sub_sent.append(kw))
    monkeypatch.setattr(report, "recheck_before_send", lambda repo, cands: checked.append(len(cands)) or cands)
    return owner_sent, sub_sent, checked


def test_report_goes_to_everyone_each_with_own_votes_and_one_liveness_check(monkeypatch):
    owner_sent, sub_sent, checked = sending(monkeypatch)
    repo = FakeRepo(votes=[vote_row(1), vote_row(2, voted=True)], strong=[strong_row(1)], subs=SUBS,
                    sub_votes={"22": [vote_row(7)], "33": [vote_row(8, voted=True)]})
    assert report.send_weekly_report(repo, now=NOW) is True
    assert len(owner_sent) == 1 and [m["chat_id"] for m in sub_sent] == ["22", "33"]  # sahip bir kez (sahip satırı atlanır)
    assert checked == [1]  # canlılık kontrolü herkes için bir kez
    assert repo.calls["votes_for"] == [("1", True), ("22", False), ("33", False)]
    assert "2 fırsat bildirimi gitti, 1 tanesine oy verdin" in owner_sent[0]["text"]
    sub22, sub33 = (m["text"] for m in sub_sent)
    assert "1 fırsat bildirimi gitti, 0 tanesine oy verdin" in sub22 and "/ilan/7-bmw" in sub22 and "/ilan/1-bmw" not in sub22
    assert sub_sent[0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"].endswith("000000000007")
    assert "Hepsini oyladın" in sub33 and "reply_markup" not in sub_sent[1]
    assert all("BİLDİRMEDİĞİM FIRSATLAR (1 ilan)" in m["text"] and "az önce kontrol ettim" in m["text"] for m in [*owner_sent, *sub_sent])
    assert repo.marked == ["weekly_report"]


def test_subscriber_text_has_no_owner_only_lines(monkeypatch):
    monkeypatch.setattr(report, "late_sources", lambda repo, now=None: [{"name": "A"}])
    repo = FakeRepo(votes=[vote_row(1)], sub_votes={"22": [vote_row(7)]}, sent={"guclu": 3}, gone=[{"reason": "satildi", "n": 2, "alerted": 1}],
                    marks={"est_off:": ["fiat|egea"]})
    owner_text = build(repo)[0]
    sub_text = report.build_weekly_report(repo, now=NOW, chat_id="22", owner=False)[0]
    assert "Sistemi senin oylarınla ölçüyorum" in owner_text and "/durum" in owner_text and "🔧" in owner_text
    assert "sen söyledin" in owner_text and "sana 3 🟢 fırsat gitti" in owner_text
    assert report.VOTE_ASK_SUBSCRIBER in sub_text and "Sistemi senin oylarınla" not in sub_text
    assert "/durum" not in sub_text and "🔧" not in sub_text and "sen söyledin" not in sub_text and "sana" not in sub_text
    assert "3 🟢 fırsat bildirildi" in sub_text and "aktif ilan var; ⚠️ 1 kaynakta gecikme var." in sub_text
    assert "bize bildirildi" in sub_text and "1 tanesi bildirdiğim fırsattı" in sub_text


def test_no_subscriber_gets_it_when_the_owner_send_failed(monkeypatch):
    owner_sent, sub_sent, _ = sending(monkeypatch)
    monkeypatch.setattr(report, "notify_owner", lambda *a, **k: False)  # sonraki turda hepsi birlikte yeniden denenir
    assert report.send_weekly_report(FakeRepo(subs=SUBS), now=NOW) is False and sub_sent == []


def test_one_subscriber_failing_does_not_stop_the_others_and_logs_masked(monkeypatch, capsys):
    owner_sent, sub_sent, _ = sending(monkeypatch)

    def flaky(*a, **kw):
        if kw["chat_id"] == "123456722":
            raise report.TelegramError("sendMessage", 403, "Forbidden: bot was blocked by the user")
        sub_sent.append(kw)
    monkeypatch.setattr(report, "api", flaky)
    subs = [{"chat_id": "1", "is_owner": True}, {"chat_id": "123456722", "is_owner": False}, {"chat_id": "33", "is_owner": False}]
    assert report.send_weekly_report(FakeRepo(subs=subs), now=NOW) is True
    assert [m["chat_id"] for m in sub_sent] == ["33"]
    out = capsys.readouterr().out
    assert "haftalık rapor gönderilemedi (chat …722): 403" in out and "123456722" not in out  # log'a kimlik tam yazılmaz

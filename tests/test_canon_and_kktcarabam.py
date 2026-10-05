"""Adım 2c: (1) vites/yakıt standart yazımı: KKTCarabam "düz/elektrik" yazıyor, diğer siteler "manuel/elektrikli" — aynı araç tipi emsal
olabilsin; (2) KKTCarabam engel ya da boş sayfa gelirse sessiz başarı değil hata."""
from contextlib import nullcontext
from datetime import datetime, timezone

import pytest

from application import collect_kktcarabam as kka
from domain.comparables import _is_comparable
from domain.normalize import canon_fuel, canon_transmission
from domain.settings import Settings
from infrastructure.collectors import kktcarabam

NOW = datetime(2026, 10, 3, tzinfo=timezone.utc)


def car(i, **kw):
    base = dict(id=i, brand_norm="Toyota", model_norm="auris", year=2015, km=80_000, steering="RHD", transmission="manuel", fuel="benzin",
                engine_l=None, price_gbp=6000.0, currency_guess=False, first_seen_at=NOW, ref_date=NOW, is_active=True,
                duplicate_of=None, urgency_signals=[])
    return base | kw


def test_canon_maps_only_unambiguous_spellings():
    assert canon_transmission("Düz") == "manuel" and canon_transmission("duz vites") == "manuel" and canon_transmission("manual") == "manuel"
    assert canon_transmission("otomatik") == "otomatik" and canon_transmission("yarı otomatik") == "yarı otomatik"  # ayrı sınıf, karışmaz
    assert canon_transmission("atomatik") == "otomatik"
    assert canon_fuel("elektrik") == "elektrikli" and canon_fuel("Elektrikli") == "elektrikli" and canon_fuel("mazot") == "dizel"
    assert canon_fuel("hybrid") == "hibrit" and canon_fuel("benzin / hibrit") == "benzin / hibrit"  # bilinmeyen yazım olduğu gibi kalır
    assert canon_fuel(None) is None and canon_transmission("") == ""


def test_same_vehicle_type_written_differently_is_a_comparable():
    target = car("t", transmission="düz", fuel="elektrik")  # KKTCarabam yazımı
    row = car("r", transmission="manuel", fuel="elektrikli")  # KibrisArabaAl yazımı
    assert _is_comparable(target, row, 1, NOW, Settings())


def test_genuinely_different_types_are_still_not_comparables():
    s = Settings()
    assert not _is_comparable(car("t", transmission="otomatik"), car("r", transmission="manuel"), 1, NOW, s)
    assert not _is_comparable(car("t", fuel="dizel"), car("r", fuel="benzin"), 1, NOW, s)
    assert not _is_comparable(car("t", transmission="yarı otomatik"), car("r", transmission="otomatik"), 1, NOW, s)


def test_kktcarabam_card_gets_standard_spelling():
    card = kktcarabam.Card("1", "https://www.kktcarabam.com/1-toyota-auris-girne-elektrik-duz", "2013 Model Düz Toyota Auris", "9.000 GBP", "Toyota Auris")
    data = kktcarabam.card_to_listing(card)
    assert data["transmission"] == "manuel" and data["fuel"] == "elektrikli"


class Repo:
    def __init__(self):
        self.checked = []

    def known_item_ids(self, source_id):
        return set()

    def mark_alive(self, source_id, item_ids):
        return 0

    def mark_checked(self, *a, **k):
        self.checked.append(1)

    def count_recent(self, source_id):
        return 0


SOURCE = {"id": "k1", "name": "KKTCarabam"}


def test_blocked_page_raises_instead_of_silent_success(monkeypatch):
    monkeypatch.setattr(kktcarabam, "open_session", lambda: nullcontext())
    monkeypatch.setattr(kktcarabam, "fetch_html", lambda session, url: None)
    repo = Repo()
    with pytest.raises(RuntimeError, match="liste sayfası alınamadı"):
        kka.collect_kktcarabam(repo, SOURCE)
    assert repo.checked == []  # "kontrol edildi" işaretlenmedi: bayat alarmı da devreye girer


def test_zero_cards_raises(monkeypatch):
    monkeypatch.setattr(kktcarabam, "open_session", lambda: nullcontext())
    monkeypatch.setattr(kktcarabam, "fetch_html", lambda session, url: "<html>engel sayfası</html>")
    with pytest.raises(RuntimeError, match="hiç ilan kartı"):
        kka.collect_kktcarabam(Repo(), SOURCE)

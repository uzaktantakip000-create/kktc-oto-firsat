from application import settings_store as ss
from application.evaluate import evaluate_new
from domain.profit import Tier
from domain.settings import Settings
from tests.test_evaluate import FakeRepo, POOL, car


class Store:
    def __init__(self):
        self.d, self.phones = {}, []

    def get_state(self, k, default=None):
        return self.d.get(k, default)

    def set_state(self, k, v):
        self.d[k] = v

    def blocked_phones(self):
        return self.phones


def test_threshold_budget_and_brand_commands_validate_and_persist():
    st = Store()
    assert "en az %25" in ss.set_threshold(st, " 25")
    assert "arasında olmalı" in ss.set_threshold(st, "5") and "Kullanım" in ss.set_threshold(st, "abc")
    assert ss.load_settings(st).strong_threshold == 0.25
    assert "20.000" in ss.set_budget(st, "20000") and ss.load_settings(st).max_buy_gbp == 20000
    assert "kaldırıldı" in ss.set_budget(st, "yok") and not ss.load_settings(st).max_buy_gbp
    assert "Fiat" in ss.block_brand(st, "fiat", True) and ss.load_settings(st).blocked_brands == ["Fiat"]
    ss.block_brand(st, "FIAT", False)
    assert ss.load_settings(st).blocked_brands == []
    assert "Kullanım" in ss.block_brand(st, "", True)


def test_describe_lists_everything():
    st = Store()
    ss.set_threshold(st, "25")
    out = ss.describe(st)
    assert "%25 (varsayılan %20)" in out and "Alış bütçesi sınırı: yok" in out


def run(listing, settings):
    (ev,) = evaluate_new(FakeRepo([listing], POOL), settings)
    return ev.profit.tier


def test_user_decisions_change_the_verdict():
    deal = car("t", 5000, seller_phone="905330000001")
    assert run(deal, Settings()) is Tier.STRONG
    assert run(deal, Settings(blocked_brands=["Toyota"])) is Tier.NONE            # /istemiyorum
    assert run(deal, Settings(max_buy_gbp=4000)) is Tier.NONE                      # /butce
    assert run(deal, Settings(blocked_phones=["905330000001"])) is Tier.NONE       # kusurlu satıcı
    assert run(deal, Settings(muted_models=["Toyota|vitz"])) is Tier.NEGOTIABLE    # 3 kez pas
    assert run(deal, Settings(strong_threshold=0.80)) is Tier.NEGOTIABLE           # /esik 80 (pratikte) -> 🟡


def test_estimated_toggle_persists_and_shows_in_describe():
    st = Store()
    assert ss.load_settings(st).estimated_alerts is True  # varsayılan açık
    assert ss.set_estimated(st, " kapat") == "🟠 tahmini fırsat bildirimleri kapalı"
    assert ss.load_settings(st).estimated_alerts is False and "Tahmini fırsat bildirimleri (az emsal, değer eğrisiyle): kapalı" in ss.describe(st)
    assert ss.set_estimated(st, "aç") == "🟠 tahmini fırsat bildirimleri açık"
    assert ss.load_settings(st).estimated_alerts is True
    assert "şu an açık" in ss.set_estimated(st, "") and "Kullanım" in ss.set_estimated(st, "belki")
    st.d["cfg:est_min_discount_to_lower"] = "0.7"
    assert ss.load_settings(st).est_min_discount_to_lower == 0.7 and "%70" in ss.describe(st)

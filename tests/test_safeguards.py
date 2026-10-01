import pytest

from application.safeguards import check_read_rate, sitemap_shrunk


class Store:
    def __init__(self):
        self.d = {}

    def get_state(self, k, default=None):
        return self.d.get(k, default)

    def set_state(self, k, v):
        self.d[k] = v


def test_sitemap_shrink_detected_and_last_size_kept():
    s = Store()
    assert sitemap_shrunk(s, "A", 2000) is False
    assert sitemap_shrunk(s, "A", 1900) is False and s.d["sitemap_n:A"] == "1900"
    assert sitemap_shrunk(s, "A", 300) is True and s.d["sitemap_n:A"] == "1900"  # şüpheli boyut kaydedilmez
    assert sitemap_shrunk(s, "A", 1950) is False


def test_read_rate_alarm():
    check_read_rate("x", 10, 2)
    check_read_rate("x", 3, 3)  # çok az örnek: alarm yok
    with pytest.raises(RuntimeError, match="şablon"):
        check_read_rate("x", 10, 6)

import json
from pathlib import Path

from domain.caption_parser import parse_caption

POSTS = json.loads((Path(__file__).parent / "fixtures/ilan_sayfalari_sablon2.json").read_text())


def test_all_fixture_variants_parse():
    for p in POSTS:
        r = parse_caption(p["caption"])
        assert r is not None, p["shortCode"]
        assert r.brand and r.year and r.price_amount, p["shortCode"]


def test_uppercase_try_listing():
    p = next(p for p in POSTS if "100.000 TL" in p["caption"])
    r = parse_caption(p["caption"])
    assert (r.currency, r.price_amount, r.year, r.km, r.steering, r.swap) == ("TRY", 100000, 2001, 308000, "RHD", False)
    assert r.phone == "905330000014"

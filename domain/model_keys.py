"""Model anahtarı kuralları (Adım 6a): `normalize_model` çoğu markada yalnızca ilk kelimeyi alır ("cx-5" -> "cx", "fit aria" -> "fit").
Burada FARKLI araçları tek anahtarda birleştiren aileler ayrılır (fiyatları çok farklı: CX-3 ≠ CX-5, Fit ≠ Fit Aria, Yaris ≠ Yaris Cross).
Kurallar KISA ve açıktır: marka -> [(desen, anahtar şablonu)], ilk eşleşen kazanır. Desenler `normalize_model`'in hazırladığı metne uygulanır:
küçük harf, aksansız, "-" -> boşluk, noktalama yok ("cx-5 2.0 skyactiv-g" -> "cx 5 2.0 skyactiv g").
Saf mantık (G/Ç yok). Eşleşme yoksa None döner ve eski davranış (ilk kelime) sürer: bu yüzden bu dosya yalnız AYIRIR, hiç ilanı kaybetmez.
Ekleme kuralı: yeni bir aile ayrılacaksa buraya bir satır + tests/fixtures/model_golden.csv'ye gerçek ad örneği.
Yazım birleştirme (27.10.2026, canlı denetim): Facebook'taki yaygın yanlış yazımlar ("Tida", "Elentra", "RAV 4") ve BMW şasi kodları
("F30 320d", "E90") sitelerin anahtarına bağlanır; yoksa emsalsiz kalıyorlardı. Yalnız tek modele karşılık gelen yazımlar: X serisi
şasileri (E70, F15, E83…), coupe/cabrio şasileri (E92, E93) ve 3 GT (F34) bağlanmaz."""
import re

_RULES: dict[str, list[tuple[str, str]]] = {
    "Toyota": [(r"^yaris cross", "yaris cross"), (r"^corolla cross", "corolla cross"), (r"^(?:corolla )?axio", "axio"),
               (r"^c ?hr\b", "c-hr"), (r"^rav(?: ?4)?\b", "rav4")],  # Toyota "RAV" yalnız RAV4
    "Mazda": [(r"^cx ?(\d{1,2})\b", r"cx-\1"), (r"^rx ?(\d)\b", r"rx-\1")],
    "Honda": [(r"^fit aria", "fit aria"), (r"^cr ?v\b", "cr-v"), (r"^cr ?z\b", "cr-z"), (r"^zr ?v\b", "zr-v"), (r"^hr ?v\b", "hr-v")],
    "Volkswagen": [(r"^t ?roc\b", "t-roc"), (r"^t ?cross\b", "t-cross")],
    "Nissan": [(r"^x ?trail\b", "x-trail"), (r"^tida\b", "tiida")],
    "Hyundai": [(r"^elentra\b", "elantra"), (r"^santafe\b", "santa")],
    "Renault": [(r"^megan\b", "megane")],
    "Suzuki": [(r"^(?:swfit|siwft)\b", "swift")],
    "Mitsubishi": [(r"^lancer (?:evo|evolution|\d\.\d evolution)", "lancer evo"), (r"^l ?200\b", "l200")],
    "Mercedes-Benz": [(r"^(gle|glc) coupe\b", r"\1 coupe")],
    "BMW": [(r"^m serisi (m\d)\b", r"\1"), (r"^z serisi (z\d)\b", r"\1"),
            (r"^(?:f10|f11|e60|e61)\b(?! m\d)", "5"), (r"^(?:f30|f31|e46|e90|e91|g20)\b(?! m\d)", "3"),
            (r"^(?:f20|f21|e87)\b(?! m\d)", "1")],
    "Ford": [(r"^transit custom", "transit custom"), (r"^transit courier", "transit courier"), (r"^fokus\b", "focus")],
    "Peugeot": [(r"^(20[678]|30[78]) (?:cc|cabrio)\b", r"\1 cc"), (r"^307 sw\b", "307 sw")],
    "Mini": [(r"^cooper (?:cabrio|clubman|countryman)\b", None)],  # aşağıda: "cooper <ek>"
}
_COMPILED = {brand: [(re.compile(p), t) for p, t in rules] for brand, rules in _RULES.items()}


def model_key(brand_norm: str | None, folded_model: str) -> str | None:
    """`folded_model` (hazırlanmış küçük harf metin) için kural anahtarı; kural yoksa None (çağıran eski ilk-kelime davranışını sürdürür)."""
    for pat, template in _COMPILED.get(brand_norm or "", ()):
        m = pat.match(folded_model)
        if not m:
            continue
        if template is None:  # Mini: "cooper cabrio" -> "cooper cabrio"
            return " ".join(folded_model.split()[:2])
        return m.expand(template)
    return None

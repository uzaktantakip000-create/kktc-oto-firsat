import json
import re

import httpx

URL = "https://openrouter.ai/api/v1/chat/completions"

CHECK_PROMPT = """Sen KKTC (Kuzey Kıbrıs) ikinci el araç piyasasını bilen bir uzmansın. Aşağıdaki ilan, benzer ilanlara göre
ucuz görünüyor. Gerçek bir fırsat mı, yoksa tuzak/hata (yanlış fiyat, hasar, eksik bilgi, galerici numarası) var mı?
Sadece şu JSON'u döndür, başka metin yazma:
{"gercek_firsat_mi": true/false, "risk_notlari": ["..."], "fiyat_yorumu": "1 cümle", "sorulacak_sorular": ["..."]}

İLAN:
%s

PİYASA ÖZETİ:
%s
"""


_PHONE = re.compile(r"(?<!\d)(?:\+|00)?\d(?:[\s.\-()]?\d){8,13}(?!\d)")


def mask_phones(text: str) -> str:
    """Satıcı telefonları üçüncü taraf modele gönderilmez. Fiyat/km (en çok 7 hane) etkilenmez."""
    return _PHONE.sub("[tel]", text)


def ask_json(api_key: str, model: str, prompt: str, timeout: int = 60) -> dict | None:
    r = httpx.post(
        URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.1},
        timeout=timeout,
    )
    if r.status_code != 200:
        return None
    text = r.json()["choices"][0]["message"]["content"] or ""
    m = re.search(r"\{.*\}", text, re.S)
    try:
        return json.loads(m.group()) if m else None
    except json.JSONDecodeError:
        return None


def check_deal(api_key: str, model: str, listing_text: str, market_summary: str) -> dict | None:
    return ask_json(api_key, model, CHECK_PROMPT % (mask_phones(listing_text), market_summary))

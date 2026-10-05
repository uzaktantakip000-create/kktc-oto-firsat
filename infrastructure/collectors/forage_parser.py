# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Kaynak: Forage v2.1.0 (https://github.com/jwmoss/forage, MPL-2.0, özgün yazar: jwmoss),
# dosya src/forage/parser.py, commit f28c7c5a7df76bf81a8c954a2bede26c62cda9e1.
# Alınanlar: parse_timestamp, _resolve_yearless, extract_post_id ve _article_content'in satır temizleme döngüsü (clean_parts).
# Değişiklikler (KKTC Oto Fırsat Sistemi, 05.10.2026): Playwright/rich bağımlılığı ve tarayıcıya bağlı işlevler alınmadı (yalnız
# saf işlevler); göreli zaman kalıpları RELATIVE_PATTERNS sabitine çıkarıldı ve is_relative eklendi; clean_parts yazar adı
# karşılaştırması yapmaz (yazar adı hiç okunmaz). Mantık başka yönden aynıdır.
# MPL-2.0 dosya düzeyindedir: bu dosya (ve ileride yapılacak değişiklikleri) MPL-2.0 altında kalır; Forage kodu yalnız bu
# dosyada tutulur, repodaki diğer dosyalar bu lisanstan etkilenmez.
"""Forage'dan alınan saf ayrıştırma işlevleri (tarayıcı gerektirmez, çevrimdışı test edilir).
Saat dilimi YOKTUR: parse_timestamp, verilen `now` ile aynı (saf/naive) duvar saatini döndürür; Asia/Famagusta'ya ve UTC'ye
çevirme infrastructure/collectors/facebook_browser.parse_fb_time'dadır."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import parse_qs, urlparse

# Forage parse_timestamp'taki sırayla: (kalıp, now'dan çıkarılacak süre)
RELATIVE_PATTERNS = (
    (r"(\d+)\s*(?:m|min|mins|minute|minutes)\b", lambda n: timedelta(minutes=n)),
    (r"(\d+)\s*(?:h|hr|hrs|hour|hours)\b", lambda n: timedelta(hours=n)),
    (r"(\d+)\s*(?:d|day|days)\b", lambda n: timedelta(days=n)),
    (r"(\d+)\s*(?:w|wk|wks|week|weeks)\b", lambda n: timedelta(weeks=n)),
    # Approximate long ranges; most scrapes target recent posts.
    (r"(\d+)\s*(?:mo|mos|month|months)\b", lambda n: timedelta(days=30 * n)),
    (r"(\d+)\s*(?:y|yr|yrs|year|years)\b", lambda n: timedelta(days=365 * n)),
)

UI_LINES = {"Like", "Comment", "Share", "Reply", "·"}


def _normalize(text: str) -> str:
    return re.sub(r"[  ]", " ", text or "").strip()


def is_relative(text: str) -> bool:
    """parse_timestamp metni `now`a göre süre olarak mı okur? ("Just now", "5m", "6h", "2d", saatsiz "Yesterday")."""
    lower_text = _normalize(text).lower()
    if not lower_text:
        return False
    if "just now" in lower_text or any(re.search(p, lower_text) for p, _ in RELATIVE_PATTERNS):
        return True
    return "yesterday" in lower_text and not re.search(
        r"yesterday\s*(?:at\s*)?(\d{1,2}(?::\d{2})?\s*[APap][Mm])", lower_text)


def _resolve_yearless(parsed: datetime, now: datetime) -> datetime:
    """Pick the year for a yearless date: posts can't be from the future."""
    if parsed <= now:
        return parsed
    try:
        return parsed.replace(year=parsed.year - 1)
    except ValueError:  # Feb 29 when last year wasn't a leap year
        return parsed.replace(year=parsed.year - 1, day=28)


def parse_timestamp(text: str, *, now: Optional[datetime] = None) -> Optional[datetime]:
    """
    Parse Facebook's relative/absolute timestamps to datetime.

    Handles formats like:
    - "2h" (2 hours ago)
    - "3d" (3 days ago)
    - "1w" (1 week ago)
    - "Yesterday at 3:45 PM"
    - "January 15 at 2:30 PM"
    - "January 15, 2024 at 2:30 PM"
    """
    if not text:
        return None

    text = _normalize(text)
    if not text:
        return None

    now = now or datetime.now()
    lower_text = text.lower()

    if "just now" in lower_text:
        return now

    for pattern, delta in RELATIVE_PATTERNS:
        match = re.search(pattern, lower_text)
        if match:
            return now - delta(int(match.group(1)))

    if "yesterday" in lower_text:
        time_match = re.search(
            r"yesterday\s*(?:at\s*)?(\d{1,2}(?::\d{2})?\s*[APap][Mm])",
            text,
            re.IGNORECASE,
        )
        if time_match:
            time_str = time_match.group(1).strip().upper()
            time_str = re.sub(r"\s*(AM|PM)$", r" \1", time_str)

            for time_fmt in ("%I:%M %p", "%I %p"):
                try:
                    parsed_time = datetime.strptime(time_str, time_fmt).time()
                except ValueError:
                    continue

                yesterday = (now - timedelta(days=1)).date()
                return datetime.combine(yesterday, parsed_time)

        return now - timedelta(days=1)

    date_formats = [
        "%A, %B %d, %Y at %I:%M %p",
        "%A, %b %d, %Y at %I:%M %p",
        "%a, %B %d, %Y at %I:%M %p",
        "%a, %b %d, %Y at %I:%M %p",
        "%B %d, %Y at %I:%M %p",
        "%b %d, %Y at %I:%M %p",
        "%B %d, %Y",
        "%b %d, %Y",
        "%d %b %Y",
        "%d %B %Y",
        "%m/%d/%Y",
        "%m/%d/%y",
    ]

    for fmt in date_formats:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue

    # Yearless month/day formats (avoid strptime default-year deprecation).
    month_day_at_match = re.match(
        r"^([A-Za-z]+\.?)\s+(\d{1,2})\s+at\s+(.+)$",
        text,
        re.IGNORECASE,
    )
    if month_day_at_match:
        month, day, time_part = month_day_at_match.groups()
        month = month.rstrip(".")
        time_str = time_part.strip().upper()
        time_str = re.sub(r"\s*(AM|PM)$", r" \1", time_str)

        candidate = f"{month} {int(day)} {now.year} at {time_str}"
        for fmt in (
            "%B %d %Y at %I:%M %p",
            "%B %d %Y at %I %p",
            "%b %d %Y at %I:%M %p",
            "%b %d %Y at %I %p",
        ):
            try:
                return _resolve_yearless(datetime.strptime(candidate, fmt), now)
            except ValueError:
                continue

    month_day_match = re.match(r"^([A-Za-z]+\.?)\s+(\d{1,2})$", text, re.IGNORECASE)
    if month_day_match:
        month, day = month_day_match.groups()
        month = month.rstrip(".")
        candidate = f"{month} {int(day)} {now.year}"
        for fmt in ("%B %d %Y", "%b %d %Y"):
            try:
                return _resolve_yearless(datetime.strptime(candidate, fmt), now)
            except ValueError:
                continue

    return None


def extract_post_id(url: str) -> Optional[str]:
    """Extract post ID from a Facebook URL."""
    if not url:
        return None

    parsed = urlparse(url)

    if "story_fbid" in url:
        params = parse_qs(parsed.query)
        story_fbid = params.get("story_fbid", [None])[0]
        if story_fbid:
            return story_fbid

    match = re.search(r"/posts/(\d+)", url)
    if match:
        return match.group(1)

    match = re.search(r"pfbid[a-zA-Z0-9]+", url)
    if match:
        return match.group(0)

    return None


def clean_parts(parts: list[str]) -> str:
    """Gönderi gövdesinin parçalarından arayüz kırıntısını atar (Forage _article_content temizleme döngüsü)."""
    content: list[str] = []
    for part in parts:
        cleaned = re.sub(r"\s*…?\s*See more\s*$", "", part).strip()
        if not cleaned or cleaned in UI_LINES:
            continue
        if re.fullmatch(r"\d+[hdwm]", cleaned):
            continue
        if cleaned not in content:
            content.append(cleaned)
    return "\n".join(content)

"""Sosyal medya işçisinin yerel dosyaları (VPS, deneme kipi): durum (JSON), deneme çıktısı (JSONL), kaynak listesi (CSV).
Hepsi repo DIŞINDA durur (/var/lib/kktc-social, /etc/kktc-social); dosyalar yalnız sahibin okuyabileceği izinle (600) yazılır.
Kaynak listesi gizli grup adreslerini içerir: GitHub'a girmez, günlüğe yalnız takma ad (alias) yazılır."""
import csv
import json
import os
import re
import tempfile
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from application.social_port import PLATFORMS, SocialPost, SocialSource


class FileStateStore:
    """StateStore'un dosya hâli (Repository.get_state/set_state ile aynı imza). Her yazım geçici dosya + rename ile atomiktir:
    yarıda kesilen yazım freni ya da imleci bozamaz. Bozuk dosya sessizce sıfırlanmaz (fren kaybolmasın): hata verir."""

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def _load(self) -> dict[str, str]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        try:
            data = json.loads(raw) if raw.strip() else {}
        except ValueError as e:
            raise RuntimeError(f"durum dosyası bozuk: {self.path} (elle kontrol et; silinirse fren ve imleçler kaybolur)") from e
        if not isinstance(data, dict):
            raise RuntimeError(f"durum dosyası beklenen biçimde değil: {self.path}")
        return data

    def get_state(self, key: str, default: str | None = None) -> str | None:
        return self._load().get(key, default)

    def set_state(self, key: str, value: str) -> None:
        data = self._load()
        data[key] = value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def keys(self, prefix: str = "") -> list[str]:
        return sorted(k for k in self._load() if k.startswith(prefix))


def _jsonable(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


_LISTING_FIELDS = ("brand", "model", "year", "km", "price_amount", "currency", "price_gbp", "extraction_by")


class JsonlTrialSink:
    """Deneme kipi çıktısı: her gönderi trial/<platform>-YYYYMMDD.jsonl dosyasına tek satır (ilan olsun olmasın). Veritabanına yazılmaz.
    Aynı gönderi ikinci kez yazılmaz (kimlikler platformda tekildir; bilinenler mevcut deneme dosyalarından okunur).
    Telefon ayrı alan olarak yazılmaz; gönderi metni (text) değerlendirme için olduğu gibi durur (dosya 600, 14 günde silinir)."""

    def __init__(self, dir: Path | str, platform: str, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 aliases: dict[str, str] | None = None):
        self.dir, self.platform, self.clock = Path(dir), platform, clock
        self.aliases = dict(aliases or {})  # source_id -> alias (yalnız upsert_listing yolu için)
        self._known: set[str] | None = None

    def _known_ids(self) -> set[str]:
        if self._known is None:
            ids: set[str] = set()
            for f in sorted(self.dir.glob(f"{self.platform}-*.jsonl")):
                for line in f.read_text(encoding="utf-8").splitlines():
                    try:
                        ids.add(str(json.loads(line)["post_id"]))
                    except (ValueError, KeyError, TypeError):
                        continue  # yarım satır (kesilen yazım) okumayı bozmasın
            self._known = ids
        return self._known

    def known_item_ids(self, source_id) -> set[str]:
        return set(self._known_ids())

    def _write(self, row: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.dir / f"{self.platform}-{self.clock():%Y%m%d}.jsonl"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(json.dumps({k: _jsonable(v) for k, v in row.items()}, ensure_ascii=False) + "\n")
        self._known_ids().add(str(row["post_id"]))

    def _row(self, alias: str, post_id: str, url, posted_at, image_url, data: dict, reason: str | None, text) -> dict:
        return {"platform": self.platform, "alias": alias, "post_id": post_id, "url": url, "posted_at": posted_at,
                "seen_at": self.clock(), "has_image": bool(image_url), "image_url": image_url,
                "outcome": "ilan" if reason is None else "ilan_degil", "reason": reason,
                **{k: data.get(k) for k in _LISTING_FIELDS}, "text": text}

    def record_post(self, alias: str, post: SocialPost, data: dict | None, reason: str | None) -> bool:
        """reason None = ilan. Dönüş: yeni ilan yazıldı mı (ilan olmayan ya da zaten yazılmış gönderi False)."""
        if post.post_id in self._known_ids():
            return False
        self._write(self._row(alias, post.post_id, post.url, post.posted_at, post.image_url, data or {}, reason, post.text))
        return reason is None

    def upsert_listing(self, source_id, item_id: str, data: dict) -> bool:
        """ListingSink imzası (Repository ile aynı): yalnız satırı olan ilan; ayrıntılı yol record_post'tur."""
        if item_id in self._known_ids():
            return False
        photos = data.get("photo_urls") or []
        listing = bool(data.get("extraction_by")) and data.get("is_active", True) is not False
        self._write(self._row(self.aliases.get(str(source_id), ""), item_id, data.get("url"), data.get("posted_at"),
                              photos[0] if photos else None, data, None if listing else "okunamadi", data.get("raw_text")))
        return listing


_DATED = re.compile(r"-(\d{8})(?:-\d{4,6})?\.jsonl?$")


def delete_old_trial_files(dir: Path | str, days: int = 14, now: datetime | None = None) -> int:
    """Adındaki tarihe göre `days` günden eski deneme dosyalarını (jsonl, karşılaştırma json) siler. Silinen dosya sayısı."""
    d = Path(dir)
    if not d.is_dir():
        return 0
    today = (now or datetime.now(timezone.utc)).date()
    n = 0
    for f in d.iterdir():
        m = _DATED.search(f.name)
        if not (f.is_file() and m):
            continue
        try:
            day = date(int(m.group(1)[:4]), int(m.group(1)[4:6]), int(m.group(1)[6:]))
        except ValueError:
            continue
        if today - day > timedelta(days=days):
            f.unlink()
            n += 1
    return n


class SourcesFileError(ValueError):
    pass


COLUMNS = ("platform", "key", "url", "alias", "slug", "priority", "default_steering", "active")
_TRUE, _FALSE = {"1", "true", "evet", "e", "yes", "y", "açık", "acik"}, {"0", "false", "hayır", "hayir", "h", "no", "n", "kapalı", "kapali", ""}
_ALIAS = re.compile(r"^[\w.-]{1,32}$")
_IG_USER = re.compile(r"^[a-z0-9._]{1,30}$")


def _source(row: dict[str, str], platform: str) -> SocialSource:
    """Tek satır -> SocialSource. Hata metni değeri DEĞİL sütunu söyler (gizli adres/kimlik günlüğe düşmesin)."""
    key, url, alias = row["key"], row["url"], row["alias"]
    if platform == "facebook":
        if not key.isdigit():
            raise ValueError("key: Facebook'ta grubun sayısal kimliği olmalı")
        if not re.match(r"^https://(www\.|m\.)?facebook\.com/groups/", url):
            raise ValueError("url: https://www.facebook.com/groups/... biçiminde olmalı")
    else:
        key = key.lower().lstrip("@")
        if not _IG_USER.match(key):
            raise ValueError("key: Instagram kullanıcı adı olmalı (harf, rakam, nokta, alt çizgi)")
        if not re.match(r"^https://(www\.)?instagram\.com/", url):
            raise ValueError("url: https://www.instagram.com/... biçiminde olmalı")
    if not _ALIAS.match(alias):
        raise ValueError("alias: boşluksuz kısa takma ad olmalı (en çok 32 karakter: harf, rakam, - _ .)")
    if alias.lower() in {key.lower(), (row["slug"] or "").lower()}:
        raise ValueError("alias: kaynağın kimliği/kısa adıyla aynı olamaz (takma ad günlüğe yazılır)")
    try:
        priority = int(row["priority"]) if row["priority"] else 100
    except ValueError:
        raise ValueError("priority: tam sayı olmalı (küçük = önce)") from None
    steering = (row["default_steering"] or "").upper() or None
    if steering not in (None, "LHD", "RHD"):
        raise ValueError("default_steering: boş, LHD ya da RHD olmalı")
    return SocialSource(platform=platform, key=key, url=url, alias=alias, slug=row["slug"] or None,
                        priority=priority, default_steering=steering)


def load_sources(csv_path: Path | str, platform: str) -> list[SocialSource]:
    """Kaynak listesi (repo DIŞINDA): platform,key,url,alias,slug,priority,default_steering,active. Pasifler atlanır;
    '#' ile başlayan satır yorumdur. Hatalı satır varsa hiçbiri yüklenmez (hepsi tek hata metninde, satır numarasıyla)."""
    if platform not in PLATFORMS:
        raise SourcesFileError(f"bilinmeyen platform: {platform}")
    path = Path(csv_path)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise SourcesFileError(f"kaynak dosyası yok: {path}") from None
    numbered = [(i, ln) for i, ln in enumerate(text.splitlines(), start=1) if ln.strip() and not ln.lstrip().startswith("#")]
    reader = csv.DictReader([ln for _, ln in numbered])
    missing = [c for c in COLUMNS if c not in (reader.fieldnames or [])]
    if missing:
        raise SourcesFileError(f"kaynak dosyasında sütun eksik: {', '.join(missing)} (gereken: {','.join(COLUMNS)})")
    out: list[SocialSource] = []
    errors: list[str] = []
    for (n, _), row in zip(numbered[1:], reader):  # n: dosyadaki gerçek satır numarası
        row = {k: (row.get(k) or "").strip() for k in COLUMNS}
        if row["platform"].lower() not in PLATFORMS:
            errors.append(f"satır {n}: platform facebook ya da instagram olmalı")
            continue
        if row["platform"].lower() != platform:
            continue
        active = row["active"].lower()
        if active not in _TRUE | _FALSE:
            errors.append(f"satır {n}: active 1/0 (evet/hayır) olmalı")
            continue
        if active in _FALSE:
            continue
        try:
            out.append(_source(row, platform))
        except ValueError as e:
            errors.append(f"satır {n}: {e}")
    for field, label in (("key", "kimlik"), ("alias", "takma ad")):
        seen: set[str] = set()
        for s in out:
            v = getattr(s, field).lower()
            if v in seen:
                errors.append(f"aynı {label} iki kez kullanılmış ({platform}" + (f": {s.alias})" if field == "alias" else ")"))
            seen.add(v)
    if errors:
        raise SourcesFileError("kaynak dosyası hatalı:\n  " + "\n  ".join(errors))
    return out

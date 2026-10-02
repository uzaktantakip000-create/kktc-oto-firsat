"""Apify aktör çalıştırması: başlat, bekle, maliyeti HER durumda ölç.
`ActorClient.call` çalıştırma yarıda kesilirse (ağ hatası, süre aşımı) maliyeti bilmeden hata atar; harcama kaydı o zaman kayboluyordu.
Burada çalıştırma `start` ile açılır, hata olursa `ApifyRunError.cost_usd` ile gerçek (ya da en az başlangıç) ücret taşınır."""
from datetime import timedelta
from typing import Any

from apify_client import ApifyClient

TERMINAL = ("SUCCEEDED", "FAILED", "TIMED-OUT", "ABORTED")
GRACE = timedelta(seconds=90)  # aktörün kendi süre sınırından sonra durumu almak için pay


class ApifyRunError(RuntimeError):
    """Çalıştırma başladıktan sonraki hata. cost_usd: bu çalıştırmanın (bilinen/tahmini) ücreti; çağıran harcamaya yazmalı."""

    def __init__(self, message: str, cost_usd: float = 0.0):
        super().__init__(message)
        self.cost_usd = cost_usd


def run_cost(run: Any, minimum: float = 0.0) -> float:
    """Çalıştırma nesnesinden ücret (usage_total_usd, yoksa usage_usd); bilinmiyorsa `minimum`."""
    value = 0.0
    for attr in ("usage_total_usd", "usage_usd"):
        raw = getattr(run, attr, None)
        if isinstance(raw, (int, float)) and raw > 0:
            value = float(raw)
            break
    return max(value, minimum)


def run_actor(client: ApifyClient, actor: str, run_input: dict, *, run_timeout: timedelta, min_cost: float = 0.0,
              label: str = "", **start_kwargs):
    """Aktörü çalıştırır, bitmesini en çok run_timeout + 90 sn bekler. Döner: bitmiş çalıştırma nesnesi (SUCCEEDED/TIMED-OUT/...).
    Bitmeden bırakılırsa çalıştırma iptal edilir ve ApifyRunError (maliyetle) atılır. Her çalıştırmanın maliyeti satır olarak yazılır."""
    run = client.actor(actor).start(run_input=run_input, run_timeout=run_timeout, **start_kwargs)
    run_client = client.run(run.id)
    try:
        finished = run_client.wait_for_finish(wait_duration=run_timeout + GRACE)
    except Exception as e:  # ağ kesintisi vb.: çalıştırma sürüyor olabilir, ücreti kaybetme
        cost = _abort_and_cost(run_client, run, min_cost)
        print(f"Apify {label or actor}: bekleme hatası {type(e).__name__}, maliyet=${cost:.4f}")
        raise ApifyRunError(f"Apify çalıştırması beklenirken hata ({type(e).__name__})", cost) from e
    finished = finished or run
    status = str(getattr(finished, "status", "") or "")
    if status not in TERMINAL:
        cost = _abort_and_cost(run_client, finished, min_cost)
        print(f"Apify {label or actor}: süre doldu (durum={status}), iptal edildi, maliyet=${cost:.4f}")
        raise ApifyRunError("Apify çalıştırması süre sınırında bitmedi", cost)
    print(f"Apify {label or actor}: durum={status} maliyet=${run_cost(finished, min_cost):.4f}")
    return finished


def _abort_and_cost(run_client, run: Any, minimum: float) -> float:
    latest = run
    try:
        latest = run_client.abort() or run
    except Exception:  # iptal başarısız olsa da elimizdeki bilgiyle maliyet yazılır
        try:
            latest = run_client.get() or run
        except Exception:
            pass
    return run_cost(latest, minimum)

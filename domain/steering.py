"""İlan metninden direksiyon yönü (RHD = sağ, LHD = sol). Belirsizse None — varsayım yapılmaz."""
import re

_LHD = re.compile(r"\bsol\s+(direksiyon|dumen|dümen)|\blhd\b|left[\s-]*hand", re.I)
_RHD = re.compile(r"\bsa[ğg]\s+(direksiyon|dumen|dümen)|\brhd\b|right[\s-]*hand", re.I)


def steering_from_text(text: str | None) -> str | None:
    if not text:
        return None
    lhd, rhd = bool(_LHD.search(text)), bool(_RHD.search(text))
    if lhd and not rhd:
        return "LHD"
    if rhd and not lhd:
        return "RHD"
    return None  # hiçbiri ya da çelişkili

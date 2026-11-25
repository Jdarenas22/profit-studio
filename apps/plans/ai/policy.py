"""Una política de acceso para Gemini real, independiente del tipo de cuenta."""

FREE_MODELS = frozenset({"gemini-3.5-flash-lite"})


def configuration_errors(settings):
    missing = [
        name
        for name in ("GEMINI_API_KEY", "GEMINI_MODEL")
        if not str(getattr(settings, name, "") or "").strip()
    ]
    tier = getattr(settings, "GEMINI_ACCESS_TIER", "free")
    if tier == "free":
        if not getattr(settings, "GEMINI_FREE_TIER_CONFIRMED", False):
            missing.append("GEMINI_FREE_TIER_CONFIRMED=true")
        if getattr(settings, "GEMINI_BILLING_CONFIRMED", False):
            missing.append("GEMINI_BILLING_CONFIRMED=false para Free")
        if getattr(settings, "GEMINI_MODEL", "") not in FREE_MODELS:
            missing.append("GEMINI_MODEL compatible con Free")
    elif tier == "paid":
        if not getattr(settings, "GEMINI_BILLING_CONFIRMED", False):
            missing.append("GEMINI_BILLING_CONFIRMED=true")
    else:
        missing.append("GEMINI_ACCESS_TIER=free o paid")
    limit = getattr(settings, "GEMINI_MAX_DAILY_CALLS", 30)
    if type(limit) is not int or not 1 <= limit <= 10000:
        missing.append("GEMINI_MAX_DAILY_CALLS entre 1 y 10000")
    return missing

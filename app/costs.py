"""The Costs page: what each retreat cost, by part and by company, from the call log
(every model, search and conversation call is logged in llm_calls) and the recorded
voices (characters per clip)."""

from . import pricing, search, tts

SECTIONS = {
    "plan": "Planning", "heart": "For the heart", "deep": "Deep dive", "search_queries": "Research",
    "research": "Research", "guide": "Spoken guidance", "talk": "Talk it over",
}
SECTION_ORDER = ["Planning", "For the heart", "Deep dive", "Research", "Spoken guidance", "Voices", "Talk it over", "Other"]
VENDORS = {"openrouter": "Anthropic (via OpenRouter)", "anthropic": "Anthropic", "jetstream": "Jetstream (academic, free)",
           "openai": "OpenAI GPT-Live", "xai": "xAI Grok voice"}


def vendor(provider: str | None) -> str:
    return VENDORS.get(provider or "") or search.PROVIDERS.get(provider or "") or (provider or "unknown")


def _add(bucket: dict, key: str, usd: float, **counts) -> None:
    b = bucket.setdefault(key, {"usd": 0.0, "calls": 0})
    b["usd"] += usd or 0.0
    b["calls"] += 1
    for k, v in counts.items():
        b[k] = b.get(k, 0) + (v or 0)


def _voices(retreat: dict) -> dict:
    """Characters in the recordings the retreat has now, by tier."""
    chars = {"free": 0, "premium": 0}
    for day in retreat.get("days", {}).values():
        for group in ("tracks", "guide"):
            for clip in day.get(group, {}).values():
                if clip.get("status") == "ready":
                    try:
                        tier = tts.tier_of(clip["voice"])
                    except (tts.TTSError, KeyError):
                        tier = "premium"
                    chars[tier] += clip.get("characters", 0)
    return chars


def report(retreats: list[dict], rows: list[dict]) -> dict:
    by_retreat: dict[str, list[dict]] = {}
    other: list[dict] = []
    for r in rows:
        (by_retreat.setdefault(r["retreat_id"], []) if r.get("retreat_id") else other).append(r)

    out, all_vendors, grand = [], {}, 0.0
    for retreat in retreats:
        sections: dict = {}
        vendors: dict = {}
        for r in by_retreat.pop(retreat["id"], []):
            section = SECTIONS.get(r.get("purpose"), "Other")
            counts = {"input_tokens": r.get("input_tokens"), "output_tokens": r.get("output_tokens"),
                      "searches": r.get("web_searches"), "seconds": (r.get("duration_ms") or 0) / 1000 if section == "Talk it over" else 0}
            _add(sections, section, r.get("usd") or 0.0, **counts)
            _add(vendors, vendor(r.get("provider")), r.get("usd") or 0.0, **counts)
        chars = _voices(retreat)
        voice_usd = pricing.tts_usd(chars["premium"], "premium")
        if chars["premium"]:
            sections["Voices"] = {"usd": voice_usd, "characters": chars["premium"], "calls": 0}
            vendors["ElevenLabs"] = {"usd": voice_usd, "characters": chars["premium"], "calls": 0}
        if chars["free"]:
            sections.setdefault("Voices", {"usd": 0.0, "calls": 0})["free_characters"] = chars["free"]
            vendors["Microsoft voices (free)"] = {"usd": 0.0, "characters": chars["free"], "calls": 0}
        total = sum(v["usd"] for v in sections.values())
        grand += total
        for name, v in vendors.items():
            all_vendors[name] = all_vendors.get(name, 0.0) + v["usd"]
        plan = retreat.get("plan") or {}
        out.append({
            "id": retreat["id"], "title": plan.get("title") or retreat.get("filename"), "created_at": retreat.get("created_at"),
            "model": retreat.get("model"), "days": len(retreat.get("days", {})), "total_usd": round(total, 4),
            "sections": [{"name": n, **_round(sections[n])} for n in SECTION_ORDER if n in sections],
            "vendors": [{"name": n, **_round(v)} for n, v in sorted(vendors.items(), key=lambda kv: -kv[1]["usd"])],
        })
    # Calls for retreats since deleted, and calls tied to no retreat (About me summaries, conversation memory).
    leftovers = other + [r for rs in by_retreat.values() for r in rs]
    extra: dict = {}
    for r in leftovers:
        _add(extra, vendor(r.get("provider")), r.get("usd") or 0.0)
    extra_usd = sum(v["usd"] for v in extra.values())
    for name, v in extra.items():
        all_vendors[name] = all_vendors.get(name, 0.0) + v["usd"]
    return {
        "retreats": sorted(out, key=lambda r: -(r["created_at"] or 0)),
        "other": {"usd": round(extra_usd, 4), "vendors": [{"name": n, **_round(v)} for n, v in extra.items()]},
        "total_usd": round(grand + extra_usd, 4),
        "by_vendor": [{"name": n, "usd": round(u, 4)} for n, u in sorted(all_vendors.items(), key=lambda kv: -kv[1])],
    }


def _round(d: dict) -> dict:
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in d.items()}

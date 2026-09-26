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
    """Each retreat by part and by company, what isn't tied to a retreat, and totals."""
    by_retreat, other = _group_rows(rows)
    out, all_vendors, grand = [], {}, 0.0
    for retreat in retreats:
        entry, sections, vendors = _retreat_costs(retreat, by_retreat.pop(retreat["id"], []))
        out.append(entry)
        grand += sum(v["usd"] for v in sections.values())
        _add_to_totals(all_vendors, vendors)
    # Calls for retreats since deleted, and calls tied to no retreat (About me summaries, conversation memory).
    extra = _by_vendor(other + [r for rs in by_retreat.values() for r in rs])
    extra_usd = sum(v["usd"] for v in extra.values())
    _add_to_totals(all_vendors, extra)
    return {
        "retreats": sorted(out, key=lambda r: -(r["created_at"] or 0)),
        "other": {"usd": round(extra_usd, 4), "vendors": [{"name": n, **_round(v)} for n, v in extra.items()]},
        "total_usd": round(grand + extra_usd, 4),
        "by_vendor": [{"name": n, "usd": round(u, 4)} for n, u in sorted(all_vendors.items(), key=lambda kv: -kv[1])],
    }


def _group_rows(rows: list[dict]) -> tuple[dict[str, list[dict]], list[dict]]:
    """Logged calls by retreat, and those tied to none."""
    by_retreat: dict[str, list[dict]] = {}
    other: list[dict] = []
    for r in rows:
        if r.get("purpose") in ("voice", "step"):  # voices are counted from the recordings; steps cost nothing
            continue
        (by_retreat.setdefault(r["retreat_id"], []) if r.get("retreat_id") else other).append(r)
    return by_retreat, other


def _retreat_costs(retreat: dict, rows: list[dict]) -> tuple[dict, dict, dict]:
    """One retreat's entry for the page, with its costs by part and by company."""
    sections: dict = {}
    vendors: dict = {}
    for r in rows:
        section = SECTIONS.get(r.get("purpose"), "Other")
        counts = {"input_tokens": r.get("input_tokens"), "output_tokens": r.get("output_tokens"),
                  "searches": r.get("web_searches"),
                  "seconds": (r.get("duration_ms") or 0) / 1000 if section == "Talk it over" else 0}
        _add(sections, section, r.get("usd") or 0.0, **counts)
        _add(vendors, vendor(r.get("provider")), r.get("usd") or 0.0, **counts)
    _add_voices(retreat, sections, vendors)
    plan = retreat.get("plan") or {}
    entry = {
        "id": retreat["id"], "title": plan.get("title") or retreat.get("filename"), "created_at": retreat.get("created_at"),
        "model": retreat.get("model"), "days": len(retreat.get("days", {})),
        "total_usd": round(sum(v["usd"] for v in sections.values()), 4),
        "sections": [{"name": n, **_round(sections[n])} for n in SECTION_ORDER if n in sections],
        "vendors": [{"name": n, **_round(v)} for n, v in sorted(vendors.items(), key=lambda kv: -kv[1]["usd"])],
    }
    return entry, sections, vendors


def _add_voices(retreat: dict, sections: dict, vendors: dict) -> None:
    """Voices come from the recordings the retreat has now, not from the call log."""
    chars = _voices(retreat)
    voice_usd = pricing.tts_usd(chars["premium"], "premium")
    if chars["premium"]:
        sections["Voices"] = {"usd": voice_usd, "characters": chars["premium"], "calls": 0}
        vendors["ElevenLabs"] = {"usd": voice_usd, "characters": chars["premium"], "calls": 0}
    if chars["free"]:
        sections.setdefault("Voices", {"usd": 0.0, "calls": 0})["free_characters"] = chars["free"]
        vendors["Microsoft voices (free)"] = {"usd": 0.0, "characters": chars["free"], "calls": 0}


def _by_vendor(rows: list[dict]) -> dict:
    out: dict = {}
    for r in rows:
        _add(out, vendor(r.get("provider")), r.get("usd") or 0.0)
    return out


def _add_to_totals(totals: dict, vendors: dict) -> None:
    for name, v in vendors.items():
        totals[name] = totals.get(name, 0.0) + v["usd"]


def _round(d: dict) -> dict:
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in d.items()}

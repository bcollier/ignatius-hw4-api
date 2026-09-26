def context(retreat: dict | None, about: str, notes: str, history: dict | None = None, local_time: str | None = None) -> str:
    """What the companion knows: the person's notes and wishes, the time where they
    are, past conversations, and the retreat with what they've listened to, including
    what's new since they last talked."""
    now, when = _when(local_time)
    parts = [COMPANION, prompts.BACKGROUND, "Right now: " + when]
    if about.strip():
        parts.append(prompts.person_block(about))
    if notes.strip():
        parts.append("What they've said they want from this conversation companion:\n<wants>\n" + notes.strip() + "\n</wants>")

    history = history or {}
    past = history.get("conversations", [])
    last = past[-1] if past else None
    last_time = last.get("ended_at") or last.get("started_at") if last else None
    if last:
        gap = _days_between(last_time, now)
        ago = "earlier today" if gap == 0 else "yesterday" if gap == 1 else f"{gap} days ago"
        parts.append(f"Your last conversation with them was {ago} ({str(last_time)[:10]}), about {last.get('retreat_title') or 'their prayer'}. "
                     "Pick up naturally from it if it helps; don't recite it back.")
    else:
        parts.append("This is your first conversation with them.")
    if history.get("memory"):
        parts.append("What you remember from earlier conversations (a summary):\n<memory>\n" + history["memory"] + "\n</memory>")
    recent = past[-3:]
    if recent:
        blocks = []
        for c in recent:
            blocks.append(f"[{str(c.get('started_at'))[:16]} · {c.get('retreat_title') or 'no retreat'} · {round((c.get('seconds') or 0) / 60)} min]\n"
                          + (c.get("transcript") or "")[-4000:])
        parts.append("Your most recent conversations with them (transcripts, newest last):\n<recent>\n" + "\n\n".join(blocks) + "\n</recent>")

    if retreat and retreat.get("plan"):
        plan = retreat["plan"]
        lines = [f"The retreat they're making: {plan['title']}. {plan.get('summary', '')}"]
        if retreat.get("start_date"):
            day_no = _days_between(retreat["start_date"], now)
            if day_no is not None:
                lines.append(f"They started it on {retreat['start_date']}, so by the calendar today is day {day_no + 1} of {len(plan['days'])}.")
        since = []
        for d in plan["days"]:
            st = retreat["days"].get(str(d["day"]), {})
            listening = st.get("listening") or {}
            touched = st.get("prayed_at") or listening.get("updated_at")
            if st.get("prayed_at"):
                done = f"prayed ({st['prayed_at'][:10]})"
            elif listening.get("parts_played"):
                done = f"started ({str(listening.get('updated_at'))[:10]}), stopped at {listening.get('last_part') or 'part way'}"
            else:
                done = "not listened to yet"
            if touched and last_time and str(touched) > str(last_time):
                since.append(f"Day {d['day']}")
            lines.append(f"\nDay {d['day']}: {d['title']} ({d.get('source_ref', '')}). Grace: {d.get('grace', '')}. Status: {done}.")
            lines.append(f"Passage: {d.get('passage_text', '')[:900]}")
            heart = (st.get("tracks", {}).get("heart") or {}).get("script", "")
            if heart:
                lines.append(f"The reflection they heard began: {heart[:500]}")
            journal = st.get("journal") or {}
            if journal.get("word"):
                lines.append(f"The word that stayed with them: {journal['word']}")
            if journal.get("note"):
                lines.append(f"Their note: {journal['note'][:400]}")
        if last:
            lines.insert(1, ("Since your last conversation they have listened to or prayed " + ", ".join(since) + ".")
                         if since else "They haven't listened to any days of this retreat since your last conversation.")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)[:60_000]


# ---------------------------------------------------------------- memory of past conversations
# Saved in the person's storage folder (Supabase Storage in production) as
# conversations.json: {"memory": summary of older talks, "conversations": [...]}.


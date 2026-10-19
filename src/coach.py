"""Plain-language coach notes written from the measured angles and the plan (offline)."""

SIGNATURE = "Created with love for the passion of triathlon by Pruthvi"


def coach_notes(recommendations, profile_name="Road"):
    """Readable notes that walk the rider through their adjustment plan."""
    lines = [f"Position summary for {profile_name}."]
    if not recommendations:
        lines.append("All measured angles are within the typical range for this riding style. "
                     "If you're comfortable and pain-free, leave it alone. Great fit!")
    else:
        lines.append("Here is what to work on, in order of importance:")
        for i, r in enumerate(recommendations, 1):
            lines.append(f"{i}. {r['title']}. {r['detail']}")
    lines.append("Golden rule: change one thing at a time, in small steps, ride for a few days, "
                 "then re-test. Comfort and pain-free riding always beat textbook numbers.")
    return "\n\n".join(lines)

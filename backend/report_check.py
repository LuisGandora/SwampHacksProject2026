"""Is a crew hazard report specific enough to alert a site right away?

A report with a photo, or severity 4-5, always alerts at once: a real emergency is never held back. Anything else that
is too vague to act on ("Stuff", "Yo", "I am here") or doesn't mention its hazard at all is saved with status
'needs_review' and a reason. It raises no site alert and no Discord/Slack post until a supervisor approves it.
"""
import re

FILLER = {"i", "im", "am", "is", "are", "was", "be", "here", "there", "the", "a", "an", "and", "or", "of", "to", "at",
          "in", "on", "it", "its", "this", "that", "yo", "hi", "hello", "hey", "test", "testing", "stuff", "thing",
          "things", "something", "some", "big", "came", "up", "lol", "ok", "okay", "present", "pls", "please", "look"}
MIN_WORDS = 3
# Words (prefixes) that show a report is about its category; categories not listed are only checked for length.
KEYWORDS = {
    "wildlife": ["gator", "alligator", "croc", "snake", "moccasin", "cottonmouth", "rattle", "bear", "hog", "boar",
                 "dog", "wasp", "bee", "hornet", "ant", "spider", "animal", "deer", "coyote", "panther", "bird", "nest"],
    "flooding": ["flood", "water", "standing", "submerg", "overflow", "puddl", "wash", "pond", "creek", "river"],
    "tidal_flooding": ["tide", "flood", "water", "surge"],
    "lightning": ["lightning", "thunder", "strike", "storm"],
    "extreme_heat": ["heat", "hot", "temperature", "sun", "dizz", "faint", "cramp", "dehydrat", "exhaust"],
    "extreme_cold": ["cold", "ice", "icy", "freez", "frost", "snow", "hypotherm", "chill"],
    "high_wind": ["wind", "gust", "blow", "blew", "debris"],
    "severe_storm": ["storm", "hail", "tornado", "wind", "thunder", "lightning", "rain"],
    "tropical": ["hurricane", "tropical", "storm", "surge", "wind"],
    "wildfire": ["fire", "smoke", "burn", "flame", "ash"],
    "soil_saturation": ["mud", "soft", "stuck", "sink", "saturat", "trench", "wet", "ground", "soil"],
    "dense_fog": ["fog", "visib", "smoke", "haze", "see"],
    "coastal": ["surf", "wave", "rip", "beach", "tide"],
    "slip_trip": ["slip", "trip", "spill", "wet", "oil", "grease", "debris", "trash", "cord", "hose", "cable", "floor",
                  "walkway", "ice", "puddl", "fell", "fall"],
    "ppe": ["ppe", "helmet", "hard hat", "hardhat", "glove", "glasses", "goggle", "vest", "harness", "boot", "mask",
            "respirator", "ear", "protect"],
    "heavy_machinery": ["crane", "lift", "forklift", "excavat", "truck", "loader", "dozer", "machine", "equipment",
                        "vehicle", "backhoe", "boom", "rig"],
}


def review_reason(category, description, photo_url=None, severity=None, label=None):
    """None if the report should alert right away, else a short reason for the supervisor."""
    if photo_url or (severity or 0) >= 4:
        return None
    text = (description or "").lower()
    words = [w for w in re.findall(r"[a-z]{2,}", text) if w not in FILLER]
    if len(words) < MIN_WORDS:
        return "too vague to act on: add what you see and where (or a photo)"
    keys = KEYWORDS.get(category)
    if keys and not any(k in text for k in keys):
        return f"doesn't describe a {(label or category.replace('_', ' ')).lower()} hazard"
    return None


if __name__ == "__main__":
    assert review_reason("wildlife", "Stuff")
    assert review_reason("wildlife", "Yo jovon is here")
    assert review_reason("wildlife", "Something big came up")
    assert review_reason("wildlife", "Pls look ip")
    assert review_reason("wildlife", "Large alligator sunning by the east fence gate") is None
    assert review_reason("slip_trip", "Hydraulic oil spill across the walkway by panel 3") is None
    assert review_reason("wildlife", "Stuff", photo_url="https://x/p.jpg") is None   # a photo always alerts
    assert review_reason("flooding", "help", severity=5) is None                     # so does severity 4-5
    assert review_reason("heavy_machinery", "Someone left the gate open all night") == \
        "doesn't describe a heavy machinery hazard"
    print("report check ok")

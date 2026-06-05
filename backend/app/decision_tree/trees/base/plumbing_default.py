TREE_KEY = "plumbing_missed_call_recovery"
TREE_VERSION = "2026-06-05"

REQUIRED_FACTS = ("location", "job_type", "urgency")

OPT_OUT_EXACT = {"stop", "stopall", "unsubscribe", "cancel", "end", "quit"}
OPT_OUT_PHRASES = {
    "stop texting",
    "do not text",
    "dont text",
    "don't text",
    "remove me",
    "take me off",
}

WRONG_NUMBER_PHRASES = {
    "wrong number",
    "wrong #",
    "not my number",
    "you have the wrong",
    "who is this",
}

NO_LONGER_NEEDED_EXACT = {
    "no",
    "nope",
    "nah",
    "no thanks",
    "no thank you",
    "all good",
    "im good",
    "i'm good",
}
NO_LONGER_NEEDED_PHRASES = {
    "already handled",
    "already fixed",
    "found someone",
    "no longer need",
    "don't need",
    "dont need",
    "not needed",
    "got it fixed",
}

YES_PHRASES = {
    "yes",
    "yeah",
    "yep",
    "ya",
    "please",
    "need help",
    "still need",
    "call me",
}

JOB_TYPE_KEYWORDS = {
    "leak_or_pipe_repair": (
        "leak",
        "leaky",
        "leaking",
        "pipe",
        "burst",
        "flood",
        "flooding",
        "dripping",
        "water damage",
    ),
    "drain_or_sewer": (
        "drain",
        "clog",
        "clogged",
        "blocked",
        "backup",
        "backed up",
        "sewer",
        "toilet",
    ),
    "water_heater": (
        "water heater",
        "hot water",
        "tankless",
        "boiler",
    ),
    "fixture_or_install": (
        "install",
        "installation",
        "faucet",
        "sink",
        "shower",
        "bathtub",
        "fixture",
        "garburator",
    ),
    "quote_or_estimate": (
        "quote",
        "estimate",
        "pricing",
        "price",
        "cost",
        "how much",
    ),
}

URGENCY_KEYWORDS = {
    "emergency": (
        "emergency",
        "urgent",
        "asap",
        "right now",
        "now",
        "flood",
        "flooding",
        "burst",
        "sewage",
        "sewer backup",
        "no water",
        "water everywhere",
    ),
    "today": (
        "today",
        "tonight",
        "this morning",
        "this afternoon",
        "same day",
    ),
    "scheduled": (
        "tomorrow",
        "this week",
        "next week",
        "not urgent",
        "when available",
        "whenever",
    ),
    "quote": (
        "quote",
        "estimate",
        "pricing",
        "price",
        "cost",
        "planning",
    ),
}

GTA_LOCATION_HINTS = {
    "toronto",
    "scarborough",
    "north york",
    "etobicoke",
    "mississauga",
    "brampton",
    "markham",
    "vaughan",
    "richmond hill",
    "pickering",
    "ajax",
    "whitby",
    "oshawa",
    "oakville",
}

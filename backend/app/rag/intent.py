"""Intent routing (§8.3 node 2). Rule-based and typed: cheap, deterministic and testable. Anything that isn't
clearly a greeting, a request for a person, or an order lookup is treated as a knowledge question, so the safe
fallback is always "search the documents" (which abstains when they don't cover it)."""

import re

from app.db.models import Intent

_SMALLTALK = re.compile(
    r"^\s*(?:hi|hello|hey|hiya|good (?:morning|afternoon|evening)|thanks?(?: you| a lot)?|"
    r"thank you(?: so much)?|cheers|bye|goodbye|ok(?:ay)?|help|what can you do|who are you|what are you)"
    r"\s*[!.?,]*\s*$",
    re.IGNORECASE,
)
_SUPPORT = re.compile(
    r"\b(?:talk|speak|chat)\s+(?:to|with)\s+(?:a\s+|an\s+|someone|somebody|a real\s+)?"
    r"(?:human|person|agent|someone|somebody|representative|staff)\b|\breal person\b|\bcustomer service\b",
    re.IGNORECASE,
)
# Lookups of one particular order or repair (an order number, "where is my…", tracking, status). Questions
# *about* orders in general ("Can I cancel an order?") are policy questions for the documents (golden-v1
# G042, G043).
_ORDER = re.compile(
    r"\border\s*(?:#|number|no\.?|status)|\btrack(?:ing)?\s+(?:my|the|a|number)\b"
    r"|\bTC-\d{4,}\b|\bwhere(?:'s| is)\s+my\b|\b(?:refund|delivery|shipping|repair)\s+status\b"
    # Status phrasing only: "before my order ships" (in a cancellation question) is policy, not a lookup.
    r"|\b(?:has|did|is|when (?:will|does)|why (?:hasn't|has not|didn't))\s+my\s+order\b"
    # Asking for one's own orders: "what are my recent orders", "show me my orders", "what did I order".
    # "Do my orders ship free?" is still a policy question.
    r"|\bmy\s+(?:(?:recent|last|latest|previous|past|current|open|online)\s+)+orders?\b"
    r"|\b(?:show|list|see|view|check|find|get|give)\s+(?:me\s+)?(?:all\s+)?my\s+orders?\b"
    r"|\bmy\s+orders\b(?!\s+(?:ship|arrive|are\s+eligible))"
    r"|\bwhat\s+(?:did|have)\s+i\s+(?:order|ordered|bought|buy)\b"
    # Repairs and pickups of one's own things (ADR-031). "What's wrong with my printer?" stays a
    # troubleshooting question; "what was wrong with my laptop" (past, at the repair desk) is about a repair.
    r"|\bRP-?\d{6}\b|\bmy\s+repairs?\b|\brepair\s+(?:number|reference|job)\b"
    r"|\bis\s+my\s+(?:\w+\s+){0,2}(?:ready|fixed|repaired|done)\b"
    r"|\b(?:when|where|how)\s+(?:can|do|should)\s+i\s+(?:pick\s*up|collect)\s+"
    r"(?:my|it|them|the\s+(?:order|repair|products?|items?|device|laptop|phone))\b"
    r"|\b(?:pick\s*up|collect)\s+my\b"
    r"|\bhow\s+many\s+(?:products|items|things|orders|devices)\s+(?:do|did|have)\s+i\b"
    r"|\bwhat\s+was\s+(?:the\s+)?(?:fault|problem|issue|wrong|diagnosis)\b"
    r"|\b(?:fault|faults|diagnosis|problem)\s+(?:with|on|of|for|in)\s+my\b"
    r"|\bwhat\s+did\s+(?:the\s+)?(?:technician|repair\s+(?:team|desk)|you|they)\s+(?:find|fix|do)\b",
    re.IGNORECASE,
)


def classify(question: str) -> Intent:
    if _SMALLTALK.match(question):
        return Intent.SMALLTALK
    if _SUPPORT.search(question):
        return Intent.SUPPORT
    if _ORDER.search(question):
        return Intent.ORDER
    return Intent.KNOWLEDGE

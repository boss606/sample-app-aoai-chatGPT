"""Shared family law keyword terms for CourtListener opinion filtering."""

import re

TERMS = [
    r"marriage",
    r"divorce",
    r"custody",
    r"visitation",
    r"paternity",
    r"child\s+support",
    r"spousal\s+support",
    r"family\s+code",
    r"fam\.\s*code",
    # Expanded terms to capture more family law opinions
    r"alimony",
    r"adoption",
    r"dissolution",
    r"domestic\s+violence",
    r"marital",
    r"parental\s+rights",
    r"restraining\s+order",
    r"guardianship",
    r"emancipation",
    r"annulment",
    # NY/common law: matrimonial courts, spousal maintenance (avoids generic "maintenance")
    r"matrimonial",
    r"spousal\s+maintenance",
]
# Relaxed to 1: bulk opinions rarely have plain_text; many have html only.
# With MIN_TERMS=2 we got 0 results. MIN_TERMS=1 yields family-law opinions.
MIN_TERMS = 1

FAMILY_RE = re.compile(r"(" + "|".join(TERMS) + r")", re.IGNORECASE)

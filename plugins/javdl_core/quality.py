import re


def choose_quality(qualities: dict[str, str], requested: str = "best") -> tuple[str, str]:
    if not qualities:
        raise ValueError("No downloadable qualities were exposed by the source")

    requested = str(requested or "best").strip().lower()
    normalized = {str(k).strip().lower(): (k, v) for k, v in qualities.items()}

    if requested != "best" and requested in normalized:
        key, url = normalized[requested]
        return key, url

    pairs = []
    for key, url in qualities.items():
        match = re.search(r"(\d{3,4})p\b", str(key), re.I)
        if match:
            pairs.append((int(match.group(1)), str(key), url))

    if pairs:
        _, key, url = max(pairs, key=lambda item: item[0])
        return key, url

    key = next(iter(qualities))
    return key, qualities[key]

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Video:
    source_url: str
    title: str = ""
    thumbnail: Optional[str] = None
    duration: Optional[float] = None
    qualities: dict[str, str] = field(default_factory=dict)
    selected_quality: Optional[str] = None
    local_path: Optional[str] = None

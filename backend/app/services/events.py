"""The event vocabulary services stream to the API layer.

- `transcript` what was heard, for a spoken question (first event of a voice turn)
- `citations` the excerpts the answer is grounded in
- `progress`  human-readable status during multi-step work
- `model`     which pool member is answering
- `token`     a text delta
- `done`      terminal success, carrying persisted identifiers
- `error`     terminal failure, already phrased for the user
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

EventType = Literal["transcript", "citations", "progress", "model", "token", "done", "error"]


@dataclass(frozen=True)
class StreamEvent:
    type: EventType
    data: Any

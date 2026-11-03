"""Receive Finder/Dock open requests, including those delivered during Qt startup."""
from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QEvent, QTimer
from PyQt6.QtWidgets import QApplication


class NgpCraftApplication(QApplication):
    def __init__(self, argv: list[str]) -> None:
        # Qt can dispatch native events inside its constructor. Initialize these
        # before super(), rather than installing a filter after construction.
        self._open_requests: deque[str] = deque()
        self._open_handler: Callable[[str], None] | None = None
        self._dispatching_open = False
        super().__init__(argv)

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.FileOpen:
            url = event.url()
            if url.isLocalFile():
                path = Path(url.toLocalFile())
                if path.suffix.lower() in {".ngp", ".ngc"} and path.is_file():
                    self._open_requests.append(str(path.resolve()))
                    if self._open_handler is not None:
                        QTimer.singleShot(0, self._dispatch_open_requests)
                    event.accept()
                    return True
            event.ignore()
            return False
        return super().event(event)

    def set_open_handler(self, handler: Callable[[str], None]) -> None:
        """Attach only after the window/server are ready; drain on the GUI loop."""
        self._open_handler = handler
        QTimer.singleShot(0, self._dispatch_open_requests)

    def _dispatch_open_requests(self) -> None:
        if self._dispatching_open or self._open_handler is None:
            return
        self._dispatching_open = True
        try:
            while self._open_requests:
                # Reuse the normal ROM switch: it commits the outgoing game's
                # save and asks before ending an active link session.
                self._open_handler(self._open_requests.popleft())
        finally:
            self._dispatching_open = False

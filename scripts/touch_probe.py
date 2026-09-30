from __future__ import annotations

import os
import sys

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QInputDevice
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget


class EventLogger(QObject):
    def eventFilter(self, obj, event) -> bool:
        event_type = event.type()
        if event_type in {
            QEvent.Type.TouchBegin,
            QEvent.Type.TouchUpdate,
            QEvent.Type.TouchEnd,
            QEvent.Type.TouchCancel,
        }:
            print(f"TOUCH {event_type.name} points={event.pointCount()}", flush=True)
        elif event_type in {
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseMove,
            QEvent.Type.MouseButtonRelease,
        }:
            pos = event.position() if hasattr(event, "position") else event.pos()
            print(
                f"MOUSE {event_type.name} pos=({pos.x():.1f}, {pos.y():.1f}) "
                f"button={int(event.button())} buttons={int(event.buttons())}",
                flush=True,
            )
        return super().eventFilter(obj, event)


def main() -> int:
    QApplication.setAttribute(Qt.AA_SynthesizeMouseForUnhandledTouchEvents, True)
    QApplication.setAttribute(Qt.AA_SynthesizeTouchForUnhandledMouseEvents, True)
    app = QApplication(sys.argv)
    logger = EventLogger(app)
    app.installEventFilter(logger)

    print(f"Qt platform: {app.platformName()}", flush=True)
    print(f"DISPLAY={os.environ.get('DISPLAY', '')}", flush=True)
    print(f"WAYLAND_DISPLAY={os.environ.get('WAYLAND_DISPLAY', '')}", flush=True)
    print(f"QT_QPA_PLATFORM={os.environ.get('QT_QPA_PLATFORM', '')}", flush=True)
    devices = [
        (device.name(), str(device.type()), str(device.capabilities()))
        for device in QInputDevice.devices()
    ]
    print(f"QInputDevice.devices={devices}", flush=True)

    window = QWidget()
    window.setWindowTitle("Semi-Auto Touch Probe")
    window.setAttribute(Qt.WA_AcceptTouchEvents, True)
    layout = QVBoxLayout(window)
    label = QLabel("Tap, drag, and swipe here. Watch terminal output.")
    label.setAlignment(Qt.AlignCenter)
    label.setMinimumSize(640, 360)
    label.setAttribute(Qt.WA_AcceptTouchEvents, True)
    layout.addWidget(label)
    window.resize(800, 480)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

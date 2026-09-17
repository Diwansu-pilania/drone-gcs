"""Debug wrapper to catch and print startup crashes"""
import sys
import traceback

try:
    from PyQt6.QtWidgets import QApplication
    from ui.main_window import MainWindow

    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
except Exception as e:
    print("=" * 60)
    print("CRASH DURING STARTUP:")
    print("=" * 60)
    traceback.print_exc()
    print("=" * 60)
    input("Press Enter to exit...")


# main.py (MVC bootstrap)
# Entrypoint. I create the QApplication, build Model-View-Controller, and start the event loop.
import sys
from PySide6 import QtWidgets

from controller import AppController
from view import MainWindow

def main():
    app = QtWidgets.QApplication(sys.argv)
    ctrl = AppController()
    win = MainWindow(ctrl)
    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()

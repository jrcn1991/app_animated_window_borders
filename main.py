import sys
import traceback
from PySide6 import QtWidgets
from PySide6.QtCore import QLockFile, QDir, QStandardPaths

from src.controller import AppController
from src.view import MainWindow


def _excepthook(exc_type, exc, tb):
    err = "".join(traceback.format_exception(exc_type, exc, tb))
    try:
        QtWidgets.QMessageBox.critical(None, "Erro inesperado", err)
    except Exception:
        pass
    # mantém também no stderr
    sys.__excepthook__(exc_type, exc, tb)


def _acquire_single_instance_lock(app_name: str = "AnimatedWindowsBorders") -> QLockFile | None:
    base_dir = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
    if not base_dir:
        base_dir = QStandardPaths.writableLocation(QStandardPaths.TempLocation)

    QDir().mkpath(base_dir)
    lock_path = QDir(base_dir).filePath(f"{app_name}.lock")

    lock = QLockFile(lock_path)
    lock.setStaleLockTime(10_000)  # 10s: limpa locks obsoletos

    if not lock.tryLock(1):
        lock.removeStaleLockFile()
        if not lock.tryLock(0):
            return None

    return lock


def main() -> int:
    sys.excepthook = _excepthook

    # Instância única
    lock = _acquire_single_instance_lock()
    if lock is None:
        # Já existe outra instância ativa; encerrar silenciosamente.
        return 0

    try:
        app = QtWidgets.QApplication(sys.argv)
        # Mantém app vivo mesmo sem janelas (quando vai para o tray)
        app.setQuitOnLastWindowClosed(False)

        ctrl = AppController()
        win = MainWindow(ctrl)
        #win.show()

        if not bool(ctrl.config_data.get("service_enabled", False)):
            win.show()

        # Libera o lock ao sair
        app.aboutToQuit.connect(lambda: lock.unlock())

        # Chamada correta no PySide6 moderno
        return app.exec()

    except Exception as e:
        try:
            QtWidgets.QMessageBox.critical(None, "Falha ao iniciar", str(e))
        except Exception:
            pass
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())

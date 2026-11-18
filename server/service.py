"""Windows Service cho máy chủ (máy của bố mẹ). Dữ liệu tại %ProgramData%\\OpenGuardKidsServer."""
import os
import sys
import threading
from pathlib import Path

if sys.platform != "win32":
    raise ImportError("server.service chỉ chạy trên Windows")

import servicemanager  # noqa: E402
import win32event  # noqa: E402
import win32service  # noqa: E402
import win32serviceutil  # noqa: E402

SERVER_DATA = Path(os.environ.get("PROGRAMDATA", "C:/ProgramData")) / "OpenGuardKidsServer"
PORT = 8443


class OGKServerService(win32serviceutil.ServiceFramework):
    _svc_name_ = "OpenGuardKidsServer"
    _svc_display_name_ = "Open Guardian Kids - Bảng điều khiển phụ huynh"
    _svc_description_ = "Máy chủ bảng điều khiển của Open Guardian Kids trong mạng gia đình."

    def __init__(self, args):
        super().__init__(args)
        self.stop_evt = threading.Event()

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        self.stop_evt.set()

    def SvcDoRun(self):
        servicemanager.LogMsg(servicemanager.EVENTLOG_INFORMATION_TYPE, servicemanager.PYS_SERVICE_STARTED, (self._svc_name_, ""))
        try:
            from server.runner import run
            run(SERVER_DATA, port=PORT, stop_event=self.stop_evt)
        except BaseException:
            import traceback
            msg = traceback.format_exc()
            servicemanager.LogErrorMsg(msg)
            try:
                SERVER_DATA.mkdir(parents=True, exist_ok=True)
                with open(SERVER_DATA / "server.log", "a", encoding="utf-8") as f:
                    f.write(msg)
            except OSError:
                pass
            raise


def run_dispatcher():
    servicemanager.Initialize()
    servicemanager.PrepareToHostSingle(OGKServerService)
    servicemanager.StartServiceCtrlDispatcher()

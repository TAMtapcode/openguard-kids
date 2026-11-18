"""Windows Service của máy con (LocalSystem). Chạy qua `ogk.exe agent-service`; do OGK-Setup đăng ký bằng sc.exe (kèm tự khởi động lại)."""
import sys

if sys.platform != "win32":
    raise ImportError("agent.service chỉ chạy trên Windows")

import servicemanager  # noqa: E402
import win32event  # noqa: E402
import win32service  # noqa: E402
import win32serviceutil  # noqa: E402

from .bootstrap import build_engine, setup_logging  # noqa: E402
from .config import AgentConfig  # noqa: E402


class OGKService(win32serviceutil.ServiceFramework):
    _svc_name_ = "OpenGuardKids"
    _svc_display_name_ = "Open Guardian Kids Agent"
    _svc_description_ = ("Công cụ đồng hành giúp trẻ dùng máy lành mạnh. Minh bạch: biểu tượng khay luôn hiển thị; "
                         "chỉ ghi thời lượng, tên ứng dụng và tên miền.")

    def __init__(self, args):
        super().__init__(args)
        self.stop_evt = win32event.CreateEvent(None, 0, 0, None)
        self.engine = None

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.stop_evt)

    def SvcDoRun(self):
        servicemanager.LogMsg(servicemanager.EVENTLOG_INFORMATION_TYPE, servicemanager.PYS_SERVICE_STARTED, (self._svc_name_, ""))
        cfg = AgentConfig.load()
        setup_logging(cfg)
        self.engine = build_engine(cfg)
        self.engine.start()
        win32event.WaitForSingleObject(self.stop_evt, win32event.INFINITE)
        self.engine.stop()


def run_dispatcher():
    """SCM khởi động dịch vụ: host OGKService (dùng trong ogk.exe agent-service)."""
    servicemanager.Initialize()
    servicemanager.PrepareToHostSingle(OGKService)
    servicemanager.StartServiceCtrlDispatcher()


def run():
    """Dòng lệnh phát triển: không tham số -> host dịch vụ; có tham số -> install/remove/start/stop."""
    if len(sys.argv) == 1:
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(OGKService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        win32serviceutil.HandleCommandLine(OGKService)


if __name__ == "__main__":
    run()

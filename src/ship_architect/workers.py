"""Retained QThread jobs; cooperative cancellation, never terminate threads."""
import traceback,uuid,time
from .diagnostics import event
from PySide6.QtCore import QThread, Signal
from .chunks import Cancelled


class Task(QThread):
    result=Signal(object)
    failed=Signal(str)
    progress=Signal(int,str)
    cancelled=Signal()

    def __init__(self,function,parent=None):
        super().__init__(parent)
        self.function=function;self.trace_id=uuid.uuid4().hex;self.stage=getattr(function,'__name__','worker')

    def run(self):
        started=time.perf_counter();event('worker_started',trace_id=self.trace_id,stage=self.stage)
        try:
            self.result.emit(self.function(self.progress.emit,self.isInterruptionRequested))
            event('worker_completed',trace_id=self.trace_id,stage=self.stage,seconds=round(time.perf_counter()-started,4))
        except Cancelled:
            event('worker_cancelled',trace_id=self.trace_id,stage=self.stage);self.cancelled.emit()
        except Exception as e:
            event('worker_exception',trace_id=self.trace_id,stage=self.stage, critical=True, error=str(e), traceback=traceback.format_exc())
            self.failed.emit(f'{type(e).__name__}: {e}')

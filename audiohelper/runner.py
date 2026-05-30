import subprocess
import threading
from pathlib import Path
from typing import Callable, Sequence, Union, Optional

Job = Union[Sequence[str], Callable[[], None]]


class JobRunner:
    """Sequential job runner. Each job is either an argv list (run as subprocess)
    or a Python callable. Streams subprocess output to on_output."""

    def __init__(self, on_output: Callable[[str], None], on_done: Callable[[int], None]):
        self.on_output = on_output
        self.on_done = on_done
        self._cancel = threading.Event()
        self._proc: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._finalize: Optional[Callable[[], None]] = None
        self._job_done: Optional[Callable[[int], None]] = None

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def run_sequence(
        self,
        jobs: list[Job],
        cwd: Optional[Path] = None,
        finalize: Optional[Callable[[], None]] = None,
        on_done: Optional[Callable[[int], None]] = None,
    ) -> bool:
        if self.is_running():
            return False
        self._cancel.clear()
        self._finalize = finalize
        self._job_done = on_done
        self._thread = threading.Thread(target=self._run, args=(jobs, cwd), daemon=True)
        self._thread.start()
        return True

    def cancel(self) -> None:
        self._cancel.set()
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except OSError:
                pass

    def _run(self, jobs: list[Job], cwd: Optional[Path]) -> None:
        rc = 0
        try:
            for job in jobs:
                if self._cancel.is_set():
                    rc = 130
                    self.on_output("\n-- cancelled\n")
                    break
                if callable(job):
                    label = getattr(job, "__doc__", None) or getattr(job, "__name__", "step")
                    self.on_output(f"\n# {label}\n")
                    try:
                        job()
                    except Exception as e:
                        self.on_output(f"ERROR: {e}\n")
                        rc = 1
                        break
                    continue
                argv = [str(a) for a in job]
                self.on_output("\n$ " + " ".join(_quote(a) for a in argv) + "\n")
                try:
                    self._proc = subprocess.Popen(
                        argv,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        cwd=str(cwd) if cwd else None,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                except OSError as e:
                    self.on_output(f"ERROR: {e}\n")
                    rc = 1
                    break
                assert self._proc.stdout is not None
                while True:
                    if self._cancel.is_set():
                        self._proc.terminate()
                        break
                    chunk = self._proc.stdout.read(256)
                    if not chunk:
                        break
                    self.on_output(chunk.decode("utf-8", errors="replace"))
                self._proc.wait()
                rc = self._proc.returncode
                if rc != 0:
                    self.on_output(f"\n-- exit {rc}\n")
                    break
        finally:
            if self._finalize is not None:
                try:
                    self._finalize()
                except Exception as e:
                    self.on_output(f"finalize error: {e}\n")
                self._finalize = None
            self._proc = None
            self.on_done(rc)
            if self._job_done is not None:
                try:
                    self._job_done(rc)
                except Exception:
                    pass
                self._job_done = None


def _quote(s: str) -> str:
    return f'"{s}"' if " " in s else s

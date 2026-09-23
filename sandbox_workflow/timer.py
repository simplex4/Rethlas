"""Elapsed time reporting, separate from machine-readable stdout."""
import sys
import threading
import time


def duration(seconds):
    seconds = int(seconds)
    return f'{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}'


class ElapsedTimer:
    def __init__(self, interval=30, stream=None, clock=time.monotonic):
        self.interval, self.stream, self.clock = interval, stream or sys.stderr, clock
        self.stop = threading.Event()

    def __enter__(self):
        self.started = self.clock()
        def report():
            while not self.stop.wait(self.interval):
                print(f'[elapsed {duration(self.clock() - self.started)}] still running...', file=self.stream, flush=True)
        self.thread = threading.Thread(target=report, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join()
        print(f'Total time: {duration(self.clock() - self.started)}', file=self.stream, flush=True)

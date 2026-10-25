import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([sys.argv[0]])
    yield app


@pytest.fixture(scope="session")
def sim():
    from flightoverlay.demo import simulate
    return simulate()


@pytest.fixture(scope="session")
def demo_logs(tmp_path_factory, sim):
    from flightoverlay import demo
    d = tmp_path_factory.mktemp("logs")
    b = str(d / "flight.bin")
    t = str(d / "flight.tlog")
    demo.write_bin(b, sim)
    demo.write_tlog(t, sim)
    return {"bin": b, "tlog": t}


@pytest.fixture(scope="session")
def bin_tel(demo_logs):
    from flightoverlay.telemetry.loader import load_telemetry
    return load_telemetry(demo_logs["bin"], use_cache=False)


@pytest.fixture(scope="session")
def tlog_tel(demo_logs):
    from flightoverlay.telemetry.loader import load_telemetry
    return load_telemetry(demo_logs["tlog"], use_cache=False)

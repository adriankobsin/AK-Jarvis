import asyncio
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from jarvis.config import load_config
from jarvis.integrations import IntegrationError, IntegrationManager
from jarvis.integrations.modbus import regs_to_value, value_to_regs
from jarvis.store import Store
from jarvis.tools.base import ToolContext, build_registry


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


# ------------------------------------------------------------------ fake systems
@pytest.fixture(scope="module")
def modbus_server():
    from pymodbus.datastore import ModbusDeviceContext, ModbusSequentialDataBlock, ModbusServerContext
    from pymodbus.server import StartAsyncTcpServer

    hr = [0] * 100
    hr[10] = 723                                  # generator load x0.1 kW
    hr[20:22] = value_to_regs(21.5, "float32")    # tank temperature
    store = ModbusDeviceContext(hr=ModbusSequentialDataBlock(1, hr), ir=ModbusSequentialDataBlock(1, [0] * 100),
                                co=ModbusSequentialDataBlock(1, [False] * 100), di=ModbusSequentialDataBlock(1, [True] * 100))
    ctx = ModbusServerContext(devices=store, single=True)
    port = free_port()
    loop = asyncio.new_event_loop()
    threading.Thread(target=lambda: loop.run_until_complete(StartAsyncTcpServer(context=ctx, address=("127.0.0.1", port))), daemon=True).start()
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    return port


class FakeLutron(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(5)
        self.port = self.srv.getsockname()[1]
        self.levels = {25: 0.0, 41: 100.0}
        self.log = []

    def run(self):
        while True:
            conn, _ = self.srv.accept()
            threading.Thread(target=self.handle, args=(conn,), daemon=True).start()

    def handle(self, c):
        f = c.makefile("rb")
        c.sendall(b"login: ")
        user = f.readline().strip()
        c.sendall(b"password: ")
        pw = f.readline().strip()
        if (user, pw) != (b"lutron", b"secret"):
            c.sendall(b"bad login\r\nlogin: ")
            c.close()
            return
        c.sendall(b"GNET> ")
        for line in f:
            cmd = line.decode().strip()
            self.log.append(cmd)
            if cmd.startswith("?OUTPUT,"):
                oid = int(cmd.split(",")[1])
                c.sendall(f"~OUTPUT,{oid},1,{self.levels.get(oid, 0):.2f}\r\nGNET> ".encode())
            elif cmd.startswith("#OUTPUT,"):
                parts = cmd.split(",")
                self.levels[int(parts[1])] = float(parts[3])
                c.sendall(b"GNET> ")
            else:
                c.sendall(b"GNET> ")


@pytest.fixture(scope="module")
def lutron():
    s = FakeLutron()
    s.start()
    return s


class FakeCrestron(BaseHTTPRequestHandler):
    state = {"lights": [{"id": 1001, "name": "Salon Downlights", "roomId": 1, "level": 0}],
             "shades": [{"id": 2001, "name": "Salon Blinds", "roomId": 1, "position": 65535}],
             "scenes": [{"id": 3001, "name": "Evening", "roomId": 1}], "rooms": [{"id": 1, "name": "Salon"}]}
    recalled = []

    def log_message(self, *a):
        pass

    def send(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def authed(self):
        return self.headers.get("Crestron-RestAPI-AuthKey") == "KEY123"

    def do_GET(self):
        if self.path == "/cws/api/login":
            return self.send({"authkey": "KEY123", "version": "2.0"}) if self.headers.get("Crestron-RestAPI-AuthToken") == "TOKEN" else self.send({}, 401)
        if not self.authed():
            return self.send({}, 401)
        kind = self.path.rsplit("/", 1)[-1]
        if kind in self.state:
            return self.send({kind: self.state[kind], "version": "2.0"})
        self.send({"error": "nf"}, 404)

    def do_POST(self):
        if not self.authed():
            return self.send({}, 401)
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0) or 0)) or b"{}")
        if self.path == "/cws/api/lights/SetState":
            for l in body["lights"]:
                next(x for x in self.state["lights"] if x["id"] == l["id"])["level"] = l["level"]
            return self.send({"status": "success"})
        if self.path == "/cws/api/shades/SetState":
            for l in body["shades"]:
                next(x for x in self.state["shades"] if x["id"] == l["id"])["position"] = l["position"]
            return self.send({"status": "success"})
        if self.path.startswith("/cws/api/scenes/recall/"):
            FakeCrestron.recalled.append(int(self.path.rsplit("/", 1)[1]))
            return self.send({"status": "success"})
        self.send({}, 404)


@pytest.fixture(scope="module")
def crestron():
    srv = HTTPServer(("127.0.0.1", 0), FakeCrestron)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv.server_port


@pytest.fixture(scope="module")
def tcp_device():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    state = {"vol": 35}

    def serve():
        while True:
            c, _ = srv.accept()
            data = c.recv(100).decode().strip()
            if data == "GET VOL SALON":
                c.sendall(f"VOL SALON {state['vol']}\r\n".encode())
            elif data.startswith("SET VOL SALON "):
                state["vol"] = int(data.split()[-1])
                c.sendall(b"OK\r\n")
            elif data == "CINEMA_ON":
                c.sendall(b"CINEMA ON OK\r\n")
            c.close()

    threading.Thread(target=serve, daemon=True).start()
    return srv.getsockname()[1], state


@pytest.fixture
def cfg(tmp_path, monkeypatch, modbus_server, lutron, crestron, tcp_device):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LUTRON_PW", "secret")
    monkeypatch.setenv("CRESTRON_TOKEN", "TOKEN")
    return load_config(overrides={
        "assistant": {"data_dir": str(tmp_path / "data")},
        "integrations": [
            {"name": "engine_plc", "type": "modbus", "host": "127.0.0.1", "port": modbus_server, "unit": 1,
             "points": [
                 {"name": "gen1_load", "register": 40011, "scale": 0.1, "units": "kW"},
                 {"name": "tank_temp", "table": "holding", "address": 20, "datatype": "float32", "units": "°C", "decimals": 1},
                 {"name": "bilge_pump", "table": "coil", "address": 3, "confirm": False},
                 {"name": "setpoint", "table": "holding", "address": 30, "scale": 0.1, "units": "°C", "min": 5, "max": 30},
                 {"name": "float_switch", "table": "discrete", "address": 0}]},
            {"name": "salon_lighting", "type": "lutron", "host": "127.0.0.1", "port": lutron.port,
             "username": "lutron", "password_env": "LUTRON_PW",
             "points": [{"name": "downlights", "output": 25, "units": "%"}, {"name": "shade", "output": 41, "units": "%"}],
             "buttons": [{"name": "evening", "device": 10, "button": 3}]},
            {"name": "crestron_home", "type": "crestron_home", "host": "127.0.0.1", "port": crestron, "scheme": "http",
             "token_env": "CRESTRON_TOKEN",
             "points": [{"name": "salon_lights", "light": "Salon Downlights", "units": "%"}, {"name": "blinds", "shade": 2001, "units": "%"}]},
            {"name": "av_processor", "type": "crestron_tcp", "host": "127.0.0.1", "port": tcp_device[0],
             "commands": {"cinema_on": "CINEMA_ON"},
             "points": [{"name": "salon_volume", "query": "GET VOL SALON", "reply": r"VOL SALON (\d+)", "set": "SET VOL SALON {value}"}]},
        ]})


# ------------------------------------------------------------------ drivers
def test_modbus_codecs():
    for dtype, v in (("int16", -5), ("uint32", 70000), ("float32", 3.5), ("int32", -123456)):
        for bo in ("big", "little"):
            for wo in ("big", "little"):
                assert regs_to_value(value_to_regs(v, dtype, bo, wo), dtype, bo, wo) == pytest.approx(v)


def test_modbus_driver(cfg):
    m = IntegrationManager(cfg)
    assert m.read("engine_plc", "gen1_load") == {"point": "gen1_load", "value": 72.3, "units": "kW", "raw": 723}
    assert m.read("engine_plc", "tank_temp")["value"] == 21.5
    assert m.read("engine_plc", "float_switch")["value"] is True
    m.write("engine_plc", "bilge_pump", True)
    assert m.read("engine_plc", "bilge_pump")["value"] is True
    m.write("engine_plc", "setpoint", 22.5)
    assert m.read("engine_plc", "setpoint")["raw"] == 225
    with pytest.raises(IntegrationError, match="outside"):
        m.write("engine_plc", "setpoint", 50)
    with pytest.raises(IntegrationError, match="read-only"):
        m.write("engine_plc", "float_switch", False)
    regs = m.command("engine_plc", "read_registers", {"table": "holding", "address": 10, "count": 1})
    assert regs["values"] == [723]
    assert m.status("engine_plc")["online"] is True
    assert len(m.read_all("engine_plc")) == 5


def test_lutron_driver(cfg, lutron):
    m = IntegrationManager(cfg)
    assert m.read("salon_lighting", "shade")["value"] == 100.0
    m.write("salon_lighting", "downlights", 40)
    assert m.read("salon_lighting", "downlights")["value"] == 40.0 and lutron.levels[25] == 40.0
    m.command("salon_lighting", "press_button", {"button": "evening"})
    assert lutron.log[-2:] == ["#DEVICE,10,3,3", "#DEVICE,10,3,4"]
    cfg["integrations"][1]["password_env"] = "NOPE"
    bad = IntegrationManager(cfg)
    assert bad.status("salon_lighting")["online"] is False


def test_crestron_home_driver(cfg):
    m = IntegrationManager(cfg)
    assert m.read("crestron_home", "blinds")["value"] == 100.0
    m.write("crestron_home", "salon_lights", 40)
    assert FakeCrestron.state["lights"][0]["level"] == round(0.4 * 65535)
    assert m.read("crestron_home", "salon_lights")["value"] == 40.0
    m.command("crestron_home", "recall_scene", {"scene": "evening"})
    assert FakeCrestron.recalled == [3001]
    disc = m.get("crestron_home").discover()
    assert disc["rooms"][0]["name"] == "Salon" and "unavailable" in str(disc["thermostats"])
    assert [s["name"] for s in m.command("crestron_home", "list", {"what": "scenes"})] == ["Evening"]


def test_tcp_driver(cfg, tcp_device):
    m = IntegrationManager(cfg)
    assert m.read("av_processor", "salon_volume")["value"] == 35
    m.write("av_processor", "salon_volume", 50)
    assert tcp_device[1]["vol"] == 50
    assert m.command("av_processor", "preset", {"preset": "cinema_on"})["reply"] == "CINEMA ON OK"
    with pytest.raises(IntegrationError, match="not allowed"):
        m.command("av_processor", "send", {"text": "REBOOT"})


# ------------------------------------------------------------------ tools & programs
def test_integration_tools_confirm(cfg):
    store = Store(cfg.db_path)
    reg = build_registry(cfg)
    asked = []
    ctx = ToolContext(cfg, store, confirm=lambda m: asked.append(m) or False)
    over = json.loads(reg.execute("integrations_overview", {}, ctx)[0])
    assert {o["name"] for o in over} == {"engine_plc", "salon_lighting", "crestron_home", "av_processor"}
    assert all(o["status"]["online"] for o in over)
    out, _ = reg.execute("integration_write", {"integration": "salon_lighting", "point": "downlights", "value": 70}, ctx)
    assert "declined" in out and "set downlights to 70 %" in asked[-1]
    out, _ = reg.execute("integration_write", {"integration": "engine_plc", "point": "bilge_pump", "value": False}, ctx)
    assert json.loads(out)["written"] is False  # point marked confirm: false
    out, _ = reg.execute("integration_command", {"integration": "engine_plc", "command": "read_registers",
                                                 "params": {"address": 10, "count": 1}}, ctx)
    assert json.loads(out)["values"] == [723]  # reads don't ask
    out, _ = reg.execute("integration_command", {"integration": "crestron_home", "command": "recall_scene",
                                                 "params": {"scene": "Evening"}}, ctx)
    assert "declined" in out


def test_programs_write_test_deploy(cfg):
    store = Store(cfg.db_path)
    reg = build_registry(cfg)
    alerts = []
    ctx = ToolContext(cfg, store, confirm=lambda m: True)
    ctx.extras["program_alert"] = lambda t, m: alerts.append((t, m))
    code = '''from jarvis import sdk
load = sdk.read("engine_plc", "gen1_load")
sdk.log("load", load)
if load > 70:
    sdk.alert("Generator 1", f"Load is {load} kW")
sdk.set_state("last", load)
'''
    out = json.loads(reg.execute("write_program", {"name": "gen-load-watch", "description": "Alert on high generator load",
                                                   "code": code, "mode": "once"}, ctx)[0])
    assert out["syntax_error"] is None
    res = json.loads(reg.execute("test_program", {"name": "gen-load-watch", "timeout_seconds": 30}, ctx)[0])
    assert res["ok"], res
    assert "load 72.3" in res["output"]
    assert alerts == [("[test] Generator 1", "Load is 72.3 kW")]
    # bad syntax is caught on save
    bad = json.loads(reg.execute("write_program", {"name": "broken", "description": "x", "code": "def (:", "mode": "once"}, ctx)[0])
    assert bad["syntax_error"]
    # a service: deploy, see it running, stop it
    svc = '''from jarvis import sdk
import time
def tick():
    sdk.log("tick", sdk.read("salon_lighting", "shade"))
sdk.every(2, tick)
'''
    reg.execute("write_program", {"name": "shade-watch", "description": "Log shade level", "code": svc, "mode": "service"}, ctx)
    dep = json.loads(reg.execute("deploy_program", {"name": "shade-watch"}, ctx)[0])
    assert dep["mode"] == "service"
    for _ in range(60):
        logs = reg.execute("program_logs", {"name": "shade-watch"}, ctx)[0]
        if "tick 100.0" in logs:
            break
        time.sleep(0.25)
    assert "tick 100.0" in logs
    listed = json.loads(reg.execute("list_programs", {}, ctx)[0])
    assert next(p for p in listed if p["name"] == "shade-watch")["running"] is True
    reg.execute("stop_program", {"name": "shade-watch"}, ctx)
    time.sleep(0.5)
    listed = json.loads(reg.execute("list_programs", {}, ctx)[0])
    sw = next(p for p in listed if p["name"] == "shade-watch")
    assert sw["running"] is False and sw["deployed"] is False
    # deploy declined
    ctx.confirm = lambda m: False
    assert "did not approve" in reg.execute("deploy_program", {"name": "gen-load-watch"}, ctx)[0]


def test_web_api_integrations_programs_cameras(cfg):
    import base64

    from fastapi.testclient import TestClient

    from jarvis.household import Household
    from jarvis.web.server import create_app

    hh = Household(cfg, start_schedulers=False, show_tools=False)
    c = TestClient(create_app(cfg, hh, password="pw"))
    c.post("/api/login", json={"password": "pw"})
    over = c.get("/api/integrations").json()
    assert [i["status"]["online"] for i in over] == [True, True, True, True]
    vals = {v["point"]: v for v in c.get("/api/integrations/engine_plc/values").json()}
    assert vals["gen1_load"]["value"] == 72.3 and vals["tank_temp"]["units"] == "°C"
    assert c.post("/api/integrations/crestron_home/write", json={"point": "salon_lights", "value": 55}).status_code == 200
    assert c.get("/api/integrations/crestron_home/values").json()[0]["value"] == 55.0
    assert c.post("/api/integrations/engine_plc/write", json={"point": "float_switch", "value": 1}).status_code == 400
    assert c.post("/api/integrations/av_processor/command", json={"command": "preset", "params": {"preset": "cinema_on"}}).json()["reply"] == "CINEMA ON OK"
    assert c.get("/api/integrations/nope/values").status_code == 400
    assert "rooms" in c.get("/api/integrations/crestron_home/discover").json()
    # programs over the API
    code = 'from jarvis import sdk\nsdk.log("temp", sdk.read("engine_plc", "tank_temp"))\n'
    assert c.post("/api/programs", json={"name": "tank-log", "description": "log tank temp", "mode": "once", "code": code}).json()["syntax_error"] is None
    r = c.post("/api/programs/tank-log/test").json()
    assert r["ok"] and "temp 21.5" in r["output"]
    assert c.get("/api/programs").json()[0]["last_test_ok"] is True
    assert "temp 21.5" in c.get("/api/programs/tank-log").json()["log"]
    dash = c.get("/api/dashboard").json()
    assert {i["name"] for i in dash["integrations"]} == {"engine_plc", "salon_lighting", "crestron_home", "av_processor"}
    assert dash["programs"][0]["name"] == "tank-log"
    assert c.post("/api/programs", json={"name": "Bad Name!", "code": "x"}).status_code == 400
    # any camera: a device shares frames, Jarvis can look at it
    jpg = base64.b64encode(b"\xff\xd8sharedframe").decode()
    assert c.post("/api/cameras/share", json={"name": "Office Laptop", "image": "data:image/jpeg;base64," + jpg}).json()["name"] == "office_laptop"
    cams = c.get("/api/cameras").json()
    assert cams[0]["name"] == "office_laptop" and cams[0]["source"] == "shared" and cams[0]["live"]
    assert c.get("/api/cameras/office_laptop/snapshot").content == b"\xff\xd8sharedframe"
    owner = hh.brain("owner")
    out, err = owner.registry.execute("look_at_camera", {"name": "office_laptop"}, owner.ctx)
    assert not err and out[1]["type"] == "image"
    c.delete("/api/cameras/share/office_laptop")
    assert c.get("/api/cameras").json() == []
    hh.shutdown()


def test_usb_and_rtsp_camera_config(tmp_path, monkeypatch):
    from jarvis import cameras

    cfg = load_config(overrides={"assistant": {"data_dir": str(tmp_path)}, "cameras": [
        {"name": "engine_room", "rtsp_url": "rtsp://10.0.0.5/stream1", "username": "admin", "password_env": "CAMPW"},
        {"name": "desk", "usb": 0}]})
    monkeypatch.setenv("CAMPW", "pw")
    seen = []
    monkeypatch.setattr(cameras, "_ffmpeg_frame", lambda args, timeout=15: seen.append(args) or b"\xff\xd8rtsp")
    assert cameras.snapshot(cfg, "engine_room") == (b"\xff\xd8rtsp", "image/jpeg")
    assert "rtsp://admin:pw@10.0.0.5/stream1" in seen[0]
    assert [c["source"] for c in cameras.list_cameras(cfg)] == ["rtsp", "usb"]


def test_stopping_a_service_is_not_reported_as_failure(cfg):
    from jarvis.programs import ProgramManager

    alerts = []
    pm = ProgramManager(cfg, lambda t, m: alerts.append(t))
    pm.save("sleeper", "sleeps", "import time\nprint('up', flush=True)\ntime.sleep(60)\n", "service")
    pm.deploy("sleeper")
    for _ in range(40):
        if "up" in pm.logs("sleeper"):
            break
        time.sleep(0.1)
    pm.stop("sleeper")
    time.sleep(0.6)
    assert alerts == [] and pm.meta("sleeper")["status"] == "stopped"
    # a real crash is reported
    pm.save("crasher", "crashes", "raise SystemExit(3)\n", "once")
    pm.deploy("crasher")
    for _ in range(40):
        if alerts:
            break
        time.sleep(0.1)
    assert alerts == ["Program crasher failed"] and pm.meta("crasher")["status"] == "failed"

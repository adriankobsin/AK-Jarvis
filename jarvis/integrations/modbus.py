"""Modbus TCP / RTU driver (PLCs, generators, power meters, tank sensors, HVAC, BMS…)."""
from __future__ import annotations

import struct
from typing import Any

from .base import Driver, IntegrationError

DTYPES = {  # name -> (struct format, registers)
    "int16": ("h", 1), "uint16": ("H", 1), "int32": ("i", 2), "uint32": ("I", 2),
    "float32": ("f", 2), "int64": ("q", 4), "uint64": ("Q", 4), "float64": ("d", 4),
}
TABLES = ("holding", "input", "coil", "discrete")


def resolve_address(point: dict) -> tuple[str, int]:
    """Accept {table, address} (0-based) or a Modicon-style register (40001 = holding 0)."""
    if "address" in point:
        return point.get("table", "holding"), int(point["address"])
    reg = int(point["register"])
    ranges = ((400001, 465536, "holding"), (300001, 365536, "input"), (100001, 165536, "discrete"),
              (40001, 49999, "holding"), (30001, 39999, "input"), (10001, 19999, "discrete"), (1, 9999, "coil"))
    for lo, hi, table in ranges:
        if lo <= reg <= hi:
            return table, reg - lo
    raise IntegrationError(f"Can't work out the table for register {reg}; give table + address instead")


def regs_to_value(regs: list[int], dtype: str, byte_order: str = "big", word_order: str = "big") -> Any:
    fmt, n = DTYPES[dtype]
    words = list(regs[:n])
    if word_order == "little":
        words.reverse()
    data = b"".join(w.to_bytes(2, "big") for w in words)
    if byte_order == "little":
        data = b"".join(data[i:i + 2][::-1] for i in range(0, len(data), 2))
    return struct.unpack(">" + fmt, data)[0]


def value_to_regs(value: Any, dtype: str, byte_order: str = "big", word_order: str = "big") -> list[int]:
    fmt, n = DTYPES[dtype]
    if fmt in "hHiIqQ":
        value = int(round(float(value)))
    data = struct.pack(">" + fmt, value)
    if byte_order == "little":
        data = b"".join(data[i:i + 2][::-1] for i in range(0, len(data), 2))
    words = [int.from_bytes(data[i:i + 2], "big") for i in range(0, len(data), 2)]
    if word_order == "little":
        words.reverse()
    return words


class ModbusDriver(Driver):
    kind = "modbus"

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.client = None
        self.unit = int(cfg.get("unit", cfg.get("device_id", 1)))

    def writable(self, point: dict) -> bool:
        table = resolve_address(point)[0] if ("address" in point or "register" in point) else "holding"
        return bool(point.get("writable", table in ("holding", "coil")))

    # -- connection ------------------------------------------------------
    def _client(self):
        if self.client is not None and getattr(self.client, "connected", True):
            return self.client
        try:
            from pymodbus.client import ModbusSerialClient, ModbusTcpClient
        except ImportError as e:
            raise IntegrationError("pymodbus is not installed (pip install pymodbus)") from e
        c = self.cfg
        if c.get("serial_port"):
            self.client = ModbusSerialClient(port=c["serial_port"], baudrate=int(c.get("baudrate", 9600)),
                                             parity=c.get("parity", "N"), stopbits=int(c.get("stopbits", 1)),
                                             bytesize=int(c.get("bytesize", 8)), timeout=float(c.get("timeout", 3)))
        else:
            self.client = ModbusTcpClient(c["host"], port=int(c.get("port", 502)), timeout=float(c.get("timeout", 3)))
        if not self.client.connect():
            self.client = None
            raise IntegrationError(f"Can't connect to Modbus device at {c.get('host') or c.get('serial_port')}")
        return self.client

    def _call(self, fn_name: str, *args, **kw):
        cl = self._client()
        fn = getattr(cl, fn_name)
        last = None
        for key in ("device_id", "slave", "unit"):  # pymodbus renamed this argument across versions
            try:
                rr = fn(*args, **{key: self.unit}, **kw)
                break
            except TypeError as e:
                last = e
        else:
            raise IntegrationError(f"pymodbus API mismatch: {last}")
        if rr.isError():
            raise IntegrationError(f"Modbus error from device: {rr}")
        return rr

    def _read(self, table: str, address: int, count: int):
        fn = {"holding": "read_holding_registers", "input": "read_input_registers",
              "coil": "read_coils", "discrete": "read_discrete_inputs"}[table]
        try:
            rr = self._call(fn, address, count=count)
        except IntegrationError:
            raise
        except Exception as e:  # connection dropped: retry once
            self.close()
            raise IntegrationError(f"Modbus read failed: {e}") from e
        return rr.bits[:count] if table in ("coil", "discrete") else rr.registers

    # -- points ----------------------------------------------------------
    def read_raw(self, point: dict) -> Any:
        table, addr = resolve_address(point)
        if table in ("coil", "discrete"):
            return bool(self._read(table, addr, 1)[0])
        dtype = point.get("datatype", "uint16")
        if dtype == "bool":
            return bool(self._read(table, addr, 1)[0] & (1 << int(point.get("bit", 0))))
        regs = self._read(table, addr, DTYPES[dtype][1])
        return regs_to_value(regs, dtype, self.cfg.get("byte_order", "big"), self.cfg.get("word_order", "big"))

    def write_raw(self, point: dict, raw: Any) -> None:
        table, addr = resolve_address(point)
        if table == "coil":
            self._call("write_coil", addr, bool(raw))
            return
        if table != "holding":
            raise IntegrationError(f"{table} registers are read-only")
        regs = value_to_regs(raw, point.get("datatype", "uint16"), self.cfg.get("byte_order", "big"),
                             self.cfg.get("word_order", "big"))
        if len(regs) == 1:
            self._call("write_register", addr, regs[0])
        else:
            self._call("write_registers", addr, regs)

    def ping(self) -> None:
        self._client()

    def commands(self) -> list[dict]:
        return [
            {"name": "read_registers", "description": "Read raw registers: table (holding/input/coil/discrete), address (0-based), count",
             "params": {"table": "holding", "address": 0, "count": 10}, "writes": False},
            {"name": "write_register", "description": "Write one raw value to a holding register or coil", "params": {"table": "holding", "address": 0, "value": 0}, "writes": True},
        ]

    def command(self, name: str, params: dict) -> Any:
        table = params.get("table", "holding")
        if table not in TABLES:
            raise IntegrationError(f"table must be one of {TABLES}")
        if name == "read_registers":
            count = max(1, min(125, int(params.get("count", 10))))
            vals = self._read(table, int(params.get("address", 0)), count)
            return {"table": table, "start": int(params.get("address", 0)), "values": [int(v) if not isinstance(v, bool) else v for v in vals]}
        if name == "write_register":
            if table == "coil":
                self._call("write_coil", int(params["address"]), bool(params["value"]))
            else:
                self._call("write_register", int(params["address"]), int(params["value"]))
            return {"ok": True}
        return super().command(name, params)

    def discover(self) -> Any:
        out = {}
        for table in ("holding", "input"):
            try:
                out[table + "_0_19"] = self._read(table, 0, 20)
            except Exception as e:  # noqa: BLE001
                out[table + "_0_19"] = f"unavailable ({e})"
        return out

    def close(self) -> None:
        if self.client is not None:
            try:
                self.client.close()
            except Exception:
                pass
        self.client = None

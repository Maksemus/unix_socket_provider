from __future__ import annotations
import os
import sys
import argparse
import ast
import asyncio
import pathlib
import json
import logging

from typing import Any, Dict, List, Optional

SOCKET_LIMIT = 20 * 1024 * 1024


class UnixConnection:
    def __init__(
        self, sockpath: pathlib.Path
    ) -> None:
        self.sockpath = sockpath
        self.pending_req: Dict[str, Any] = {}
        self.connected = False
        self.kb_fd = sys.stdin.fileno()
        self.out_fd = sys.stdout.fileno()
        os.set_blocking(self.kb_fd, False)
        os.set_blocking(self.out_fd, False)
        self.kb_buf = b""
        self.kb_fut: Optional[asyncio.Future[str]] = None
        self.pending_reqs: Dict[int, asyncio.Future[Dict[str, Any]]] = {}
        self.print_lock = asyncio.Lock()
        self.mode: int = 0
        self.print_notifications: bool = False
        self.manual_entry: Dict[str, Any] = {}

    async def _mode_manual_entry(self) -> None:
        req = await self.input("Method Name (Press Enter to return to main menu): ")
        if not req:
            self.manual_entry = {}
            return
        self.manual_entry["method"] = req
        if "params" not in self.manual_entry:
            self.manual_entry["params"] = {}
        req = await self.input("Parameter Name (Press Enter to send request): ")
        if not req:
            ret = await self._send_manual_request()
            await self.print(f"Response: {ret}\n")
            self.manual_entry = {}
            return
        self.manual_entry["params"][req] = None
        params: Dict[str, Any] = self.manual_entry.get("params", {})
        if not params:
            return
        last_key = list(params.keys())[-1]
        req = await self.input(f"Parameter '{last_key}' Value: ")
        if not req:
            await self.print(f"No value selected, removing parameter {last_key}")
            params.pop(last_key, None)
        else:
            try:
                val = ast.literal_eval(req)
            except Exception as e:
                await self.print(f"Error: invalid value {req}, raised {e}")
                return
            params[last_key] = val

    async def _connect(self) -> None:
        print(f"Connecting to unix socket at {self.sockpath}")
        while True:
            try:
                reader, writer = await asyncio.open_unix_connection(
                    self.sockpath, limit=SOCKET_LIMIT
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(1.)
                continue
            break
        self.writer = writer
        self._loop.create_task(self._process_stream(reader))
        self.connected = True
        await self.print("Connected to unix socket")
        self.manual_entry = {
            "method": "server.connection.identify",
            "params": {
                "client_name": "Unix Socket Test",
                "type": "other",
            }
        }
        ret = await self._send_manual_request(False)
        self.manual_entry = {}
        await self.print(f"Client Identified With unix socket: {ret}")

    async def _process_stream(
        self, reader: asyncio.StreamReader
    ) -> None:
        errors_remaining: int = 10
        while not reader.at_eof():
            try:
                data = await reader.readuntil(b'\x03')
                decoded = data[:-1].decode(encoding="utf-8")
                item: Dict[str, Any] = json.loads(decoded)
            except (ConnectionError, asyncio.IncompleteReadError):
                break
            except asyncio.CancelledError:
                raise
            except Exception:
                errors_remaining -= 1
                if not errors_remaining or not self.connected:
                    break
                continue
            errors_remaining = 10
            if "id" in item:
                fut = self.pending_reqs.pop(item["id"], None)
                if fut is not None:
                    fut.set_result(item)
            elif self.print_notifications:
                self._loop.create_task(self.print(f"Notification: {item}\n"))
        await self.print("Unix Socket Disconnection from _process_stream()")
        await self.close()

    def _make_rpc_msg(self, method: str, **kwargs) -> Dict[str, Any]:
        msg = {"jsonrpc": "2.0", "method": method}
        uid = id(msg)
        msg["id"] = uid
        self.pending_req = msg
        if kwargs:
            msg["params"] = kwargs
        return msg

    async def _send_manual_request(
        self, echo_request: bool = True
    ) -> Dict[str, Any]:
        if not self.manual_entry:
            return
        params = self.manual_entry.get("params")
        method = self.manual_entry["method"]
        message = self._make_rpc_msg(method, **params)
        fut = self._loop.create_future()
        self.pending_reqs[message["id"]] = fut
        if echo_request:
            await self.print(f"Sending: {message}")
        await self._write_message(message)
        return await fut

    async def _write_message(self, message: Dict[str, Any]) -> None:
        data = json.dumps(message).encode() + b"\x03"
        try:
            self.writer.write(data)
            await self.writer.drain()
        except asyncio.CancelledError:
            raise
        except Exception:
            await self.close()

    async def input(self, prompt: str = "") -> str:
        if prompt:
            await self.print(prompt, is_line=False)
        self.kb_fut = self._loop.create_future()
        ret = await self.kb_fut
        self.kb_fut = None
        return ret

    async def print(self, message: str, is_line: bool = True) -> None:
        async with self.print_lock:
            if is_line:
                message += "\n"
            while message:
                fut = self._loop.create_future()
                self._loop.add_writer(self.out_fd, self._req_stdout, fut)
                await fut
                ret = sys.stdout.write(message)
                message = message[ret:]
            sys.stdout.flush()

    def _req_stdout(self, fut: asyncio.Future) -> None:
        fut.set_result(None)
        self._loop.remove_writer(self.out_fd)

    def _process_keyboard(self) -> None:
        data = os.read(self.kb_fd, 4096)
        parts = data.split(b"\n", 1)
        parts[0] = self.kb_buf + parts[0]
        self.kb_buf = parts.pop()
        if parts and self.kb_fut is not None:
            self.kb_fut.set_result(parts[0].decode())

    async def close(self):
        if not self.connected:
            return
        self.connected = False
        self.writer.close()
        await self.writer.wait_closed()


def get_connection():
    parser = argparse.ArgumentParser(
        description="Unix Socket")
    parser.add_argument(
        "-s", "--socketfile", default="~/printer_data/comms/moonraker.sock",
        metavar='<socketfile>',
        help="Path to Moonraker Unix Domain Socket"
    )
    args = parser.parse_args()
    sockpath = pathlib.Path(args.socketfile).expanduser().resolve()

    conn = UnixConnection(sockpath)
    return conn

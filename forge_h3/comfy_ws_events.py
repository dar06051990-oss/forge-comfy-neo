"""Tiny ComfyUI websocket relay, executed with ComfyUI's own Python.

It deliberately depends only on aiohttp, which is already a ComfyUI runtime dependency.
Each text websocket message is copied as one JSON line to stdout for the Forge bridge.
"""
import argparse
import asyncio
import sys

import aiohttp


async def run(host: str, port: int, client_id: str):
    url = f"http://{host}:{port}/ws?clientId={client_id}"
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=None)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.ws_connect(url, heartbeat=20) as ws:
            print("__H3_WS_READY__", flush=True)
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    # Comfy's text payload already is JSON. Keep it untouched.
                    print(msg.data, flush=True)
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--client", required=True)
    ns = ap.parse_args()
    try:
        asyncio.run(run(ns.host, ns.port, ns.client))
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(f"__H3_WS_ERROR__:{exc}", flush=True)
        raise


if __name__ == "__main__":
    main()

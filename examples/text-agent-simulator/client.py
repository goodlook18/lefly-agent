"""Small interactive client for the LeFly Agent Control WebSocket."""

import argparse
import asyncio
import json
from datetime import datetime, timezone
from uuid import uuid4

from aiohttp import ClientSession, WSMsgType


def _submission(text: str):
    return {
        "version": "1",
        "id": str(uuid4()),
        "type": "agent.submit_text",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "text": text,
    }


async def _receive(websocket) -> None:
    async for message in websocket:
        if message.type != WSMsgType.TEXT:
            continue
        value = json.loads(message.data)
        message_type = value.get("type", "unknown")
        if message_type == "agent.message":
            chat = value["message"]
            print("\n[%s] %s" % (chat["role"], chat["text"]), flush=True)
        elif message_type == "agent.error":
            print("\n[error:%s] %s" % (value["code"], value["message"]), flush=True)
        elif message_type == "agent.state":
            print("\n[state] %s" % value["state"]["phase"], flush=True)


async def run(url: str) -> None:
    async with ClientSession() as session:
        async with session.ws_connect(url) as websocket:
            receiver = asyncio.create_task(_receive(websocket))
            try:
                while True:
                    text = (await asyncio.to_thread(input, "> ")).strip()
                    if text in {"/quit", "/exit"}:
                        break
                    if text:
                        await websocket.send_json(_submission(text))
            finally:
                receiver.cancel()
                await asyncio.gather(receiver, return_exceptions=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Talk to a LeFly text agent")
    parser.add_argument("--url", default="ws://127.0.0.1:8767/ws/agent")
    args = parser.parse_args()
    asyncio.run(run(args.url))


if __name__ == "__main__":
    main()

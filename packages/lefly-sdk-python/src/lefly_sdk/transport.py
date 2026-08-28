"""Transport interfaces used by the LeFly device client."""

from typing import Protocol


class Transport(Protocol):
    async def send(self, message: str) -> None:
        ...

    async def receive(self) -> str:
        ...

    async def close(self) -> None:
        ...


class Connector(Protocol):
    async def connect(self, url: str) -> Transport:
        ...

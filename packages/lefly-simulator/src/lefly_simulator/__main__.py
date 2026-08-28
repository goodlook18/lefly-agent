"""Command-line entry point for the LeFly simulator server."""

import argparse
import asyncio
import signal
from typing import Optional

from aiohttp import web

from .engine import SimulatorEngine
from .router import TargetRouter
from .server import create_app
from .target import RemoteTarget, SimulatorTarget


def _host(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise argparse.ArgumentTypeError("host must be non-empty")
    return value


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("port must be an integer") from error
    if port < 0 or port > 65535:
        raise argparse.ArgumentTypeError("port must be between 0 and 65535")
    return port


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the LeFly simulator console")
    parser.add_argument("--host", default="127.0.0.1", type=_host)
    parser.add_argument("--port", default=8766, type=_port)
    parser.add_argument("--remote", help="remote LeFly device WebSocket URL")
    return parser


def _create_application(remote: Optional[str]) -> web.Application:
    simulator = SimulatorTarget("simulator", SimulatorEngine("lefly-sim-01"))
    targets = [simulator]
    if remote:
        targets.append(RemoteTarget("remote", url=remote, device_id="lefly-sim-01"))
    return create_app(router=TargetRouter(targets))


def _bound_url(socket) -> str:
    address = socket.getsockname()
    host, port = address[0], address[1]
    display_host = "[%s]" % host if ":" in host else host
    return "http://%s:%s" % (display_host, port)


async def serve(
    host: str,
    port: int,
    remote: Optional[str],
    *,
    shutdown_event: Optional[asyncio.Event] = None,
) -> None:
    host = _host(host)
    port = _port(str(port))
    app = _create_application(remote)
    runner = web.AppRunner(app)
    setup_complete = False
    installed_signals = []
    try:
        await runner.setup()
        setup_complete = True
        site = web.TCPSite(runner, host=host, port=port)
        await site.start()
        server = getattr(site, "_server", None)
        sockets = () if server is None else tuple(server.sockets or ())
        if not sockets:
            raise RuntimeError("server started without a bound socket")
        print("LeFly simulator: %s" % _bound_url(sockets[0]), flush=True)

        stop = shutdown_event or asyncio.Event()
        if shutdown_event is None:
            loop = asyncio.get_running_loop()
            for signum in (signal.SIGINT, signal.SIGTERM):
                try:
                    loop.add_signal_handler(signum, stop.set)
                except (NotImplementedError, RuntimeError):
                    continue
                installed_signals.append(signum)
        await stop.wait()
    finally:
        if installed_signals:
            loop = asyncio.get_running_loop()
            for signum in installed_signals:
                loop.remove_signal_handler(signum)
        if setup_complete:
            await runner.cleanup()


def main() -> None:
    args = build_parser().parse_args()
    try:
        asyncio.run(serve(args.host, args.port, args.remote))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

"""Command-line entry point for the clean LeFly text agent."""

import argparse
import asyncio
import logging
import os
import signal
import sys
from contextlib import AsyncExitStack
from typing import Optional

from .config import DEVICE_ID_PATTERN, LeFlyAgentConfig, load_agent_config
from .logging_setup import configure_debug_logging


logger = logging.getLogger(__name__)


def _host(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise argparse.ArgumentTypeError("host must be non-empty")
    host = value.strip().lower()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise argparse.ArgumentTypeError("host must be a loopback address")
    return host


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("port must be an integer") from error
    if port < 0 or port > 65535:
        raise argparse.ArgumentTypeError("port must be between 0 and 65535")
    return port


def _device_id(value: str) -> str:
    if not isinstance(value, str) or DEVICE_ID_PATTERN.fullmatch(value) is None:
        raise argparse.ArgumentTypeError(
            "device ID must match ^[a-z][a-z0-9_-]{0,63}$"
        )
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the LeFly text agent")
    parser.add_argument("--config")
    parser.add_argument("--model-profile")
    parser.add_argument("--device-url")
    parser.add_argument("--device-id", type=_device_id)
    parser.add_argument("--host", type=_host)
    parser.add_argument("--port", type=_port)
    parser.add_argument(
        "--debug",
        action="store_true",
        help="write DEBUG diagnostics to a new logs/lefly-agent.*.log file",
    )
    return parser


def _resolve_parsed_settings(args, environ) -> LeFlyAgentConfig:
    return load_agent_config(
        args.config,
        model_profile=args.model_profile,
        environ=environ,
        cli={
            "device_url": args.device_url,
            "device_id": args.device_id,
            "host": args.host,
            "port": args.port,
        },
    )


def resolve_settings(
    argv: Optional[list[str]] = None,
    *,
    environ=None,
) -> LeFlyAgentConfig:
    args = build_parser().parse_args(argv)
    return _resolve_parsed_settings(
        args,
        os.environ if environ is None else environ,
    )


def _create_model(config: LeFlyAgentConfig):
    api_key = config.secrets.llm_api_key
    if api_key is None:
        return None
    from .llm_factory import build_llm

    return build_llm(config.model, api_key=api_key)


def _load_livekit_components():
    try:
        from .livekit_agent import LeFlyLiveKitAgent
        from .livekit_session import LiveKitTextSession
    except ModuleNotFoundError as error:
        if error.name == "livekit" or (error.name or "").startswith("livekit."):
            raise RuntimeError(
                "LLM dependencies are not installed; run "
                "python -m pip install 'packages/lefly-agent[llm]'"
            ) from error
        raise
    return LeFlyLiveKitAgent, LiveKitTextSession


async def serve(
    config: LeFlyAgentConfig,
    *,
    shutdown_event: Optional[asyncio.Event] = None,
) -> None:
    import aiohttp
    from aiohttp import web
    from lefly_sdk import DeviceClient, RemoteHardwareController

    from .fast_intent import FastIntentRouter
    from .info import InfoService, QWeatherClient, TavilyClient
    from .interpreter import DeterministicChineseInterpreter
    from .robot import RobotCommandService
    from .runtime import AgentRuntime
    from .server import create_app
    from .status import StatusCoordinator
    from .touch import TouchBehaviorDispatcher

    client = DeviceClient(
        config.agent.device_url,
        request_timeout=config.agent.request_timeout,
    )
    controller = RemoteHardwareController(
        client,
        device_id=config.agent.device_id,
    )
    robot = RobotCommandService(
        controller,
        client,
        device_id=config.agent.device_id,
    )
    fast_router = FastIntentRouter(config.agent.fast_intent_aliases)

    async with AsyncExitStack() as resources:
        livekit_session = None
        info_service = None
        status_coordinator = None
        touch_dispatcher = None
        if config.secrets.llm_api_key is not None:
            LeFlyLiveKitAgent, LiveKitTextSession = _load_livekit_components()
            provider_session = await resources.enter_async_context(
                aiohttp.ClientSession()
            )
            qweather = None
            if (
                config.secrets.qweather_api_key is not None
                and config.search.qweather_api_host is not None
            ):
                qweather = QWeatherClient(
                    provider_session,
                    credential=config.secrets.qweather_api_key,
                    api_host=config.search.qweather_api_host,
                    timeout=config.agent.request_timeout,
                )
            tavily = None
            if config.secrets.tavily_api_key is not None:
                tavily = TavilyClient(
                    provider_session,
                    api_key=config.secrets.tavily_api_key,
                    base_url=config.search.tavily_base_url,
                    timeout=config.agent.request_timeout,
                )
            info_service = InfoService(
                timezone=config.agent.timezone,
                default_city=config.agent.default_city,
                qweather=qweather,
                tavily=tavily,
                search_max_results=config.search.max_results,
            )
            model = _create_model(config)
            if model is None:
                raise RuntimeError("configured LLM mode did not create a model")
            livekit_agent = LeFlyLiveKitAgent(robot, info_service)
            livekit_session = LiveKitTextSession(
                livekit_agent,
                llm=model,
                max_tool_steps=config.model.max_tool_steps,
            )
            status_coordinator = StatusCoordinator(controller, client)
            touch_dispatcher = TouchBehaviorDispatcher(robot, config.touch)

        runtime = AgentRuntime(
            DeterministicChineseInterpreter(fast_router),
            robot,
            device_client=client,
            queue_capacity=config.agent.queue_capacity,
            history_capacity=config.agent.history_capacity,
            fast_router=fast_router if livekit_session is not None else None,
            livekit_session=livekit_session,
            info_service=info_service,
            status_coordinator=status_coordinator,
            touch_dispatcher=touch_dispatcher,
        )
        runner = web.AppRunner(create_app(runtime=runtime))
        installed_signals = []
        setup_complete = False
        client_started = False
        runtime_started = False
        try:
            runtime_started = True
            await runtime.start()
            client_started = True
            await client.start()
            await _prime_robot_state(client, robot)
            await runner.setup()
            setup_complete = True
            site = web.TCPSite(
                runner,
                host=config.server.host,
                port=config.server.port,
            )
            await site.start()
            server = getattr(site, "_server", None)
            sockets = () if server is None else tuple(server.sockets or ())
            if not sockets:
                raise RuntimeError("agent server started without a bound socket")
            address = sockets[0].getsockname()
            print(
                "LeFly text agent: http://%s:%s" % (address[0], address[1]),
                flush=True,
            )

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
            if runtime_started:
                await runtime.close()
            if client_started:
                await client.close()


async def _prime_robot_state(client, robot, timeout: float = 1.0) -> None:
    """Synchronize one authoritative state when a device is already available."""
    try:
        await client.wait_until_connected(timeout=timeout)
        await robot.synchronize_state(timeout=timeout)
    except Exception as error:
        logger.info(
            "agent.device.initial_state_deferred",
            extra={"error_type": type(error).__name__},
        )


def run(argv: Optional[list[str]] = None, *, environ=None) -> None:
    args = build_parser().parse_args(argv)
    debug_session = configure_debug_logging(args.debug)
    try:
        config = _resolve_parsed_settings(
            args,
            os.environ if environ is None else environ,
        )
        if debug_session is not None:
            logger.info("agent.startup.config %s", config.health_summary())
        asyncio.run(serve(config))
    except KeyboardInterrupt:
        pass
    except Exception:
        if debug_session is not None:
            logger.exception("agent.startup.failed")
        raise
    finally:
        if debug_session is not None:
            debug_session.close()


def main() -> None:
    run(sys.argv[1:])


if __name__ == "__main__":
    main()

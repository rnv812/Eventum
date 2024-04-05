import asyncio
import importlib
import logging
import signal
from multiprocessing import Queue
from multiprocessing.sharedctypes import SynchronizedBase
from multiprocessing.synchronize import Event as EventClass
from typing import Callable, NoReturn, Optional, assert_never

import eventum.logging_config
import numpy as np
from eventum.core import settings
from eventum.core.batcher import Batcher
from eventum.core.models.application_config import (InputConfigMapping,
                                                    JinjaEventConfig,
                                                    OutputConfigMapping,
                                                    OutputType)
from eventum.core.models.time_mode import TimeMode
from eventum.core.plugins.event.base import (EventPluginConfigurationError,
                                             EventPluginRuntimeError)
from eventum.core.plugins.event.jinja import JinjaEventPlugin
from eventum.core.plugins.input.base import (InputPluginConfigurationError,
                                             InputPluginRuntimeError,
                                             LiveInputPlugin,
                                             SampleInputPlugin)
from eventum.core.plugins.output.base import (BaseOutputPlugin,
                                              OutputPluginConfigurationError,
                                              OutputPluginRuntimeError)
from eventum.core.plugins.output.file import FileOutputPlugin
from eventum.core.plugins.output.stdout import StdoutOutputPlugin
from eventum.repository.manage import ContentReadError
from numpy.typing import NDArray
from setproctitle import getproctitle, setproctitle

eventum.logging_config.apply()
logger = logging.getLogger(__name__)


def subprocess(module_name: str) -> Callable:
    """Parametrized decorator for all subprocesses."""

    def decorator(f: Callable):
        def wrapper(*args, **kwargs):
            setproctitle(f'{getproctitle()} [{module_name}]')

            signal.signal(signal.SIGINT, lambda signal, stack_frame: exit(0))

            result = f(*args, **kwargs)
            return result

        return wrapper
    return decorator


def _terminate_subprocess(
    is_done: EventClass,
    exit_code: int = 0,
    signal_queue: Optional[Queue] = None
) -> NoReturn:
    """Handle termination of subprocess."""
    if signal_queue is not None:
        signal_queue.put(None)
    is_done.set()
    exit(exit_code)


@subprocess('input')
def start_input_subprocess(
    config: InputConfigMapping,
    time_mode: TimeMode,
    queue: Queue,
    is_done: EventClass,
) -> None:
    input_type, input_conf = config.popitem()

    logger.info(f'Initializing "{input_type}" input plugin')

    try:
        plugin_module = importlib.import_module(
            f'eventum.core.plugins.input.{input_type.value}'
        )
        input_plugin: SampleInputPlugin | LiveInputPlugin = (
            plugin_module.load_plugin()     # type: ignore
        )
        input_plugin.create_from_config(config=input_conf)
    except ImportError as e:
        logger.error(f'Failed to load input plugin: {e}')
        _terminate_subprocess(is_done, 1, queue)
    except ContentReadError as e:
        logger.error(f'Failed to load content for input plugin: {e}')
        _terminate_subprocess(is_done, 1, queue)
    except InputPluginConfigurationError as e:
        logger.error(f'Failed to initialize input plugin: {e}')
        _terminate_subprocess(is_done, 1, queue)
    except Exception as e:
        logger.error(
            'Unexpected error occurred during initializing '
            f'input plugin: {e}'
        )
        _terminate_subprocess(is_done, 1, queue)

    logger.info('Input plugin is successfully initialized')

    try:
        with Batcher(
            size=settings.EVENTS_BATCH_SIZE,
            timeout=settings.EVENTS_BATCH_TIMEOUT,
            callback=queue.put
        ) as batcher:
            match time_mode:
                case TimeMode.LIVE:
                    input_plugin.live(on_event=batcher.add)
                case TimeMode.SAMPLE:
                    input_plugin.sample(on_event=batcher.add)
                case _:
                    assert_never(time_mode)
    except AttributeError:
        logger.error(
            f'Specified input plugin does not support "{time_mode}" mode'
        )
        _terminate_subprocess(is_done, 1, queue)
    except InputPluginRuntimeError as e:
        logger.error(f'Error occurred during input plugin execution: {e}')
        _terminate_subprocess(is_done, 1, queue)
    except Exception as e:
        logger.error(
            f'Unexpected error occurred during input plugin execution: {e}'
        )
        _terminate_subprocess(is_done, 1, queue)

    logger.info('Stopping input plugin')
    _terminate_subprocess(is_done, 0, queue)


@subprocess('event')
def start_event_subprocess(
    config: JinjaEventConfig,
    input_queue: Queue,
    event_queue: Queue,
    is_done: EventClass
) -> None:
    logger.info('Initializing event plugin')

    try:
        event_plugin = JinjaEventPlugin(config)
    except EventPluginConfigurationError as e:
        logger.error(f'Failed to initialize event plugin: {e}')
        _terminate_subprocess(is_done, 1, event_queue)
    except Exception as e:
        logger.error(
            f'Unexpected error occurred during initializing '
            f'event plugin: {e}'
        )
        _terminate_subprocess(is_done, 1, event_queue)

    logger.info('Event plugin is successfully initialized')

    is_running = True
    while is_running:
        timestamps_batch = input_queue.get()
        if timestamps_batch is None:
            is_running = False
            break

        try:
            with Batcher(
                size=settings.OUTPUT_BATCH_SIZE,
                timeout=settings.OUTPUT_BATCH_TIMEOUT,
                callback=event_queue.put
            ) as batcher:
                for timestamp in timestamps_batch:
                    for event in event_plugin.render(timestamp=timestamp):
                        batcher.add(event)
        except EventPluginRuntimeError as e:
            logger.error(f'Failed to produce event: {e}')
            _terminate_subprocess(is_done, 1, event_queue)
        except Exception as e:
            logger.error(
                f'Unexpected error occurred during producing event: {e}'
            )
            _terminate_subprocess(is_done, 1, event_queue)

    logger.info('Stopping event plugin')
    _terminate_subprocess(is_done, 0, event_queue)


@subprocess('output')
def start_output_subprocess(
    config: OutputConfigMapping,
    queue: Queue,
    processed_events: SynchronizedBase,
    is_done: EventClass
) -> None:
    plugins_list_fmt = ", ".join([f'"{plugin}"' for plugin in config.keys()])

    logger.info(f'Initializing [{plugins_list_fmt}] output plugins')

    output_plugins: list[BaseOutputPlugin] = []

    for output, output_conf in config.items():
        try:
            match output:
                case OutputType.STDOUT:
                    output_plugins.append(
                        StdoutOutputPlugin(format=output_conf.format)
                    )
                case OutputType.FILE:
                    output_plugins.append(
                        FileOutputPlugin(
                            filepath=output_conf.path,  # type: ignore
                            format=output_conf.format,
                        )
                    )
                case val:
                    assert_never(val)
        except OutputPluginConfigurationError as e:
            logger.error(f'Failed to initialize "{output}" output plugin: {e}')
            _terminate_subprocess(is_done, 1)
        except Exception as e:
            logger.error(
                'Unexpected error occurred during initializing '
                f'"{output}" output plugin: {e}'
            )
            _terminate_subprocess(is_done, 1)

    logger.info('Output plugins are successfully initialized')

    async def write_batch(
        plugin: BaseOutputPlugin,
        events_batch: NDArray[np.str_]
    ) -> None:
        batch_size = len(events_batch)
        try:
            if batch_size == 1:
                count = await plugin.write(events_batch[0])
            elif batch_size > 1:
                count = await plugin.write_many(events_batch)
        except OutputPluginRuntimeError as e:
            logger.error(f'Output plugin failed to write events: {e}')

        if count < batch_size:
            logger.warning(
                f'Only {count} events were written by output plugin from '
                f' batch with size {batch_size}'
            )

    async def run_loop() -> None:
        await asyncio.gather(
            *[plugin.open() for plugin in output_plugins]
        )

        is_running = True
        while is_running:
            events_batch = queue.get()

            if events_batch is None:
                is_running = False
                break

            await asyncio.gather(
                *[
                    write_batch(plugin, events_batch)
                    for plugin in output_plugins
                ]
            )

            processed_events.value += len(events_batch)  # type: ignore

        await asyncio.gather(
            *[plugin.close() for plugin in output_plugins]
        )

    asyncio.run(run_loop())

    logger.info('Stopping output plugins')
    _terminate_subprocess(is_done, 0)

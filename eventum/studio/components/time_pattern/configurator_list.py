from typing import Callable, MutableMapping, Optional

import eventum.core.models.time_pattern_config as models
import streamlit as st
from eventum.repository.manage import (ContentReadError,
                                       get_time_pattern_filenames,
                                       load_time_pattern)
from eventum.studio.components.component import BaseComponent
from eventum.studio.components.time_pattern.configurator import \
    Configurator
from eventum.studio.notifiers import NotificationLevel, default_notifier
from eventum.studio.widget_management import WidgetKeysContext
from pydantic import ValidationError


class ConfiguratorList(BaseComponent):
    """Component for managing list of `Configurator`'s."""

    _TIME_PATTERN_COLORS = ('blue', 'green', 'orange', 'red', 'violet')
    _MAX_LIST_SIZE = 5

    def __init__(
        self,
        session_state: MutableMapping = st.session_state,
        id: int = 1,
        widget_keys_context: Optional[WidgetKeysContext] = None,
        props: Optional[dict] = None
    ) -> None:
        self._check_time_pattern_colors()
        super().__init__(session_state, id, widget_keys_context, props)

    def _init_state(self):
        self._session_state['time_pattern_id_counter'] = 1
        self._session_state['time_patterns_counter'] = 0
        self._session_state['time_pattern_ids'] = []
        self._session_state['available_colors'] = set(
            ConfiguratorList._TIME_PATTERN_COLORS
        )
        self._session_state['given_colors'] = dict()
        self._session_state['loaded_timepattern_filenames'] = set()

    def _show_manage_buttons(self):
        is_max_len = len(
            self._session_state['time_pattern_ids']
        ) >= self._MAX_LIST_SIZE

        st.button(
            'Create new',
            key=self._wk.get_ephemeral(),
            disabled=is_max_len,
            on_click=lambda: self.add(),
            use_container_width=True
        )
        col1, col2 = st.columns([7, 3])

        selected_pattern = col1.selectbox(
            'Time patterns',
            options=get_time_pattern_filenames(),
            key=self._wk('pattern_selected_for_load'),
            label_visibility='collapsed'
        )

        col2.button(
            'Load',
            key=self._wk.get_ephemeral(),
            disabled=(
                is_max_len
                or not selected_pattern
            ),
            on_click=lambda: self.load_time_pattern(
                filename=selected_pattern   # type: ignore
            ),
            use_container_width=True,
        )

        if is_max_len:
            st.write('*:grey[Maximum number of patterns]*')

    def _show(self):
        st.title('Time Patterns')
        for id in self._session_state['time_pattern_ids']:
            Configurator(
                id=id,
                widget_keys_context=self._wk,
                props={
                    'save_callback': (
                        lambda filename: self._session_state[
                            'loaded_timepattern_filenames'
                        ].add(filename)
                    ),
                    'delete_callback': (
                        lambda id=id: self.delete(id)
                    )
                }
            )._show()
        if not self._session_state['time_pattern_ids']:
            st.markdown(
                (
                    '<div style="text-align: center; color: grey;">'
                    'No time patterns. Create or load one.'
                    '</div>'
                ),
                unsafe_allow_html=True
            )
        st.divider()
        self._show_manage_buttons()

    def add(
        self,
        initial_state: Optional[models.TimePatternConfig] = None,
        pattern_filename: Optional[str] = None
    ) -> None:
        """Add time pattern adjuster element to list."""
        if self._session_state['time_patterns_counter'] == self._MAX_LIST_SIZE:
            raise ValueError(
                f'Max size ({self._MAX_LIST_SIZE}) of list is exceeded'
            )

        id = self._session_state['time_pattern_id_counter']
        self._session_state['time_pattern_ids'].append(id)
        self._session_state['time_pattern_id_counter'] += 1
        self._session_state['time_patterns_counter'] += 1

        color = self._session_state['available_colors'].pop()
        self._session_state['given_colors'][id] = color

        Configurator(
            id=id,
            widget_keys_context=self._wk,
            props={
                'initial_state': initial_state,
                'pattern_filename': pattern_filename,
                'color': color
            }
        )

    def load_time_pattern(
        self,
        filename: str,
        notify_callback: Callable[
            [str, NotificationLevel], None
        ] = default_notifier
    ) -> None:
        """Load selected time pattern from repository"""
        if filename in self._session_state['loaded_timepattern_filenames']:
            notify_callback(
                'Time pattern is already loaded',
                NotificationLevel.WARNING
            )
            return

        try:
            time_pattern = load_time_pattern(filename)
        except ContentReadError as e:
            notify_callback(str(e), NotificationLevel.ERROR)
            return

        self.add(
            initial_state=time_pattern,
            pattern_filename=self._session_state['pattern_selected_for_load']
        )
        self._session_state['loaded_timepattern_filenames'].add(filename)

    def delete(self, id: int) -> None:
        """Delete specified time pattern adjuster from list."""
        time_pattern = Configurator(
            id=id,
            widget_keys_context=self._wk
        )
        if time_pattern.is_saved():
            self._session_state['loaded_timepattern_filenames'].remove(
                time_pattern.get_saved_filename()
            )
        time_pattern.release_state()

        self._session_state['time_pattern_ids'].remove(id)
        self._session_state['time_patterns_counter'] -= 1
        self._session_state['available_colors'].add(
            self._session_state['given_colors'].pop(id)
        )

    def get_pattern_configs(self) -> list[models.TimePatternConfig]:
        """Get list of currently adjusted time pattern configs."""
        configs = []
        for id in self._session_state['time_pattern_ids']:
            pattern = Configurator(id=id, widget_keys_context=self._wk)
            try:
                configs.append(pattern.get_current_configuration())
            except ValidationError as e:
                default_notifier(
                    message=(
                        f'Field validation fail for "{e.title}" '
                        f'in time pattern with label "{pattern.label}'
                    ),
                    level=NotificationLevel.ERROR
                )

        return configs

    def get_pattern_colors(
        self,
        hex_format: bool = True
    ) -> list[str]:
        """Get list of current time pattern colors."""
        colors: dict[int, str] = self._session_state['given_colors']
        if hex_format:
            st_colors = {
                'blue': '#60b4ff',
                'green': '#3dd56d',
                'orange': '#ffbd45',
                'red': '#ff4b4b',
                'violet': '#b27eff'
            }
            return [st_colors[color] for color in colors.values()]
        else:
            return [color for color in colors.values()]

    @classmethod
    def _check_time_pattern_colors(cls) -> None:
        """Check if number of unique time pattern colors is greater or equal
        to `_MAX_LIST_SIZE`. Raise ValueError otherwise.
        """
        unique_colors = len(set(cls._TIME_PATTERN_COLORS))
        if unique_colors < cls._MAX_LIST_SIZE:
            raise ValueError(
                f'`_MAX_SIZE` is set to {cls._MAX_LIST_SIZE} but there are '
                f'only {unique_colors} unique colors in `_LABEL_COLORS`'
            )

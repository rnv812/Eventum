from typing import Callable, MutableMapping, Optional

import streamlit as st
from eventum.studio.components.component import BaseComponent
from eventum.studio.components.time_pattern_adjuster import TimePatternAdjuster

from eventum.studio.key_management import WidgetKeysContext


class TimePatternAdjustersList(BaseComponent):
    """List of time pattern adjusters."""

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

    def _init_session(self):
        self._session_state['time_pattern_id_counter'] = 1
        self._session_state['time_pattern_ids'] = []
        self._session_state['available_colors'] = set(
            TimePatternAdjustersList._TIME_PATTERN_COLORS
        )

    def _show(self):
        st.title('Time Patterns')
        for id in self._session_state['time_pattern_ids']:
            TimePatternAdjuster(id=id, widget_keys_context=self._wk)

    def _release_session(self):
        del self._session_state['time_pattern_id_counter']
        del self._session_state['time_pattern_ids']
        del self._session_state['available_colors']

        super()._release_session()

    def add(self, mutate_state_callback: Callable[[int]]) -> None:
        """Add time pattern adjuster element to list."""
        id = self._session_state['time_pattern_id_counter']
        self._session_state['time_pattern_ids'].append(id)
        self._session_state['time_pattern_id_counter'] += 1

        mutate_state_callback(id)

    def delete(self, id: int, mutate_state_callback: Callable[[int]]) -> None:
        """Delete specified time pattern adjuster from list."""
        self._session_state['time_pattern_ids'].remove(id)

        mutate_state_callback(id)

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
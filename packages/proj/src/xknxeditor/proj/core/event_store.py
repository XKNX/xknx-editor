"""Undo/redo history layered over a project ``Session``.

A cursor points at the highest non-reverted event. ``undo``/``redo`` toggle the ``reverted`` flag
and move the cursor without deleting rows, so history stays replayable across reopen. ``append``
drops any redo branch, applies the event, and persists it.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from xknxeditor.proj.core.events import CompositeEvent, Event, deserialize_event
from xknxeditor.proj.core.modified import stamp_modified
from xknxeditor.proj.models import Event as EventModel


@dataclass(frozen=True)
class HistoryEntry:
    id: int
    event_type: str
    data: dict[str, Any]
    reverted: bool


class EventStore:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._cursor = self._calculate_cursor()

    def _calculate_cursor(self) -> int:
        row = self._session.execute(
            select(EventModel.id)
            .where(EventModel.reverted == False)  # noqa: E712
            .order_by(EventModel.id.desc())
            .limit(1)
        ).scalar()
        return row if row is not None else 0

    def append(self, event: Event) -> Event:
        # A failing apply/commit (e.g. a foreign-key violation from a stale id) must not leave the
        # shared session in a pending-rollback state, or every later operation raises. Roll back and
        # re-raise: the redo-branch truncation is undone too, and the cursor stays put, so the store
        # is left exactly as before the failed append.
        try:
            # Discard the redo branch: every event above the cursor (a previously-reverted tail from
            # earlier undos) is now dead history. This must run even when the cursor is 0 (everything
            # undone) — those reverted rows still carry lower ids than the new event, so redo() would
            # otherwise resurrect one of them instead of finding nothing.
            self._session.execute(
                delete(EventModel).where(EventModel.id > self._cursor)
            )

            event.apply(self._session)
            stamp_modified(self._session, event)

            model = EventModel(
                type=event.event_type,
                data=event.to_dict(),
                timestamp=datetime.now(UTC),
                reverted=False,
            )
            self._session.add(model)
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise

        self._cursor = model.id
        return event

    def group_since(self, start: int, label: str | None = None) -> None:
        """Make the events appended after the cursor ``start`` one undo step (one composite
        event, ``label`` naming it in the history). They are applied already, with the state
        their undo needs, so nothing is applied again. Fewer than two leave the history as it
        is; so does a ``start`` the history moved below since (undone events)."""
        models = (
            self._session.execute(
                select(EventModel)
                .where(EventModel.id > start)
                .order_by(EventModel.id.asc())
            )
            .scalars()
            .all()
        )
        if len(models) < 2 or any(model.reverted for model in models):
            return
        events = [deserialize_event(model.type, model.data) for model in models]
        composite = CompositeEvent(events=events, label=label)
        try:
            for model in models:
                self._session.delete(model)
            grouped = EventModel(
                type=composite.event_type,
                data=composite.to_dict(),
                timestamp=models[-1].timestamp,
                reverted=False,
            )
            self._session.add(grouped)
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise
        self._cursor = grouped.id

    def undo(self) -> bool:
        if not self.can_undo():
            return False

        model = self._session.get(EventModel, self._cursor)
        if model is None:
            return False

        event = deserialize_event(model.type, model.data)
        event.revert(self._session)
        stamp_modified(self._session, event, reverting=True)
        model.reverted = True
        self._session.commit()

        prev = self._session.execute(
            select(EventModel.id)
            .where(EventModel.id < self._cursor)
            .where(EventModel.reverted == False)  # noqa: E712
            .order_by(EventModel.id.desc())
            .limit(1)
        ).scalar()
        self._cursor = prev if prev is not None else 0
        return True

    def redo(self) -> bool:
        next_id = self._session.execute(
            select(EventModel.id)
            .where(EventModel.id > self._cursor)
            .where(EventModel.reverted == True)  # noqa: E712
            .order_by(EventModel.id.asc())
            .limit(1)
        ).scalar()
        if next_id is None:
            return False

        model = self._session.get(EventModel, next_id)
        if model is None:
            return False

        event = deserialize_event(model.type, model.data)
        event.apply(self._session)
        stamp_modified(self._session, event)
        model.reverted = False
        self._session.commit()

        self._cursor = model.id
        return True

    def can_undo(self) -> bool:
        return self._cursor > 0

    def can_redo(self) -> bool:
        return (
            self._session.execute(
                select(EventModel.id)
                .where(EventModel.id > self._cursor)
                .where(EventModel.reverted == True)  # noqa: E712
                .limit(1)
            ).scalar()
            is not None
        )

    def peek_undo(self) -> tuple[str, dict[str, Any]] | None:
        """The (type, data) of the event ``undo()`` would revert next, or ``None``."""
        if not self.can_undo():
            return None
        model = self._session.get(EventModel, self._cursor)
        return None if model is None else (model.type, model.data)

    def peek_redo(self) -> tuple[str, dict[str, Any]] | None:
        """The (type, data) of the event ``redo()`` would re-apply next, or ``None``."""
        next_id = self._session.execute(
            select(EventModel.id)
            .where(EventModel.id > self._cursor)
            .where(EventModel.reverted == True)  # noqa: E712
            .order_by(EventModel.id.asc())
            .limit(1)
        ).scalar()
        if next_id is None:
            return None
        model = self._session.get(EventModel, next_id)
        return None if model is None else (model.type, model.data)

    def jump_to(self, target_id: int) -> None:
        while self._cursor > target_id and self.can_undo():
            self.undo()
        while self._cursor < target_id and self.can_redo():
            self.redo()

    def history(self) -> list[HistoryEntry]:
        """Every event newest-first as (id, command name, payload, reverted); labelling is left to
        the caller (a UI concern)."""
        models = (
            self._session.execute(select(EventModel).order_by(EventModel.id.desc()))
            .scalars()
            .all()
        )
        return [
            HistoryEntry(
                id=model.id,
                event_type=model.type,
                data=model.data,
                reverted=model.reverted,
            )
            for model in models
        ]

    @property
    def cursor(self) -> int:
        return self._cursor

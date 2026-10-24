"""The Comment tab: the comment of the element selected last in a tree, and for a device its
installation hints too, each with bold, italic and underline."""

from __future__ import annotations

from collections.abc import Hashable
from typing import TYPE_CHECKING, cast

from imgui_bundle import imgui

from editor_gui.plugins.project.strings import S
from editor_gui.widgets.dpi import px
from editor_gui.widgets.rich_text_edit import RichTextEdit

if TYPE_CHECKING:
    from editor_gui.plugins.project.service import ProjectService

_HINTS = "installation_hints"
_COMMENT = "comment"


class CommentPanel:
    def __init__(self, project: ProjectService) -> None:
        self._project = project
        self._hints = RichTextEdit("installation_hints")
        self._comment = RichTextEdit("comment")

    def render(self) -> None:
        target = self._project.comment_target if self._project.is_open else None
        comment = None if target is None else self._project.comment(*target)
        if target is None or comment is None:
            imgui.text_disabled(S.COMMENT_NOTHING_SELECTED)
            return
        kind, element_id = target
        style = imgui.get_style()
        # Each field gets the same share of the height; its row with the title and the buttons
        # comes with it.
        extra = imgui.get_frame_height() + 2.0 * style.item_spacing.y
        if kind == "device":
            hints = self._project.comment(kind, element_id, _HINTS) or ""
            # Below the first field as much room as below the second one, which also has the
            # window's padding.
            gap = style.window_padding.y
            height = (imgui.get_content_region_avail().y - 2.0 * extra - gap) / 2.0
            self._field(
                S.COMMENT_INSTALLATION_HINTS,
                self._hints,
                target,
                _HINTS,
                hints,
                max(height, px(60.0)),
            )
            imgui.set_cursor_pos_y(imgui.get_cursor_pos_y() + gap)
        else:
            self._apply(self._hints.tick())
        height = max(imgui.get_content_region_avail().y - extra, px(60.0))
        self._field(S.GA_COMMENT, self._comment, target, _COMMENT, comment, height)

    def _field(
        self,
        title: str,
        editor: RichTextEdit,
        target: tuple[str, int],
        field: str,
        stored: str,
        height: float,
    ) -> None:
        self._apply(editor.render((*target, field), stored, height, title))

    def tick(self) -> None:
        """Every frame: store an edit whose tab was left."""
        self._apply(self._hints.tick())
        self._apply(self._comment.tick())

    def _apply(self, commit: tuple[Hashable, str] | None) -> None:
        if commit is None:
            return
        key, rtf = commit
        kind, element_id, field = cast(tuple[str, int, str], key)
        self._project.set_comment(kind, element_id, rtf, field)

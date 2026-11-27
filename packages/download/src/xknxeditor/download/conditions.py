"""Property alternatives and state-dependent writes and reads in prepared load plans."""

from dataclasses import dataclass

from xknxeditor.namespaces.intermediate.ld_ctrl_compare_prop_t import LdCtrlCompareProp
from xknxeditor.namespaces.intermediate.ld_ctrl_load_image_prop_t import (
    LdCtrlLoadImageProp,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_write_prop_t import LdCtrlWriteProp


@dataclass(frozen=True)
class PropertyAlternatives:
    comparisons: tuple[LdCtrlCompareProp, ...]

    def __post_init__(self) -> None:
        if not self.comparisons:
            raise ValueError("A property check requires at least one accepted value")


@dataclass(frozen=True)
class ConditionalWrite:
    conditions: tuple[LdCtrlCompareProp, ...]
    write: LdCtrlWriteProp


@dataclass(frozen=True)
class ConditionalRead:
    """Read a property into the image only while every condition matches the device."""

    conditions: tuple[LdCtrlCompareProp, ...]
    read: LdCtrlLoadImageProp


@dataclass(frozen=True)
class FinalPropertyRead:
    """Read a mandatory completion record after writes; preflight skips this step."""

    read: LdCtrlLoadImageProp
    size: int

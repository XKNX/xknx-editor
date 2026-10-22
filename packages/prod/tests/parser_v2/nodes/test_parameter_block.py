from xknxeditor.namespaces.intermediate.access_t import Access
from xknxeditor.namespaces.intermediate.application_program_channel_t import (
    ComObjectParameterBlock,
)
from xknxeditor.prod.parser_v2.nodes import EvalContext, GlobalState
from xknxeditor.prod.parser_v2.nodes.com_object_parameter_block import (
    ComObjectParameterBlockNode,
)
from xknxeditor.prod.parser_v2.ui.parameter_block import UiParameterBlock


def _eval(access: Access) -> list[object]:
    node = ComObjectParameterBlockNode(
        ComObjectParameterBlock(id="PB-1", text="Block", access=access), []
    )
    return list(node.eval(EvalContext(GlobalState())))


def test_read_access_does_not_restrict_the_block() -> None:
    # A block's own Access does not gate its parameters' editability (that is each parameter's
    # own access); a Read-access block is shown like any other.
    (block,) = _eval(Access.READ)
    assert isinstance(block, UiParameterBlock) and not block.hidden


def test_no_access_hides_the_block_but_keeps_it_evaluated() -> None:
    (block,) = _eval(Access.NONE)
    assert isinstance(block, UiParameterBlock) and block.hidden
    (block,) = _eval(Access.READ_WRITE)
    assert isinstance(block, UiParameterBlock) and not block.hidden

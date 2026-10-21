"""Runtime and compile errors of the VBScript interpreter, with the engine's numbers and texts."""

from __future__ import annotations

# Runtime error numbers.
INVALID_CALL = 5
OVERFLOW = 6
OUT_OF_MEMORY = 7
SUBSCRIPT_OUT_OF_RANGE = 9
DIVISION_BY_ZERO = 11
OUT_OF_STACK = 28
TYPE_MISMATCH = 13
FOR_NOT_INITIALIZED = 92
OBJECT_NOT_SET = 91
INVALID_USE_OF_NULL = 94
OBJECT_REQUIRED = 424
NO_SUCH_MEMBER = 438
WRONG_ARGUMENTS = 450
ILLEGAL_ASSIGNMENT = 501

RUNTIME_TEXTS: dict[int, str] = {
    INVALID_CALL: "Invalid procedure call or argument",
    OVERFLOW: "Overflow",
    OUT_OF_MEMORY: "Out of memory",
    SUBSCRIPT_OUT_OF_RANGE: "Subscript out of range",
    DIVISION_BY_ZERO: "Division by zero",
    OUT_OF_STACK: "Out of stack space",
    TYPE_MISMATCH: "Type mismatch",
    FOR_NOT_INITIALIZED: "For loop not initialized",
    INVALID_USE_OF_NULL: "Invalid use of Null",
    OBJECT_NOT_SET: "Object variable not set",
    OBJECT_REQUIRED: "Object required",
    NO_SUCH_MEMBER: "Object doesn't support this property or method",
    WRONG_ARGUMENTS: "Wrong number of arguments or invalid property assignment",
    ILLEGAL_ASSIGNMENT: "Illegal assignment",
    3: "Return without GoSub",
    10: "This array is fixed or temporarily locked",
    35: "Sub or Function not defined",
    48: "Error in loading DLL",
    51: "Internal error",
    53: "File not found",
    57: "Device I/O error",
    58: "File already exists",
    61: "Disk full",
    67: "Too many files",
    70: "Permission denied",
    75: "Path/File access error",
    76: "Path not found",
    93: "Invalid pattern string",
    322: "Can't create necessary temporary file",
    429: "ActiveX component can't create object",
    430: "Class doesn't support Automation",
    432: "File name or class name not found during Automation operation",
    445: "Object doesn't support this action",
    446: "Object doesn't support named arguments",
    447: "Object doesn't support current locale setting",
    448: "Named argument not found",
    449: "Argument not optional",
    451: "Object not a collection",
    453: "Specified DLL function not found",
    455: "Code resource lock error",
    457: "This key is already associated with an element of this collection",
    458: "Variable uses an Automation type not supported in VBScript",
    462: "The remote server machine does not exist or is unavailable",
    481: "Invalid picture",
    500: "Variable is undefined",
    502: "Object not safe for scripting",
    503: "Object not safe for initializing",
    504: "Object not safe for creating",
    505: "Invalid or unqualified reference",
    506: "Class not defined",
    507: "An exception occurred",
    5016: "Regular expression object expected",
    5017: "Syntax error in regular expression",
    5018: "Unexpected quantifier",
    5019: "Expected ']' in regular expression",
    5020: "Expected ')' in regular expression",
    5021: "Invalid range in character set",
    32811: "Element not found",
}

RUNTIME_SOURCE = "Microsoft VBScript runtime error"


class VBRuntimeError(Exception):
    """A runtime error raised while a script runs; ``detail`` is the name the engine appends."""

    def __init__(
        self,
        number: int,
        detail: str | None = None,
        *,
        description: str | None = None,
        source: str = RUNTIME_SOURCE,
    ) -> None:
        self.number = number
        self.detail = detail
        self.description = (
            description
            if description is not None
            else RUNTIME_TEXTS.get(number, "Unknown runtime error")
        )
        self.source = source
        super().__init__(self.message)

    @property
    def message(self) -> str:
        if self.detail is None:
            return self.description
        return f"{self.description}: '{self.detail}'"


COMPILE_SOURCE = "Microsoft VBScript compilation error"

COMPILE_NUMBERS: dict[str, int] = {
    "Out of memory": 1001,
    "Syntax error": 1002,
    "Expected ':'": 1003,
    "Expected '('": 1005,
    "Expected ')'": 1006,
    "Expected ']'": 1007,
    "Expected identifier": 1010,
    "Expected '='": 1011,
    "Expected 'If'": 1012,
    "Expected 'To'": 1013,
    "Expected 'End'": 1014,
    "Expected 'Function'": 1015,
    "Expected 'Sub'": 1016,
    "Expected 'Then'": 1017,
    "Expected 'Wend'": 1018,
    "Expected 'Loop'": 1019,
    "Expected 'Next'": 1020,
    "Expected 'Case'": 1021,
    "Expected 'Select'": 1022,
    "Expected expression": 1023,
    "Expected statement": 1024,
    "Expected end of statement": 1025,
    "Expected integer constant": 1026,
    "Expected 'While' or 'Until'": 1027,
    "Expected 'While', 'Until' or end of statement": 1028,
    "Invalid number": 1031,
    "Invalid character": 1032,
    "Unterminated string constant": 1033,
    "'loop' without 'do'": 1038,
    "Invalid 'exit' statement": 1039,
    "Name redefined": 1041,
    "Must be first statement on the line": 1042,
    "Expected literal constant": 1045,
    "Unexpected 'Next'": 1055,
}


class VBCompileError(Exception):
    """The script text cannot be compiled."""

    def __init__(self, message: str, line: int, column: int) -> None:
        super().__init__(message)
        self.message = message
        self.line = line
        self.column = column

    @property
    def number(self) -> int | None:
        return COMPILE_NUMBERS.get(self.message)

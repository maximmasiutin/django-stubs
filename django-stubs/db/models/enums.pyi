import enum
import sys
from typing import Any, TypeVar, overload, type_check_only

from _typeshed import ConvertibleToInt
from django.utils.functional import _StrOrPromise
from typing_extensions import deprecated

_Self = TypeVar("_Self")

if sys.version_info >= (3, 11):
    _enum_property = enum.property
    EnumType = enum.EnumType
    IntEnum = enum.IntEnum
    StrEnum = enum.StrEnum
else:
    _enum_property = property
    EnumType = enum.EnumMeta

    class ReprEnum(enum.Enum): ...  # type: ignore[misc]
    class IntEnum(int, ReprEnum): ...  # type: ignore[misc]
    class StrEnum(str, ReprEnum): ...  # type: ignore[misc]

class ChoicesType(EnumType):
    # There's a contradiction between mypy and PYI019 regarding metaclasses. Where mypy
    # disallows 'typing_extensions.Self' on metaclasses, while PYI019 try to enforce
    # 'typing_extensions.Self' for '__new__' methods.. We've chosen to ignore the
    # linter and trust mypy.
    def __new__(  # noqa: PYI019
        metacls: type[_Self], classname: str, bases: tuple[type, ...], classdict: enum._EnumDict, **kwds: Any
    ) -> _Self: ...
    def __contains__(self, member: Any) -> bool: ...
    @property
    def names(self) -> list[str]: ...
    @property
    def choices(self) -> list[tuple[Any, str]]: ...
    @property
    def labels(self) -> list[str]: ...
    @property
    def values(self) -> list[Any]: ...

@deprecated("ChoicesMeta is deprecated in favor of ChoicesType and will be removed in Django 6.0.")
class ChoicesMeta(ChoicesType): ...

class Choices(enum.Enum, metaclass=ChoicesType):  # type: ignore[misc]
    @property
    def label(self) -> str: ...
    @_enum_property
    def value(self) -> Any: ...
    @property
    def do_not_call_in_templates(self) -> bool: ...

# fake, to keep simulate class properties
@type_check_only
class _IntegerChoicesMeta(ChoicesType):
    @property
    def choices(self) -> list[tuple[int, str]]: ...
    @property
    def values(self) -> list[int]: ...

# In reality, the `__init__` overloads provided below should also support
# all the arguments of `int.__new__`/`str.__new__` (e.g. `base`, `encoding`).
# They are omitted on purpose to avoid having convoluted stubs for these enums:
class IntegerChoices(Choices, IntEnum, metaclass=_IntegerChoicesMeta):  # type: ignore[misc]
    @overload
    def __init__(self, x: ConvertibleToInt) -> None: ...
    @overload
    def __init__(self, x: ConvertibleToInt, label: _StrOrPromise) -> None: ...
    @_enum_property
    def value(self) -> int: ...

# fake, to keep simulate class properties
@type_check_only
class _TextChoicesMeta(ChoicesType):
    @property
    def choices(self) -> list[tuple[str, str]]: ...
    @property
    def values(self) -> list[str]: ...

class TextChoices(Choices, StrEnum, metaclass=_TextChoicesMeta):  # type: ignore[misc]
    @overload
    def __init__(self, object: str) -> None: ...
    @overload
    def __init__(self, object: str, label: _StrOrPromise) -> None: ...
    @_enum_property
    def value(self) -> str: ...

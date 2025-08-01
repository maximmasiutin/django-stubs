from collections.abc import Callable, Iterable

# Mypy has special handling for functools.cached_property, reuse typeshed's definition instead of defining our own
from functools import cached_property as cached_property
from typing import Any, Generic, Protocol, SupportsIndex, TypeAlias, TypeVar, overload, type_check_only

from django.db.models.base import Model
from typing_extensions import Self

_T = TypeVar("_T")

# Promise is only subclassed by a proxy class defined in the lazy function
# so it makes sense for it to have all the methods available in that proxy class
class Promise:
    def __init__(self, args: Any, kw: Any) -> None: ...
    def __reduce__(self) -> tuple[Any, tuple[Any]]: ...
    def __lt__(self, other: Any) -> bool: ...
    def __mod__(self, rhs: Any) -> Any: ...
    def __add__(self, other: Any) -> Any: ...
    def __radd__(self, other: Any) -> Any: ...
    def __deepcopy__(self, memo: Any) -> Self: ...

@type_check_only
class _StrPromise(Promise):
    def __add__(self, s: str, /) -> str: ...
    def __contains__(self, o: str, /) -> bool: ...
    def __ge__(self, x: str, /) -> bool: ...
    def __getitem__(self, i: SupportsIndex | slice, /) -> str: ...
    def __gt__(self, x: str, /) -> bool: ...
    def __le__(self, x: str, /) -> bool: ...
    # __len__ needed here because it defined abstract in Sequence[str]
    def __len__(self) -> int: ...
    def __lt__(self, x: str, /) -> bool: ...
    def __mod__(self, x: Any, /) -> str: ...
    def __mul__(self, n: SupportsIndex, /) -> str: ...
    def __rmul__(self, n: SupportsIndex, /) -> str: ...
    # Mypy requires this for the attribute hook to take effect
    def __getattribute__(self, name: str, /) -> Any: ...

_StrOrPromise: TypeAlias = str | _StrPromise  # noqa: PYI047
_C = TypeVar("_C", bound=Callable)

def lazy(func: _C, *resultclasses: Any) -> _C: ...
def lazystr(text: Any) -> _StrPromise: ...
def keep_lazy(*resultclasses: Any) -> Callable: ...
def keep_lazy_text(func: Callable) -> Callable: ...

empty: object

def new_method_proxy(func: Callable[..., _T]) -> Callable[..., _T]: ...

class LazyObject:
    def __init__(self) -> None: ...
    __getattr__: Callable
    def __setattr__(self, name: str, value: Any) -> None: ...
    def __delattr__(self, name: str) -> None: ...
    def __reduce__(self) -> tuple[Callable, tuple[Model]]: ...
    def __copy__(self) -> LazyObject: ...
    # TODO: Deepcopy can return a LazyObject or a wrapped object, but we'll need to make LazyObject generic first
    def __deepcopy__(self, memo: dict[int, Any]) -> Any: ...
    __bytes__: Callable
    __bool__: Callable
    __dir__: Callable
    __ne__: Callable
    __hash__: Callable
    __getitem__: Callable
    __setitem__: Callable
    __delitem__: Callable
    __iter__: Callable
    __len__: Callable
    __contains__: Callable
    __gt__: Callable
    __lt__: Callable
    __add__: Callable
    __str__: Callable[..., str]

def unpickle_lazyobject(wrapped: Model) -> Model: ...

class SimpleLazyObject(LazyObject):
    def __init__(self, func: Callable[[], Any]) -> None: ...
    def __copy__(self) -> SimpleLazyObject: ...
    __radd__: Callable

_PartitionMember = TypeVar("_PartitionMember")

def partition(
    predicate: Callable[[_PartitionMember], int | bool], values: Iterable[_PartitionMember]
) -> tuple[list[_PartitionMember], list[_PartitionMember]]: ...

_Get = TypeVar("_Get", covariant=True)

class classproperty(Generic[_Get]):
    fget: Callable[[Any], _Get] | None
    def __init__(self, method: Callable[[Any], _Get] | None = None) -> None: ...
    def __get__(self, instance: Any | None, cls: type[Any] | None = None) -> _Get: ...
    def getter(self, method: Callable[[Any], _Get]) -> classproperty[_Get]: ...

@type_check_only
class _Getter(Protocol[_Get]):  # noqa: PYI046
    """
    Type fake to declare some read-only properties (until `property` builtin is generic).

    We can use something like `Union[_Getter[str], str]` in base class to avoid errors
    when redefining attribute with property or property with attribute.
    """

    @overload
    def __get__(self, instance: None, typeobj: type[Any] | None, /) -> Self: ...
    @overload
    def __get__(self, instance: Any, typeobj: type[Any] | None, /) -> _Get: ...

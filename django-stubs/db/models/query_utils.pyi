from collections.abc import Collection, Iterable, Iterator, Mapping, Sequence
from typing import Any, ClassVar, Literal, NamedTuple, TypeVar

from _typeshed import Incomplete
from django.db.backends.base.base import BaseDatabaseWrapper
from django.db.models.base import Model
from django.db.models.expressions import BaseExpression
from django.db.models.fields import Field
from django.db.models.fields.mixins import FieldCacheMixin
from django.db.models.lookups import Lookup, Transform
from django.db.models.options import Options
from django.db.models.sql.compiler import SQLCompiler, _AsSqlType
from django.db.models.sql.query import Query
from django.db.models.sql.where import WhereNode
from django.utils import tree
from django.utils.functional import cached_property

class PathInfo(NamedTuple):
    from_opts: Options
    to_opts: Options
    target_fields: tuple[Field, ...]
    join_field: Field
    m2m: bool
    direct: bool
    filtered_relation: FilteredRelation | None

def subclasses(cls: type[RegisterLookupMixin]) -> Iterator[type[RegisterLookupMixin]]: ...

class Q(tree.Node):
    AND: str
    OR: str
    conditional: bool
    def __init__(self, *args: Any, **kwargs: Any) -> None: ...
    # Fake signature, the real is
    # def __init__(self, *args: Any, _connector: Any | None = ..., _negated: bool = ..., **kwargs: Any) -> None: ...
    def __or__(self, other: Q) -> Q: ...
    def __and__(self, other: Q) -> Q: ...
    def __xor__(self, other: Q) -> Q: ...
    def __invert__(self) -> Q: ...
    def resolve_expression(
        self,
        query: Query | None = None,
        allow_joins: bool = True,
        reuse: set[str] | None = None,
        summarize: bool = False,
        for_save: bool = False,
    ) -> WhereNode: ...
    def flatten(self) -> Iterator[Incomplete]: ...
    def check(self, against: dict[str, Any], using: str = "default") -> bool: ...
    def deconstruct(self) -> tuple[str, Sequence[Any], dict[str, Any]]: ...
    @cached_property
    def referenced_base_fields(self) -> set[str]: ...

class DeferredAttribute:
    field: Field
    def __init__(self, field: Field) -> None: ...
    def __get__(self, instance: Model | None, cls: type[Model] | None = None) -> Any: ...

_R = TypeVar("_R", bound=type)

class RegisterLookupMixin:
    class_lookups: ClassVar[dict[str, Any]]
    lookup_name: str
    @classmethod
    def get_lookups(cls) -> dict[str, Any]: ...
    def get_lookup(self, lookup_name: str) -> type[Lookup] | None: ...
    def get_transform(self, lookup_name: str) -> type[Transform] | None: ...
    @staticmethod
    def merge_dicts(dicts: Iterable[dict[str, Any]]) -> dict[str, Any]: ...
    @classmethod
    def register_lookup(cls, lookup: _R, lookup_name: str | None = None) -> _R: ...
    @classmethod
    def _unregister_lookup(cls, lookup: type[Lookup], lookup_name: str | None = None) -> None: ...

def select_related_descend(
    field: Field,
    restricted: bool,
    requested: Mapping[str, Any] | None,
    load_fields: Collection[str] | None,
    reverse: bool = False,
) -> bool: ...

_E = TypeVar("_E", bound=BaseExpression)

def refs_expression(
    lookup_parts: Sequence[str], annotations: Mapping[str, _E]
) -> tuple[Literal[False] | _E, Sequence[str]]: ...
def check_rel_lookup_compatibility(model: type[Model], target_opts: Any, field: FieldCacheMixin) -> bool: ...

class FilteredRelation:
    relation_name: str
    alias: str | None
    condition: Q
    def __init__(self, relation_name: str, *, condition: Q = ...) -> None: ...
    def clone(self) -> FilteredRelation: ...
    def relabeled_clone(self, change_map: dict[str, str]) -> FilteredRelation: ...
    def resolve_expression(self, query: Query, reuse: set[str], *args: Any, **kwargs: Any) -> FilteredRelation: ...
    def as_sql(self, compiler: SQLCompiler, connection: BaseDatabaseWrapper) -> _AsSqlType: ...

import os
import sys
from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from functools import cached_property
from typing import TYPE_CHECKING, Any, Literal, Union

from django.core.exceptions import FieldDoesNotExist, FieldError
from django.db import models
from django.db.models.base import Model
from django.db.models.expressions import Expression
from django.db.models.fields import AutoField, CharField, Field
from django.db.models.fields.related import ForeignKey, RelatedField
from django.db.models.fields.reverse_related import ForeignObjectRel
from django.db.models.lookups import Exact
from django.db.models.sql.query import Query
from mypy.checker import TypeChecker
from mypy.nodes import TypeInfo
from mypy.plugin import MethodContext
from mypy.typeanal import make_optional_type
from mypy.types import AnyType, Instance, ProperType, TypeOfAny, UnionType, get_proper_type
from mypy.types import Type as MypyType

from mypy_django_plugin.exceptions import UnregisteredModelError
from mypy_django_plugin.lib import fullnames, helpers

# This import fails when `psycopg2` is not installed, avoid crashing the plugin.
try:
    from django.contrib.postgres.fields import ArrayField
except ImportError:

    class ArrayField:  # type: ignore[no-redef]
        pass


if TYPE_CHECKING:
    from django.apps.registry import Apps
    from django.conf import LazySettings
    from django.contrib.contenttypes.fields import GenericForeignKey


@contextmanager
def temp_environ() -> Iterator[None]:
    """Allow the ability to set os.environ temporarily"""
    environ = dict(os.environ)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(environ)


def initialize_django(settings_module: str) -> tuple["Apps", "LazySettings"]:
    with temp_environ():
        os.environ["DJANGO_SETTINGS_MODULE"] = settings_module

        # add current directory to sys.path
        sys.path.append(os.getcwd())

        from django.apps import apps
        from django.conf import settings

        apps.get_swappable_settings_name.cache_clear()  # type: ignore[attr-defined]
        apps.clear_cache()

        if not settings.configured:
            settings._setup()  # type: ignore[misc]
        apps.populate(settings.INSTALLED_APPS)

    assert apps.apps_ready, "Apps are not ready"
    assert settings.configured, "Settings are not configured"

    return apps, settings


class LookupsAreUnsupported(Exception):
    pass


def get_field_type_from_model_type_info(info: TypeInfo | None, field_name: str) -> Instance | None:
    if info is None:
        return None
    field_node = info.get(field_name)
    if field_node is None:
        return None
    field_type = get_proper_type(field_node.type)
    if not isinstance(field_type, Instance):
        return None
    # Field declares a set and a get type arg. Fallback to `None` when we can't find any args
    elif len(field_type.args) != 2:
        return None
    else:
        return field_type


def _get_field_set_type_from_model_type_info(info: TypeInfo | None, field_name: str) -> MypyType | None:
    field_type = get_field_type_from_model_type_info(info, field_name)
    if field_type is not None:
        return field_type.args[0]
    else:
        return None


def _get_field_get_type_from_model_type_info(info: TypeInfo | None, field_name: str) -> MypyType | None:
    field_type = get_field_type_from_model_type_info(info, field_name)
    if field_type is not None:
        return field_type.args[1]
    else:
        return None


class DjangoContext:
    def __init__(self, django_settings_module: str) -> None:
        self.django_settings_module = django_settings_module

        apps, settings = initialize_django(self.django_settings_module)
        self.apps_registry = apps
        self.settings = settings

    @cached_property
    def model_modules(self) -> dict[str, dict[str, type[Model]]]:
        """All modules that contain Django models."""
        modules: dict[str, dict[str, type[Model]]] = defaultdict(dict)
        for concrete_model_cls in self.apps_registry.get_models(include_auto_created=True, include_swapped=True):
            modules[concrete_model_cls.__module__][concrete_model_cls.__name__] = concrete_model_cls
            # collect abstract=True models
            for model_cls in concrete_model_cls.mro()[1:]:
                if issubclass(model_cls, Model) and hasattr(model_cls, "_meta") and model_cls._meta.abstract:
                    modules[model_cls.__module__][model_cls.__name__] = model_cls
        return modules

    def get_model_class_by_fullname(self, fullname: str) -> type[Model] | None:
        """Returns None if Model is abstract"""
        module, _, model_cls_name = fullname.rpartition(".")
        return self.model_modules.get(module, {}).get(model_cls_name)

    def get_model_fields(self, model_cls: type[Model]) -> Iterator["Field[Any, Any]"]:
        for field in model_cls._meta.get_fields():
            if isinstance(field, Field):
                yield field

    def get_model_foreign_keys(self, model_cls: type[Model]) -> Iterator["ForeignKey[Any, Any]"]:
        for field in model_cls._meta.get_fields():
            if isinstance(field, ForeignKey):
                yield field

    def get_model_related_fields(self, model_cls: type[Model]) -> Iterator["RelatedField[Any, Any]"]:
        """Get model forward relations"""
        for field in model_cls._meta.get_fields():
            if isinstance(field, RelatedField):
                yield field

    def get_model_relations(self, model_cls: type[Model]) -> Iterator[ForeignObjectRel]:
        """Get model reverse relations"""
        for field in model_cls._meta.get_fields():
            if isinstance(field, ForeignObjectRel):
                yield field

    def get_field_lookup_exact_type(
        self, api: TypeChecker, field: Union["Field[Any, Any]", ForeignObjectRel]
    ) -> MypyType:
        if isinstance(field, RelatedField | ForeignObjectRel):
            related_model_cls = self.get_field_related_model_cls(field)
            rel_model_info = helpers.lookup_class_typeinfo(api, related_model_cls)
            if rel_model_info is None:
                return AnyType(TypeOfAny.explicit)

            primary_key_field = self.get_primary_key_field(related_model_cls)
            primary_key_type = self.get_field_get_type(api, rel_model_info, primary_key_field, method="init")

            model_and_primary_key_type = UnionType.make_union([Instance(rel_model_info, []), primary_key_type])
            return make_optional_type(model_and_primary_key_type)

        field_info = helpers.lookup_class_typeinfo(api, field.__class__)
        if field_info is None:
            return AnyType(TypeOfAny.explicit)
        return helpers.get_private_descriptor_type(field_info, "_pyi_lookup_exact_type", is_nullable=field.null)

    def get_related_target_field(
        self, related_model_cls: type[Model], field: "ForeignKey[Any, Any]"
    ) -> "Field[Any, Any] | None":
        # ForeignKey only supports one `to_fields` item (ForeignObject supports many)
        assert len(field.to_fields) == 1
        to_field_name = field.to_fields[0]
        if to_field_name:
            rel_field = related_model_cls._meta.get_field(to_field_name)
            if not isinstance(rel_field, Field):
                return None  # Not supported
            return rel_field
        else:
            return self.get_primary_key_field(related_model_cls)

    def get_primary_key_field(self, model_cls: type[Model]) -> "Field[Any, Any]":
        for field in model_cls._meta.get_fields():
            if isinstance(field, Field):
                if field.primary_key:
                    return field
        raise ValueError("No primary key defined")

    def get_expected_types(self, api: TypeChecker, model_cls: type[Model], *, method: str) -> dict[str, MypyType]:
        contenttypes_in_apps = self.apps_registry.is_installed("django.contrib.contenttypes")
        if contenttypes_in_apps:
            from django.contrib.contenttypes.fields import GenericForeignKey

        expected_types = {}
        # add pk if not abstract=True
        if not model_cls._meta.abstract:
            primary_key_field = self.get_primary_key_field(model_cls)
            field_set_type = self.get_field_set_type(api, primary_key_field, method=method)
            expected_types["pk"] = field_set_type

        model_info = helpers.lookup_class_typeinfo(api, model_cls)
        for field in model_cls._meta.get_fields():
            if isinstance(field, Field):
                field_name = field.attname
                # Can not determine target_field for recursive relationship when model is abstract
                if field.related_model == "self" and model_cls._meta.abstract:
                    continue
                # Try to retrieve set type from a model's TypeInfo object and fallback to retrieving it manually
                # from django-stubs own declaration. This is to align with the setter types declared for
                # assignment.
                field_set_type = _get_field_set_type_from_model_type_info(
                    model_info, field_name
                ) or self.get_field_set_type(api, field, method=method)
                expected_types[field_name] = field_set_type

                if isinstance(field, ForeignKey):
                    field_name = field.name
                    foreign_key_info = helpers.lookup_class_typeinfo(api, field.__class__)
                    if foreign_key_info is None:
                        # maybe there's no type annotation for the field
                        expected_types[field_name] = AnyType(TypeOfAny.unannotated)
                        continue

                    try:
                        related_model = self.get_field_related_model_cls(field)
                    except UnregisteredModelError:
                        # Recognise the field but don't say anything about its type..
                        expected_types[field_name] = AnyType(TypeOfAny.from_error)
                        continue

                    if related_model._meta.proxy_for_model is not None:
                        related_model = related_model._meta.proxy_for_model

                    related_model_info = helpers.lookup_class_typeinfo(api, related_model)
                    if related_model_info is None:
                        expected_types[field_name] = AnyType(TypeOfAny.unannotated)
                        continue

                    is_nullable = self.get_field_nullability(field, method)
                    foreign_key_set_type = helpers.get_private_descriptor_type(
                        foreign_key_info, "_pyi_private_set_type", is_nullable=is_nullable
                    )
                    model_set_type = helpers.convert_any_to_type(foreign_key_set_type, Instance(related_model_info, []))

                    expected_types[field_name] = model_set_type

            elif contenttypes_in_apps and isinstance(field, GenericForeignKey):
                # it's generic, so cannot set specific model
                field_name = field.name
                gfk_info = helpers.lookup_class_typeinfo(api, field.__class__)
                if gfk_info is None:
                    gfk_set_type: MypyType = AnyType(TypeOfAny.unannotated)
                else:
                    gfk_set_type = helpers.get_private_descriptor_type(
                        gfk_info, "_pyi_private_set_type", is_nullable=True
                    )
                expected_types[field_name] = gfk_set_type

        return expected_types

    @cached_property
    def all_registered_model_classes(self) -> set[type[models.Model]]:
        model_classes = self.apps_registry.get_models()

        all_model_bases = set()
        for model_cls in model_classes:
            for base_cls in model_cls.mro():
                if issubclass(base_cls, models.Model):
                    all_model_bases.add(base_cls)

        return all_model_bases

    @cached_property
    def model_class_fullnames_by_label(self) -> Mapping[str, str]:
        return {
            klass._meta.label: helpers.get_class_fullname(klass)
            for klass in self.all_registered_model_classes
            if klass is not models.Model
        }

    def get_field_nullability(self, field: Union["Field[Any, Any]", ForeignObjectRel], method: str | None) -> bool:
        if method in ("values", "values_list"):
            return field.null

        nullable = field.null
        if not nullable and isinstance(field, CharField) and field.blank:
            return True
        if method == "__init__":
            if (isinstance(field, Field) and field.primary_key) or isinstance(field, ForeignKey):
                return True
        if method == "create":
            if isinstance(field, AutoField):
                return True
        if isinstance(field, Field) and field.has_default():
            return True
        return nullable

    def get_field_set_type(
        self, api: TypeChecker, field: Union["Field[Any, Any]", ForeignObjectRel], *, method: str
    ) -> MypyType:
        """Get a type of __set__ for this specific Django field."""
        target_field = field
        if isinstance(field, ForeignKey):
            try:
                # We gotta be careful for exceptions when we're triggering '__get__'.
                # Related model could very well be unresolvable
                target_field = field.target_field
            except ValueError:
                return AnyType(TypeOfAny.from_error)

        field_info = helpers.lookup_class_typeinfo(api, target_field.__class__)
        if field_info is None:
            return AnyType(TypeOfAny.from_error)

        field_set_type = helpers.get_private_descriptor_type(
            field_info, "_pyi_private_set_type", is_nullable=self.get_field_nullability(field, method)
        )
        if isinstance(target_field, ArrayField):
            argument_field_type = self.get_field_set_type(api, target_field.base_field, method=method)
            field_set_type = helpers.convert_any_to_type(field_set_type, argument_field_type)
        return field_set_type

    def get_field_get_type(
        self,
        api: TypeChecker,
        model_info: TypeInfo | None,
        field: Union["Field[Any, Any]", ForeignObjectRel],
        *,
        method: str,
    ) -> MypyType:
        """Get a type of __get__ for this specific Django field."""
        if isinstance(field, Field):
            get_type = _get_field_get_type_from_model_type_info(model_info, field.attname)
            if get_type is not None:
                return get_type

        field_info = helpers.lookup_class_typeinfo(api, field.__class__)
        if field_info is None:
            return AnyType(TypeOfAny.unannotated)

        is_nullable = self.get_field_nullability(field, method)
        if isinstance(field, RelatedField):
            related_model_cls = self.get_field_related_model_cls(field)
            rel_model_info = helpers.lookup_class_typeinfo(api, related_model_cls)

            if method in ("values", "values_list"):
                primary_key_field = self.get_primary_key_field(related_model_cls)
                return self.get_field_get_type(api, rel_model_info, primary_key_field, method=method)

            model_info = helpers.lookup_class_typeinfo(api, related_model_cls)
            if model_info is None:
                return AnyType(TypeOfAny.unannotated)

            return Instance(model_info, [])
        else:
            return helpers.get_private_descriptor_type(field_info, "_pyi_private_get_type", is_nullable=is_nullable)

    def get_field_related_model_cls(self, field: Union["RelatedField[Any, Any]", ForeignObjectRel]) -> type[Model]:
        if isinstance(field, RelatedField):
            related_model_cls = field.remote_field.model
        else:
            related_model_cls = field.field.model

        if related_model_cls is None:
            raise UnregisteredModelError

        if isinstance(related_model_cls, str):
            if related_model_cls == "self":  # type: ignore[unreachable]
                # same model
                related_model_cls = field.model
            elif "." not in related_model_cls:
                # same file model
                related_model_fullname = f"{field.model.__module__}.{related_model_cls}"
                related_model_cls = self.get_model_class_by_fullname(related_model_fullname)
                if related_model_cls is None:
                    raise UnregisteredModelError
            else:
                try:
                    related_model_cls = self.apps_registry.get_model(related_model_cls)
                except LookupError as e:
                    raise UnregisteredModelError from e

        return related_model_cls

    def _resolve_field_from_parts(
        self, field_parts: Iterable[str], model_cls: type[Model]
    ) -> tuple[Union["Field[Any, Any]", ForeignObjectRel], type[Model]]:
        currently_observed_model = model_cls
        field: Field[Any, Any] | ForeignObjectRel | GenericForeignKey | None = None
        for field_part in field_parts:
            if field_part == "pk":
                field = self.get_primary_key_field(currently_observed_model)
                continue

            field = currently_observed_model._meta.get_field(field_part)
            if isinstance(field, RelatedField):
                currently_observed_model = self.get_field_related_model_cls(field)
                model_name = currently_observed_model._meta.model_name
                if model_name is not None and field_part == (model_name + "_id"):
                    field = self.get_primary_key_field(currently_observed_model)

            if isinstance(field, ForeignObjectRel):
                currently_observed_model = self.get_field_related_model_cls(field)

        # Guaranteed by `query.solve_lookup_type` before.
        assert isinstance(field, Field | ForeignObjectRel)
        return field, currently_observed_model

    def solve_lookup_type(
        self, model_cls: type[Model], lookup: str
    ) -> tuple[Sequence[str], Sequence[str], Expression | Literal[False]] | None:
        query = Query(model_cls)
        if (lookup == "pk" or lookup.startswith("pk__")) and query.get_meta().pk is None:
            # Primary key lookup when no primary key field is found, model is presumably
            # abstract and we can't say anything about 'pk'.
            return None
        try:
            return query.solve_lookup_type(lookup)
        # This occurs when the following conditions are met:
        # - model_cls._meta.abstract = True
        # - part of the lookup is a foreign key defined on model_cls where the 'to' argument is a string
        # On abstract models the 'to' parameter is only coalesced into the actual class whenever a subclass of it is
        # instantiated, therefore it is never swapped out for abstract base classes.
        except AttributeError:
            pass
        query_parts = lookup.split("__")
        try:
            field = query.get_meta().get_field(query_parts[0])
        except FieldDoesNotExist:
            return None

        if len(query_parts) == 1:
            return [], [query_parts[0]], False

        if not isinstance(field, RelatedField | ForeignObjectRel):
            return None

        related_model = self.get_field_related_model_cls(field)
        sub_query = Query(related_model).solve_lookup_type("__".join(query_parts[1:]))
        entire_query_parts = [query_parts[0], *sub_query[1]]
        return sub_query[0], entire_query_parts, sub_query[2]

    def resolve_lookup_into_field(
        self, model_cls: type[Model], lookup: str
    ) -> tuple[Union["Field[Any, Any]", ForeignObjectRel, None], type[Model]]:
        solved_lookup = self.solve_lookup_type(model_cls, lookup)
        if solved_lookup is None:
            return None, model_cls
        lookup_parts, field_parts, is_expression = solved_lookup
        if lookup_parts:
            raise LookupsAreUnsupported()
        return self._resolve_field_from_parts(field_parts, model_cls)

    def resolve_lookup_expected_type(
        self, ctx: MethodContext, model_cls: type[Model], lookup: str, model_instance: Instance
    ) -> MypyType:
        try:
            solved_lookup = self.solve_lookup_type(model_cls, lookup)
        except FieldError as exc:
            if helpers.is_annotated_model(model_instance.type) and model_instance.extra_attrs:
                # If the field comes from .annotate(), we assume Any for it
                # and allow chaining any lookups.
                lookup_base_field, *_ = lookup.split("__")
                if lookup_base_field in model_instance.extra_attrs.attrs:
                    return model_instance.extra_attrs.attrs[lookup_base_field]

            msg = exc.args[0]
            if model_instance.extra_attrs:
                msg = ", ".join((msg, *model_instance.extra_attrs.attrs.keys()))
            ctx.api.fail(msg, ctx.context)
            return AnyType(TypeOfAny.from_error)

        if solved_lookup is None:
            return AnyType(TypeOfAny.implementation_artifact)
        lookup_parts, field_parts, is_expression = solved_lookup
        if is_expression:
            return AnyType(TypeOfAny.explicit)

        field, _ = self._resolve_field_from_parts(field_parts, model_cls)

        lookup_cls = None
        if lookup_parts:
            lookup = lookup_parts[-1]
            lookup_cls = field.get_lookup(lookup)
            if lookup_cls is None:
                # unknown lookup
                return AnyType(TypeOfAny.explicit)

        if lookup_cls is None or isinstance(lookup_cls, Exact):
            return self.get_field_lookup_exact_type(helpers.get_typechecker_api(ctx), field)

        assert lookup_cls is not None

        lookup_info = helpers.lookup_class_typeinfo(helpers.get_typechecker_api(ctx), lookup_cls)
        if lookup_info is None:
            return AnyType(TypeOfAny.explicit)

        for lookup_base in helpers.iter_bases(lookup_info):
            if lookup_base.args and isinstance((lookup_type := get_proper_type(lookup_base.args[0])), Instance):
                # if it's Field, consider lookup_type a __get__ of current field
                if isinstance(lookup_type, Instance) and lookup_type.type.fullname == fullnames.FIELD_FULLNAME:
                    field_info = helpers.lookup_class_typeinfo(helpers.get_typechecker_api(ctx), field.__class__)
                    if field_info is None:
                        return AnyType(TypeOfAny.explicit)
                    lookup_type = get_proper_type(
                        helpers.get_private_descriptor_type(field_info, "_pyi_private_get_type", is_nullable=field.null)
                    )
                return lookup_type

        return AnyType(TypeOfAny.explicit)

    def resolve_f_expression_type(self, f_expression_type: Instance) -> ProperType:
        return AnyType(TypeOfAny.explicit)

    @cached_property
    def is_contrib_auth_installed(self) -> bool:
        return "django.contrib.auth" in self.settings.INSTALLED_APPS

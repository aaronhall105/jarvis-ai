"""Grounded parameter controls projected from Home Assistant metadata.

The bindings in this module describe official Home Assistant API contracts.  They
are not language intents: a binding is exposed only when the live service schema
and an entity's live feature/attribute metadata both support it.  Conversation
code never supplies an entity id, service name, field name, or raw payload.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import re
from typing import Any, Mapping, Sequence


def _normalise(value: Any) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).split())


def _attributes(entity: Mapping[str, Any]) -> Mapping[str, Any]:
    value = entity.get("attributes")
    return value if isinstance(value, Mapping) else {}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class _Binding:
    parameter: str
    aliases: tuple[str, ...]
    domain: str
    service: str
    field: str
    value_type: str
    read_attribute: str | None = None
    read_state: bool = False
    semantic_unit: str | None = None
    entity_min_attribute: str | None = None
    entity_max_attribute: str | None = None
    entity_step_attribute: str | None = None
    entity_options_attribute: str | None = None
    relative_field: str | None = None
    semantic_scale: float = 1.0
    read_scale: float = 1.0


# Central official-API adapter metadata.  Live service and entity metadata remain
# authoritative: absent or unsupported fields never become capabilities.
_BINDINGS = (
    _Binding(
        "brightness",
        ("brightness", "light level", "level"),
        "light",
        "turn_on",
        "brightness_pct",
        "PERCENT",
        read_attribute="brightness",
        semantic_unit="%",
        relative_field="brightness_step_pct",
        read_scale=100 / 255,
    ),
    _Binding(
        "color_temperature",
        ("color temperature", "colour temperature", "temperature"),
        "light",
        "turn_on",
        "color_temp_kelvin",
        "COLOR_TEMPERATURE",
        read_attribute="color_temp_kelvin",
        semantic_unit="K",
        entity_min_attribute="min_color_temp_kelvin",
        entity_max_attribute="max_color_temp_kelvin",
    ),
    _Binding(
        "color",
        ("color", "colour"),
        "light",
        "turn_on",
        "color_name",
        "COLOR",
        read_attribute="color_name",
    ),
    _Binding(
        "volume",
        ("volume", "audio level"),
        "media_player",
        "volume_set",
        "volume_level",
        "PERCENT",
        read_attribute="volume_level",
        semantic_unit="%",
        semantic_scale=0.01,
        read_scale=100,
    ),
    _Binding(
        "target_temperature",
        ("target temperature", "temperature", "setpoint"),
        "climate",
        "set_temperature",
        "temperature",
        "TEMPERATURE",
        read_attribute="temperature",
        entity_min_attribute="min_temp",
        entity_max_attribute="max_temp",
        entity_step_attribute="target_temp_step",
    ),
    _Binding(
        "fan_percentage",
        ("fan percentage", "fan speed", "speed", "percentage"),
        "fan",
        "set_percentage",
        "percentage",
        "PERCENT",
        read_attribute="percentage",
        semantic_unit="%",
        entity_step_attribute="percentage_step",
    ),
    _Binding(
        "fan_preset",
        ("fan preset", "preset", "mode"),
        "fan",
        "set_preset_mode",
        "preset_mode",
        "ENUM",
        read_attribute="preset_mode",
        entity_options_attribute="preset_modes",
    ),
    _Binding(
        "position",
        ("position", "opening", "level"),
        "cover",
        "set_cover_position",
        "position",
        "POSITION",
        read_attribute="current_position",
        semantic_unit="%",
    ),
    _Binding(
        "tilt_position",
        ("tilt position", "tilt"),
        "cover",
        "set_cover_tilt_position",
        "tilt_position",
        "POSITION",
        read_attribute="current_tilt_position",
        semantic_unit="%",
    ),
    _Binding(
        "value",
        ("value", "level", "limit", "setting"),
        "number",
        "set_value",
        "value",
        "NUMBER",
        read_state=True,
        entity_min_attribute="min",
        entity_max_attribute="max",
        entity_step_attribute="step",
    ),
    _Binding(
        "option",
        ("option", "mode", "selection"),
        "select",
        "select_option",
        "option",
        "ENUM",
        read_state=True,
        entity_options_attribute="options",
    ),
    _Binding(
        "source",
        ("source", "input"),
        "media_player",
        "select_source",
        "source",
        "ENUM",
        read_attribute="source",
        entity_options_attribute="source_list",
    ),
    _Binding(
        "hvac_mode",
        ("hvac mode", "heating mode", "mode"),
        "climate",
        "set_hvac_mode",
        "hvac_mode",
        "ENUM",
        read_state=True,
        entity_options_attribute="hvac_modes",
    ),
    _Binding(
        "climate_fan_mode",
        ("climate fan mode", "fan mode"),
        "climate",
        "set_fan_mode",
        "fan_mode",
        "ENUM",
        read_attribute="fan_mode",
        entity_options_attribute="fan_modes",
    ),
)


@dataclass(frozen=True, slots=True)
class ParameterizedCapability:
    capability_id: str
    parameter: str
    aliases: tuple[str, ...]
    value_type: str
    unit: str | None
    minimum: float | None
    maximum: float | None
    step: float | None
    options: tuple[str, ...]
    supports_absolute: bool
    supports_relative: bool
    current_value: float | str | None
    available: bool
    provenance: tuple[str, ...]
    entity_id: str
    domain: str
    service: str
    field: str
    relative_field: str | None
    read_attribute: str | None
    read_state: bool
    semantic_scale: float
    read_scale: float

    def public(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "parameter": self.parameter,
            "value_type": self.value_type,
            "unit": self.unit,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "step": self.step,
            "options": list(self.options),
            "supports_absolute": self.supports_absolute,
            "supports_relative": self.supports_relative,
            "current_value": self.current_value,
            "available": self.available,
            "provenance": list(self.provenance),
        }

    def matches(self, value: str) -> float:
        wanted = set(_normalise(value).split())
        if not wanted:
            return 0.0
        best = 0.0
        for candidate in (self.parameter, *self.aliases):
            normalised = _normalise(candidate)
            if normalised == _normalise(value):
                return 1.0
            tokens = set(normalised.split())
            if tokens:
                best = max(best, len(tokens & wanted) / len(wanted | tokens))
        return best


class CompiledServiceSchemas(dict[tuple[str, str], Mapping[str, Any]]):
    """Marker type so repeated entity projection never recompiles the HA schema."""


def compile_service_schemas(raw: Any) -> CompiledServiceSchemas:
    """Compile HA's service response once for repeated entity projections."""

    if isinstance(raw, CompiledServiceSchemas):
        return raw
    result = CompiledServiceSchemas()
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        return result
    for block in raw:
        if not isinstance(block, Mapping):
            continue
        domain = str(block.get("domain") or "").casefold()
        services = block.get("services")
        if not domain or not isinstance(services, Mapping):
            continue
        for service, schema in services.items():
            if isinstance(schema, Mapping):
                result[(domain, str(service).casefold())] = schema
    return result


def _feature_match(required: Any, supported: int) -> bool:
    if not isinstance(required, Sequence) or isinstance(required, (str, bytes, bytearray)):
        return True
    bits = [int(value) for value in required if isinstance(value, int)]
    return not bits or any(supported & bit == bit for bit in bits)


def _attribute_match(required: Any, attributes: Mapping[str, Any]) -> bool:
    if not isinstance(required, Mapping):
        return True
    for key, expected in required.items():
        actual = attributes.get(str(key))
        if isinstance(expected, Sequence) and not isinstance(expected, (str, bytes, bytearray)):
            actual_values = (
                set(str(value) for value in actual)
                if isinstance(actual, Sequence) and not isinstance(actual, (str, bytes, bytearray))
                else {str(actual)}
            )
            if not actual_values & {str(value) for value in expected}:
                return False
        elif actual != expected:
            return False
    return True


def _filter_match(spec: Mapping[str, Any], entity: Mapping[str, Any]) -> bool:
    supported = int(entity.get("supported_features") or 0)
    attributes = _attributes(entity)
    return _feature_match(spec.get("supported_features"), supported) and _attribute_match(
        spec.get("attribute"), attributes
    )


def _target_supports(schema: Mapping[str, Any], entity: Mapping[str, Any]) -> bool:
    target = schema.get("target")
    if not isinstance(target, Mapping):
        return True
    candidates = target.get("entity")
    if not isinstance(candidates, Sequence) or isinstance(candidates, (str, bytes, bytearray)):
        return True
    domain = str(entity.get("domain") or "").casefold()
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        domains = candidate.get("domain")
        if isinstance(domains, Sequence) and not isinstance(domains, (str, bytes, bytearray)):
            if domain not in {str(value).casefold() for value in domains}:
                continue
        if _filter_match(candidate, entity):
            return True
    return False


def _selector(field_schema: Mapping[str, Any]) -> tuple[str, Mapping[str, Any]]:
    selector = field_schema.get("selector")
    if not isinstance(selector, Mapping) or not selector:
        return "", {}
    kind, value = next(iter(selector.items()))
    return str(kind), value if isinstance(value, Mapping) else {}


def _limits(
    binding: _Binding,
    field_schema: Mapping[str, Any],
    entity: Mapping[str, Any],
) -> tuple[float | None, float | None, float | None, str | None, tuple[str, ...]]:
    _kind, selector = _selector(field_schema)
    attributes = _attributes(entity)
    minimum = _number(attributes.get(binding.entity_min_attribute or ""))
    maximum = _number(attributes.get(binding.entity_max_attribute or ""))
    step = _number(attributes.get(binding.entity_step_attribute or ""))
    if minimum is None:
        minimum = _number(selector.get("min"))
    if maximum is None:
        maximum = _number(selector.get("max"))
    if step is None:
        step = _number(selector.get("step"))
    unit = (
        binding.semantic_unit
        or str(
            attributes.get("unit_of_measurement") or selector.get("unit_of_measurement") or ""
        ).strip()
        or None
    )
    if unit is None and binding.value_type == "TEMPERATURE":
        unit = str(attributes.get("temperature_unit") or "").strip() or None
    raw_options = attributes.get(binding.entity_options_attribute or "")
    if not isinstance(raw_options, Sequence) or isinstance(raw_options, (str, bytes, bytearray)):
        raw_options = selector.get("options")
    options = (
        tuple(str(value) for value in raw_options)
        if isinstance(raw_options, Sequence)
        and not isinstance(raw_options, (str, bytes, bytearray))
        else ()
    )
    return minimum, maximum, step, unit, options


def read_parameter(
    capability: ParameterizedCapability,
    entity: Mapping[str, Any],
) -> float | str | None:
    raw = (
        entity.get("state")
        if capability.read_state
        else _attributes(entity).get(capability.read_attribute or "")
    )
    if capability.value_type in {"ENUM", "COLOR"}:
        return str(raw) if raw not in {None, "", "unknown", "unavailable"} else None
    number = _number(raw)
    if number is None and capability.parameter == "brightness":
        state = str(entity.get("state") or "").casefold()
        return 0.0 if state == "off" else None
    return round(number * capability.read_scale, 4) if number is not None else None


def project_parameterized_capabilities(
    entities: Sequence[Mapping[str, Any]],
    service_schemas: Any,
) -> tuple[ParameterizedCapability, ...]:
    services = compile_service_schemas(service_schemas)
    result: list[ParameterizedCapability] = []
    for entity in entities:
        domain = str(entity.get("domain") or "").casefold()
        entity_id = str(entity.get("entity_id") or "")
        if not entity_id or str(entity.get("state") or "").casefold() in {"unknown", "unavailable"}:
            available = False
        else:
            available = True
        for binding in (item for item in _BINDINGS if item.domain == domain):
            schema = services.get((binding.domain, binding.service))
            if schema is None or not _target_supports(schema, entity):
                continue
            fields = schema.get("fields")
            if not isinstance(fields, Mapping):
                continue
            field_schema = fields.get(binding.field)
            if not isinstance(field_schema, Mapping) or not _filter_match(
                _mapping(field_schema.get("filter")), entity
            ):
                continue
            relative_supported = False
            if binding.relative_field:
                relative_schema = fields.get(binding.relative_field)
                relative_supported = isinstance(relative_schema, Mapping) and _filter_match(
                    _mapping(relative_schema.get("filter")), entity
                )
            minimum, maximum, step, unit, options = _limits(binding, field_schema, entity)
            if binding.parameter == "volume":
                minimum = minimum * 100 if minimum is not None else 0.0
                maximum = maximum * 100 if maximum is not None else 100.0
                step = step * 100 if step is not None else 1.0
            seed = "\n".join((entity_id, binding.parameter, binding.service, binding.field))
            capability = ParameterizedCapability(
                capability_id=f"parameter:{hashlib.sha256(seed.encode()).hexdigest()[:24]}",
                parameter=binding.parameter,
                aliases=binding.aliases,
                value_type=binding.value_type,
                unit=unit,
                minimum=minimum,
                maximum=maximum,
                step=step,
                options=options,
                supports_absolute=True,
                supports_relative=(
                    relative_supported
                    or binding.value_type in {"PERCENT", "NUMBER", "TEMPERATURE", "POSITION"}
                ),
                current_value=None,
                available=available,
                provenance=(
                    f"home_assistant_service:{binding.domain}.{binding.service}",
                    f"home_assistant_field:{binding.field}",
                    "live_entity_metadata",
                ),
                entity_id=entity_id,
                domain=binding.domain,
                service=binding.service,
                field=binding.field,
                relative_field=binding.relative_field if relative_supported else None,
                read_attribute=binding.read_attribute,
                read_state=binding.read_state,
                semantic_scale=binding.semantic_scale,
                read_scale=binding.read_scale,
            )
            result.append(
                ParameterizedCapability(
                    **{
                        field: getattr(capability, field)
                        for field in capability.__dataclass_fields__
                        if field != "current_value"
                    },
                    current_value=read_parameter(capability, entity),
                )
            )
    return tuple(result)


def match_parameter(
    capabilities: Sequence[ParameterizedCapability],
    requested: str,
) -> ParameterizedCapability | None:
    ranked = sorted(
        ((item.matches(requested), item) for item in capabilities),
        key=lambda pair: (pair[0], pair[1].parameter, pair[1].entity_id),
        reverse=True,
    )
    return ranked[0][1] if ranked and ranked[0][0] >= 0.5 else None


def _normalise_unit(value: str | None) -> str:
    unit = str(value or "").strip().casefold()
    return {
        "percent": "%",
        "percentage": "%",
        "pct": "%",
        "celsius": "°c",
        "fahrenheit": "°f",
        "kelvin": "k",
        "degrees c": "°c",
        "degrees f": "°f",
    }.get(unit, unit)


@dataclass(frozen=True, slots=True)
class ResolvedParameterValue:
    semantic_value: float | str
    service_value: float | str
    before_value: float | str | None
    normalized_to_step: bool
    qualitative_delta: float | None = None


def resolve_parameter_value(
    capability: ParameterizedCapability,
    *,
    mode: str,
    direction: str,
    requested_value: Any,
    requested_unit: str | None,
) -> ResolvedParameterValue:
    mode = str(mode or "ABSOLUTE").upper()
    direction = str(direction or "NONE").upper()
    if mode not in {"ABSOLUTE", "RELATIVE"}:
        raise ValueError("Unsupported parameter mode")
    if direction not in {"NONE", "INCREASE", "DECREASE"}:
        raise ValueError("Unsupported parameter direction")
    if mode == "ABSOLUTE" and direction != "NONE":
        raise ValueError("An absolute value cannot have a relative direction")
    if mode == "RELATIVE" and direction == "NONE":
        raise ValueError("A relative value requires increase or decrease")

    if capability.value_type in {"ENUM", "COLOR"}:
        if mode != "ABSOLUTE" or not isinstance(requested_value, str):
            raise ValueError(f"{capability.parameter} requires one supported option")
        matches = [
            option
            for option in capability.options
            if option.casefold() == requested_value.casefold()
        ]
        if len(matches) != 1:
            allowed = ", ".join(capability.options) or "the entity's advertised options"
            raise ValueError(f"Supported {capability.parameter} options are: {allowed}")
        return ResolvedParameterValue(matches[0], matches[0], capability.current_value, False)

    if requested_unit and capability.unit:
        wanted_unit = _normalise_unit(requested_unit)
        actual_unit = _normalise_unit(capability.unit)
        if wanted_unit and actual_unit and wanted_unit != actual_unit:
            raise ValueError(
                f"{capability.parameter} uses {capability.unit}; {requested_unit} is not compatible"
            )
    requested = _number(requested_value)
    qualitative_delta: float | None = None
    if mode == "RELATIVE":
        before = _number(capability.current_value)
        if before is None:
            raise ValueError(f"The current {capability.parameter} value is unknown")
        if requested is None:
            if capability.minimum is None or capability.maximum is None:
                raise ValueError(
                    f"A qualitative {capability.parameter} change needs a grounded range"
                )
            qualitative_delta = max(
                capability.step or 0.0,
                (capability.maximum - capability.minimum) * 0.1,
            )
            requested = qualitative_delta
        requested = before + (requested if direction == "INCREASE" else -requested)
    elif requested is None:
        raise ValueError(f"An absolute {capability.parameter} value is required")

    if capability.minimum is not None and requested < capability.minimum:
        raise ValueError(
            f"{capability.parameter} must be at least {capability.minimum:g}{capability.unit or ''}"
        )
    if capability.maximum is not None and requested > capability.maximum:
        raise ValueError(
            f"{capability.parameter} must be at most {capability.maximum:g}{capability.unit or ''}"
        )

    normalized = False
    if capability.step and capability.step > 0:
        anchor = capability.minimum or 0.0
        try:
            units = (Decimal(str(requested)) - Decimal(str(anchor))) / Decimal(str(capability.step))
            representable = Decimal(str(anchor)) + units.quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            ) * Decimal(str(capability.step))
            normalized_value = float(representable)
        except (InvalidOperation, ValueError) as exc:  # pragma: no cover - defensive
            raise ValueError("The requested value could not be validated") from exc
        normalized = abs(normalized_value - requested) > 1e-9
        requested = normalized_value

    service_value = requested * capability.semantic_scale
    return ResolvedParameterValue(
        semantic_value=round(requested, 6),
        service_value=round(service_value, 6),
        before_value=capability.current_value,
        normalized_to_step=normalized,
        qualitative_delta=qualitative_delta,
    )


def verification_tolerance(capability: ParameterizedCapability) -> float:
    if capability.parameter == "brightness":
        return max(1.0, (capability.step or 0.0) / 2)
    return max(1e-6, (capability.step or 0.0) / 2)


__all__ = [
    "ParameterizedCapability",
    "ResolvedParameterValue",
    "compile_service_schemas",
    "match_parameter",
    "project_parameterized_capabilities",
    "read_parameter",
    "resolve_parameter_value",
    "verification_tolerance",
]

"""Map the snake_case names in ``webgpu.json`` to their C and Python spellings.

The C spellings are produced by the same rules the upstream
``webgpu-headers`` generator uses (``gen/utils.go``): ``PascalCase`` capitalizes
the first letter and every letter following an underscore while preserving all
other characters, so acronym casing already carried in the spec names (e.g.
``RGBA8_unorm``) round-trips correctly (``RGBA8Unorm``).

Every C name produced here is validated against the actually-compiled extension
at generation time, so a future submodule bump that changes a convention fails
loudly instead of silently producing wrong bindings.
"""

from __future__ import annotations

C_PREFIX = "WGPU"
C_FUNC_PREFIX = "wgpu"


def pascal_case(name: str) -> str:
    out: list[str] = []
    upper_next = True
    for ch in name:
        if upper_next:
            out.append(ch.upper())
            upper_next = False
        elif ch == "_":
            upper_next = True
        else:
            out.append(ch)
    return "".join(out)


def camel_case(name: str) -> str:
    out: list[str] = []
    upper_next = False
    for ch in name:
        if upper_next:
            out.append(ch.upper())
            upper_next = False
        elif ch == "_":
            upper_next = True
        else:
            out.append(ch)
    return "".join(out)


# ---- C spellings -----------------------------------------------------------

def c_type_name(spec_name: str) -> str:
    """``buffer_descriptor`` -> ``WGPUBufferDescriptor`` (structs, enums, objects)."""
    return C_PREFIX + pascal_case(spec_name)


def c_enum_value(enum_spec_name: str, entry_spec_name: str) -> str:
    """``texture_format`` + ``RGBA8_unorm`` -> ``WGPUTextureFormat_RGBA8Unorm``."""
    return f"{c_type_name(enum_spec_name)}_{pascal_case(entry_spec_name)}"


def c_struct_field(member_spec_name: str) -> str:
    """``mapped_at_creation`` -> ``mappedAtCreation``."""
    return camel_case(member_spec_name)


def c_method_func(object_spec_name: str, method_spec_name: str) -> str:
    """object ``device`` + method ``create_buffer`` -> ``wgpuDeviceCreateBuffer``."""
    return C_FUNC_PREFIX + pascal_case(object_spec_name) + pascal_case(method_spec_name)


def c_object_lifecycle(object_spec_name: str, verb: str) -> str:
    """``device`` + ``Release`` -> ``wgpuDeviceRelease`` (also ``AddRef``)."""
    return C_FUNC_PREFIX + pascal_case(object_spec_name) + verb


def c_free_function(spec_name: str) -> str:
    """``adapter_info`` -> ``wgpuAdapterInfoFreeMembers``."""
    return C_FUNC_PREFIX + pascal_case(spec_name) + "FreeMembers"


def c_toplevel_func(func_spec_name: str) -> str:
    """``create_instance`` -> ``wgpuCreateInstance``."""
    return C_FUNC_PREFIX + pascal_case(func_spec_name)


# ---- Python spellings ------------------------------------------------------

def py_class_name(object_spec_name: str) -> str:
    """``command_encoder`` -> ``CommandEncoder`` (GPU prefix added by the shim)."""
    return pascal_case(object_spec_name)


def py_enum_name(enum_spec_name: str) -> str:
    return pascal_case(enum_spec_name)


def py_member_name(member_spec_name: str) -> str:
    """Keep spec snake_case for Pythonic attribute/keyword names."""
    return member_spec_name


def py_enum_member(entry_spec_name: str) -> str:
    """Python enum member identifier: lower snake_case, safe for Python."""
    name = entry_spec_name.replace("-", "_")
    if name and name[0].isdigit():
        name = "_" + name
    return name

"""Extract the converter RSA key from a user's KNX signing assembly.

The genuine signing key is deliberately not shipped. A user with the KNX authoring tool installed can
extract it from their own ``Knx.Ets.XmlSigning.dll`` and feed it to
:func:`knxproj_signing.set_signing_key`, so exports are signed as valid without the key ever living in
this source tree. The key is the same across all supported versions, so any installed version works.

Extraction runs the assembly's converter-key accessor by reflection (see ``_extract.cs``) on a
.NET runtime that provides ``RSACryptoServiceProvider``:

- **.NET / CoreCLR** (``dotnet`` CLI) — preferred: loads obfuscated assemblies that Mono's stricter
  metadata loader rejects.
- **Mono** (``mono`` + ``mcs``) — works for unobfuscated builds.
- **pythonnet** in-process on Windows (native .NET Framework).

If no runtime is available a :class:`KeyExtractionError` explains how to enable one. Nothing here is
bundled; extraction is entirely optional and the caller falls back to the placeholder key.

This builds on the approach of OpenKNXproducer (https://github.com/OpenKNX/OpenKNXproducer), which
drives the same ``Knx.Ets.XmlSigning`` assembly for KNX product signing.
"""

from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

_EXTRACT_CS = Path(__file__).with_name("_extract.cs")
_EXTRACT_TRACE_CS = Path(__file__).with_name("_extract_trace.cs")
_EXTRACT_OAUTH_CS = Path(__file__).with_name("_extract_oauth.cs")
_TIMEOUT = 120.0

#: The assembly file that carries the signing key.
DLL_NAME = "Knx.Ets.XmlSigning.dll"

#: The assembly file that carries the project-trace encryptor (project-log comments).
TRACE_DLL_NAME = "Knx.Ets.Common.dll"

#: The assembly file that carries the MyKnx OAuth client id/secret.
MYKNX_DLL_NAME = "Knx.Ets.MyKnx.dll"


class KeyExtractionError(RuntimeError):
    """Raised when the converter key cannot be extracted from the assembly."""


def _default_dll_path(name: str) -> str:
    from xknxeditor.proj.core._dll_signer import ETS_ROOTS

    for root in ETS_ROOTS:
        candidate = Path(root) / name
        if candidate.is_file():
            return str(candidate)
    return ""


def default_dll_path() -> str:
    """Return the ``Knx.Ets.XmlSigning.dll`` in the first detected install, or ``""``.

    Used to point the file picker at the right place when the KNX authoring tool is installed locally.
    """
    return _default_dll_path(DLL_NAME)


def default_trace_dll_path() -> str:
    """Return the ``Knx.Ets.Common.dll`` in the first detected install, or ``""``."""
    return _default_dll_path(TRACE_DLL_NAME)


def default_myknx_dll_path() -> str:
    """Return the ``Knx.Ets.MyKnx.dll`` in the first detected install, or ``""``."""
    return _default_dll_path(MYKNX_DLL_NAME)


def dotnet_available() -> bool:
    """Whether the ``dotnet`` CLI (SDK) is on PATH."""
    return shutil.which("dotnet") is not None


def mono_available() -> bool:
    """Whether a Mono toolchain (``mono`` + ``mcs``) is on PATH."""
    return shutil.which("mono") is not None and shutil.which("mcs") is not None


def pythonnet_available() -> bool:
    """Whether in-process .NET (pythonnet) is usable on this platform (Windows)."""
    if sys.platform != "win32":
        return False
    try:
        import pythonnet  # type: ignore[import-not-found]  # noqa: F401
    except ImportError:
        return False
    return True


def extraction_backend() -> str | None:
    """Return the backend that would be used, or ``None`` if none is available.

    Order: ``"dotnet"`` (widest coverage) > ``"mono"`` > ``"pythonnet"``.
    """
    if dotnet_available():
        return "dotnet"
    if mono_available():
        return "mono"
    if pythonnet_available():
        return "pythonnet"
    return None


def _parse_output(text: str) -> tuple[int, int, int]:
    """Parse the ``MOD=``/``EXP=``/``D=`` base64 lines into ``(modulus, private_exp, public_exp)``."""
    fields: dict[str, int] = {}
    for key in ("MOD", "EXP", "D"):
        m = re.search(rf"^{key}=(\S+)$", text, re.MULTILINE)
        if not m:
            raise KeyExtractionError(f"extractor output missing {key}:\n{text}")
        fields[key] = int.from_bytes(base64.b64decode(m.group(1)), "big")
    return fields["MOD"], fields["D"], fields["EXP"]


def _run(cmd: list[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
    env = {"DOTNET_CLI_TELEMETRY_OPTOUT": "1", "DOTNET_NOLOGO": "1"}
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
        cwd=cwd,
        env={**os.environ, **env},
    )


def _dotnet_tfm() -> str:
    """Target framework matching the installed SDK major version (e.g. ``net10.0``)."""
    try:
        out = _run(["dotnet", "--version"]).stdout.strip()
        major = int(out.split(".", 1)[0])
        return f"net{major}.0"
    except (ValueError, OSError, subprocess.SubprocessError):
        return "net8.0"


def _build_dotnet_exe(
    source: Path, subdir: str, package_refs: dict[str, str] | None = None
) -> Path:
    """Compile ``source`` into a console app under a temp ``subdir`` and return the built dll."""
    tfm = _dotnet_tfm()
    proj_dir = Path(tempfile.gettempdir()) / subdir
    proj_dir.mkdir(exist_ok=True)
    packages = "".join(
        f'    <PackageReference Include="{name}" Version="{ver}" />\n'
        for name, ver in (package_refs or {}).items()
    )
    csproj = proj_dir / "xknxkeyx.csproj"
    csproj.write_text(
        '<Project Sdk="Microsoft.NET.Sdk">\n'
        "  <PropertyGroup>\n"
        "    <OutputType>Exe</OutputType>\n"
        f"    <TargetFramework>{tfm}</TargetFramework>\n"
        "    <Nullable>disable</Nullable>\n"
        "    <ImplicitUsings>disable</ImplicitUsings>\n"
        "    <AssemblyName>xknxkeyx</AssemblyName>\n"
        "    <EnableDefaultCompileItems>false</EnableDefaultCompileItems>\n"
        "  </PropertyGroup>\n"
        "  <ItemGroup>\n"
        f'    <Compile Include="{source}" />\n'
        f"{packages}"
        "  </ItemGroup>\n"
        "</Project>\n",
        encoding="utf-8",
    )
    build = _run(
        ["dotnet", "build", "-c", "Release", "-v", "q", "-nologo", str(csproj)]
    )
    if build.returncode != 0:
        raise KeyExtractionError(
            f"dotnet build failed:\n{build.stdout or build.stderr}"
        )
    return proj_dir / "bin" / "Release" / tfm / "xknxkeyx.dll"


def _extract_via_dotnet(dll: Path) -> tuple[int, int, int]:
    app = _build_dotnet_exe(_EXTRACT_CS, "xknx_keyextract_dotnet")
    # Run from the DLL's directory so its sibling assemblies resolve.
    proc = _run(["dotnet", str(app), str(dll)], cwd=str(dll.parent))
    if proc.returncode != 0:
        raise KeyExtractionError(f"extractor failed:\n{proc.stderr or proc.stdout}")
    return _parse_output(proc.stdout)


def _extract_via_mono(dll: Path) -> tuple[int, int, int]:
    exe = Path(tempfile.gettempdir()) / "xknx_keyextract.exe"
    build = _run(["mcs", f"-out:{exe}", str(_EXTRACT_CS)])
    if build.returncode != 0:
        raise KeyExtractionError(
            f"mcs failed to compile the extractor:\n{build.stderr}"
        )
    proc = _run(["mono", str(exe), str(dll)], cwd=str(dll.parent))
    if proc.returncode != 0:
        # Mono can abort with a native crash dump on obfuscated builds. Keep the message short and
        # steer the user to install the .NET SDK, which loads those.
        detail = "\n".join((proc.stderr or proc.stdout or "").strip().splitlines()[:3])
        raise KeyExtractionError(
            "Mono could not load this assembly (obfuscated builds need the .NET SDK — "
            f"install `dotnet`).\n{detail}"
        )
    return _parse_output(proc.stdout)


def _extract_via_pythonnet(dll: Path) -> tuple[int, int, int]:
    # Everything from the CLR is dynamically typed; bind each symbol to Any so member access is allowed.
    import clr as _clr_mod  # type: ignore[import-not-found]
    import System as _system  # type: ignore[import-not-found]
    import System.Reflection as _reflection  # type: ignore[import-not-found]

    clr: Any = _clr_mod
    system: Any = _system
    reflection: Any = _reflection

    clr.AddReference(str(dll))
    asm: Any = reflection.Assembly.LoadFrom(str(dll))
    t: Any = asm.GetType("Knx.Ets.XmlSigning.XmlSigning")
    if t is None:
        raise KeyExtractionError("type Knx.Ets.XmlSigning.XmlSigning not found")
    bf: Any = reflection.BindingFlags
    flags = bf.NonPublic | bf.Public | bf.Static
    m: Any = t.GetMethod("GetConverterRsaKey", flags)
    if m is None:
        # Obfuscated builds rename it: first static, zero-arg method returning an RSA key.
        for mm in t.GetMethods(flags):
            try:
                if mm.GetParameters().Length == 0 and str(
                    mm.ReturnType.Name
                ).startswith("RSA"):
                    m = mm
                    break
            except Exception:
                continue
    if m is None:
        raise KeyExtractionError("no converter-key accessor found")
    rsa: Any = m.Invoke(None, system.Array[system.Object](0))
    p: Any = rsa.ExportParameters(True)

    def _to_int(b: Any) -> int:
        return int.from_bytes(bytes(b), "big")

    return _to_int(p.Modulus), _to_int(p.D), _to_int(p.Exponent)


def extract_converter_key(dll_path: str | Path) -> tuple[int, int, int]:
    """Extract ``(modulus, private_exponent, public_exponent)`` from ``dll_path``.

    Uses .NET (``dotnet``) if available, else Mono, else in-process pythonnet on Windows. Raises
    :class:`KeyExtractionError` if the file is missing or no .NET runtime is available.
    """
    dll = Path(dll_path)
    if not dll.is_file():
        raise KeyExtractionError(f"assembly not found: {dll}")
    backend = extraction_backend()
    if backend == "dotnet":
        return _extract_via_dotnet(dll)
    if backend == "mono":
        return _extract_via_mono(dll)
    if backend == "pythonnet":
        return _extract_via_pythonnet(dll)
    raise KeyExtractionError(
        "no .NET runtime available to read the assembly. Install the .NET SDK "
        "(`brew install dotnet-sdk` on macOS, your package manager on Linux) or run on Windows "
        "with pythonnet installed."
    )


# --- MyKnx OAuth client credentials ------------------------------------------------------------
#
# The client id/secret for the OAuth device-code flow (2FA logins) are `const` fields in
# Knx.Ets.MyKnx.dll. See ``_extract_oauth.cs``; they are read from metadata, no cctor needed.


def _parse_oauth_output(text: str) -> tuple[str, str]:
    """Parse ``CID=``/``SECRET=`` base64 lines into ``(client_id, client_secret)``."""
    fields: dict[str, str] = {}
    for key in ("CID", "SECRET"):
        m = re.search(rf"^{key}=(\S+)$", text, re.MULTILINE)
        if not m:
            raise KeyExtractionError(f"extractor output missing {key}:\n{text}")
        fields[key] = base64.b64decode(m.group(1)).decode("utf-8")
    return fields["CID"], fields["SECRET"]


def _extract_oauth_via_dotnet(dll: Path) -> tuple[str, str]:
    app = _build_dotnet_exe(_EXTRACT_OAUTH_CS, "xknx_oauthextract_dotnet")
    proc = _run(["dotnet", str(app), str(dll)], cwd=str(dll.parent))
    if proc.returncode != 0:
        raise KeyExtractionError(f"extractor failed:\n{proc.stderr or proc.stdout}")
    return _parse_oauth_output(proc.stdout)


def _extract_oauth_via_mono(dll: Path) -> tuple[str, str]:
    exe = Path(tempfile.gettempdir()) / "xknx_oauthextract.exe"
    build = _run(["mcs", f"-out:{exe}", str(_EXTRACT_OAUTH_CS)])
    if build.returncode != 0:
        raise KeyExtractionError(
            f"mcs failed to compile the extractor:\n{build.stderr}"
        )
    proc = _run(["mono", str(exe), str(dll)], cwd=str(dll.parent))
    if proc.returncode != 0:
        detail = "\n".join((proc.stderr or proc.stdout or "").strip().splitlines()[:3])
        raise KeyExtractionError(
            "Mono could not load this assembly (obfuscated builds need the .NET SDK — "
            f"install `dotnet`).\n{detail}"
        )
    return _parse_oauth_output(proc.stdout)


def _extract_oauth_via_pythonnet(dll: Path) -> tuple[str, str]:
    import clr as _clr_mod  # type: ignore[import-not-found]
    import System.Reflection as _reflection  # type: ignore[import-not-found]

    clr: Any = _clr_mod
    reflection: Any = _reflection

    clr.AddReference(str(dll))
    asm: Any = reflection.Assembly.LoadFrom(str(dll))
    t: Any = asm.GetType("Knx.Ets.MyKnx.OAuth.OAuthDefines")
    if t is None:
        raise KeyExtractionError("type Knx.Ets.MyKnx.OAuth.OAuthDefines not found")
    bf: Any = reflection.BindingFlags
    flags = bf.NonPublic | bf.Public | bf.Static
    cid: Any = t.GetField("ClientId", flags)
    secret: Any = t.GetField("ClientSecret", flags)
    if cid is None or secret is None:
        raise KeyExtractionError("OAuth client fields not found")
    return str(cid.GetRawConstantValue()), str(secret.GetRawConstantValue())


def extract_oauth_credentials(dll_path: str | Path) -> tuple[str, str]:
    """Extract ``(client_id, client_secret)`` for the OAuth device-code flow from ``Knx.Ets.MyKnx.dll``.

    Uses .NET (``dotnet``) if available, else Mono, else in-process pythonnet on Windows. Raises
    :class:`KeyExtractionError` if the file is missing or no .NET runtime is available.
    """
    dll = Path(dll_path)
    if not dll.is_file():
        raise KeyExtractionError(f"assembly not found: {dll}")
    backend = extraction_backend()
    if backend == "dotnet":
        return _extract_oauth_via_dotnet(dll)
    if backend == "mono":
        return _extract_oauth_via_mono(dll)
    if backend == "pythonnet":
        return _extract_oauth_via_pythonnet(dll)
    raise KeyExtractionError(
        "no .NET runtime available to read the assembly. Install the .NET SDK "
        "(`brew install dotnet-sdk` on macOS, your package manager on Linux) or run on Windows "
        "with pythonnet installed."
    )


#
# The trace encryptor lives in Knx.Ets.Common.dll and needs its sibling assemblies (log4net,
# Autofac, ...) to load, so extraction requires a COMPLETE install directory. See ``_extract_trace.cs``.

# System.Configuration.ConfigurationManager: log4net's static ctor needs it on CoreCLR (it is part of
# .NET Framework, so pythonnet on Windows and Mono do not need the extra reference).
_TRACE_PACKAGES = {"System.Configuration.ConfigurationManager": "8.0.0"}


def _parse_trace_output(text: str) -> tuple[bytes, bytes, str]:
    """Parse ``KEY=``/``IV=``/``ENC=`` into ``(key, iv, marker)``.

    The marker is derived from ``ENC`` (the encryptor's output for :data:`TRACE_SENTINEL`) by
    stripping the base64 ciphertext we can reproduce from the recovered key/IV.
    """
    from xknxeditor.proj.core.trace_crypto import derive_marker

    fields: dict[str, str] = {}
    for key in ("KEY", "IV", "ENC"):
        m = re.search(rf"^{key}=(\S+)$", text, re.MULTILINE)
        if not m:
            raise KeyExtractionError(f"extractor output missing {key}:\n{text}")
        fields[key] = m.group(1)
    key = base64.b64decode(fields["KEY"])
    iv = base64.b64decode(fields["IV"])
    output = base64.b64decode(fields["ENC"]).decode("utf-8")
    try:
        return key, iv, derive_marker(output, key, iv)
    except ValueError as exc:
        raise KeyExtractionError(str(exc)) from exc


def _extract_trace_via_dotnet(dll: Path) -> tuple[bytes, bytes, str]:
    from xknxeditor.proj.core.trace_crypto import TRACE_SENTINEL

    app = _build_dotnet_exe(
        _EXTRACT_TRACE_CS, "xknx_traceextract_dotnet", _TRACE_PACKAGES
    )
    proc = _run(
        [
            "dotnet",
            str(app),
            str(dll),
            TRACE_SENTINEL,
        ],
        cwd=str(dll.parent),
    )
    if proc.returncode != 0:
        raise KeyExtractionError(f"extractor failed:\n{proc.stderr or proc.stdout}")
    return _parse_trace_output(proc.stdout)


def _extract_trace_via_mono(dll: Path) -> tuple[bytes, bytes, str]:
    from xknxeditor.proj.core.trace_crypto import TRACE_SENTINEL

    exe = Path(tempfile.gettempdir()) / "xknx_traceextract.exe"
    build = _run(["mcs", f"-out:{exe}", str(_EXTRACT_TRACE_CS)])
    if build.returncode != 0:
        raise KeyExtractionError(
            f"mcs failed to compile the extractor:\n{build.stderr}"
        )
    proc = _run(["mono", str(exe), str(dll), TRACE_SENTINEL], cwd=str(dll.parent))
    if proc.returncode != 0:
        detail = "\n".join((proc.stderr or proc.stdout or "").strip().splitlines()[:3])
        raise KeyExtractionError(
            "Mono could not load this assembly (obfuscated builds need the .NET SDK — "
            f"install `dotnet`).\n{detail}"
        )
    return _parse_trace_output(proc.stdout)


def _extract_trace_via_pythonnet(dll: Path) -> tuple[bytes, bytes, str]:
    import clr as _clr_mod  # type: ignore[import-not-found]
    import System as _system  # type: ignore[import-not-found]
    import System.Reflection as _reflection  # type: ignore[import-not-found]

    from xknxeditor.proj.core.trace_crypto import TRACE_SENTINEL, derive_marker

    clr: Any = _clr_mod
    system: Any = _system
    reflection: Any = _reflection

    clr.AddReference(str(dll))
    asm: Any = reflection.Assembly.LoadFrom(str(dll))
    bf: Any = reflection.BindingFlags
    flags = bf.NonPublic | bf.Public | bf.Instance

    def string_methods(tt: Any) -> list[Any]:
        return [
            m
            for m in tt.GetMethods(bf.Public | bf.Instance | bf.DeclaredOnly)
            if str(m.ReturnType.FullName) == "System.String"
            and m.GetParameters().Length == 1
            and str(m.GetParameters()[0].ParameterType.FullName) == "System.String"
        ]

    def is_cipher(ft: Any) -> bool:
        while ft is not None:
            if str(ft.FullName) == "System.Security.Cryptography.SymmetricAlgorithm":
                return True
            ft = ft.BaseType
        return False

    # Since 6.4 the encryptor has a name; 6.3 obfuscates class and method names, so fall back to
    # its shape: a class with a SymmetricAlgorithm field and public string(string) methods.
    t: Any = asm.GetType("Knx.Ets.Common.Security.ProjectTraceEncryptor")
    if t is None:
        try:
            types: Any = asm.GetTypes()
        except (
            Exception
        ) as exc:  # ReflectionTypeLoadException: keep the types that loaded
            types = getattr(exc, "Types", None) or []
        for tt in types:
            try:
                if (
                    tt is not None
                    and tt.IsClass
                    and not tt.IsAbstract
                    and len(string_methods(tt)) >= 2
                    and any(is_cipher(f.FieldType) for f in tt.GetFields(flags))
                ):
                    t = tt
                    break
            except Exception:
                continue
    if t is None:
        raise KeyExtractionError(
            f"project-trace encryptor type not found in {asm.GetName()}"
        )
    inst: Any = None
    for ctor in t.GetConstructors(flags):
        ps = ctor.GetParameters()
        try:
            if ps.Length == 0:
                inst = ctor.Invoke(None)
                break
            if ps.Length == 1 and str(ps[0].ParameterType.Name) == "Int32":
                inst = ctor.Invoke(system.Array[system.Object]([system.Int32(1)]))
                break
        except Exception:
            continue
    if inst is None:
        raise KeyExtractionError(f"could not instantiate the encryptor {t.FullName}")

    def call(method: Any, text: str) -> str | None:
        try:
            result = method.Invoke(inst, system.Array[system.Object]([text]))
        except Exception:
            return None
        return None if result is None else str(result)

    # Without the method name, Encrypt is the method whose output another one turns back into
    # the sentinel.
    output: str | None = None
    named: Any = t.GetMethod("Encrypt", system.Array[system.Type]([system.String]))
    if named is not None:
        output = call(named, TRACE_SENTINEL)
    else:
        methods = string_methods(t)
        for enc in methods:
            candidate = call(enc, TRACE_SENTINEL)
            if candidate is None or candidate == TRACE_SENTINEL:
                continue
            if any(
                call(dec, candidate) == TRACE_SENTINEL
                for dec in methods
                if not dec.Equals(enc)
            ):
                output = candidate
                break
    if output is None:
        raise KeyExtractionError(
            f"no encrypt method found on the encryptor {t.FullName}"
        )
    key = iv = None
    for f in t.GetFields(flags):
        v = f.GetValue(inst)
        if v is not None and hasattr(v, "Key") and hasattr(v, "IV"):
            key = bytes(v.Key)
            iv = bytes(v.IV)
            break
    if key is None or iv is None:
        raise KeyExtractionError("no AES field found on the encryptor")
    try:
        return key, iv, derive_marker(output, key, iv)
    except ValueError as exc:
        raise KeyExtractionError(str(exc)) from exc


def extract_trace_key(dll_path: str | Path) -> tuple[bytes, bytes, str]:
    """Extract ``(key, iv, marker)`` for project-log comments from ``Knx.Ets.Common.dll``.

    Needs a **complete** install directory (the encryptor loads log4net/Autofac). Uses .NET
    (``dotnet``) if available, else Mono, else in-process pythonnet on Windows. Raises
    :class:`KeyExtractionError` if the file is missing or no .NET runtime is available.
    """
    dll = Path(dll_path)
    if not dll.is_file():
        raise KeyExtractionError(f"assembly not found: {dll}")
    backend = extraction_backend()
    if backend == "dotnet":
        return _extract_trace_via_dotnet(dll)
    if backend == "mono":
        return _extract_trace_via_mono(dll)
    if backend == "pythonnet":
        return _extract_trace_via_pythonnet(dll)
    raise KeyExtractionError(
        "no .NET runtime available to read the assembly. Install the .NET SDK "
        "(`brew install dotnet-sdk` on macOS, your package manager on Linux) or run on Windows "
        "with pythonnet installed."
    )

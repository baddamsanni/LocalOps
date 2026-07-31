"""Host OS detection for platform-correct shell command generation."""

from __future__ import annotations

import platform
from dataclasses import dataclass


@dataclass(frozen=True)
class HostPlatform:
    os_name: str
    os_version: str


def detect_host_platform() -> HostPlatform:
    os_name = platform.system()  # Darwin | Linux | Windows | …
    if os_name == "Darwin":
        os_version = platform.mac_ver()[0] or platform.release()
    else:
        os_version = platform.release()
    return HostPlatform(
        os_name=os_name or "Unknown",
        os_version=os_version or "unknown",
    )


def build_platform_prompt(host: HostPlatform) -> str:
    """
    Platform-specific guidance prepended to the CMD: system prompt.
    Only includes the section for the actual OS to avoid confusing small models.
    """
    header = (
        f"You are running on {host.os_name} ({host.os_version}). Always generate shell\n"
        f"commands that are correct for THIS operating system — never assume\n"
        f"Linux commands work on macOS or vice versa."
    )

    if host.os_name == "Darwin":
        body = f"""
macOS equivalents to remember (this system is macOS/Darwin):
- Memory info: use `vm_stat` or `sysctl hw.memsize`, NOT `free`
- CPU core count: use `sysctl -n hw.ncpu` or `sysctl -n hw.physicalcpu`,
  NOT `lscpu`
- OS version: use `sw_vers`, NOT `cat /etc/os-release`
- Disk usage: `df -h` works the same on both, this one is fine
- Process list: `ps -ef` or `ps aux` works the same on both

Only use the commands appropriate for {host.os_name}. If you generate a
command and are not sure it exists on {host.os_name}, prefer the safer,
more universally available option (e.g. `df -h`, `ps aux`, `uname -a`).
""".strip()
    elif host.os_name == "Linux":
        body = f"""
Linux equivalents (this system is Linux):
- Memory: `free -h`
- CPU cores: `lscpu` or `nproc`
- OS version: `cat /etc/os-release`
- Disk usage: `df -h`
- Process list: `ps -ef` or `ps aux`

Only use the commands appropriate for {host.os_name}. If you generate a
command and are not sure it exists on {host.os_name}, prefer the safer,
more universally available option (e.g. `df -h`, `ps aux`, `uname -a`).
""".strip()
    else:
        body = f"""
Only use the commands appropriate for {host.os_name}. If you generate a
command and are not sure it exists on {host.os_name}, prefer the safer,
more universally available option (e.g. `df -h`, `ps aux`, `uname -a`).
""".strip()

    return f"{header}\n\n{body}"

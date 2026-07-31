"""Tests for SafetyChecker — rules come from AppConfig/SafetyConfig only."""

from __future__ import annotations

from pathlib import Path

from config import SafetyConfig
from safety import SafetyChecker, check_mv_overwrite

# Test fixture mirroring production safety rules for deletion/move policy
TEST_SAFETY = SafetyConfig(
    blocked_patterns=[
        r"rm\s+-rf\s+/",
        r"dd\s+if=",
        r"mkfs",
        r"> /dev/",
        r"\brm\b",
        r"\brmdir\b",
        r"\bunlink\b",
        r"\bshred\b",
        r"\btruncate\b",
        r"find.*-delete",
        r"find.*-exec\s+rm",
        r"git\s+clean\s+-[a-z]*f",
        r"mv\s+.*\s+/dev/null",
        r"mv\s+(\S+)\s+\1\b",
    ],
    destructive_keywords=[
        "drop",
        "delete",
        "kill",
        "pkill",
        "format",
        "wipe",
    ],
    read_only_commands=[
        "cd",
        "ls",
        "ps",
        "df",
        "du",
        "cat",
        "tail",
        "head",
        "grep",
        "find",
        "curl",
        "ping",
        "top",
        "htop",
        "docker ps",
        "git status",
        "git log",
        "sysctl",
        "sysctl -n hw.memsize",
        "sysctl -n hw.ncpu",
        "sysctl -n hw.physicalcpu",
        "system_profiler",
        "uname",
        "lscpu",
        "free",
        "vm_stat",
        "diskutil list",
        "diskutil info",
        "system_profiler SPHardwareDataType",
        "sw_vers",
        "hostinfo",
        "nproc",
    ],
)


def make_checker(working_directory: str | None = None) -> SafetyChecker:
    return SafetyChecker(TEST_SAFETY, working_directory=working_directory)


def test_rm_rf_root_blocked():
    result = make_checker().check("rm -rf /")
    assert result.verdict == "blocked"


def test_rm_dist_blocked():
    result = make_checker().check("rm ./dist")
    assert result.verdict == "blocked"


def test_rm_rf_anything_blocked():
    result = make_checker().check("rm -rf ./anything")
    assert result.verdict == "blocked"


def test_rmdir_blocked():
    result = make_checker().check("rmdir ./folder")
    assert result.verdict == "blocked"


def test_find_delete_blocked():
    result = make_checker().check("find . -name '*.tmp' -delete")
    assert result.verdict == "blocked"


def test_git_clean_fd_blocked():
    result = make_checker().check("git clean -fd")
    assert result.verdict == "blocked"


def test_mv_to_dev_null_blocked():
    result = make_checker().check("mv file.txt /dev/null")
    assert result.verdict == "blocked"


def test_shred_blocked():
    result = make_checker().check("shred somefile.txt")
    assert result.verdict == "blocked"


def test_kill_still_destructive():
    result = make_checker().check("kill -9 1234")
    assert result.verdict == "destructive"


def test_pkill_still_destructive():
    result = make_checker().check("pkill node")
    assert result.verdict == "destructive"


def test_mv_to_new_path_is_write(tmp_path: Path):
    src = tmp_path / "old_name.txt"
    src.write_text("hi")
    dest = tmp_path / "new_name.txt"
    assert not dest.exists()
    result = make_checker(str(tmp_path)).check("mv old_name.txt new_name.txt")
    assert result.verdict == "write"


def test_mv_overwrite_existing_file_blocked(tmp_path: Path):
    (tmp_path / "file1.txt").write_text("one")
    (tmp_path / "file2.txt").write_text("two")
    result = make_checker(str(tmp_path)).check("mv file1.txt file2.txt")
    assert result.verdict == "blocked"
    assert "overwrite" in result.reason.lower()


def test_check_mv_overwrite_detects_existing_dest(tmp_path: Path):
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.txt").write_text("b")
    assert check_mv_overwrite("mv a.txt b.txt", str(tmp_path)) is True
    assert check_mv_overwrite("mv a.txt c.txt", str(tmp_path)) is False
    assert check_mv_overwrite("mv -n a.txt b.txt", str(tmp_path)) is False


def test_ps_aux_read():
    result = make_checker().check("ps aux")
    assert result.verdict == "read"


def test_ls_la_read():
    result = make_checker().check("ls -la")
    assert result.verdict == "read"


def test_cd_is_read():
    assert make_checker().check("cd ~/Desktop").verdict == "read"
    assert make_checker().check("cd").verdict == "read"


def test_cat_read():
    result = make_checker().check("cat file.txt")
    assert result.verdict == "read"


def test_docker_ps_read():
    result = make_checker().check("docker ps")
    assert result.verdict == "read"


def test_git_pull_write():
    result = make_checker().check("git pull")
    assert result.verdict == "write"


def test_npm_install_write():
    result = make_checker().check("npm install")
    assert result.verdict == "write"


def test_stricter_merge_prefers_blocked():
    checker = make_checker()
    safety = checker.check("rm -rf /")
    merged = checker.merge("read", safety)
    assert merged.verdict == "blocked"


def test_stricter_merge_blocked_rm_over_write():
    checker = make_checker()
    safety = checker.check("rm ./dist")
    merged = checker.merge("write", safety)
    assert merged.verdict == "blocked"


def test_sysctl_hw_memsize_is_read(tmp_path: Path):
    result = make_checker(str(tmp_path)).check("sysctl hw.memsize")
    assert result.verdict == "read"


def test_cat_proc_meminfo_is_read(tmp_path: Path):
    result = make_checker(str(tmp_path)).check("cat /proc/meminfo")
    assert result.verdict == "read"


def test_system_profiler_hardware_is_read(tmp_path: Path):
    result = make_checker(str(tmp_path)).check(
        "system_profiler SPHardwareDataType"
    )
    assert result.verdict == "read"


def test_df_h_is_read(tmp_path: Path):
    result = make_checker(str(tmp_path)).check("df -h")
    assert result.verdict == "read"


def test_free_h_is_read(tmp_path: Path):
    result = make_checker(str(tmp_path)).check("free -h")
    assert result.verdict == "read"


def test_vm_stat_is_read():
    assert make_checker().check("vm_stat").verdict == "read"


def test_sysctl_n_hw_memsize_is_read():
    assert make_checker().check("sysctl -n hw.memsize").verdict == "read"


def test_sysctl_n_hw_ncpu_is_read():
    assert make_checker().check("sysctl -n hw.ncpu").verdict == "read"


def test_sw_vers_is_read():
    assert make_checker().check("sw_vers").verdict == "read"


def test_rm_outside_still_blocked(tmp_path: Path):
    result = make_checker(str(tmp_path)).check(
        "rm /Users/sunny/Documents/important.pdf"
    )
    assert result.verdict == "blocked"


def test_write_outside_working_dir_blocked(tmp_path: Path):
    """Path confinement still blocks mutating commands outside cwd."""
    result = make_checker(str(tmp_path)).check(
        "touch /Users/sunny/Documents/important.pdf"
    )
    assert result.verdict == "blocked"
    assert "working directory" in result.reason.lower()


def test_mv_outside_working_dir_is_write(tmp_path: Path):
    """Relocate outside cwd needs confirmation, but is not auto-blocked."""
    (tmp_path / "local_file.txt").write_text("x")
    result = make_checker(str(tmp_path)).check(
        "mv ./local_file.txt /Users/sunny/Desktop/file.txt"
    )
    assert result.verdict == "write"

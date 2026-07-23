import ctypes
import os
import subprocess
import sys
import time


def _write_log(path, message):
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("%s %s\n" % (
                time.strftime("%Y-%m-%d %H:%M:%S"), message))
    except OSError:
        pass


def _wait_for_windows_process(process_id, timeout_seconds):
    synchronize = 0x00100000
    wait_object_0 = 0x00000000
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.argtypes = [
        ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.WaitForSingleObject.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32]
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_bool
    handle = kernel32.OpenProcess(synchronize, False, process_id)
    if not handle:
        return True
    try:
        result = kernel32.WaitForSingleObject(
            handle, int(timeout_seconds * 1000))
        return result == wait_object_0
    finally:
        kernel32.CloseHandle(handle)


def _process_exists(process_id):
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_for_process(process_id, timeout_seconds=60.0):
    if os.name == "nt":
        return _wait_for_windows_process(process_id, timeout_seconds)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not _process_exists(process_id):
            return True
        time.sleep(0.1)
    return False


def _start_application(executable, working_directory):
    arguments = [executable]
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        subprocess.Popen(
            arguments,
            cwd=working_directory,
            close_fds=True,
            creationflags=flags)
    else:
        subprocess.Popen(
            arguments,
            cwd=working_directory,
            close_fds=True,
            start_new_session=True)


def main():
    if len(sys.argv) < 4:
        return 2
    process_id = int(sys.argv[1])
    executable = os.path.realpath(sys.argv[2])
    working_directory = os.path.realpath(sys.argv[3])
    log_path = os.path.realpath(sys.argv[4]) if len(sys.argv) > 4 else ""
    if not os.path.isfile(executable):
        _write_log(log_path, "Painter executable was not found: " + executable)
        return 3
    if not _wait_for_process(process_id):
        _write_log(log_path, "Painter did not exit before the restart timeout.")
        return 4
    try:
        _start_application(executable, working_directory)
    except Exception as exception:
        _write_log(log_path, "Painter restart failed: " + repr(exception))
        return 5
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

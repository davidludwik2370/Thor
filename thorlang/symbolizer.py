"""ASan's standard llvm-symbolizer protocol, with original Thor locations."""
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

from .memorymap import Locations, read_binary


def find_llvm(preferred=""):
    candidates = [os.environ.get("THOR_LLVM_SYMBOLIZER"), preferred,
                  shutil.which("llvm-symbolizer"),
                  "/opt/homebrew/opt/llvm/bin/llvm-symbolizer",
                  "/usr/local/opt/llvm/bin/llvm-symbolizer"]
    for path in candidates:
        if path and Path(path).is_file() and os.access(path, os.X_OK):
            return str(Path(path).resolve())
    return None


class Symbolizer:
    def __init__(self, arguments=()):
        self.arguments = arguments
        self.modules = {}
        self.locations = []
        self.backend = None
        self.preferred = ""

    def load(self, command):
        try:
            words = shlex.split(command)
            if len(words) < 2:
                return
            path = words[1]
            if not Path(path).is_file() and path.rsplit(":", 1)[-1] in {"arm64", "arm64e", "x86_64", "x86_64h"}:
                path = path.rsplit(":", 1)[0]
            if path not in self.modules:
                self.modules[path] = None
                data = read_binary(path)
                locations = Locations(data)
                self.modules[path] = locations
                self.locations.append(locations)
                self.preferred = self.preferred or data.get("symbolizer", "")
        except (OSError, ValueError, KeyError, TypeError, IndexError):
            # Ordinary Odin, C and system libraries have no Thor section.
            return

    def transform(self, response, command="CODE"):
        result = response.splitlines(keepends=True)
        for index, text in enumerate(result):
            match = re.fullmatch(r"(.+):(\d+):(\d+)\n", text)
            has_column = bool(match)
            if not match:
                match = re.fullmatch(r"(.+):(\d+)\n", text)
            if not match:
                continue
            path, line = match[1], int(match[2])
            column = int(match[3]) if has_column else 1
            mapped = next((value for locations in self.locations
                           if (value := locations.lookup(path, line, column))), None)
            if mapped:
                file, row, col, procedure = mapped
                result[index] = f"{file}:{row}" + (f":{col}" if has_column else "") + "\n"
                if command == "CODE" and index and procedure and "__thor_" in result[index - 1]:
                    result[index - 1] = procedure + "\n"
        return "".join(result)

    def request(self, command):
        self.load(command)
        if self.backend is None:
            path = find_llvm(self.preferred)
            if path:
                self.backend = subprocess.Popen([path, *self.arguments], stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", bufsize=1)
        if self.backend is None:
            return "??\n0 0\n??:0\n\n" if command.startswith("DATA ") else "??\n??:0:0\n\n"
        self.backend.stdin.write(command)
        self.backend.stdin.flush()
        output = []
        while True:
            line = self.backend.stdout.readline()
            if not line:
                raise OSError("llvm-symbolizer closed its output")
            output.append(line)
            if line == "\n":
                break
        return self.transform("".join(output), command.split(" ", 1)[0])

    def close(self):
        if self.backend:
            self.backend.stdin.close()
            try:
                self.backend.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.backend.kill()
                self.backend.wait()
            self.backend.stdout.close()
            self.backend = None


def main():
    symbolizer = Symbolizer(sys.argv[1:])
    try:
        for command in sys.stdin:
            try:
                response = symbolizer.request(command)
            except (OSError, ValueError):
                symbolizer.close()
                response = "??\n0 0\n??:0\n\n" if command.startswith("DATA ") else "??\n??:0:0\n\n"
            sys.stdout.write(response)
            sys.stdout.flush()
    except BrokenPipeError:
        pass
    finally:
        symbolizer.close()


if __name__ == "__main__":
    main()

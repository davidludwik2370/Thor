#!/usr/bin/env python3
"""Exercise OLS's reviewed SDK JSON adapter with checks that survive -disable-assert."""
import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
SOURCE=r'''package server
import "core:encoding/json"
import "core:io"
import "core:strings"
Marshal_Data_Error :: enum {None, Unsupported_Type}
Marshal_Error :: union #shared_nil {Marshal_Data_Error, io.Error}
main :: proc() {
    inputs := []string{"simple", "quote\" backslash\\ newline\n", "tab\t control\x01", "héllo 世界 😀"}
    for input in inputs {
        builder := strings.builder_make()
        err := thor_installer_marshal_json_string(strings.to_writer(&builder), input)
        if err != nil {panic("JSON writer failed")}
        encoded := strings.to_string(builder)
        decoded: string
        decode_error := json.unmarshal(transmute([]u8)encoded, &decoded)
        if decode_error != nil || decoded != input {panic("JSON writer changed text")}
        delete(decoded)
        strings.builder_destroy(&builder)
    }
    err := thor_installer_marshal_json_string(io.Writer{}, "must propagate IO errors")
    if err == nil {panic("JSON writer swallowed an IO error")}
}
'''

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--odin',default='odin')
    args=parser.parse_args()
    odin=shutil.which(args.odin) or str(Path(args.odin).resolve(strict=True))
    with tempfile.TemporaryDirectory(prefix='thor-ols-json-') as temp:
        directory=Path(temp)
        shutil.copyfile(ROOT/'install/ols-json-compat.odin',directory/'compat.odin')
        (directory/'main.odin').write_text(SOURCE,encoding='utf-8')
        for flags in [[],['-o:speed','-disable-assert','-no-bounds-check','-no-type-assert']]:
            subprocess.run([odin,'run',str(directory),'-thread-count:2',*flags],cwd=directory,check=True)
    print('PASS: OLS JSON escaping and IO error propagation, checked and unchecked')

if __name__=='__main__':main()

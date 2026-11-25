# Native runtime provenance

`LLVM-22.1.8.txt` is the unmodified LLVM project license from
https://github.com/llvm/llvm-project/blob/llvmorg-22.1.8/LICENSE.TXT.

The reviewed Windows Odin commit `9a6991685ddf663956d9836602eb421e141fc443`
includes the LLVM 22.1.8 `LLVM-C.dll`. Windows bundles carry this license alongside
their generated Rust dependency inventory and Odin/SDK notices. macOS/Linux
builders retain notices belonging to their actual copied LLVM/support libraries.

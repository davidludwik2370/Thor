package main
Installer_Odin_Record :: struct {number: int}
installer_odin_identity :: proc(value: $T) -> T { return value }
installer_odin_probe :: proc() {
    record := installer_odin_identity(Installer_Odin_Record{1})
    _ = record.number
}

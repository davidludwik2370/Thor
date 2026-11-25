//! Reviewed allocation-free native boundaries. This is deliberately a finite
//! source-verified list, not a general exemption for foreign calls or LLVM names.
use super::*;
#[derive(serde::Deserialize)]
struct Instruction {
    sources: Vec<String>,
}
fn instructions() -> &'static std::collections::HashMap<String, Instruction> {
    static DATA: std::sync::OnceLock<std::collections::HashMap<String, Instruction>> =
        std::sync::OnceLock::new();
    DATA.get_or_init(|| {
        #[derive(serde::Deserialize)]
        struct Catalog {
            entries: std::collections::HashMap<String, Instruction>,
        }
        serde_json::from_str::<Catalog>(include_str!("../data/noalloc-instructions.json"))
            .expect("reviewed LLVM memory-effect catalog")
            .entries
    })
}
impl Check<'_, '_> {
    pub(super) fn system_leaf(&mut self, w: &Work) -> Result<bool> {
        let source = &self.r.sources[w.at.file];
        let Some(root) = source.info["odin_root"].as_str() else {
            return Ok(false);
        };
        let Ok(relative) = source.path.strip_prefix(root) else {
            return Ok(false);
        };
        let path = relative.to_string_lossy().replace('\\', "/");
        let Some(decl) = source.node(w.at.node).parent else {
            return Ok(false);
        };
        let Some(name) = source
            .group(decl, "names")
            .first()
            .map(|node| source.text(*node))
        else {
            return Ok(false);
        };
        let allowed = match path.as_str() {
            "base/runtime/procs.odin" => ["RtlMoveMemory", "RtlFillMemory"].contains(&name),
            "core/sys/windows/kernel32.odin" => [
                "ReadFile", "WriteFile", "FlushFileBuffers", "GetLastError", "SetLastError", "GetConsoleMode", "GetFileType",
                "GetCurrentProcess", "GetCurrentProcessId", "GetCurrentThread", "GetCurrentThreadId",
                "QueryPerformanceCounter", "QueryPerformanceFrequency", "GetTickCount64",
                "GetSystemTimeAsFileTime", "GetSystemTimePreciseAsFileTime", "Sleep", "SwitchToThread",
                "InitializeSRWLock", "AcquireSRWLockExclusive", "AcquireSRWLockShared",
                "ReleaseSRWLockExclusive", "ReleaseSRWLockShared", "TryAcquireSRWLockExclusive",
                "TryAcquireSRWLockShared", "WaitOnAddress", "WakeByAddressSingle", "WakeByAddressAll",
            ].contains(&name),
            "core/sys/linux/sys.odin" => [
                "read","write","pread","pwrite","readv","writev","close","lseek","fsync","fdatasync",
                "clock_gettime",
                "clock_getres",
                "nanosleep",
                "clock_nanosleep",
                "sched_yield",
                "sched_getaffinity",
                "getpid",
                "getppid",
                "getuid",
                "geteuid",
                "getgid",
                "getegid",
            ]
            .contains(&name),
            "core/sys/posix/time.odin" => ["clock_gettime", "nanosleep"].contains(&name),
            "core/sys/posix/unistd.odin" => [
                "getpid", "getppid", "getuid", "geteuid", "getgid", "getegid",
                "read","write","pread","pwrite","close","lseek","fsync",
            ]
            .contains(&name),
            "core/sys/posix/sys_uio.odin" => ["readv","writev"].contains(&name),
            "core/sys/posix/sched.odin" => name == "sched_yield",
            "core/c/libc/string.odin" => [
                "memcpy", "memmove", "memcmp", "memchr", "strcpy", "strncpy", "strcat", "strncat",
                "strcmp", "strncmp", "strchr", "strrchr", "strcspn", "strpbrk", "strlen",
            ]
            .contains(&name),
            "core/c/libc/errno.odin" => name == "_get_errno",
            "core/os/process_posix_darwin.odin" => {
                ["_sysctlbyname", "pthread_threadid_np"].contains(&name)
            }
            _ => false,
        };
        let mut allowed = allowed;
        if !allowed && source.role(w.at.node, "body").is_none() {
            let foreign = source
                .ancestors(w.at.node)
                .find(|node| source.node(*node).kind == "ForeignBlock");
            if foreign.is_some_and(|node| source.node(node).data["calling_convention"] == "none") {
                let attributes = self.r.attributes(Ref { node: decl, ..w.at });
                for (name, value) in attributes {
                    if name != "link_name" {
                        continue;
                    }
                    let mut selected=value;
                    for _ in 0..16 {
                        let Some(at)=selected else{break;};
                        let source=&self.r.sources[at.file];
                        if !matches!(source.node(at.node).kind.as_str(),"Paren"|"WhenExpr"|"Ternary"){break;}
                        let role=if source.node(at.node).kind=="Paren"{"expr"}else{
                            let Some(cond)=source.role(at.node,"cond") else{break;};
                            let Some(value)=self.r.context_condition(Ref{node:cond,..at},&w.bindings) else{selected=None;break;};
                            if value{"x"}else{"y"}
                        };
                        selected=source.role(at.node,role).map(|node|Ref{node,..at});
                    }
                    if let Some(crate::semantics::Constant::Text(link)) =
                        selected.and_then(|value| self.r.constant_value(value, &w.bindings, None))
                    {
                        allowed = instructions()
                            .get(&link)
                            .is_some_and(|entry| entry.sources.contains(&path));
                    }
                }
            }
        }
        if !allowed {
            return Ok(false);
        }
        self.io
            .verify_sdk(self.r, self.overlays)
            .map_err(|reason| self.error(w, w.at, &reason))?;
        crate::profile::count("no_alloc.system_leaves", 1);
        Ok(true)
    }
}

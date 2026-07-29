use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};

pub struct MapviewHandle {
    child: Child,
}

impl MapviewHandle {
    pub fn is_alive(&mut self) -> bool {
        matches!(self.child.try_wait(), Ok(None))
    }

    pub fn send_data(&mut self, data_json: &str) -> std::io::Result<()> {
        let stdin = self.child.stdin.as_mut().expect("piped stdin");
        writeln!(stdin, "{data_json}")?;
        stdin.flush()
    }
}

impl Drop for MapviewHandle {
    fn drop(&mut self) {
        let _ = self.child.kill();
    }
}

fn which(name: &str) -> Option<PathBuf> {
    let path_var = std::env::var_os("PATH")?;
    std::env::split_paths(&path_var)
        .map(|dir| dir.join(name))
        .find(|candidate| candidate.is_file())
}

fn find_mapview_binary() -> Result<PathBuf, String> {
    let exe_name = if cfg!(windows) {
        "bt-mapview.exe"
    } else {
        "bt-mapview"
    };

    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            let candidate = dir.join(exe_name);
            if candidate.exists() {
                return Ok(candidate);
            }
        }
    }

    which(exe_name).ok_or_else(|| {
        format!("could not locate {exe_name} next to bt-gui's own executable or on PATH")
    })
}

fn spawn(binary: &Path) -> Result<Child, String> {
    Command::new(binary)
        .stdin(Stdio::piped())
        .spawn()
        .map_err(|e| format!("failed to spawn {}: {e}", binary.display()))
}

pub fn ensure_running_and_send(
    handle: &mut Option<MapviewHandle>,
    data_json: &str,
) -> Result<(), String> {
    let alive = handle.as_mut().is_some_and(MapviewHandle::is_alive);
    if !alive {
        let binary = find_mapview_binary()?;
        let child = spawn(&binary)?;
        *handle = Some(MapviewHandle { child });
    }

    let h = handle.as_mut().expect("just ensured Some above");
    h.send_data(data_json).map_err(|e| {
        *handle = None;
        format!("failed to write to bt-mapview: {e}")
    })
}

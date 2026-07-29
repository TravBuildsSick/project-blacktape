use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};

#[cfg(unix)]
const BRAIN_MEMORY_LIMIT_BYTES: u64 = 512 * 1024 * 1024;

#[cfg(unix)]
fn limit_memory(cmd: &mut Command) {
    use std::os::unix::process::CommandExt;

    unsafe {
        cmd.pre_exec(|| {
            let limit = libc::rlimit {
                rlim_cur: BRAIN_MEMORY_LIMIT_BYTES as libc::rlim_t,
                rlim_max: BRAIN_MEMORY_LIMIT_BYTES as libc::rlim_t,
            };
            if libc::setrlimit(libc::RLIMIT_AS, &limit) != 0 {
                return Err(std::io::Error::last_os_error());
            }
            Ok(())
        });
    }
}

#[cfg(not(unix))]
fn limit_memory(_cmd: &mut Command) {}

fn which(name: &str) -> Result<PathBuf, ()> {
    let path_var = std::env::var_os("PATH").ok_or(())?;
    for dir in std::env::split_paths(&path_var) {
        let candidate = dir.join(name);
        if candidate.is_file() {
            return Ok(candidate);
        }
    }
    Err(())
}

pub fn find_brain_binary() -> Result<PathBuf, String> {
    if let Ok(path) = which("blacktape-brain") {
        return Ok(path);
    }

    let manifest_dir = Path::new(env!("CARGO_MANIFEST_DIR"));
    let project_root = manifest_dir.parent().unwrap_or(manifest_dir);
    let candidate = project_root
        .join("brain")
        .join(".venv")
        .join("bin")
        .join("blacktape-brain");

    if candidate.exists() {
        return Ok(candidate);
    }

    Err(format!(
        "could not locate blacktape-brain on PATH or at {}",
        candidate.display()
    ))
}

pub fn spawn_ingest_stream(binary: &Path, export_dir: &Path) -> Result<Child, String> {
    let mut command = Command::new(binary);
    command
        .arg("ingest-stream")
        .arg(export_dir)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    limit_memory(&mut command);

    command.spawn().map_err(|e| format!("failed to spawn: {e}"))
}

pub fn spawn_ingest_stream_files(binary: &Path, files: &[PathBuf]) -> Result<Child, String> {
    let mut command = Command::new(binary);
    command
        .arg("ingest-stream")
        .arg("--files")
        .args(files)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    limit_memory(&mut command);

    command.spawn().map_err(|e| format!("failed to spawn: {e}"))
}

pub fn run_purge(binary: &Path, db_path: &Path) -> Result<String, String> {
    let mut command = Command::new(binary);
    command
        .arg("purge")
        .arg("--db")
        .arg(db_path)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    limit_memory(&mut command);

    let output = command
        .output()
        .map_err(|e| format!("failed to spawn: {e}"))?;
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(format!("purge failed ({}): {stderr}", output.status));
    }

    Ok(String::from_utf8_lossy(&output.stdout).trim().to_string())
}

pub fn run_query(
    binary: &Path,
    subcommand: &str,
    db_path: &Path,
    extra_args: &[&str],
) -> Result<serde_json::Value, String> {
    let mut command = Command::new(binary);
    command
        .arg("query")
        .arg(subcommand)
        .arg("--db")
        .arg(db_path)
        .args(extra_args)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    limit_memory(&mut command);

    let output = command
        .output()
        .map_err(|e| format!("failed to spawn: {e}"))?;
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(format!(
            "query {subcommand} failed ({}): {stderr}",
            output.status
        ));
    }

    serde_json::from_slice(&output.stdout).map_err(|e| format!("bad query response: {e}"))
}

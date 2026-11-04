//! A child process together with everything it starts, so the whole tree can be ended at once.
//!
//! Windows: each child goes into its own job object marked kill-on-close. The receiver starts the
//! eye tracker and the speech worker; they join the job, so ending the job ends them too, and if
//! the app itself dies the system closes the handle and ends them all (no orphaned `pythonw.exe`).
//! Unix (development builds): the child leads a new process group, which is signalled as a whole.

use std::io;
use std::process::{Child, Command, ExitStatus};

pub struct Tree {
    child: Child,
    #[cfg(windows)]
    job: job::Job,
}

impl Tree {
    pub fn spawn(command: &mut Command) -> io::Result<Self> {
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            command.creation_flags(CREATE_NO_WINDOW);
            let job = job::Job::new()?;
            let child = command.spawn()?;
            if let Err(error) = job.assign(&child) {
                let mut child = child;
                let _ = child.kill();
                return Err(error);
            }
            Ok(Self { child, job })
        }
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            command.process_group(0);
            // Linux development builds: the child dies with the app, as a job object does on Windows.
            #[cfg(target_os = "linux")]
            unsafe {
                command.pre_exec(|| {
                    libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGKILL);
                    Ok(())
                });
            }
            Ok(Self { child: command.spawn()? })
        }
    }

    pub fn id(&self) -> u32 {
        self.child.id()
    }

    pub fn try_wait(&mut self) -> io::Result<Option<ExitStatus>> {
        self.child.try_wait()
    }

    /// End the child and everything it started.
    pub fn kill(&mut self) {
        #[cfg(windows)]
        self.job.terminate();
        #[cfg(unix)]
        unsafe {
            libc::kill(-(self.child.id() as i32), libc::SIGKILL);
        }
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

impl Drop for Tree {
    fn drop(&mut self) {
        if matches!(self.child.try_wait(), Ok(None)) {
            self.kill();
        }
    }
}

#[cfg(windows)]
mod job {
    use std::io;
    use std::os::windows::io::AsRawHandle;
    use std::process::Child;
    use std::ptr::null;

    use windows_sys::Win32::Foundation::{CloseHandle, HANDLE};
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation, SetInformationJobObject,
        TerminateJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };

    pub struct Job(HANDLE);

    // The handle is only used through the kernel's own synchronisation.
    unsafe impl Send for Job {}
    unsafe impl Sync for Job {}

    impl Job {
        pub fn new() -> io::Result<Self> {
            unsafe {
                let handle = CreateJobObjectW(null(), null());
                if handle.is_null() {
                    return Err(io::Error::last_os_error());
                }
                let job = Job(handle);
                let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
                info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
                let ok = SetInformationJobObject(
                    handle,
                    JobObjectExtendedLimitInformation,
                    &info as *const _ as *const core::ffi::c_void,
                    std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
                );
                if ok == 0 {
                    return Err(io::Error::last_os_error());
                }
                Ok(job)
            }
        }

        pub fn assign(&self, child: &Child) -> io::Result<()> {
            let ok = unsafe { AssignProcessToJobObject(self.0, child.as_raw_handle() as HANDLE) };
            if ok == 0 {
                return Err(io::Error::last_os_error());
            }
            Ok(())
        }

        pub fn terminate(&self) {
            unsafe {
                TerminateJobObject(self.0, 1);
            }
        }
    }

    impl Drop for Job {
        fn drop(&mut self) {
            unsafe {
                CloseHandle(self.0);
            }
        }
    }
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    use std::time::{Duration, Instant};

    /// Running, and not a zombie waiting for a parent that may never reap it in a container.
    fn alive(pid: u32) -> bool {
        match std::fs::read_to_string(format!("/proc/{pid}/stat")) {
            Ok(stat) => stat.rsplit(')').next().map(|rest| rest.trim_start().starts_with('Z')) == Some(false),
            Err(_) => false,
        }
    }

    #[test]
    fn kill_ends_the_child_and_its_children() {
        // A shell that starts a grandchild, prints its id, and waits.
        let dir = std::env::temp_dir().join(format!("daycube-tree-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let pidfile = dir.join("grandchild");
        let mut command = Command::new("sh");
        command.arg("-c").arg(format!("sleep 60 & echo $! > {}; wait", pidfile.display()));
        let mut tree = Tree::spawn(&mut command).unwrap();
        let start = Instant::now();
        while !pidfile.exists() || std::fs::read_to_string(&pidfile).unwrap().trim().is_empty() {
            assert!(start.elapsed() < Duration::from_secs(5));
            std::thread::sleep(Duration::from_millis(20));
        }
        let grandchild: u32 = std::fs::read_to_string(&pidfile).unwrap().trim().parse().unwrap();
        assert!(alive(grandchild));
        tree.kill();
        let start = Instant::now();
        while alive(grandchild) {
            // Reaped by init once killed; give it a moment.
            assert!(start.elapsed() < Duration::from_secs(5), "grandchild survived");
            std::thread::sleep(Duration::from_millis(20));
        }
        assert!(tree.try_wait().unwrap().is_some());
    }
}

#[cfg(all(test, windows))]
mod windows_tests {
    use super::*;
    use std::time::{Duration, Instant};

    fn alive(pid: u32) -> bool {
        let out = Command::new("tasklist").args(["/FI", &format!("PID eq {pid}"), "/NH"]).output().unwrap();
        String::from_utf8_lossy(&out.stdout).contains(&pid.to_string())
    }

    #[test]
    fn kill_ends_the_child_and_its_children() {
        let dir = std::env::temp_dir().join(format!("daycube-tree-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let pidfile = dir.join("grandchild.txt");
        let _ = std::fs::remove_file(&pidfile);
        // A child that starts a grandchild, records its id, and waits: as the receiver starts the tracker.
        let script = format!(
            "$p = Start-Process -FilePath ping -ArgumentList '-n','120','127.0.0.1' -PassThru -WindowStyle Hidden; \
             Set-Content -Path '{}' -Value $p.Id; Start-Sleep -Seconds 120",
            pidfile.display()
        );
        let mut command = Command::new("powershell");
        command.args(["-NoProfile", "-NonInteractive", "-Command", &script]);
        let mut tree = Tree::spawn(&mut command).unwrap();
        let start = Instant::now();
        let grandchild: u32 = loop {
            if let Ok(text) = std::fs::read_to_string(&pidfile) {
                if let Ok(pid) = text.trim().trim_start_matches('\u{feff}').parse() {
                    break pid;
                }
            }
            assert!(start.elapsed() < Duration::from_secs(30), "no grandchild");
            std::thread::sleep(Duration::from_millis(100));
        };
        assert!(alive(grandchild));
        tree.kill();
        let start = Instant::now();
        while alive(grandchild) {
            assert!(start.elapsed() < Duration::from_secs(10), "the grandchild outlived the job");
            std::thread::sleep(Duration::from_millis(100));
        }
        assert!(tree.try_wait().unwrap().is_some());
    }
}

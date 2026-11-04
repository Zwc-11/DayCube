//! Loopback HTTP for the app's own services (the API and the capture receiver). HTTP/1.0, so the
//! server answers with a plain body (no chunking) and closes; nothing here leaves 127.0.0.1.

use std::io::{self, Read, Write};
use std::net::{Ipv4Addr, SocketAddr, TcpListener, TcpStream};
use std::time::Duration;

pub struct Reply {
    pub status: u16,
    pub body: String,
}

pub fn request(
    method: &str,
    port: u16,
    path: &str,
    token: Option<&str>,
    timeout: Duration,
) -> io::Result<Reply> {
    let address = SocketAddr::from((Ipv4Addr::LOCALHOST, port));
    let mut stream = TcpStream::connect_timeout(&address, timeout)?;
    stream.set_read_timeout(Some(timeout))?;
    stream.set_write_timeout(Some(timeout))?;
    let auth = token
        .map(|t| format!("Authorization: Bearer {t}\r\n"))
        .unwrap_or_default();
    let length = if method == "POST" {
        "Content-Length: 0\r\n"
    } else {
        ""
    };
    write!(stream, "{method} {path} HTTP/1.0\r\nHost: 127.0.0.1:{port}\r\n{auth}{length}Connection: close\r\n\r\n")?;
    let mut raw = Vec::new();
    stream.read_to_end(&mut raw)?;
    parse(&raw)
}

pub fn get(port: u16, path: &str, timeout: Duration) -> io::Result<Reply> {
    request("GET", port, path, None, timeout)
}

fn parse(raw: &[u8]) -> io::Result<Reply> {
    let bad = || io::Error::new(io::ErrorKind::InvalidData, "not an HTTP response");
    let split = raw
        .windows(4)
        .position(|w| w == b"\r\n\r\n")
        .ok_or_else(bad)?;
    let head = std::str::from_utf8(&raw[..split]).map_err(|_| bad())?;
    let status = head
        .split_whitespace()
        .nth(1)
        .and_then(|s| s.parse().ok())
        .ok_or_else(bad)?;
    Ok(Reply {
        status,
        body: String::from_utf8_lossy(&raw[split + 4..]).into_owned(),
    })
}

pub fn port_free(port: u16) -> bool {
    TcpListener::bind((Ipv4Addr::LOCALHOST, port)).is_ok()
}

/// ``preferred`` when free, else one the system hands out.
pub fn choose_port(preferred: &[u16]) -> io::Result<u16> {
    if let Some(&port) = preferred.iter().find(|&&p| p != 0 && port_free(p)) {
        return Ok(port);
    }
    Ok(TcpListener::bind((Ipv4Addr::LOCALHOST, 0))?
        .local_addr()?
        .port())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::thread;

    fn serve_once(answer: &'static [u8]) -> (u16, thread::JoinHandle<String>) {
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        let port = listener.local_addr().unwrap().port();
        let handle = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut seen = Vec::new();
            let mut buf = [0u8; 512];
            while !seen.windows(4).any(|w| w == b"\r\n\r\n") {
                let n = stream.read(&mut buf).unwrap();
                assert!(n > 0, "request ended early");
                seen.extend_from_slice(&buf[..n]);
            }
            stream.write_all(answer).unwrap();
            String::from_utf8_lossy(&seen).into_owned()
        });
        (port, handle)
    }

    #[test]
    fn request_sends_token_and_reads_reply() {
        let (port, server) = serve_once(
            b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\n\r\n{\"paused\":false}",
        );
        let reply = request("POST", port, "/pause", Some("abc"), Duration::from_secs(2)).unwrap();
        assert_eq!(reply.status, 200);
        assert_eq!(reply.body, "{\"paused\":false}");
        let sent = server.join().unwrap();
        assert!(sent.starts_with("POST /pause HTTP/1.0\r\n"));
        assert!(sent.contains("Authorization: Bearer abc\r\n"));
        assert!(sent.contains("Content-Length: 0\r\n"));
    }

    #[test]
    fn closed_port_is_an_error_and_free() {
        let port = TcpListener::bind((Ipv4Addr::LOCALHOST, 0))
            .unwrap()
            .local_addr()
            .unwrap()
            .port();
        assert!(get(port, "/", Duration::from_millis(300)).is_err());
        assert!(port_free(port));
    }

    #[test]
    fn choose_port_skips_a_taken_one() {
        let taken = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        let port = taken.local_addr().unwrap().port();
        let chosen = choose_port(&[port]).unwrap();
        assert_ne!(chosen, port);
        assert!(port_free(chosen));
    }

    #[test]
    fn garbage_is_rejected() {
        assert!(parse(b"hello").is_err());
        assert!(parse(b"HTTP/1.1 abc\r\n\r\n").is_err());
    }
}

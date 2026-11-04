//! ``DayCube.exe --self-test``: the installed app proves itself without a person watching. It starts
//! as usual, then checks that the API answers, that the receiver answers on 127.0.0.1:7700, and,
//! from the API's request record (``/__selftest``, on only in this mode), that the window loaded the
//! viewer's page, scripts and data, and that the token the app injected is on the page and accepted
//! (a probe the app runs inside the window calls ``/__desktop/token`` with it).

use serde::Serialize;
use serde_json::Value;
use std::time::{Duration, Instant};

use crate::http;

#[derive(Debug, Default, Serialize)]
pub struct Report {
    pub pass: bool,
    pub api_health: bool,
    pub page_loaded: bool,
    pub scripts_loaded: usize,
    pub api_reads: usize,
    pub token_accepted: bool,
    pub refused_api_calls: Vec<String>,
    pub receiver: Option<Value>,
    pub receiver_required: bool,
    pub seconds: f64,
    pub problems: Vec<String>,
}

/// Judge the window's requests as the API recorded them.
pub fn judge(requests: &[Value], report: &mut Report) {
    let ok = |r: &Value| r["status"].as_u64().is_some_and(|s| (200..400).contains(&s));
    let path = |r: &Value| r["path"].as_str().unwrap_or("").to_string();
    report.page_loaded = requests.iter().any(|r| path(r) == "/" && ok(r));
    report.scripts_loaded =
        requests.iter().filter(|r| path(r).starts_with("/assets/") && path(r).ends_with(".js") && ok(r)).count();
    // The app's own health checks also call /api/health: count the viewer's data reads.
    let api: Vec<&Value> =
        requests.iter().filter(|r| path(r).starts_with("/api/") && path(r) != "/api/health").collect();
    report.api_reads = api.iter().filter(|r| ok(r)).count();
    report.refused_api_calls = api.iter().filter(|r| r["status"].as_u64() == Some(401)).map(|r| path(r)).collect();
    report.token_accepted = requests.iter().any(|r| path(r) == "/__desktop/token" && r["status"].as_u64() == Some(204));
}

fn complete(report: &Report) -> bool {
    report.api_health
        && report.page_loaded
        && report.api_reads > 0
        && report.token_accepted
        && report.refused_api_calls.is_empty()
        && (!report.receiver_required || report.receiver.is_some())
}

/// The probe run inside the viewer window: the injected token against ``/__desktop/token``.
pub const PROBE: &str = "fetch('/__desktop/token',{cache:'no-store',headers:{Authorization:'Bearer '+(((window.__DAYCUBE_DESKTOP__||{}).token)||'')}});";

pub fn run(port: u16, receiver_required: bool, limit: Duration, probe: impl Fn()) -> Report {
    let started = Instant::now();
    let mut report = Report { receiver_required, ..Report::default() };
    let short = Duration::from_secs(3);
    while started.elapsed() < limit {
        report.api_health = http::get(port, "/api/health", short).is_ok_and(|r| r.status == 200);
        if let Ok(reply) = http::get(port, "/__selftest", short) {
            if let Ok(Value::Object(body)) = serde_json::from_str::<Value>(&reply.body) {
                if let Some(Value::Array(requests)) = body.get("requests") {
                    judge(requests, &mut report);
                }
                // Data reads come from the viewer's scripts (from the network or the webview's cache).
                if report.page_loaded && report.api_reads > 0 && !report.token_accepted {
                    probe(); // again each round until accepted: the page may still be loading
                }
            }
        }
        if receiver_required && report.receiver.is_none() {
            report.receiver = http::get(crate::RECEIVER_PORT, "/status", short)
                .ok()
                .filter(|r| r.status == 200)
                .and_then(|r| serde_json::from_str(&r.body).ok());
        }
        if complete(&report) {
            break;
        }
        std::thread::sleep(Duration::from_millis(500));
    }
    report.pass = complete(&report);
    if !report.pass {
        let checks = [
            (report.api_health, "the API did not answer /api/health"),
            (report.page_loaded, "the window did not load the viewer's page"),
            (report.api_reads > 0, "the viewer's scripts read no data from the API"),
            (report.token_accepted, "the token injected into the window was missing or refused"),
            (report.refused_api_calls.is_empty(), "the API refused the window's token"),
            (!report.receiver_required || report.receiver.is_some(), "the receiver did not answer on 127.0.0.1:7700"),
        ];
        report.problems = checks.iter().filter(|(ok, _)| !ok).map(|(_, why)| why.to_string()).collect();
    }
    report.seconds = started.elapsed().as_secs_f64();
    report
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn a_rendered_viewer_with_the_token_passes() {
        let requests = vec![
            json!({"path": "/", "status": 200, "bearer": false}),
            json!({"path": "/assets/index-a1.js", "status": 200, "bearer": false}),
            json!({"path": "/assets/index-a1.css", "status": 200, "bearer": false}),
            json!({"path": "/api/health", "status": 200, "bearer": false}),
            json!({"path": "/api/day/2026-10-20", "status": 304, "bearer": false}),
            json!({"path": "/__desktop/token", "status": 204, "bearer": true}),
        ];
        let mut report = Report { api_health: true, ..Report::default() };
        judge(&requests, &mut report);
        assert!(report.page_loaded);
        assert_eq!(report.scripts_loaded, 1);
        assert_eq!(report.api_reads, 1, "health checks are the app's, not the viewer's");
        assert!(report.token_accepted);
        assert!(complete(&report));
    }

    #[test]
    fn missing_token_or_receiver_fails() {
        let requests = vec![
            json!({"path": "/", "status": 200, "bearer": false}),
            json!({"path": "/assets/index-a1.js", "status": 200, "bearer": false}),
            json!({"path": "/api/days", "status": 200, "bearer": false}),
            json!({"path": "/api/ask", "status": 401, "bearer": false}),
            json!({"path": "/__desktop/token", "status": 401, "bearer": true}),
        ];
        let mut report = Report { api_health: true, ..Report::default() };
        judge(&requests, &mut report);
        assert!(!report.token_accepted);
        assert_eq!(report.refused_api_calls, ["/api/ask"]);
        assert!(!complete(&report));

        let mut with_token = Report { api_health: true, receiver_required: true, ..Report::default() };
        judge(
            &[
                json!({"path": "/", "status": 200}),
                json!({"path": "/assets/x.js", "status": 200}),
                json!({"path": "/api/days", "status": 200}),
                json!({"path": "/__desktop/token", "status": 204, "bearer": true}),
            ],
            &mut with_token,
        );
        assert!(!complete(&with_token), "the receiver is required");
        with_token.receiver = Some(json!({"paused": false}));
        assert!(complete(&with_token));
    }
}

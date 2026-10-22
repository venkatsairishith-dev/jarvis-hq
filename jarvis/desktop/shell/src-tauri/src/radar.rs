use std::thread;
use std::time::{Duration, Instant};
use tauri::{Emitter, WebviewWindow};

use crate::platform;

const ACTIVE_POLL_INTERVAL: Duration = Duration::from_millis(16);
const IDLE_POLL_INTERVAL: Duration = Duration::from_millis(33);
const IDLE_HEARTBEAT: Duration = Duration::from_millis(100);
const ACTIVE_HOLD: Duration = Duration::from_millis(200);

fn cursor_changed(previous: Option<(f64, f64)>, current: (f64, f64)) -> bool {
    match previous {
        Some((previous_x, previous_y)) => {
            (previous_x - current.0).abs() >= 0.5 || (previous_y - current.1).abs() >= 0.5
        }
        None => true,
    }
}

pub(crate) fn start(window: WebviewWindow) {
    thread::spawn(move || {
        let diagnostics_enabled = std::env::var_os("NEKO_TAURI_RADAR_DIAGNOSTIC").is_some();
        let mut last_cursor = None;
        let mut last_emit = Instant::now()
            .checked_sub(IDLE_HEARTBEAT)
            .unwrap_or_else(Instant::now);
        let mut last_diagnostic = Instant::now()
            .checked_sub(Duration::from_secs(1))
            .unwrap_or_else(Instant::now);
        let mut active_until = Instant::now();

        loop {
            let sample = platform::cursor_sample(&window);
            if diagnostics_enabled && last_diagnostic.elapsed() >= Duration::from_millis(500) {
                match sample {
                    Some(sample) => println!(
                        "[RadarDiagnostic] absolute=({:.1},{:.1}) relative_css=({:.1},{:.1}) zero_position={}",
                        sample.absolute.0,
                        sample.absolute.1,
                        sample.relative.0,
                        sample.relative.1,
                        sample.absolute == (0.0, 0.0)
                    ),
                    None => println!(
                        "[RadarDiagnostic] cursor_sample=unavailable; native Wayland may not expose global cursor/window coordinates"
                    ),
                }
                last_diagnostic = Instant::now();
            }

            if let Some(sample) = sample {
                let changed = cursor_changed(last_cursor, sample.absolute);
                let heartbeat_due = last_emit.elapsed() >= IDLE_HEARTBEAT;

                if changed {
                    active_until = Instant::now() + ACTIVE_HOLD;
                }

                if changed || heartbeat_due {
                    let _ = window.emit("neko_radar_tick", sample.relative);
                    last_cursor = Some(sample.absolute);
                    last_emit = Instant::now();
                }
            }

            let poll_interval = if Instant::now() < active_until {
                ACTIVE_POLL_INTERVAL
            } else {
                IDLE_POLL_INTERVAL
            };
            thread::sleep(poll_interval);
        }
    });
}

#[cfg(test)]
mod tests {
    use super::cursor_changed;

    #[test]
    fn emits_initial_and_changed_cursor_positions_only() {
        assert!(cursor_changed(None, (100.0, 200.0)));
        assert!(!cursor_changed(Some((100.0, 200.0)), (100.2, 200.2)));
        assert!(cursor_changed(Some((100.0, 200.0)), (100.5, 200.0)));
    }
}

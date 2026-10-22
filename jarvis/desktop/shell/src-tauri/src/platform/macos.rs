use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::thread;
use std::time::Duration;

use cocoa::base::{id, nil, NO, YES};
use cocoa::foundation::{NSPoint, NSRect, NSSize};
use objc::{class, msg_send, sel, sel_impl};

use super::CursorSample;

pub(crate) const INJECT_CLICK_THROUGH_BRIDGE: bool = true;
const NEKO_INTERACTIVE_WINDOW_LEVEL: i32 = 8;

static WINDOW_PTR: AtomicUsize = AtomicUsize::new(0);
static CLICK_THROUGH: AtomicBool = AtomicBool::new(false);

#[derive(Debug, Clone, Copy, PartialEq)]
struct DesktopBounds {
    x: f64,
    y: f64,
    width: f64,
    height: f64,
}

fn validated_desktop_bounds(frame: DesktopBounds) -> Option<DesktopBounds> {
    (frame.x.is_finite()
        && frame.y.is_finite()
        && frame.width.is_finite()
        && frame.height.is_finite()
        && frame.width > 0.0
        && frame.height > 0.0)
        .then_some(frame)
}

unsafe fn cocoa_primary_screen_frame() -> Option<DesktopBounds> {
    // NSScreen frames are logical Cocoa points. screens[0] is the display
    // owning the menu bar and does not drift with the current key window.
    let screens: id = msg_send![class!(NSScreen), screens];
    if screens == nil {
        return None;
    }
    let count: usize = msg_send![screens, count];
    if count == 0 {
        return None;
    }
    let screen: id = msg_send![screens, objectAtIndex: 0_usize];
    let frame: NSRect = msg_send![screen, frame];

    validated_desktop_bounds(DesktopBounds {
        x: frame.origin.x,
        y: frame.origin.y,
        width: frame.size.width,
        height: frame.size.height,
    })
}

unsafe fn apply_primary_screen_frame(ns_window: id) {
    let Some(frame) = cocoa_primary_screen_frame() else {
        eprintln!("没有可用的 macOS 主显示器布局");
        return;
    };

    println!(
        "应用 macOS 主屏窗口边界: x={}, y={}, width={}, height={}",
        frame.x, frame.y, frame.width, frame.height
    );
    let native_frame = NSRect::new(
        NSPoint::new(frame.x, frame.y),
        NSSize::new(frame.width, frame.height),
    );
    let () = msg_send![ns_window, setFrame: native_frame display: YES];
}

pub(crate) fn prepare_process_environment() {}

pub(crate) fn configure_app(app: &mut tauri::App) {
    app.set_activation_policy(tauri::ActivationPolicy::Accessory);
}

pub(crate) fn configure_main_window(
    app: &tauri::App,
    window: &tauri::WebviewWindow,
) -> Result<(), String> {
    window
        .set_always_on_top(true)
        .map_err(|error| format!("无法将 macOS 主窗口置顶: {error}"))?;

    let app_handle = app.handle().clone();
    let window = window.clone();
    thread::spawn(move || {
        thread::sleep(Duration::from_millis(500));

        let _ = app_handle.run_on_main_thread(move || {
            if let Ok(ns_window_ptr) = window.ns_window() {
                let ns_window = ns_window_ptr as id;
                unsafe {
                    WINDOW_PTR.store(ns_window as usize, Ordering::SeqCst);
                    let () = msg_send![ns_window, setCollectionBehavior: 273_u64];
                    let () = msg_send![ns_window, setLevel: NEKO_INTERACTIVE_WINDOW_LEVEL];
                    let () = msg_send![ns_window, orderOut: nil];
                    let () = msg_send![ns_window, orderFrontRegardless];

                    // Reordering can restore Tauri's original frame. Apply the
                    // final geometry after the window becomes visible.
                    apply_primary_screen_frame(ns_window);

                    let ignore = CLICK_THROUGH.load(Ordering::SeqCst);
                    let value = if ignore { YES } else { NO };
                    let () = msg_send![ns_window, setIgnoresMouseEvents: value];
                }
            }
        });
    });

    Ok(())
}

pub(crate) fn startup_input_region(_window: &tauri::WebviewWindow) -> Result<(), String> {
    Ok(())
}

pub(crate) fn after_main_window_revealed(_window: &tauri::WebviewWindow) {}

pub(crate) fn set_click_through(
    window: &tauri::Window,
    ignore: bool,
    _reason: &str,
    _details: &str,
) -> Result<(), String> {
    CLICK_THROUGH.store(ignore, Ordering::SeqCst);
    let ptr = WINDOW_PTR.load(Ordering::SeqCst);
    if ptr == 0 {
        return window
            .set_ignore_cursor_events(ignore)
            .map_err(|error| error.to_string());
    }

    let ns_window = ptr as id;
    unsafe {
        let value = if ignore { YES } else { NO };
        let () = msg_send![ns_window, setIgnoresMouseEvents: value];
    }
    Ok(())
}

pub(crate) fn prepare_for_navigation(window: &tauri::WebviewWindow) {
    CLICK_THROUGH.store(false, Ordering::SeqCst);
    let _ = window.set_ignore_cursor_events(false);
}

pub(crate) fn diagnostic_query() -> Option<&'static str> {
    None
}

pub(crate) fn cursor_sample(window: &tauri::WebviewWindow) -> Option<CursorSample> {
    let cursor = window.cursor_position().ok()?;
    let window_position = window.inner_position().ok()?;
    let window_scale = window.scale_factor().ok()?;
    let cursor_scale = window
        .primary_monitor()
        .ok()
        .flatten()
        .map(|monitor| monitor.scale_factor())
        .filter(|scale| scale.is_finite() && *scale > 0.0)
        .unwrap_or(window_scale);
    let cursor_logical = cursor.to_logical::<f64>(cursor_scale);
    let window_logical = window_position.to_logical::<f64>(window_scale);

    Some(CursorSample {
        absolute: (cursor.x, cursor.y),
        relative: (
            cursor_logical.x - window_logical.x,
            cursor_logical.y - window_logical.y,
        ),
    })
}

pub(crate) fn open_settings_window(
    app: &tauri::AppHandle,
    url: tauri::Url,
    label: String,
) -> Result<(), String> {
    tauri::WebviewWindowBuilder::new(app, label, tauri::WebviewUrl::External(url))
        .title("设置面板")
        .inner_size(900.0, 700.0)
        .transparent(false)
        .decorations(true)
        .build()
        .map(|_| ())
        .map_err(|error| error.to_string())
}

#[cfg(test)]
mod tests {
    use super::{validated_desktop_bounds, DesktopBounds};

    #[test]
    fn valid_main_screen_frame_is_preserved() {
        let frame = DesktopBounds {
            x: 0.0,
            y: 0.0,
            width: 2560.0,
            height: 1440.0,
        };
        assert_eq!(validated_desktop_bounds(frame), Some(frame));
    }

    #[test]
    fn invalid_main_screen_frame_is_rejected() {
        assert_eq!(
            validated_desktop_bounds(DesktopBounds {
                x: 0.0,
                y: 0.0,
                width: f64::NAN,
                height: 100.0,
            }),
            None
        );
    }
}

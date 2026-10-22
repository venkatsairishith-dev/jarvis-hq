use super::CursorSample;

pub(crate) const INJECT_CLICK_THROUGH_BRIDGE: bool = true;

pub(crate) fn prepare_process_environment() {}

pub(crate) fn configure_app(_app: &mut tauri::App) {}

pub(crate) fn configure_main_window(
    _app: &tauri::App,
    window: &tauri::WebviewWindow,
) -> Result<(), String> {
    resize_to_virtual_desktop(window)?;
    window
        .set_always_on_top(true)
        .map_err(|error| format!("无法将 Windows 主窗口置顶: {error}"))
}

fn resize_to_virtual_desktop(window: &tauri::WebviewWindow) -> Result<(), String> {
    let monitors = window
        .available_monitors()
        .map_err(|error| error.to_string())?;
    let Some(first) = monitors.first() else {
        return Ok(());
    };

    let first_position = first.position();
    let first_size = first.size();
    let mut min_x = first_position.x;
    let mut min_y = first_position.y;
    let mut max_x = first_position.x + first_size.width as i32;
    let mut max_y = first_position.y + first_size.height as i32;

    for monitor in &monitors[1..] {
        let position = monitor.position();
        let size = monitor.size();
        min_x = min_x.min(position.x);
        min_y = min_y.min(position.y);
        max_x = max_x.max(position.x + size.width as i32);
        max_y = max_y.max(position.y + size.height as i32);
    }

    let _ = window.unmaximize();
    window
        .set_position(tauri::PhysicalPosition::new(min_x, min_y))
        .map_err(|error| error.to_string())?;
    window
        .set_size(tauri::PhysicalSize::new(
            (max_x - min_x) as u32,
            (max_y - min_y) as u32,
        ))
        .map_err(|error| error.to_string())
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
    window
        .set_ignore_cursor_events(ignore)
        .map_err(|error| error.to_string())
}

pub(crate) fn prepare_for_navigation(window: &tauri::WebviewWindow) {
    let _ = window.set_ignore_cursor_events(false);
}

pub(crate) fn diagnostic_query() -> Option<&'static str> {
    None
}

pub(crate) fn cursor_sample(window: &tauri::WebviewWindow) -> Option<CursorSample> {
    let cursor = window.cursor_position().ok()?;
    let origin = window.inner_position().ok()?;
    let scale = window.scale_factor().ok()?;

    Some(CursorSample {
        absolute: (cursor.x, cursor.y),
        relative: (
            (cursor.x - f64::from(origin.x)) / scale,
            (cursor.y - f64::from(origin.y)) / scale,
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

use std::io::Cursor;

use base64::engine::general_purpose::STANDARD;
use base64::Engine;
use image::codecs::jpeg::JpegEncoder;
use image::imageops::FilterType;
use image::DynamicImage;
use serde::Serialize;
use xcap::{Monitor, Window};

const DEFAULT_MAX_WIDTH: u32 = 1280;
const MIN_MAX_WIDTH: u32 = 320;
const MAX_MAX_WIDTH: u32 = 3840;
const DEFAULT_QUALITY: u8 = 80;
const MIN_QUALITY: u8 = 30;
const MAX_QUALITY: u8 = 95;

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct ScreenCaptureCapabilities {
    provider: &'static str,
    can_list_screens: bool,
    can_list_windows: bool,
    can_capture_frames: bool,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct ScreenCaptureSource {
    id: String,
    kind: &'static str,
    name: String,
    thumbnail: Option<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct ScreenCaptureFrame {
    success: bool,
    data_url: Option<String>,
    error: Option<String>,
    width: Option<u32>,
    height: Option<u32>,
}

impl ScreenCaptureFrame {
    fn success(data_url: String, width: u32, height: u32) -> Self {
        Self {
            success: true,
            data_url: Some(data_url),
            error: None,
            width: Some(width),
            height: Some(height),
        }
    }

    fn failure(error: impl Into<String>) -> Self {
        Self {
            success: false,
            data_url: None,
            error: Some(error.into()),
            width: None,
            height: None,
        }
    }
}

#[tauri::command]
pub(crate) fn get_screen_capture_capabilities() -> ScreenCaptureCapabilities {
    ScreenCaptureCapabilities {
        provider: "tauri-native",
        can_list_screens: true,
        can_list_windows: true,
        can_capture_frames: true,
    }
}

#[tauri::command]
pub(crate) async fn get_screen_capture_sources(
    types: Option<Vec<String>>,
) -> Result<Vec<ScreenCaptureSource>, String> {
    tauri::async_runtime::spawn_blocking(move || list_sources(types))
        .await
        .map_err(|error| format!("获取屏幕源任务失败: {error}"))?
}

#[tauri::command]
pub(crate) async fn capture_screen_source(
    source_id: String,
    max_width: Option<u32>,
    quality: Option<u8>,
) -> Result<ScreenCaptureFrame, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let max_width = max_width
            .unwrap_or(DEFAULT_MAX_WIDTH)
            .clamp(MIN_MAX_WIDTH, MAX_MAX_WIDTH);
        let quality = quality
            .unwrap_or(DEFAULT_QUALITY)
            .clamp(MIN_QUALITY, MAX_QUALITY);
        capture_source(&source_id, max_width, quality)
    })
    .await
    .map_err(|error| format!("屏幕捕获任务失败: {error}"))
}

fn list_sources(types: Option<Vec<String>>) -> Result<Vec<ScreenCaptureSource>, String> {
    let requested = types.unwrap_or_else(|| vec!["screen".to_string(), "window".to_string()]);
    let include_screens = requested.iter().any(|kind| kind == "screen");
    let include_windows = requested.iter().any(|kind| kind == "window");
    let mut sources = Vec::new();

    if include_screens {
        for monitor in Monitor::all().map_err(|error| error.to_string())? {
            sources.push(ScreenCaptureSource {
                id: format!("screen:{}", monitor.id()),
                kind: "screen",
                name: monitor.name().to_string(),
                thumbnail: None,
            });
        }
    }

    if include_windows {
        for window in Window::all().map_err(|error| error.to_string())? {
            if window.is_minimized() {
                continue;
            }
            let app_name = window.app_name().trim();
            let title = window.title().trim();
            let name = if title.is_empty() || title == app_name {
                app_name.to_string()
            } else if app_name.is_empty() {
                title.to_string()
            } else {
                format!("{app_name} — {title}")
            };
            if name.is_empty() {
                continue;
            }
            sources.push(ScreenCaptureSource {
                id: format!("window:{}", window.id()),
                kind: "window",
                name,
                thumbnail: None,
            });
        }
    }

    Ok(sources)
}

fn capture_source(source_id: &str, max_width: u32, quality: u8) -> ScreenCaptureFrame {
    let Some((kind, raw_id)) = source_id.split_once(':') else {
        return ScreenCaptureFrame::failure("Invalid source id");
    };
    let Ok(id) = raw_id.parse::<u32>() else {
        return ScreenCaptureFrame::failure("Invalid source id");
    };

    let captured = match kind {
        "screen" => Monitor::all()
            .map_err(|error| error.to_string())
            .and_then(|monitors| {
                monitors
                    .into_iter()
                    .find(|monitor| monitor.id() == id)
                    .ok_or_else(|| "Source not found".to_string())
            })
            .and_then(|monitor| monitor.capture_image().map_err(|error| error.to_string())),
        "window" => Window::all()
            .map_err(|error| error.to_string())
            .and_then(|windows| {
                windows
                    .into_iter()
                    .find(|window| window.id() == id && !window.is_minimized())
                    .ok_or_else(|| "Source not found".to_string())
            })
            .and_then(|window| window.capture_image().map_err(|error| error.to_string())),
        _ => return ScreenCaptureFrame::failure("Invalid source id"),
    };

    let image = match captured {
        Ok(image) => DynamicImage::ImageRgba8(image),
        Err(error) => return ScreenCaptureFrame::failure(error),
    };
    encode_frame(image, max_width, quality)
        .unwrap_or_else(|error| ScreenCaptureFrame::failure(error))
}

fn encode_frame(
    image: DynamicImage,
    max_width: u32,
    quality: u8,
) -> Result<ScreenCaptureFrame, String> {
    let image = if image.width() > max_width {
        let height = ((u64::from(image.height()) * u64::from(max_width)) / u64::from(image.width()))
            .max(1) as u32;
        image.resize_exact(max_width, height, FilterType::Triangle)
    } else {
        image
    };
    let width = image.width();
    let height = image.height();
    let mut bytes = Cursor::new(Vec::new());
    JpegEncoder::new_with_quality(&mut bytes, quality)
        .encode_image(&image)
        .map_err(|error| format!("JPEG 编码失败: {error}"))?;
    let data_url = format!(
        "data:image/jpeg;base64,{}",
        STANDARD.encode(bytes.into_inner())
    );
    Ok(ScreenCaptureFrame::success(data_url, width, height))
}

#[cfg(test)]
mod tests {
    use super::{encode_frame, ScreenCaptureFrame};
    use image::{DynamicImage, RgbaImage};

    #[test]
    fn frame_encoding_respects_max_width() {
        let image = DynamicImage::ImageRgba8(RgbaImage::new(1920, 1080));
        let frame = encode_frame(image, 1280, 80).unwrap();
        assert!(frame.success);
        assert_eq!(frame.width, Some(1280));
        assert_eq!(frame.height, Some(720));
        assert!(frame
            .data_url
            .as_deref()
            .is_some_and(|value| value.starts_with("data:image/jpeg;base64,")));
    }

    #[test]
    fn capture_failure_has_no_image_payload() {
        let frame = ScreenCaptureFrame::failure("Source not found");
        assert!(!frame.success);
        assert_eq!(frame.error.as_deref(), Some("Source not found"));
        assert!(frame.data_url.is_none());
    }
}

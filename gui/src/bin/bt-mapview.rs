use std::io::BufRead;

use tao::event::{Event, WindowEvent};
use tao::event_loop::{ControlFlow, EventLoopBuilder};
use tao::window::WindowBuilder;
use wry::WebViewBuilder;

const MAP_HTML: &str = include_str!("../../ui/mapview.html");

enum UserEvent {
    SetData(String),
    StdinClosed,
}

fn main() -> wry::Result<()> {
    let event_loop = EventLoopBuilder::<UserEvent>::with_user_event().build();
    let proxy = event_loop.create_proxy();

    std::thread::spawn(move || {
        let stdin = std::io::stdin();
        for line in stdin.lock().lines() {
            match line {
                Ok(text) if !text.trim().is_empty() => {
                    if proxy.send_event(UserEvent::SetData(text)).is_err() {
                        break;
                    }
                }
                Ok(_) => continue,
                Err(_) => break,
            }
        }
        let _ = proxy.send_event(UserEvent::StdinClosed);
    });

    let window = WindowBuilder::new()
        .with_title("project-blacktape — GPS map")
        .with_inner_size(tao::dpi::LogicalSize::new(900.0, 700.0))
        .build(&event_loop)
        .expect("failed to create map window");

    let builder = WebViewBuilder::new()
        .with_initialization_script(
            "window.__btPending = []; window.setData = function(d) { window.__btPending.push(d); };",
        )
        .with_html(MAP_HTML);

    #[cfg(any(target_os = "windows", target_os = "macos"))]
    let webview = builder.build(&window)?;
    #[cfg(not(any(target_os = "windows", target_os = "macos")))]
    let webview = {
        use tao::platform::unix::WindowExtUnix;
        use wry::WebViewBuilderExtUnix;
        let vbox = window.default_vbox().expect("gtk vbox");
        builder.build_gtk(vbox)?
    };

    event_loop.run(move |event, _, control_flow| {
        *control_flow = ControlFlow::Wait;

        match event {
            Event::WindowEvent {
                event: WindowEvent::CloseRequested,
                ..
            } => {
                *control_flow = ControlFlow::Exit;
            }
            Event::UserEvent(UserEvent::SetData(data_json)) => {
                let _ = webview.evaluate_script(&format!("window.setData({data_json});"));
            }
            Event::UserEvent(UserEvent::StdinClosed) => {
                *control_flow = ControlFlow::Exit;
            }
            _ => {}
        }
    });
}

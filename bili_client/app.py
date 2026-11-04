from __future__ import annotations


def main(started_at: float | None = None) -> None:
    from bili_client.desktop.runtime import DesktopRuntime
    from bili_client.desktop.window import run_desktop
    from bili_client.services.single_instance import SingleInstance

    instance = SingleInstance()
    if not instance.acquire():
        if instance.notify_existing():
            return
        instance = None
    runtime = DesktopRuntime()
    try:
        run_desktop(runtime, instance)
    finally:
        if instance is not None:
            instance.close()
        runtime.close()

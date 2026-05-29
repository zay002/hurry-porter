from hurry_porter.windows_setup import USBIPD_WINGET_ID, usbipd_install_command
from hurry_porter.system import windows_path_to_wsl


def test_usbipd_install_command_uses_official_winget_id():
    command = usbipd_install_command()

    assert "winget install" in command
    assert "--interactive" in command
    assert "--exact" in command
    assert USBIPD_WINGET_ID in command


def test_windows_path_to_wsl_converts_program_files_path():
    assert (
        windows_path_to_wsl(r"C:\Program Files\usbipd-win\usbipd.exe")
        == "/mnt/c/Program Files/usbipd-win/usbipd.exe"
    )

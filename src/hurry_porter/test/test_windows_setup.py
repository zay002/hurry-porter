from hurry_porter.windows_setup import USBIPD_WINGET_ID, usbipd_install_command


def test_usbipd_install_command_uses_official_winget_id():
    command = usbipd_install_command()

    assert "winget install" in command
    assert "--interactive" in command
    assert "--exact" in command
    assert USBIPD_WINGET_ID in command

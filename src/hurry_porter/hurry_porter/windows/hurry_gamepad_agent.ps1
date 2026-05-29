param(
    [string]$Target = "127.0.0.1",
    [int]$Port = 47777,
    [int]$Hz = 250,
    [int]$Index = 0
)

$ErrorActionPreference = "Stop"

Add-Type -AssemblyName System.Runtime.WindowsRuntime
[void][Windows.Gaming.Input.Gamepad,Windows.Gaming.Input,ContentType=WindowsRuntime]
[void][Windows.Gaming.Input.GamepadButtons,Windows.Gaming.Input,ContentType=WindowsRuntime]

$client = [System.Net.Sockets.UdpClient]::new()
$endpoint = [System.Net.IPEndPoint]::new([System.Net.IPAddress]::Parse($Target), $Port)
$periodMs = [Math]::Max(1, [int](1000 / [Math]::Max(1, $Hz)))

function Test-Button {
    param(
        [object]$Buttons,
        [string]$Name
    )
    $flag = [Windows.Gaming.Input.GamepadButtons]::$Name
    return (($Buttons -band $flag) -ne 0)
}

function Button-Int {
    param([bool]$Pressed)
    if ($Pressed) { return 1 }
    return 0
}

Write-Host "hurry gamepad agent: sending Windows.Gaming.Input packets to udp://$Target`:$Port at ${Hz}Hz"

while ($true) {
    $pads = [Windows.Gaming.Input.Gamepad]::Gamepads
    if ($pads.Count -le $Index) {
        Start-Sleep -Milliseconds 250
        continue
    }

    $reading = $pads[$Index].GetCurrentReading()
    $buttons = $reading.Buttons

    $dpadX = 0
    if (Test-Button $buttons "DPadLeft") { $dpadX -= 1 }
    if (Test-Button $buttons "DPadRight") { $dpadX += 1 }

    $dpadY = 0
    if (Test-Button $buttons "DPadDown") { $dpadY -= 1 }
    if (Test-Button $buttons "DPadUp") { $dpadY += 1 }

    $packet = [ordered]@{
        schema = "hurry.gamepad.v1"
        source = "windows-gaming-input"
        connected = $true
        index = $Index
        timestamp_ms = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
        axes = @(
            [double]$reading.LeftThumbstickX,
            [double]$reading.LeftThumbstickY,
            [double]$reading.RightThumbstickX,
            [double]$reading.RightThumbstickY,
            [double]$reading.LeftTrigger,
            [double]$reading.RightTrigger,
            [double]$dpadX,
            [double]$dpadY
        )
        buttons = @(
            (Button-Int (Test-Button $buttons "A")),
            (Button-Int (Test-Button $buttons "B")),
            (Button-Int (Test-Button $buttons "X")),
            (Button-Int (Test-Button $buttons "Y")),
            (Button-Int (Test-Button $buttons "LeftShoulder")),
            (Button-Int (Test-Button $buttons "RightShoulder")),
            (Button-Int (Test-Button $buttons "View")),
            (Button-Int (Test-Button $buttons "Menu")),
            (Button-Int (Test-Button $buttons "LeftThumbstick")),
            (Button-Int (Test-Button $buttons "RightThumbstick")),
            (Button-Int (Test-Button $buttons "DPadUp")),
            (Button-Int (Test-Button $buttons "DPadDown")),
            (Button-Int (Test-Button $buttons "DPadLeft")),
            (Button-Int (Test-Button $buttons "DPadRight"))
        )
    }

    $json = ($packet | ConvertTo-Json -Compress)
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
    [void]$client.Send($bytes, $bytes.Length, $endpoint)
    Start-Sleep -Milliseconds $periodMs
}

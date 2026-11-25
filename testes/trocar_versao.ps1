# Abre a versao nova ao lado da antiga (como o ajudante da Sala 3D abre, sem janela) e espera ela assumir na
# ponte. A antiga termina a fala e sai sozinha; a pagina da Sala nao cai. Ver nucleo/ponte.py.
# Mede se a janela da frente mudou (sem foco, a Sala para de receber o controle) e o Bluetooth.
param([switch]$SoForaDoJogo)
$d = Split-Path $PSScriptRoot -Parent
Add-Type @'
using System; using System.Text; using System.Runtime.InteropServices;
public class Frente {
  [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint p);
  public static uint Pid() { uint p; GetWindowThreadProcessId(GetForegroundWindow(), out p); return p; }
}
'@
function Nome-Frente { $p = [Frente]::Pid(); try { (Get-Process -Id $p -ErrorAction Stop).ProcessName + " ($p)" } catch { "? ($p)" } }
$ini = Get-Date
$f0 = Nome-Frente
"na frente: $f0"
if ($SoForaDoJogo -and $f0 -match '^chrome') { "o jogo esta na frente: nao troquei"; exit 0 }
$j = Get-Content "$env:LOCALAPPDATA\Sala3d\voz-universal.json" -Raw | ConvertFrom-Json
$u = "http://127.0.0.1:$($j.porta)/estado?t=$($j.token)"
$antes = Invoke-RestMethod $u
Start-Process -FilePath 'C:\Users\Leandro\zelda-voz\venv\Scripts\pythonw.exe' -ArgumentList 'app.py', '--minimizada', '--troca' -WorkingDirectory $d -WindowStyle Minimized
$caiu = $false
$ok = $false
for ($i = 0; $i -lt 1600; $i++) {
    Start-Sleep -Milliseconds 100
    $f = Nome-Frente
    if ($f -ne $f0) { "frente mudou aos $([math]::Round($i/10,1)) s: $f"; $f0 = $f }
    if ($i % 10 -eq 0) {
        $e = Invoke-RestMethod $u
        if ($antes.pagina -and -not $e.pagina) { $caiu = $true }
        if ($e.ativo -and $e.ativo -ne $antes.ativo) { $ok = $true; break }
    }
}
"assumiu: $ok  segundos: $([math]::Round($i/10))  pagina caiu: $caiu"
for ($k = 0; $k -lt 80; $k++) { Start-Sleep -Milliseconds 100; $f = Nome-Frente; if ($f -ne $f0) { "frente mudou depois: $f"; $f0 = $f } }
(Invoke-RestMethod $u) | ConvertTo-Json -Compress
$bt = @(Get-WinEvent -FilterHashtable @{LogName = 'System'; ProviderName = 'HidBth'; StartTime = $ini } -ErrorAction SilentlyContinue)
"quedas de Bluetooth durante a troca: $($bt.Count)"
Get-Content "$d\log.txt" -Tail 4 -Encoding UTF8

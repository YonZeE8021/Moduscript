# MCmodAgent Windows environment setup (from scratch)
# Usage: .\scripts\setup.ps1 [-AdminEmail your@email.com] [-InstallPython] [-InstallJava] [-InstallGit] [-Force]

[CmdletBinding()]
param(
    [switch]$InstallPython,
    [switch]$InstallJava,
    [switch]$InstallGit,
    [string]$AdminEmail = "",
    [switch]$SkipPlaywright,
    [switch]$SkipVerify,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$RootDir = Split-Path $PSScriptRoot -Parent
$VenvDir = Join-Path $RootDir ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$Requirements = Join-Path $RootDir "server\requirements.txt"
$DevRequirements = Join-Path $RootDir "server\requirements-dev.txt"
$EnvExample = Join-Path $RootDir ".env.example"
$EnvFile = Join-Path $RootDir ".env"
$Warnings = [System.Collections.Generic.List[string]]::new()

function Write-Step([string]$Message) {
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Write-Ok([string]$Message) {
    Write-Host "  OK: $Message" -ForegroundColor Green
}

function Write-WarnMsg([string]$Message) {
    Write-Host "  WARN: $Message" -ForegroundColor Yellow
    $script:Warnings.Add($Message)
}

function Write-Fail([string]$Message) {
    Write-Host "  FAIL: $Message" -ForegroundColor Red
}

function Test-ExecutionPolicyAllowed {
    $policy = Get-ExecutionPolicy -Scope CurrentUser
    if ($policy -in @("Restricted", "AllSigned")) {
        Write-WarnMsg "当前用户 ExecutionPolicy 为 $policy，可能无法直接运行 .ps1 脚本。"
        Write-Host "  建议执行: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned" -ForegroundColor Gray
    }
}

function Get-PythonVersionFromOutput([string]$Output) {
    if ($Output -match "Python\s+(\d+)\.(\d+)\.(\d+)") {
        return [PSCustomObject]@{
            Major = [int]$Matches[1]
            Minor = [int]$Matches[2]
            Patch = [int]$Matches[3]
            Text  = "$($Matches[1]).$($Matches[2]).$($Matches[3])"
        }
    }
    return $null
}

function Find-PythonExecutable {
    $candidates = @(
        @{ Exe = "py"; Args = @("-3.12", "-V") },
        @{ Exe = "py"; Args = @("-3.11", "-V") },
        @{ Exe = "py"; Args = @("-3", "-V") },
        @{ Exe = "python"; Args = @("-V") }
    )

    foreach ($item in $candidates) {
        try {
            $output = & $item.Exe @($item.Args) 2>&1 | Out-String
            $ver = Get-PythonVersionFromOutput $output
            if ($null -eq $ver) { continue }
            if ($ver.Major -lt 3 -or ($ver.Major -eq 3 -and $ver.Minor -lt 11)) { continue }

            if ($item.Exe -eq "py") {
                $launchArgs = @($item.Args[0], "-c", "import sys; print(sys.executable)")
                $resolved = (& py @launchArgs 2>&1 | Select-Object -Last 1).ToString().Trim()
                if ($resolved -and (Test-Path $resolved)) {
                    return @{ Exe = $resolved; Version = $ver }
                }
            } else {
                $resolved = (Get-Command python -ErrorAction Stop).Source
                return @{ Exe = $resolved; Version = $ver }
            }
        } catch {
            continue
        }
    }
    return $null
}

function Install-PythonViaWinget {
    Write-Step "通过 winget 安装 Python 3.12"
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Fail "未找到 winget。请从 https://www.python.org/downloads/ 手动安装 Python 3.11+"
        exit 1
    }
    & winget install --id Python.Python.3.12 -e --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "winget 安装 Python 失败（退出码 $LASTEXITCODE）"
        exit 1
    }
    Write-Ok "Python 安装完成，请重新打开终端后再次运行 setup.ps1"
    exit 0
}

function Get-JavaMajorVersion {
    if (-not (Get-Command java -ErrorAction SilentlyContinue)) {
        return $null
    }
    try {
        $output = (& java -version 2>&1 | Out-String)
        if ($output -match 'version "(\d+)') {
            $major = [int]$Matches[1]
            if ($major -eq 1 -and $output -match 'version "1\.(\d+)') {
                $major = [int]$Matches[1]
            }
            return $major
        }
    } catch {
        return $null
    }
    return $null
}

function Install-JavaViaWinget {
    Write-Step "通过 winget 安装 Microsoft OpenJDK 17"
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-WarnMsg "未找到 winget，请手动安装 JDK 17+ 并设置 JAVA_HOME"
        return
    }
    & winget install --id Microsoft.OpenJDK.17 -e --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) {
        Write-WarnMsg "winget 安装 Java 失败（退出码 $LASTEXITCODE）。Gradle 编译将不可用。"
        return
    }
    Write-Ok "OpenJDK 17 安装完成。若 java 仍不可用，请重启终端或设置 JAVA_HOME。"
}

function Install-GitViaWinget {
    Write-Step "通过 winget 安装 Git"
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-WarnMsg "未找到 winget，请从 https://git-scm.com/download/win 手动安装 Git"
        return
    }
    & winget install --id Git.Git -e --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) {
        Write-WarnMsg "winget 安装 Git 失败（退出码 $LASTEXITCODE）。会话分支与工作区回滚将不可用。"
        return
    }
    Write-Ok "Git 安装完成。若 git 仍不可用，请重启终端。"
}

function Initialize-EnvFile {
    Write-Step "初始化 .env"
    $created = $false

    if (-not (Test-Path $EnvFile)) {
        if (-not (Test-Path $EnvExample)) {
            Write-Fail "未找到 .env.example: $EnvExample"
            exit 1
        }
        Copy-Item $EnvExample $EnvFile
        $created = $true
        Write-Ok "已从 .env.example 创建 .env"
    } else {
        Write-Ok ".env 已存在，保留现有配置"
    }

    $content = Get-Content $EnvFile -Raw
    $jwtBytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($jwtBytes)
    $jwtSecret = [Convert]::ToBase64String($jwtBytes)

    if ($created -or $content -match 'JWT_SECRET=change-this-to-a-long-random-string') {
        $content = $content -replace 'JWT_SECRET=change-this-to-a-long-random-string', "JWT_SECRET=$jwtSecret"
        Write-Ok "已生成随机 JWT_SECRET"
    } elseif ($content -match 'JWT_SECRET=dev-change-me-in-production') {
        Write-WarnMsg "JWT_SECRET 仍为开发默认值，生产环境请修改 .env"
    }

    if ($AdminEmail) {
        if ($content -match '(?m)^MCMOD_BOOTSTRAP_ADMIN_EMAIL=.*$') {
            $content = $content -replace '(?m)^MCMOD_BOOTSTRAP_ADMIN_EMAIL=.*$', "MCMOD_BOOTSTRAP_ADMIN_EMAIL=$AdminEmail"
        } else {
            $content += "`nMCMOD_BOOTSTRAP_ADMIN_EMAIL=$AdminEmail`n"
        }
        Write-Ok "已设置 MCMOD_BOOTSTRAP_ADMIN_EMAIL=$AdminEmail"
    } elseif ($content -match '(?m)^MCMOD_BOOTSTRAP_ADMIN_EMAIL=admin@example\.com') {
        Write-WarnMsg "MCMOD_BOOTSTRAP_ADMIN_EMAIL 仍为示例值，请编辑 .env 或使用 -AdminEmail 参数"
    }

    Set-Content -Path $EnvFile -Value $content -NoNewline -Encoding utf8
}

function Initialize-DeployFiles {
    Write-Step "初始化 deploy 配置"
    $deployDir = Join-Path $RootDir "deploy"
    $pskExample = Join-Path $deployDir "keys\psk.hex.example"
    $pskFile = Join-Path $deployDir "keys\psk.hex"
    $senderExample = Join-Path $deployDir "config\sender.example.json"
    $senderFile = Join-Path $deployDir "config\sender.json"

    if (-not (Test-Path $pskFile)) {
        if (Test-Path $pskExample) {
            $hex = python -c "import secrets; print(secrets.token_hex(32))" 2>$null
            if ($hex) {
                Set-Content -Path $pskFile -Value $hex.Trim() -NoNewline -Encoding ascii
                Write-Ok "已生成 deploy/keys/psk.hex"
            } else {
                Copy-Item $pskExample $pskFile
                Write-WarnMsg "已复制 psk.hex.example；请手动生成并写入 deploy/keys/psk.hex"
            }
        }
    } else {
        Write-Ok "deploy/keys/psk.hex 已存在"
    }

    if (-not (Test-Path $senderFile)) {
        if (Test-Path $senderExample) {
            Copy-Item $senderExample $senderFile
            Write-Ok "已从 sender.example.json 创建 sender.json"
        }
    } else {
        Write-Ok "deploy/config/sender.json 已存在"
    }
}

function Invoke-EnvironmentVerify {
    Write-Step "校验 Python 包与 Agent 环境"

    $verifyImports = Join-Path $RootDir "scripts\verify_imports.py"
    $verifyCli = Join-Path $RootDir "scripts\verify_cli.py"

    Push-Location (Join-Path $RootDir "server")
    try {
        $importOut = & $VenvPython $verifyImports 2>&1 | Out-String
        if ($importOut -notmatch 'IMPORT_OK') {
            Write-Fail "Python 包导入失败: $($importOut.Trim())"
            exit 1
        }
        Write-Ok "关键 Python 包导入正常"

        $cliOut = (& $VenvPython $verifyCli 2>&1 | Out-String).Trim()
        if ($cliOut -match 'Traceback') {
            Write-WarnMsg "Claude CLI 校验脚本异常: $($cliOut -replace '\s+', ' ')"
        } else {
            foreach ($line in ($cliOut -split "`n")) {
                $line = $line.Trim()
                if ($line -match '^CLI_OK:(.+):(.+)$') {
                    Write-Ok "Claude CLI: $($Matches[1]) ($($Matches[2]))"
                }
                elseif ($line -match '^CLI_FAIL:(.+)$') {
                    Write-WarnMsg "Claude CLI 不可用: $($Matches[1])"
                }
                elseif ($line -match '^ASYNC_OK') {
                    Write-Ok "Windows asyncio 子进程校验通过"
                }
                elseif ($line -match '^ASYNC_WARN:(.+)$') {
                    Write-WarnMsg "asyncio 子进程: $($Matches[1])"
                }
            }
        }
    }
    finally {
        Pop-Location
    }

    Write-Step "运行 smoke test (pytest)"
    Push-Location (Join-Path $RootDir "server")
    try {
        & $VenvPython -m pytest test_auth.py test_http_utils.py -q --tb=no
        if ($LASTEXITCODE -ne 0) {
            Write-WarnMsg "部分 pytest 未通过（退出码 $LASTEXITCODE），不影响基础启动，请查看输出。"
        } else {
            Write-Ok "smoke test 通过"
        }
    } catch {
        Write-WarnMsg "pytest 执行失败: $($_.Exception.Message)"
    } finally {
        Pop-Location
    }
}

# --- Main ---

Write-Host "MCmodAgent Windows 环境搭建" -ForegroundColor White
Write-Host "项目目录: $RootDir" -ForegroundColor Gray

Test-ExecutionPolicyAllowed

Write-Step "检测 Python 3.11+"
$pythonInfo = Find-PythonExecutable
if (-not $pythonInfo) {
    if ($InstallPython) {
        Install-PythonViaWinget
    }
    Write-Fail "未找到 Python 3.11+。"
    Write-Host "  安装方式 1: .\scripts\setup.ps1 -InstallPython" -ForegroundColor Gray
    Write-Host "  安装方式 2: winget install Python.Python.3.12" -ForegroundColor Gray
    exit 1
}
$PythonExe = $pythonInfo.Exe
Write-Ok "Python $($pythonInfo.Version.Text) -> $PythonExe"

Write-Step "检测 Java 17+ (Gradle 编译需要)"
$javaMajor = Get-JavaMajorVersion
if ($null -eq $javaMajor) {
    if ($InstallJava) {
        Install-JavaViaWinget
        $javaMajor = Get-JavaMajorVersion
    }
    if ($null -eq $javaMajor) {
        Write-WarnMsg "未检测到 Java。服务端可启动，但 gradlew build 将失败。"
        Write-Host "  安装: .\scripts\setup.ps1 -InstallJava" -ForegroundColor Gray
        Write-Host "  或: winget install Microsoft.OpenJDK.17" -ForegroundColor Gray
        Write-Host "  安装后设置 JAVA_HOME 并确保 java 在 PATH 中。" -ForegroundColor Gray
    }
} elseif ($javaMajor -lt 17) {
    Write-WarnMsg "检测到 Java $javaMajor，建议升级到 JDK 17+（Fabric 1.20.1 / Gradle）。"
} else {
    Write-Ok "Java $javaMajor 可用"
}

Write-Step "检测 Git (会话分支与工作区回滚需要)"
$gitCmd = Get-Command git -ErrorAction SilentlyContinue
if (-not $gitCmd) {
    if ($InstallGit) {
        Install-GitViaWinget
        $gitCmd = Get-Command git -ErrorAction SilentlyContinue
    }
    if (-not $gitCmd) {
        Write-WarnMsg "未检测到 Git。服务端可启动，但重新生成/分支切换/工作区回滚将不可用。"
        Write-Host "  安装: .\scripts\setup.ps1 -InstallGit" -ForegroundColor Gray
        Write-Host "  或: winget install Git.Git" -ForegroundColor Gray
    }
} else {
    $gitVer = (& git --version 2>&1) -join " "
    Write-Ok "Git 可用 ($gitVer)"
}

Write-Step "创建 Python 虚拟环境"
if ($Force -and (Test-Path $VenvDir)) {
    Remove-Item $VenvDir -Recurse -Force
    Write-Ok "已删除旧 .venv (-Force)"
}
if (-not (Test-Path $VenvPython)) {
    & $PythonExe -m venv $VenvDir
    Write-Ok "已创建 $VenvDir"
} else {
    Write-Ok ".venv 已存在，跳过创建（使用 -Force 可重建）"
}
& $VenvPython -m pip install --upgrade pip | Out-Null
Write-Ok "pip 已升级"

Write-Step "安装 Python 依赖"
if (-not (Test-Path $Requirements)) {
    Write-Fail "未找到 requirements.txt: $Requirements"
    exit 1
}
& $VenvPython -m pip install -r $Requirements
if ($LASTEXITCODE -ne 0) {
    Write-Fail "pip install 失败（退出码 $LASTEXITCODE）"
    exit 1
}
if (Test-Path $DevRequirements) {
    & $VenvPython -m pip install -r $DevRequirements
    if ($LASTEXITCODE -ne 0) {
        Write-WarnMsg "开发依赖安装失败（退出码 $LASTEXITCODE）；pytest 可能不可用"
    }
}
Write-Ok "依赖安装完成"

Write-Step "预拉取规划反编译工具 (Vineflower / tiny-remapper)"
try {
    Push-Location (Join-Path $RootDir "server")
    & $VenvPython -m plan.decompile_tools --ensure
    if ($LASTEXITCODE -ne 0) {
        Write-WarnMsg "反编译工具预拉取未完成（规划闭源参考仍可首次使用时自动下载）"
    } else {
        Write-Ok "反编译工具已就绪"
    }
} catch {
    Write-WarnMsg "反编译工具预拉取跳过: $($_.Exception.Message)"
} finally {
    Pop-Location
}

if (-not $SkipPlaywright) {
    Write-Step "安装 Playwright Chromium (Fabric 模板 bootstrap)"
    try {
        & $VenvPython -m playwright install chromium
        if ($LASTEXITCODE -ne 0) {
            throw "playwright install 退出码 $LASTEXITCODE"
        }
        Write-Ok "Chromium 已安装"
    } catch {
        Write-Fail "Playwright 安装失败: $($_.Exception.Message)"
        Write-Host "  请检查网络/代理，或参考 docs/DEPLOYMENT.md 中 bootstrap 故障排查。" -ForegroundColor Gray
        exit 1
    }
} else {
    Write-WarnMsg "已跳过 Playwright（-SkipPlaywright）。Fabric 模板下载将不可用。"
}

Initialize-EnvFile
Initialize-DeployFiles

if (-not $SkipVerify) {
    Invoke-EnvironmentVerify
} else {
    Write-WarnMsg "已跳过环境校验（-SkipVerify）"
}

Write-Host "`n========================================" -ForegroundColor Green
Write-Host " 环境就绪" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host "  虚拟环境: .venv"
Write-Host "  启动命令: .\scripts\run.ps1"
Write-Host "  访问地址: http://127.0.0.1:8000/"
Write-Host ""
Write-Host "  下一步:"
Write-Host "    1. 确认 .env 中 MCMOD_BOOTSTRAP_ADMIN_EMAIL 为你的邮箱"
Write-Host "    2. 注册 -> 登录 -> /admin.html 配置 LLM API"
Write-Host "    3. 首页「开始编写」创建会话"

if ($Warnings.Count -gt 0) {
    Write-Host "`n  警告汇总:" -ForegroundColor Yellow
    foreach ($w in $Warnings) {
        Write-Host "    - $w" -ForegroundColor Yellow
    }
}

Write-Host ""

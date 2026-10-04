param(
    [string]$SdkRoot = 'D:\AndroidTools\sdk',
    [string]$JdkRoot = 'D:\AndroidTools\jdk\jdk-21.0.12.1+1'
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$buildRoot = Join-Path $projectRoot 'build'
$classes = Join-Path $buildRoot 'classes'
$dex = Join-Path $buildRoot 'dex'
$tools = Join-Path $SdkRoot 'build-tools\37.0.0'
$platformJar = Join-Path $SdkRoot 'platforms\android-37.0\android.jar'
$unsigned = Join-Path $buildRoot 'unsigned.apk'
$aligned = Join-Path $buildRoot 'aligned.apk'
$compiledRes = Join-Path $buildRoot 'compiled-res.zip'
$output = Join-Path $buildRoot 'misty-quick-register-debug.apk'
$keystore = 'D:\AndroidTools\signing\misty-account-helper-debug.jks'

foreach ($needed in @((Join-Path $JdkRoot 'bin\javac.exe'), (Join-Path $JdkRoot 'bin\jar.exe'),
    (Join-Path $JdkRoot 'bin\keytool.exe'), (Join-Path $tools 'aapt2.exe'),
    (Join-Path $tools 'd8.bat'), (Join-Path $tools 'zipalign.exe'),
    (Join-Path $tools 'apksigner.bat'), $platformJar)) {
    if (-not (Test-Path -LiteralPath $needed)) { throw "缺少构建工具：$needed" }
}

$safeRoot = [System.IO.Path]::GetFullPath($projectRoot).TrimEnd('\') + '\'
foreach ($target in @($classes, $dex)) {
    $resolved = [System.IO.Path]::GetFullPath($target)
    if (-not $resolved.StartsWith($safeRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝清理工作区之外的目录：$resolved"
    }
    if (Test-Path -LiteralPath $resolved) { Remove-Item -LiteralPath $resolved -Recurse -Force }
}
New-Item -ItemType Directory -Force -Path $classes, $dex, (Split-Path -Parent $keystore) | Out-Null
$sources = @(Get-ChildItem -LiteralPath (Join-Path $projectRoot 'src') -Recurse -Filter '*.java' -File | ForEach-Object FullName)
if ($sources.Count -eq 0) { throw '没有找到 Java 源码' }

& (Join-Path $JdkRoot 'bin\javac.exe') -encoding UTF-8 --release 8 -cp $platformJar -d $classes @sources
if ($LASTEXITCODE -ne 0) { throw 'Java 编译失败' }

if (Test-Path -LiteralPath $compiledRes) { Remove-Item -LiteralPath $compiledRes -Force }
& (Join-Path $tools 'aapt2.exe') compile --dir (Join-Path $projectRoot 'res') -o $compiledRes
if ($LASTEXITCODE -ne 0) { throw 'APK 资源编译失败' }

& (Join-Path $tools 'aapt2.exe') link -o $unsigned --manifest (Join-Path $projectRoot 'AndroidManifest.xml') -I $platformJar --min-sdk-version 26 --target-sdk-version 34 --version-code 4 --version-name 0.4.0 $compiledRes
if ($LASTEXITCODE -ne 0) { throw 'APK 资源打包失败' }

$classFiles = @(Get-ChildItem -LiteralPath $classes -Recurse -Filter '*.class' -File | ForEach-Object FullName)
$env:JAVA_HOME = $JdkRoot
& (Join-Path $tools 'd8.bat') --min-api 26 --lib $platformJar --output $dex @classFiles
if ($LASTEXITCODE -ne 0) { throw 'DEX 编译失败' }

& (Join-Path $JdkRoot 'bin\jar.exe') uf $unsigned -C $dex classes.dex
if ($LASTEXITCODE -ne 0) { throw 'DEX 写入 APK 失败' }
& (Join-Path $tools 'zipalign.exe') -f 4 $unsigned $aligned
if ($LASTEXITCODE -ne 0) { throw 'APK 对齐失败' }

if (-not (Test-Path -LiteralPath $keystore)) {
    & (Join-Path $JdkRoot 'bin\keytool.exe') -genkeypair -keystore $keystore -alias misty-helper -keyalg RSA -keysize 3072 -validity 10000 -storepass android -keypass android -dname 'CN=Misty Account Helper'
    if ($LASTEXITCODE -ne 0) { throw '生成本机测试签名失败' }
}
& (Join-Path $tools 'apksigner.bat') sign --ks $keystore --ks-key-alias misty-helper --ks-pass pass:android --key-pass pass:android --out $output $aligned
if ($LASTEXITCODE -ne 0) { throw 'APK 签名失败' }
& (Join-Path $tools 'apksigner.bat') verify --verbose $output
if ($LASTEXITCODE -ne 0) { throw 'APK 签名校验失败' }

Write-Output "完成：$output"

param([Parameter(Mandatory=$true)][ValidateSet('wordcraft','gridcraft','deckcraft','pdfcraft')][string]$App)
$ErrorActionPreference='Stop'
$repository=Split-Path $PSScriptRoot -Parent
$buildRoot=Join-Path $repository '.build'
python (Join-Path $PSScriptRoot 'office.py') prepare $App
if ($LASTEXITCODE -ne 0) { throw 'Source preparation or patch compatibility check failed' }
$config=Get-Content -LiteralPath (Join-Path $buildRoot ($App+'-config.json')) -Raw | ConvertFrom-Json
$candidates=@('C:\mingw64\bin','C:\msys64\mingw64\bin','C:\Apps\mingw64\bin')
$foundGcc=Get-Command gcc.exe -ErrorAction SilentlyContinue
if ($foundGcc) { $candidates=@((Split-Path $foundGcc.Source -Parent))+$candidates }
$mingwBin=$null
foreach ($candidate in ($candidates | Select-Object -Unique)) {
    if (-not (Test-Path -LiteralPath (Join-Path $candidate 'gcc.exe'))) { continue }
    $machine=& (Join-Path $candidate 'gcc.exe') -dumpmachine
    $requiredTools=@('g++.exe','objdump.exe','strip.exe','ar.exe','windres.exe')
    $missingTools=@($requiredTools | Where-Object { -not (Test-Path -LiteralPath (Join-Path $candidate $_)) })
    if ($machine -and $machine.Trim() -eq 'x86_64-w64-mingw32' -and $missingTools.Count -eq 0) { $mingwBin=$candidate;break }
}
if (-not $mingwBin) { throw 'The runner has no native x64 MinGW toolchain; no unverified compiler will be installed' }
$toolchain='1.100.0-x86_64-pc-windows-gnu'
rustup toolchain install $toolchain --profile minimal
if ($LASTEXITCODE -ne 0) { throw 'Rust toolchain setup failed' }
$rustc=(& rustup which --toolchain $toolchain rustc).Trim()
$cargo=Join-Path (Split-Path $rustc -Parent) 'cargo.exe'
$env:RUSTC=$rustc
$env:Path=(Split-Path $rustc -Parent)+';'+$mingwBin+';'+$env:Path
$env:CARGO_HOME=Join-Path $buildRoot 'cargo'
$env:CARGO_TARGET_DIR=Join-Path $buildRoot 'target'
$env:CARGO_BUILD_JOBS='4'
$env:CARGO_INCREMENTAL='0'
$env:CARGO_HTTP_MULTIPLEXING='false'
$env:CARGO_HTTP_TIMEOUT='60'
$env:RUSTFLAGS='--remap-path-prefix='+$repository+'=/build/office-portable'
$env:TEMP=Join-Path $buildRoot 'temp'
$env:TMP=$env:TEMP
$env:OFFICE_MINGW_BIN=$mingwBin
$env:CARGO_TARGET_X86_64_PC_WINDOWS_GNU_LINKER=Join-Path $mingwBin 'gcc.exe'
$env:CC=$env:CARGO_TARGET_X86_64_PC_WINDOWS_GNU_LINKER
$env:CXX=Join-Path $mingwBin 'g++.exe'
$env:AR=Join-Path $mingwBin 'ar.exe'
$env:CRAFT_FONTS_REQUIRED='1'
$env:CRAFT_FONTS_DIR=$config.fonts
if ($config.i18n) { $env:CRAFT_I18N_DIR=$config.i18n } else { Remove-Item Env:\CRAFT_I18N_DIR -ErrorAction SilentlyContinue }
Set-Item -Path ('Env:\'+$App.ToUpperInvariant()+'_REQUIRE_WINRES') -Value '1'
New-Item -ItemType Directory -Force -Path $env:TEMP,(Join-Path $buildRoot 'tests') | Out-Null
$manifest=Join-Path $config.source 'Cargo.toml'
# For the pinned base release, preserve the locally tested lockfile. On later upstream releases,
# Cargo reconciles the released lockfile with the removed updater and the local existing library.
$base=Get-Content -LiteralPath (Join-Path $repository 'base-versions.json') -Raw | ConvertFrom-Json
$state=Get-Content -LiteralPath (Join-Path $buildRoot 'state.json') -Raw | ConvertFrom-Json
Set-Item -Path ('Env:\'+$App.ToUpperInvariant()+'_BUILD_SHA') -Value $state.sources.$App.commit
Set-Item -Path ('Env:\'+$App.ToUpperInvariant()+'_BUILD_DATE') -Value ([DateTime]::UtcNow.ToString('yyyy-MM-dd'))
$buildArgs=@('build','--manifest-path',$manifest,'--release','--target','x86_64-pc-windows-gnu','-p',$App,'-p',($App+'-cli'))
if ($state.sources.$App.tag -eq $base.upstream.$App) { $buildArgs+='--locked' }
& $cargo @buildArgs
if ($LASTEXITCODE -ne 0) { throw ('Native build failed: '+$App) }
if ($App -eq 'pdfcraft') {
    $portableSource=Join-Path $config.source 'crates\ui-egui\src\platform\portable.rs'
    $rustPath=$portableSource.Replace('\','/') | ConvertTo-Json -Compress
    $shim='extern crate self as log;' + "`n" + '#[macro_export] macro_rules! info { ($($a:tt)*) => {{ let _ = format_args!($($a)*); }}; }' + "`n" + '#[macro_export] macro_rules! warn { ($($a:tt)*) => {{ let _ = format_args!($($a)*); }}; }' + "`n" + '#[path = '+$rustPath+'] mod portable;'
    $portableSource=Join-Path $buildRoot 'tests\pdf-profile.rs'
    [IO.File]::WriteAllText($portableSource,$shim,[Text.UTF8Encoding]::new($false))
} else {
    $crate=if ($App -eq 'wordcraft') { 'control-key' } else { 'engine' }
    $portableSource=Join-Path $config.source ('crates\'+$crate+'\src\portable.rs')
}
$testBinary=Join-Path $buildRoot ('tests\'+$App+'-profile.exe')
& $rustc --edition 2024 --test '-C' ('linker='+$env:CC) $portableSource -o $testBinary
if ($LASTEXITCODE -ne 0) { throw 'Profile test compilation failed' }
& $testBinary
if ($LASTEXITCODE -ne 0) { throw 'Native portable storage regression test failed' }
if ($App -eq 'wordcraft') {
    & $cargo test --manifest-path $manifest --release --locked --target x86_64-pc-windows-gnu -p craft-font-store portable_profile_separates_settings_indexes_staging_and_locks
    if ($LASTEXITCODE -ne 0) { throw 'Language-store separation test failed' }
}
python (Join-Path $PSScriptRoot 'office.py') stage $App
if ($LASTEXITCODE -ne 0) { throw 'Binary / resource validation failed' }
$title=@{wordcraft='Word';gridcraft='Excel';deckcraft='PowerPoint';pdfcraft='PDF'}[$App]
$exe=Join-Path $buildRoot ('out\'+$title+'\'+$title+'.exe')
if ((Get-Item -LiteralPath $exe).VersionInfo.ProductName -ne $title) { throw 'The executable has the wrong Windows display name' }

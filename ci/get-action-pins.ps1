$ErrorActionPreference='Stop'
function Get-GitHubJson([string]$ApiPath) {
    for ($attempt=0;$attempt -lt 4;$attempt++) {
        $result=& gh api $ApiPath 2>$null
        if ($LASTEXITCODE -eq 0) { return ($result | ConvertFrom-Json) }
        if ($attempt -lt 3) { Start-Sleep -Seconds 2 }
    }
    throw ('Could not resolve official action: '+$ApiPath)
}
$pins=@{}
foreach ($action in @('checkout','upload-artifact','download-artifact','cache')) {
    $release=Get-GitHubJson ('repos/actions/'+$action+'/releases/latest')
    $reference=Get-GitHubJson ('repos/actions/'+$action+'/git/ref/tags/'+$release.tag_name)
    $object=$reference.object
    for ($depth=0;$object.type -eq 'tag' -and $depth -lt 4;$depth++) { $object=(Get-GitHubJson ('repos/actions/'+$action+'/git/tags/'+$object.sha)).object }
    if ($object.type -ne 'commit' -or $object.sha -notmatch '^[0-9a-f]{40}$') { throw 'Invalid official action commit' }
    $pins[$action]=@{tag=$release.tag_name;sha=$object.sha}
}
$pins | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'action-pins.json') -Encoding utf8
$pins | ConvertTo-Json -Depth 5

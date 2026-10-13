[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)]
    [ValidateSet('Import','Check','List','View','Create','Edit','Comment','Close')]
    [string]$Action,
    [Parameter(Mandatory=$true)][string]$Repository,
    [string]$TokenFile,
    [int]$IssueNumber,
    [string]$Title,
    [string]$BodyFile,
    [string[]]$Labels
)
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'This helper requires Windows user credential encryption.' }
if ($Repository -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') { throw 'Use an explicit owner/repo.' }
if ($Action -in @('View','Edit','Comment','Close') -and $IssueNumber -lt 1) { throw 'A positive IssueNumber is required.' }
if ($Action -eq 'Create' -and [string]::IsNullOrWhiteSpace($Title)) { throw 'Create requires Title.' }
if ($Action -eq 'Comment' -and -not $BodyFile) { throw 'Comment requires BodyFile.' }
$credentialRoot = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Codex\credentials'
$credentialPath = Join-Path $credentialRoot ($Repository.Replace('/','--') + '.dpapi')
$secureToken = $null
$plainToken = $null
$sourceText = $null
$tokenMatches = $null
$headers = $null

function Invoke-GitHub([string]$Method, [string]$Endpoint, $Payload = $null) {
    $request = @{ Uri=('https://api.github.com/' + $Endpoint); Method=$Method; Headers=$headers; ErrorAction='Stop' }
    if ($null -ne $Payload) {
        $request.ContentType = 'application/json; charset=utf-8'
        $request.Body = [Text.Encoding]::UTF8.GetBytes(($Payload | ConvertTo-Json -Depth 8 -Compress))
    }
    try { Invoke-RestMethod @request }
    catch {
        $statusCode = 'unknown'
        if ($_.Exception.Response) { $statusCode = [int]$_.Exception.Response.StatusCode }
        throw "GitHub request failed (HTTP $statusCode). Check authentication, repository access and permissions."
    }
}

try {
    if ($Action -eq 'Import') {
        if (-not $TokenFile) { throw 'Import requires a local TokenFile.' }
        $sourceText = [IO.File]::ReadAllText((Resolve-Path -LiteralPath $TokenFile).Path)
        $tokenMatches = [regex]::Matches($sourceText, '(?<![A-Za-z0-9_])(?:github_pat_[A-Za-z0-9_]+|ghp_[A-Za-z0-9]+)(?![A-Za-z0-9_])')
        $uniqueTokens = @($tokenMatches | ForEach-Object { $_.Value } | Select-Object -Unique)
        if ($uniqueTokens.Count -ne 1) { throw 'The file must contain exactly one distinct GitHub PAT; its contents were not printed.' }
        $plainToken = $uniqueTokens[0]
        $uniqueTokens = $null
        $secureToken = ConvertTo-SecureString -String $plainToken -AsPlainText -Force
    } else {
        if (-not (Test-Path -LiteralPath $credentialPath -PathType Leaf)) { throw 'No encrypted credential for this repository. Import a fresh local token first.' }
        $secureToken = ConvertTo-SecureString -String ([IO.File]::ReadAllText($credentialPath))
        $tokenPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
        try { $plainToken = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($tokenPointer) }
        finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($tokenPointer) }
    }
    $headers = @{ Authorization=('Bearer ' + $plainToken); Accept='application/vnd.github+json'; 'User-Agent'='Codex-Issue-Helper'; 'X-GitHub-Api-Version'='2022-11-28' }
    $endpoint = 'repos/' + $Repository
    if ($Action -in @('Import','Check')) {
        $profile = Invoke-GitHub 'GET' 'user'
        $repo = Invoke-GitHub 'GET' $endpoint
        $null = Invoke-GitHub 'GET' ($endpoint + '/issues?per_page=1')
        if ($Action -eq 'Import') {
            $null = New-Item -ItemType Directory -Path $credentialRoot -Force
            $identitySid = [Security.Principal.WindowsIdentity]::GetCurrent().User
            $folderAcl = Get-Acl -LiteralPath $credentialRoot
            $folderAcl.SetAccessRuleProtection($true, $false)
            foreach ($sid in @($identitySid, [Security.Principal.SecurityIdentifier]::new('S-1-5-18'))) {
                $rule = [Security.AccessControl.FileSystemAccessRule]::new($sid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
                $folderAcl.AddAccessRule($rule)
            }
            Set-Acl -LiteralPath $credentialRoot -AclObject $folderAcl
            $encryptedToken = ConvertFrom-SecureString -SecureString $secureToken
            $temporaryPath = Join-Path $credentialRoot ([guid]::NewGuid().ToString() + '.tmp')
            try {
                [IO.File]::WriteAllText($temporaryPath, $encryptedToken)
                Move-Item -LiteralPath $temporaryPath -Destination $credentialPath -Force
            } finally {
                if (Test-Path -LiteralPath $temporaryPath) { Remove-Item -LiteralPath $temporaryPath }
            }
        }
        [pscustomobject]@{account=$profile.login; repository=$repo.full_name; authenticated=$true; issue_read=$true; issue_write='not tested'; credential_source='Windows CurrentUser DPAPI'; credential_path=$credentialPath} | ConvertTo-Json
    } elseif ($Action -eq 'List') {
        $listedIssues = Invoke-GitHub 'GET' ($endpoint + '/issues?state=open&per_page=100')
        $summaries = @($listedIssues | Where-Object { -not $_.pull_request } | Select-Object number,title,state,html_url,labels)
        ConvertTo-Json -InputObject $summaries -Depth 6
    } else {
        $payload = @{}
        if ($Title) { $payload.title = $Title }
        if ($BodyFile) { $payload.body = [IO.File]::ReadAllText((Resolve-Path -LiteralPath $BodyFile).Path) }
        if ($PSBoundParameters.ContainsKey('Labels')) { $payload.labels = @($Labels) }
        switch ($Action) {
            'View' { $issue = Invoke-GitHub 'GET' ($endpoint + '/issues/' + $IssueNumber) }
            'Create' { $issue = Invoke-GitHub 'POST' ($endpoint + '/issues') $payload }
            'Edit' { if ($payload.Count -eq 0) { throw 'Edit requires Title, BodyFile or Labels.' }; $issue = Invoke-GitHub 'PATCH' ($endpoint + '/issues/' + $IssueNumber) $payload }
            'Comment' { $issue = Invoke-GitHub 'POST' ($endpoint + '/issues/' + $IssueNumber + '/comments') @{body=$payload.body} }
            'Close' { $issue = Invoke-GitHub 'PATCH' ($endpoint + '/issues/' + $IssueNumber) @{state='closed'} }
        }
        $issue | Select-Object number,title,state,html_url,labels,body | ConvertTo-Json -Depth 6
    }
} finally {
    $headers = $null
    $plainToken = $null
    $sourceText = $null
    $tokenMatches = $null
    if ($secureToken) { $secureToken.Dispose() }
}

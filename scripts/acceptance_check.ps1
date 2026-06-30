param(
    [string]$ApiBaseUrl = "http://127.0.0.1:8765",
    [string]$PythonExe = "python",
    [switch]$StartServer,
    [switch]$KeepServer,
    [string]$Topic = "Redis",
    [string]$Question = "Why is Redis suitable for caching?",
    [string]$StudentAnswer = "Redis is memory-based, fast to access, and supports multiple data structures, so it works well for high-frequency read scenarios.",
    [int]$QuestionCount = 4,
    [int]$StartupRetries = 20,
    [int]$StartupSleepSeconds = 2,
    [string]$ReportDir = ".\backend\data\acceptance_reports"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot
Add-Type -AssemblyName System.Net.Http
$HttpClient = New-Object System.Net.Http.HttpClient
$HttpClient.Timeout = [TimeSpan]::FromSeconds(180)

if ([System.IO.Path]::IsPathRooted($ReportDir)) {
    $ResolvedReportDir = $ReportDir
}
else {
    $ResolvedReportDir = Join-Path $ProjectRoot $ReportDir
}

function Write-Section {
    param([string]$Title)
    Write-Host ""
    Write-Host "=== $Title ===" -ForegroundColor Cyan
}

function Invoke-StudyLoopApi {
    param(
        [string]$Method,
        [string]$Path,
        [object]$Body = $null
    )

    $uri = "$ApiBaseUrl$Path"
    $request = New-Object System.Net.Http.HttpRequestMessage ([System.Net.Http.HttpMethod]::$Method), $uri
    $request.Headers.Accept.Clear()
    $request.Headers.Accept.Add(
        [System.Net.Http.Headers.MediaTypeWithQualityHeaderValue]::new("application/json")
    )

    if ($null -ne $Body) {
        $jsonBody = $Body | ConvertTo-Json -Depth 10
        $utf8Body = [System.Text.Encoding]::UTF8.GetBytes($jsonBody)
        $content = New-Object System.Net.Http.ByteArrayContent(,$utf8Body)
        $content.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::new("application/json")
        $content.Headers.ContentType.CharSet = "utf-8"
        $request.Content = $content
    }

    $response = $HttpClient.SendAsync($request).GetAwaiter().GetResult()
    $responseBytes = $response.Content.ReadAsByteArrayAsync().GetAwaiter().GetResult()
    $responseText = [System.Text.Encoding]::UTF8.GetString($responseBytes)

    if (-not $response.IsSuccessStatusCode) {
        throw "HTTP $([int]$response.StatusCode) $($response.ReasonPhrase): $responseText"
    }
    if ([string]::IsNullOrWhiteSpace($responseText)) {
        return $null
    }

    return $responseText | ConvertFrom-Json
}

function New-CheckResult {
    param(
        [string]$Name,
        [bool]$Passed,
        [string]$Summary,
        [object]$Payload = $null
    )

    return [PSCustomObject]@{
        name = $Name
        passed = $Passed
        summary = $Summary
        payload = $Payload
    }
}

function Wait-ForHealth {
    for ($index = 1; $index -le $StartupRetries; $index++) {
        try {
            $health = Invoke-StudyLoopApi -Method "GET" -Path "/health"
            if ($health.status -eq "ok") {
                return $health
            }
        }
        catch {
            # Retry until the service is up.
        }
        Start-Sleep -Seconds $StartupSleepSeconds
    }
    throw "Backend health check timed out. Please confirm uvicorn started successfully."
}

$startedProcess = $null
$checks = New-Object System.Collections.Generic.List[object]
$rawPayloads = [ordered]@{}

try {
    if ($StartServer) {
        Write-Section "Start Backend"
        $apiUri = [Uri]$ApiBaseUrl
        $port = if ($apiUri.Port -gt 0) { $apiUri.Port } else { 8765 }
        $arguments = @(
            "-m",
            "uvicorn",
            "backend.app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "$port"
        )
        $startedProcess = Start-Process `
            -FilePath $PythonExe `
            -ArgumentList $arguments `
            -WorkingDirectory $ProjectRoot `
            -WindowStyle Hidden `
            -PassThru
        Write-Host "Started backend process. PID=$($startedProcess.Id)"
    }

    Write-Section "Health"
    $health = Wait-ForHealth
    $rawPayloads["health"] = $health
    $checks.Add(
        (New-CheckResult -Name "health" -Passed ($health.status -eq "ok") -Summary "llm_mode=$($health.llm_mode)" -Payload $health)
    )

    Write-Section "Topics"
    $topicsPayload = Invoke-StudyLoopApi -Method "GET" -Path "/study/topics"
    $rawPayloads["topics"] = $topicsPayload
    $topicNames = @(
        $topicsPayload.topics |
        ForEach-Object {
            @($_.name, $_.display_name, $_.full_path_label) |
            Where-Object { $_ }
        }
    )
    $topicHit = $false
    foreach ($name in $topicNames) {
        if ([string]$name -like "*$Topic*") {
            $topicHit = $true
            break
        }
    }
    $checks.Add(
        (New-CheckResult `
            -Name "topics" `
            -Passed (($topicsPayload.topics.Count -gt 0) -and $topicHit) `
            -Summary "topics=$($topicsPayload.topics.Count), topic_hit=$topicHit" `
            -Payload $topicsPayload)
    )

    Write-Section "Explain"
    $explainPayload = Invoke-StudyLoopApi -Method "POST" -Path "/study/explain" -Body @{
        question = $Question
        current_topic = $Topic
    }
    $rawPayloads["explain"] = $explainPayload
    $explainPassed = (
        -not [string]::IsNullOrWhiteSpace([string]$explainPayload.answer)
    ) -and (
        $explainPayload.evidence.Count -gt 0
    )
    $checks.Add(
        (New-CheckResult `
            -Name "explain" `
            -Passed $explainPassed `
            -Summary "answer_len=$(([string]$explainPayload.answer).Length), evidence=$($explainPayload.evidence.Count)" `
            -Payload $explainPayload)
    )

    Write-Section "Quiz"
    $quizPayload = Invoke-StudyLoopApi -Method "POST" -Path "/study/quiz" -Body @{
        current_topic = $Topic
        question_count = $QuestionCount
        question_types = @("multiple_choice", "open_ended")
        focus_mode = "manual"
    }
    $rawPayloads["quiz"] = $quizPayload
    $questions = @($quizPayload.quiz_set.questions)
    $questionTypes = @($questions | ForEach-Object { $_.question_type } | Where-Object { $_ })
    $hasOpenEnded = $questionTypes -contains "open_ended"
    $hasMultipleChoice = $questionTypes -contains "multiple_choice"
    $quizPassed = (
        $questions.Count -ge $QuestionCount
    ) -and $hasOpenEnded -and $hasMultipleChoice
    $checks.Add(
        (New-CheckResult `
            -Name "quiz" `
            -Passed $quizPassed `
            -Summary "questions=$($questions.Count), open_ended=$hasOpenEnded, multiple_choice=$hasMultipleChoice" `
            -Payload $quizPayload)
    )

    Write-Section "Grade"
    $gradeQuestion = $Question
    $referenceAnswer = $null
    $openQuestion = $questions | Where-Object { $_.question_type -eq "open_ended" } | Select-Object -First 1
    if ($null -ne $openQuestion) {
        $gradeQuestion = [string]$openQuestion.question
        $referenceAnswer = [string]$openQuestion.reference_answer
    }
    $gradeBody = @{
        question = $gradeQuestion
        student_answer = $StudentAnswer
        current_topic = $Topic
    }
    if (-not [string]::IsNullOrWhiteSpace($referenceAnswer)) {
        $gradeBody["reference_answer"] = $referenceAnswer
    }
    $gradePayload = Invoke-StudyLoopApi -Method "POST" -Path "/study/grade" -Body $gradeBody
    $rawPayloads["grade"] = $gradePayload
    $gradePassed = (
        $null -ne $gradePayload.result
    ) -and (
        $null -ne $gradePayload.mastery_after
    ) -and (
        $null -ne $gradePayload.next_plan
    )
    $checks.Add(
        (New-CheckResult `
            -Name "grade" `
            -Passed $gradePassed `
            -Summary "score=$($gradePayload.result.score), mastery_after=$($gradePayload.mastery_after), remediation=$($null -ne $gradePayload.remediation_quiz)" `
            -Payload $gradePayload)
    )

    Write-Section "State"
    $statePayload = Invoke-StudyLoopApi -Method "GET" -Path "/study/state"
    $rawPayloads["state"] = $statePayload
    $statePassed = (
        $null -ne $statePayload.last_grade
    ) -or (
        $null -ne $statePayload.last_auto_context
    )
    $checks.Add(
        (New-CheckResult `
            -Name "state" `
            -Passed $statePassed `
            -Summary "has_last_grade=$($null -ne $statePayload.last_grade), has_last_auto_context=$($null -ne $statePayload.last_auto_context)" `
            -Payload $statePayload)
    )

    Write-Section "Results"
    foreach ($check in $checks) {
        $color = if ($check.passed) { "Green" } else { "Red" }
        $status = if ($check.passed) { "PASS" } else { "FAIL" }
        Write-Host "[$status] $($check.name) -> $($check.summary)" -ForegroundColor $color
    }

    $passedCount = @($checks | Where-Object { $_.passed }).Count
    $failedCount = $checks.Count - $passedCount

    New-Item -ItemType Directory -Path $ResolvedReportDir -Force | Out-Null
    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $reportPath = Join-Path $ResolvedReportDir "acceptance_$timestamp.json"
    $report = [ordered]@{
        api_base_url = $ApiBaseUrl
        topic = $Topic
        question = $Question
        started_server = [bool]$StartServer
        checks = $checks
        raw_payloads = $rawPayloads
        generated_at = (Get-Date).ToString("s")
    }
    $reportJson = $report | ConvertTo-Json -Depth 12
    [System.IO.File]::WriteAllText(
        $reportPath,
        $reportJson,
        [System.Text.UTF8Encoding]::new($false)
    )

    Write-Host ""
    Write-Host "Summary: $passedCount/$($checks.Count) passed, $failedCount failed"
    Write-Host "Report: $reportPath"

    if ($failedCount -gt 0) {
        exit 1
    }
}
finally {
    if ($null -ne $startedProcess -and -not $KeepServer) {
        try {
            Stop-Process -Id $startedProcess.Id -Force -ErrorAction Stop
            Write-Host "Stopped backend process started by this script. PID=$($startedProcess.Id)"
        }
        catch {
            Write-Warning "Failed to stop backend process automatically. Please check PID=$($startedProcess.Id)"
        }
    }
    if ($null -ne $HttpClient) {
        $HttpClient.Dispose()
    }
}
